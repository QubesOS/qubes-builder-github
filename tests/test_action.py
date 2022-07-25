import datetime
import os.path
import subprocess
import tempfile
from pathlib import Path

import dnf
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


def test_action_build_component(workdir):
    tmpdir, env = workdir

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--local-log-file",
        f"{tmpdir}/build-component.log",
        "--signer-fpr",
        FEPITRE_FPR,
        "build-component",
        f"{tmpdir}/qubes-builderv2",
        f"{tmpdir}/builder.yml",
        "app-linux-split-gpg",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)
    _build_component_check(tmpdir)


def _build_component_check(tmpdir):
    assert (
        tmpdir
        / f"artifacts/components/app-linux-split-gpg/2.0.60-1/host-fc32/publish/gpg-split-dom0.publish.yml"
    ).exists()

    assert (
        tmpdir
        / f"artifacts/components/app-linux-split-gpg/2.0.60-1/vm-bullseye/publish/debian.publish.yml"
    ).exists()

    assert (
        tmpdir
        / f"artifacts/components/app-linux-split-gpg/2.0.60-1/vm-fc36/publish/gpg-split.publish.yml"
    ).exists


def test_action_upload_component(workdir):
    tmpdir, env = workdir

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--local-log-file",
        f"{tmpdir}/upload-component.log",
        "--signer-fpr",
        FEPITRE_FPR,
        "upload-component",
        f"{tmpdir}/qubes-builderv2",
        f"{tmpdir}/builder.yml",
        "app-linux-split-gpg",
        "c5316c91107b8930ab4dc3341bc75293139b5b84",
        "security-testing",
        "--distribution",
        "vm-bullseye",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)

    _fix_timestamp_repo(tmpdir)

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--local-log-file",
        f"{tmpdir}/upload-component.log",
        "--signer-fpr",
        FEPITRE_FPR,
        "upload-component",
        f"{tmpdir}/qubes-builderv2",
        f"{tmpdir}/builder.yml",
        "app-linux-split-gpg",
        "c5316c91107b8930ab4dc3341bc75293139b5b84",
        "current",
        "--distribution",
        "all",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)
    _upload_component_check(tmpdir)


def _fix_timestamp_repo(tmpdir):
    for distribution in ["host-fc32", "vm-bullseye", "vm-fc36"]:
        if distribution == "host-fc32":
            artifacts_path = (
                tmpdir
                / f"artifacts/components/app-linux-split-gpg/2.0.60-1/{distribution}/publish/gpg-split-dom0.publish.yml"
            )
        elif distribution == "vm-bullseye":
            artifacts_path = (
                tmpdir
                / f"artifacts/components/app-linux-split-gpg/2.0.60-1/{distribution}/publish/debian.publish.yml"
            )
        else:
            artifacts_path = (
                tmpdir
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


def _upload_component_check(tmpdir):
    # host-fc32
    rpms = [
        "qubes-gpg-split-dom0-2.0.60-1.fc32.src.rpm",
        "qubes-gpg-split-dom0-2.0.60-1.fc32.x86_64.rpm",
    ]
    for repository in ["current-testing", "security-testing", "current"]:
        repository_dir = f"file://{tmpdir}/artifacts/repository-publish/rpm/r4.2/{repository}/host/fc32"
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
        repository_dir = f"file://{tmpdir}/artifacts/repository-publish/rpm/r4.2/{repository}/vm/fc36"
        packages = rpm_packages_list(repository_dir)
        assert set(rpms) == set(packages)

    # vm-bullseye
    repository_dir = tmpdir / "artifacts/repository-publish/deb/r4.2/vm"
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
    tmpdir, env = workdir

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    with open(tmpdir / "timestamp", "w") as f:
        f.write(timestamp)

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--local-log-file",
        f"{tmpdir}/build-template.log",
        "--signer-fpr",
        FEPITRE_FPR,
        "build-template",
        f"{tmpdir}/qubes-builderv2",
        f"{tmpdir}/builder.yml",
        "debian-11",
        timestamp,
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)
    _build_template_check(tmpdir)


def _build_template_check(tmpdir):
    assert (tmpdir / f"artifacts/templates/debian-11.publish.yml").exists()


def test_action_upload_template(workdir):
    tmpdir, env = workdir

    with open(tmpdir / "timestamp", "r") as f:
        build_timestamp = f.read().rstrip("\n")

    _fix_template_timestamp_repo(tmpdir)

    cmd = [
        str(PROJECT_PATH / "github-action.py"),
        "--local-log-file",
        f"{tmpdir}/upload-template.log",
        "--signer-fpr",
        FEPITRE_FPR,
        "upload-template",
        f"{tmpdir}/qubes-builderv2",
        f"{tmpdir}/builder.yml",
        "debian-11",
        build_timestamp,
        f"4.1.0-{build_timestamp}",
        "templates-itl",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)
    _upload_template_check(tmpdir)


def _fix_template_timestamp_repo(tmpdir):
    artifacts_path = tmpdir / f"artifacts/templates/debian-11.publish.yml"
    info = yaml.safe_load(artifacts_path.read())
    publish_timestamp = None
    for repo in info["repository-publish"]:
        if repo["name"] == "templates-itl-testing":
            publish_timestamp = datetime.datetime.strptime(
                repo["timestamp"], "%Y%m%d%H%M"
            )
            break

    if not publish_timestamp:
        raise ValueError("Cannot find timestamp value.")

    for repo in info["repository-publish"]:
        if repo["name"] == "templates-itl-testing":
            repo["timestamp"] = (
                publish_timestamp - datetime.timedelta(days=7)
            ).strftime("%Y%m%d%H%M")
            break

    with open(artifacts_path, "w") as f:
        f.write(yaml.dump(info))


def _upload_template_check(tmpdir):
    with open(tmpdir / "timestamp", "r") as f:
        build_timestamp = f.read().rstrip("\n")

    # host-fc32
    rpms = [
        f"qubes-template-debian-11-4.1.0-{build_timestamp}.noarch.rpm",
    ]
    for repository in ["templates-itl-testing", "templates-itl"]:
        repository_dir = (
            f"file://{tmpdir}/artifacts/repository-publish/rpm/r4.2/{repository}"
        )
        packages = rpm_packages_list(repository_dir)
        assert set(rpms) == set(packages)
