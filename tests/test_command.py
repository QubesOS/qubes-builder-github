import logging
import os.path
import re
import shutil
import subprocess
import tempfile
import yaml
import dateutil.parser
from dateutil.parser import parse as parsedate
from datetime import datetime, timedelta

from pathlib import Path

PROJECT_PATH = Path(__file__).resolve().parents[1]
DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder.yml"

FEPITRE_FPR = "9FA64B92F95E706BF28E2CA6484010B5CDC576E2"
TESTUSER_FPR = "632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"

# def qb_call(builder_conf, *args, **kwargs):
#     subprocess.check_call(
#         [PROJECT_PATH / "qb", "--verbose", "--builder-conf", builder_conf, *args],
#         **kwargs,
#     )
#
#
# def qb_call_output(builder_conf, *args, **kwargs):
#     return subprocess.check_output(
#         [PROJECT_PATH / "qb", "--verbose", "--builder-conf", builder_conf, *args],
#         **kwargs,
#     )


def create_builders_list(builders, directory):
    with open(f"{directory}/builders.list", "w") as f:
        for line in builders:
            release, builder_dir, builder_conf = line
            f.write(f"{release}={builder_dir}={builder_conf}")


def test_build_component():
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Better copy testing keyring into a separate directory to prevent locks inside
        # local sources (when executed locally).
        gnupghome = f"{tmpdir}/.gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        # Enforce keyring location
        env["GNUPGHOME"] = gnupghome

        # We prevent rpm to find ~/.rpmmacros
        env["HOME"] = tmpdir

        # Copy builder.yml
        shutil.copy2(DEFAULT_BUILDER_CONF, tmpdir)

        with open(f"{tmpdir}/builder.yml", "a") as f:
            f.write(f"""
artifacts-dir: {tmpdir}/artifacts

repository-upload-remote-host:
  rpm: {tmpdir}/repo/rpm/r4.2
  deb: {tmpdir}/repo/deb/r4.2
""")

        # Create builder list
        create_builders_list(
            [("r4.2", f"{tmpdir}/qubes-builderv2", f"{tmpdir}/builder.yml")], tmpdir
        )

        # Clone qubes-builderv2
        subprocess.run(
            [
                "git",
                "-C",
                tmpdir,
                "clone",
                "-b",
                "devel",
                "https://github.com/fepitre/qubes-builderv2",
            ]
        )

        with open(f"{tmpdir}/command", "w") as f:
            f.write(f"Build-component r4.2 app-linux-split-gpg")

        cmd = [
            str(PROJECT_PATH / "github-command.py"),
            "--scripts-dir",
            str(PROJECT_PATH),
            "--config-file",
            f"{tmpdir}/builders.list",
            "--signer-fpr",
            FEPITRE_FPR,
            "Build-component",
            f"{tmpdir}/command",
        ]
        subprocess.run(cmd, check=True, capture_output=True)
