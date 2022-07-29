import datetime
import subprocess
import os

from test_action import (
    _build_component_check,
    _upload_component_check,
    _build_template_check,
    _upload_template_check,
    _fix_timestamp_repo,
    _fix_template_timestamp_repo,
)

FEPITRE_FPR = "9FA64B92F95E706BF28E2CA6484010B5CDC576E2"
TESTUSER_FPR = "632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"


def create_builders_list(directory):
    builders = [("r4.2", f"{directory}/qubes-builderv2", f"{directory}/builder.yml")]
    with open(f"{directory}/builders.list", "w") as f:
        for line in builders:
            release, builder_dir, builder_conf = line
            f.write(f"{release}={builder_dir}={builder_conf}")
    return builders


def fix_scripts_dir(tmpdir, logfile, env=None):
    if env is None:
        env = os.environ.copy()

    scripts_dir = tmpdir / "qubes-builder-github"

    subprocess.run(
        [f"gpg2 --export {TESTUSER_FPR} > {tmpdir}/trusted-keys-for-commands.gpg"],
        env=env,
        shell=True,
        check=True,
    )

    builder_maintainers_keyring = tmpdir / "builder-maintainers-keyring"
    if not builder_maintainers_keyring.exists():
        builder_maintainers_keyring.mkdir()
    subprocess.run(
        [
            f"gpg2 --import {tmpdir}/qubes-builder-github/keys/9FA64B92F95E706BF28E2CA6484010B5CDC576E2.asc {tmpdir}/qubes-builder-github/keys/qubes-developers-keys.asc "
            f"&& echo '427F11FD0FAA4B080123F01CDDFA1A3E36879494:6:' | gpg --import-ownertrust "
            f"&& echo '9FA64B92F95E706BF28E2CA6484010B5CDC576E2:6:' | gpg --import-ownertrust"
        ],
        env={"GNUPGHOME": f"{tmpdir}/builder-maintainers-keyring"},
        shell=True,
        check=True,
    )
    os.chmod(builder_maintainers_keyring, 0o700)

    # Use local keyring for trusted keys for updating builders
    content = (scripts_dir / "update-qubes-builder").read_text(encoding="utf-8")
    content = content.replace(
        'keyring_path="$HOME/.config/qubes-builder-github/builder-maintainers-keyring"',
        f'keyring_path="{tmpdir}/builder-maintainers-keyring"',
    )
    (scripts_dir / "update-qubes-builder").write_text(content, encoding="utf-8")

    # change config_file location
    content = (scripts_dir / "lib/functions.sh").read_text(encoding="utf-8")
    content = content.replace(
        'config_file="$HOME/.config/qubes-builder-github/builders.list"',
        f'config_file="{tmpdir}/builders.list"',
    )

    (scripts_dir / "lib/functions.sh").write_text(content, encoding="utf-8")

    for rpc in ["qubesbuilder.TriggerBuild", "qubesbuilder.ProcessGithubCommand"]:
        content = (scripts_dir / f"rpc-services/{rpc}").read_text(encoding="utf-8")

        # change scripts_dir location
        content = content.replace(
            'scripts_dir="/usr/local/lib/qubes-builder-github"',
            f'scripts_dir="{tmpdir}/qubes-builder-github"',
        )

        # wait for processes, set scripts dir and config file
        content = content.replace(
            '"$scripts_dir/github-command.py"',
            f'"$scripts_dir/github-command.py" --wait --scripts-dir {scripts_dir} --config-file {tmpdir / "builders.list"} --local-log-file {logfile}',
        )

        # Use local keyring for trusted keys for commands
        content = content.replace(
            'keyring_path="$HOME/.config/qubes-builder-github/trusted-keys-for-commands.gpg"',
            f'keyring_path="{tmpdir}/trusted-keys-for-commands.gpg"',
        )

        (scripts_dir / f"rpc-services/{rpc}").write_text(content, encoding="utf-8")


def generate_signed_upload_component_command(env, repository="current"):
    return subprocess.run(
        [
            f"echo Upload-component r4.2 app-linux-split-gpg c5316c91107b8930ab4dc3341bc75293139b5b84 {repository} all | gpg2 --clearsign -u {TESTUSER_FPR}"
        ],
        shell=True,
        check=True,
        capture_output=True,
        env=env,
    ).stdout


def generate_signed_build_template_command(env, timestamp=None):
    if not timestamp:
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    return subprocess.run(
        [
            f"echo Build-template r4.2 debian-11 {timestamp} | gpg2 --clearsign -u {TESTUSER_FPR}"
        ],
        shell=True,
        check=True,
        capture_output=True,
        env=env,
    ).stdout


def generate_signed_upload_template_command(
    env, timestamp=None, repository="templates-itl"
):
    if not timestamp:
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    return subprocess.run(
        [
            f"echo Upload-template r4.2 debian-11 4.1.0-{timestamp} {repository} | gpg2 --clearsign -u {TESTUSER_FPR}"
        ],
        shell=True,
        check=True,
        capture_output=True,
        env=env,
    ).stdout


def _parse_command(tmpdir, env, signed_command):
    subprocess.run(
        [
            str(tmpdir / "qubes-builder-github/lib/parse-command"),
            str(tmpdir / "command"),
            str(tmpdir / "command.sig"),
        ],
        check=True,
        env=env,
        capture_output=True,
        input=signed_command,
    )


def test_rpc_00_parse_command_upload_component(workdir):
    tmpdir, env = workdir
    signed_command = generate_signed_upload_component_command(env)
    _parse_command(tmpdir, env, signed_command)


def test_rpc_01_parse_command_build_template(workdir):
    tmpdir, env = workdir
    signed_command = generate_signed_build_template_command(env)
    _parse_command(tmpdir, env, signed_command)


def test_rpc_02_parse_command_upload_template(workdir):
    tmpdir, env = workdir
    signed_command = generate_signed_upload_template_command(env)
    _parse_command(tmpdir, env, signed_command)


def test_rpc_03_trigger_build(workdir):
    tmpdir, env = workdir

    # Create builder list
    create_builders_list(tmpdir)

    # Adapt RPC for tests
    fix_scripts_dir(tmpdir, logfile=str(tmpdir / "trigger-build.log"), env=env)

    subprocess.run(
        [
            str(tmpdir / "qubes-builder-github/rpc-services/qubesbuilder.TriggerBuild"),
            "app-linux-split-gpg",
        ],
        check=True,
        env=env,
    )
    _build_component_check(tmpdir)


def test_rpc_04_upload_component_command(workdir):
    tmpdir, env = workdir

    # Create builder list
    create_builders_list(tmpdir)

    # Adapt RPC for tests
    fix_scripts_dir(tmpdir, logfile=str(tmpdir / "upload-command.log"), env=env)

    # create signed upload command for 'security-testing' repository
    signed_command = generate_signed_upload_component_command(
        env, repository="security-testing"
    )
    subprocess.run(
        [
            str(
                tmpdir
                / "qubes-builder-github/rpc-services/qubesbuilder.ProcessGithubCommand"
            )
        ],
        input=signed_command,
        check=True,
        capture_output=True,
        env=env,
    )

    # fake time for 'current' repository
    _fix_timestamp_repo(tmpdir)

    # create signed upload command for 'current' repository
    signed_command = generate_signed_upload_component_command(env)
    subprocess.run(
        [
            str(
                tmpdir
                / "qubes-builder-github/rpc-services/qubesbuilder.ProcessGithubCommand"
            )
        ],
        input=signed_command,
        check=True,
        capture_output=True,
        env=env,
    )

    # check everything is in repositories as expected
    _upload_component_check(tmpdir)


def test_rpc_05_build_template_command(workdir):
    tmpdir, env = workdir

    # Create builder list
    create_builders_list(tmpdir)

    # Adapt RPC for tests
    fix_scripts_dir(tmpdir, logfile=str(tmpdir / "build-command.log"), env=env)

    # create signed build template command
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    with open(tmpdir / "timestamp", "w") as f:
        f.write(timestamp)
    signed_command = generate_signed_build_template_command(env)
    subprocess.run(
        [
            str(
                tmpdir
                / "qubes-builder-github/rpc-services/qubesbuilder.ProcessGithubCommand"
            )
        ],
        input=signed_command,
        check=True,
        capture_output=True,
        env=env,
    )

    # check everything is in repositories as expected
    _build_template_check(tmpdir)


def test_rpc_06_upload_template_command(workdir):
    tmpdir, env = workdir

    # Create builder list
    create_builders_list(tmpdir)

    # Adapt RPC for tests
    fix_scripts_dir(tmpdir, logfile=str(tmpdir / "upload-command.log"), env=env)

    # Get timestamp from build test
    with open(tmpdir / "timestamp", "r") as f:
        timestamp = f.read().rstrip("\n")

    # fake time for 'templates-itl' repository
    _fix_template_timestamp_repo(tmpdir)

    # create signed upload command for 'current' repository
    signed_command = generate_signed_upload_template_command(env, timestamp=timestamp)
    subprocess.run(
        [
            str(
                tmpdir
                / "qubes-builder-github/rpc-services/qubesbuilder.ProcessGithubCommand"
            )
        ],
        input=signed_command,
        check=True,
        capture_output=True,
        env=env,
    )

    # check everything is in repositories as expected
    _upload_template_check(tmpdir)
