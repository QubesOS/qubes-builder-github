import datetime
import subprocess
import time
from pathlib import Path

import psutil
import yaml

FEPITRE_FPR = "9FA64B92F95E706BF28E2CA6484010B5CDC576E2"
TESTUSER_FPR = "632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"


def get_all_processes():
    all_processes = []
    for i in range(50):
        for proc in psutil.process_iter():
            try:
                if not proc.cmdline():
                    continue
                cmd = " ".join(proc.cmdline())
                if cmd not in all_processes:
                    all_processes.append(cmd)
            except psutil.Error:
                pass
        time.sleep(0.1)
    return all_processes


def find_github_action(processes, cmdline):
    found = False
    for p in processes:
        if p == cmdline:
            found = True
    return found


def create_builders_list(directory):
    builders = [("r4.2", f"{directory}/qubes-builderv2", f"{directory}/builder.yml")]
    with open(f"{directory}/builders.list", "w") as f:
        for line in builders:
            release, builder_dir, builder_conf = line
            f.write(f"{release}={builder_dir}={builder_conf}")
    return builders


def set_dry_run(builder_conf):
    with open(builder_conf, "r") as f:
        conf = yaml.safe_load(f.read())

    conf["github"]["dry-run"] = True
    with open(builder_conf, "w") as f:
        f.write(yaml.dump(conf))


def test_command_00_build_component(workdir):
    # Create builder list
    builders_list = create_builders_list(workdir)

    # Write command
    with open(f"{workdir}/command", "w") as f:
        f.write(f"Build-component app-linux-split-gpg")

    # Dry-run
    set_dry_run(f"{workdir}/builder.yml")

    cmd = [
        str(workdir / "qubes-builder-github/github-command.py"),
        "--scripts-dir",
        str(workdir / "qubes-builder-github"),
        "--config-file",
        f"{workdir}/builders.list",
        "--signer-fpr",
        FEPITRE_FPR,
        "Build-component",
        f"{workdir}/command",
    ]
    command_process = subprocess.Popen(cmd)
    all_processes = get_all_processes()
    for b in builders_list:
        release, builder_dir, builder_conf = b
        cmdline = f"flock -s {builder_dir}/builder.lock bash -c {workdir / 'qubes-builder-github'}/github-action.py --signer-fpr {FEPITRE_FPR} build-component {builder_dir} {builder_conf} app-linux-split-gpg"
        if not find_github_action(all_processes, cmdline):
            raise ValueError(f"{cmdline}: cannot find process.")
    command_process.communicate()
    if command_process.poll() != 0:
        raise ValueError("github-command failed.")


def test_command_01_upload_component(workdir):
    commit_sha = "c5316c91107b8930ab4dc3341bc75293139b5b84"

    # Create builder list
    builders_list = create_builders_list(workdir)

    # Write command
    with open(f"{workdir}/command", "w") as f:
        f.write(f"Upload-component r4.2 app-linux-split-gpg {commit_sha} current all")

    # Dry-run
    set_dry_run(f"{workdir}/builder.yml")

    cmd = [
        str(workdir / "qubes-builder-github/github-command.py"),
        "--scripts-dir",
        str(workdir / "qubes-builder-github"),
        "--config-file",
        f"{workdir}/builders.list",
        "--signer-fpr",
        FEPITRE_FPR,
        "Upload-component",
        f"{workdir}/command",
    ]
    subprocess.run(cmd, check=True)
    all_processes = get_all_processes()
    for b in builders_list:
        release, builder_dir, builder_conf = b
        cmdline = f"flock -s {builder_dir}/builder.lock bash -c {workdir / 'qubes-builder-github'}/github-action.py --signer-fpr {FEPITRE_FPR} upload-component {builder_dir} {builder_conf} app-linux-split-gpg {commit_sha} current --distribution all"
        if not find_github_action(all_processes, cmdline):
            raise ValueError(f"{cmdline}: cannot find process.")


def test_command_02_build_template(workdir):
    # Create builder list
    builders_list = create_builders_list(workdir)

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    with open(workdir / "timestamp", "w") as f:
        f.write(timestamp)

    # Write command
    with open(f"{workdir}/command", "w") as f:
        f.write(f"Build-template r4.2 debian-11 {timestamp}")

    # Dry-run
    set_dry_run(f"{workdir}/builder.yml")

    cmd = [
        str(workdir / "qubes-builder-github/github-command.py"),
        "--scripts-dir",
        str(workdir / "qubes-builder-github"),
        "--config-file",
        f"{workdir}/builders.list",
        "--signer-fpr",
        FEPITRE_FPR,
        "Build-template",
        f"{workdir}/command",
    ]
    command_process = subprocess.Popen(cmd)
    all_processes = get_all_processes()
    for b in builders_list:
        release, builder_dir, builder_conf = b
        cmdline = f"flock -s {builder_dir}/builder.lock bash -c {workdir / 'qubes-builder-github'}/github-action.py --signer-fpr {FEPITRE_FPR} build-template {builder_dir} {builder_conf} debian-11 {timestamp}"
        if not find_github_action(all_processes, cmdline):
            raise ValueError(f"{cmdline}: cannot find process.")
    command_process.communicate()
    if command_process.poll() != 0:
        raise ValueError("github-command failed.")


def test_command_03_upload_template(workdir):
    # Create builder list
    builders_list = create_builders_list(workdir)

    with open(workdir / "timestamp", "r") as f:
        timestamp = f.read().rstrip("\n")

    # Write command
    with open(f"{workdir}/command", "w") as f:
        f.write(
            f"Upload-template r4.2 debian-11 {timestamp} 4.1.0-{timestamp} templates-itl"
        )

    # Dry-run
    set_dry_run(f"{workdir}/builder.yml")

    cmd = [
        str(workdir / "qubes-builder-github/github-command.py"),
        "--scripts-dir",
        str(workdir / "qubes-builder-github"),
        "--config-file",
        f"{workdir}/builders.list",
        "--signer-fpr",
        FEPITRE_FPR,
        "Upload-template",
        f"{workdir}/command",
    ]
    subprocess.run(cmd, check=True)
    all_processes = get_all_processes()
    for b in builders_list:
        release, builder_dir, builder_conf = b
        cmdline = f"flock -s {builder_dir}/builder.lock bash -c {workdir / 'qubes-builder-github'}/github-action.py --signer-fpr {FEPITRE_FPR} upload-template {builder_dir} {builder_conf} debian-11 {timestamp} 4.1.0-{timestamp} templates-itl"
        if not find_github_action(all_processes, cmdline):
            raise ValueError(f"{cmdline}: cannot find process.")
