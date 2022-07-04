import datetime
import logging
import os.path
import shutil
import subprocess
import tempfile
import pytest
import dnf
from pathlib import Path

import yaml

PROJECT_PATH = Path(__file__).resolve().parents[1]
DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder.yml"

FEPITRE_FPR = "9FA64B92F95E706BF28E2CA6484010B5CDC576E2"
TESTUSER_FPR = "632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"


# From fepitre/qubes-builderv2/tests/test_cli.py
def deb_packages_list(repository_dir, suite, **kwargs):
    return (
        subprocess.check_output(
            ["reprepro", "-b", repository_dir, "list", suite],
            **kwargs,
        )
        .decode()
        .splitlines()
    )


# From fepitre/qubes-builderv2/tests/test_cli.py
def rpm_packages_list(repository_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        base = dnf.Base()
        base.conf.installroot = tmpdir
        base.conf.cachedir = tmpdir + "/cache"
        base.repos.add_new_repo(
            repoid="local", conf=base.conf, baseurl=[repository_dir]
        )
        base.fill_sack()
        q = base.sack.query()
        return [str(p) + ".rpm" for p in q.available()]


@pytest.fixture(scope="session")
def workdir(tmpdir_factory):
    tmpdir = tmpdir_factory.mktemp("github-")

    # Better copy testing keyring into a separate directory to prevent locks inside
    # local sources (when executed locally).
    gnupghome = f"{tmpdir}/.gnupg"
    shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
    os.chmod(gnupghome, 0o700)

    # Copy builder.yml
    shutil.copy2(DEFAULT_BUILDER_CONF, tmpdir)

    with open(f"{tmpdir}/builder.yml", "a") as f:
        f.write(
            f"""
artifacts-dir: {tmpdir}/artifacts

repository-upload-remote-host:
  rpm: {tmpdir}/repo/rpm/r4.2
  deb: {tmpdir}/repo/deb/r4.2

executor:
  type: qubes
  options:
    dispvm: "qubes-builder-dvm"
"""
        )

    # Clone qubes-builderv2
    subprocess.run(
        [
            "git",
            "-C",
            str(tmpdir),
            "clone",
            "-b",
            "devel",
            "https://github.com/fepitre/qubes-builderv2",
        ]
    )

    yield tmpdir
    # shutil.rmtree(tmpdir)


def test_action_build_component(workdir):
    env = os.environ.copy()

    # Enforce keyring location
    env["GNUPGHOME"] = workdir / ".gnupg"

    # We prevent rpm to find ~/.rpmmacros
    env["HOME"] = workdir

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--signer-fpr",
        FEPITRE_FPR,
        "build-component",
        f"{workdir}/qubes-builderv2",
        f"{workdir}/builder.yml",
        "app-linux-split-gpg",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)


def test_action_upload_component(workdir):
    env = os.environ.copy()

    # Enforce keyring location
    env["GNUPGHOME"] = workdir / ".gnupg"

    # We prevent rpm to find ~/.rpmmacros
    env["HOME"] = workdir

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--signer-fpr",
        FEPITRE_FPR,
        "upload-component",
        f"{workdir}/qubes-builderv2",
        f"{workdir}/builder.yml",
        "app-linux-split-gpg",
        "c5316c91107b8930ab4dc3341bc75293139b5b84",
        "security-testing",
        "--distribution",
        "vm-bullseye",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)

    for distribution in ["host-fc32", "vm-bullseye", "vm-fc36"]:
        if distribution == "host-fc32":
            artifacts_path = (
                workdir
                / f"artifacts/components/app-linux-split-gpg/2.0.60-1/{distribution}/publish/gpg-split-dom0.publish.yml"
            )
        elif distribution == "vm-bullseye":
            artifacts_path = (
                workdir
                / f"artifacts/components/app-linux-split-gpg/2.0.60-1/{distribution}/publish/debian.publish.yml"
            )
        else:
            artifacts_path = (
                workdir
                / f"artifacts/components/app-linux-split-gpg/2.0.60-1/{distribution}/publish/gpg-split.publish.yml"
            )
        info = yaml.safe_load(artifacts_path.read())

        timestamp = None
        for repo in info["repository-publish"]:
            if repo["name"] == "current-testing":
                timestamp = datetime.datetime.strptime(repo["timestamp"], "%Y%m%d%H%M")
                break

        if not timestamp:
            raise ValueError("Cannot find timestamp value.")

        for repo in info["repository-publish"]:
            if repo["name"] == "current-testing":
                repo["timestamp"] = (timestamp - datetime.timedelta(days=7)).strftime(
                    "%Y%m%d%H%M"
                )
                break

        with open(artifacts_path, "w") as f:
            f.write(yaml.dump(info))

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--signer-fpr",
        FEPITRE_FPR,
        "upload-component",
        f"{workdir}/qubes-builderv2",
        f"{workdir}/builder.yml",
        "app-linux-split-gpg",
        "c5316c91107b8930ab4dc3341bc75293139b5b84",
        "current",
        "--distribution",
        "all",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)

    # host-fc32
    rpms = [
        "qubes-gpg-split-dom0-2.0.60-1.fc32.src.rpm",
        "qubes-gpg-split-dom0-2.0.60-1.fc32.x86_64.rpm",
    ]
    for repository in ["current-testing", "security-testing", "current"]:
        repository_dir = f"file://{workdir}/artifacts/repository-publish/rpm/r4.2/{repository}/host/fc32"
        packages = rpm_packages_list(repository_dir)
        assert set(rpms) == set(packages)

    # vm-fc36
    rpms = [
        "qubes-gpg-split-2.0.60-1.fc36.src.rpm",
        "qubes-gpg-split-2.0.60-1.fc36.x86_64.rpm",
        "qubes-gpg-split-tests-2.0.60-1.fc36.x86_64.rpm",
        "qubes-gpg-split-debuginfo-2.0.60-1.fc36.x86_64.rpm",
        "qubes-gpg-split-debugsource-2.0.60-1.fc36.x86_64.rpm",
    ]
    for repository in ["current-testing", "security-testing", "current"]:
        repository_dir = f"file://{workdir}/artifacts/repository-publish/rpm/r4.2/{repository}/vm/fc36"
        packages = rpm_packages_list(repository_dir)
        assert set(rpms) == set(packages)

    # vm-bullseye
    repository_dir = workdir / "artifacts/repository-publish/deb/r4.2/vm"
    for codename in ["bullseye-testing", "bullseye-securitytesting", "bullseye"]:
        packages = deb_packages_list(repository_dir, codename)
        expected_packages = [
            f"{codename}|main|amd64: qubes-gpg-split 2.0.60-1+deb11u1",
            f"{codename}|main|amd64: qubes-gpg-split-dbgsym 2.0.60-1+deb11u1",
            f"{codename}|main|amd64: qubes-gpg-split-tests 2.0.60-1+deb11u1",
            f"{codename}|main|source: qubes-gpg-split 2.0.60-1+deb11u1",
        ]
        assert set(packages) == set(expected_packages)


def test_action_build_template(workdir):
    env = os.environ.copy()

    # Enforce keyring location
    env["GNUPGHOME"] = workdir / ".gnupg"

    # We prevent rpm to find ~/.rpmmacros
    env["HOME"] = workdir

    with open(f"{workdir}/builder.yml", "a") as f:
        f.write(
            f"""
executor:
  type: qubes
  options:
    dispvm: "qubes-builder-dvm"
"""
        )

    # Clone qubes-builderv2
    subprocess.run(
        [
            "git",
            "-C",
            workdir,
            "clone",
            "-b",
            "devel",
            "https://github.com/fepitre/qubes-builderv2",
        ]
    )

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    with open(workdir/"timestamp", "w") as f:
        f.write(timestamp)

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--signer-fpr",
        FEPITRE_FPR,
        "build-template",
        f"{workdir}/qubes-builderv2",
        f"{workdir}/builder.yml",
        "debian-11",
        timestamp,
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)


def test_action_upload_template(workdir):
    env = os.environ.copy()

    # Enforce keyring location
    env["GNUPGHOME"] = workdir / ".gnupg"

    # We prevent rpm to find ~/.rpmmacros
    env["HOME"] = workdir

    with open(workdir/"timestamp", "r") as f:
        build_timestamp = f.read().rstrip("\n")

    artifacts_path = (
            workdir
            / f"artifacts/templates/debian-11.publish.yml"
    )

    info = yaml.safe_load(artifacts_path.read())

    publish_timestamp = None
    for repo in info["repository-publish"]:
        if repo["name"] == "templates-itl-testing":
            publish_timestamp = datetime.datetime.strptime(repo["timestamp"], "%Y%m%d%H%M")
            break

    if not publish_timestamp:
        raise ValueError("Cannot find timestamp value.")

    for repo in info["repository-publish"]:
        if repo["name"] == "templates-itl-testing":
            repo["timestamp"] = (publish_timestamp - datetime.timedelta(days=7)).strftime(
                "%Y%m%d%H%M"
            )
            break

    with open(artifacts_path, "w") as f:
        f.write(yaml.dump(info))

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--signer-fpr",
        FEPITRE_FPR,
        "upload-template",
        f"{workdir}/qubes-builderv2",
        f"{workdir}/builder.yml",
        "debian-11",
        build_timestamp,
        f"4.1.0-{build_timestamp}",
        "templates-itl",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)

    # host-fc32
    rpms = [
        f"qubes-template-debian-11-4.1.0-{build_timestamp}.noarch.rpm",
    ]
    for repository in ["templates-itl-testing", "templates-itl"]:
        repository_dir = f"file://{workdir}/artifacts/repository-publish/rpm/r4.2/{repository}"
        packages = rpm_packages_list(repository_dir)
        assert set(rpms) == set(packages)
