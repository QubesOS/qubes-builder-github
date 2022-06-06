#!/usr/bin/python3

# This is script to automate build process in reaction to pushing updates
# sources to git. The workflow is:
# - fetch sources, check if properly signed
# - check if version tag is on top
# - build package(s) according to builder.yml
# - upload to current-testing repository
#
# All the above should be properly logged

import argparse
import datetime
import logging
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from qubesbuilder.cli.cli_package import _component_stage
from qubesbuilder.cli.cli_template import _template_stage
from qubesbuilder.cli.cli_repository import (
    _check_release_status_for_component,
    _check_release_status_for_template,
)
from qubesbuilder.config import Config
from qubesbuilder.log import init_logging
from qubesbuilder.component import ComponentError
from qubesbuilder.plugins import PluginError
from qubesbuilder.plugins.template import TEMPLATE_VERSION

PROJECT_PATH = Path(__file__).resolve().parent


def raise_timeout(signum, frame):
    raise TimeoutError


@contextmanager
def timeout(time):
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(time)
    try:
        yield
    except TimeoutError:
        pass
    finally:
        signal.signal(signal.SIGALRM, signal.SIG_IGN)


log = init_logging(level="DEBUG")
log.name = "auto-build"


class AutoBuildError(Exception):
    def __init__(self, log_file=None):
        self.log_file = log_file


class BaseAutoBuild:
    def __init__(self, builder_dir, state_dir):
        self.builder_dir = Path(builder_dir).resolve()
        self.builder_conf = self.builder_dir / "builder.yml"
        self.state_dir = Path(state_dir).resolve()
        self.config = Config(self.builder_conf)

        self.qubes_release = self.config.get("qubes-release")

        if not self.builder_dir.exists():
            raise AutoBuildError(f"No such directory for builder '{self.builder_dir}'.")

        self.state_dir.mkdir(exist_ok=True, parents=True)

        self.api_key = self.config.get("github", {}).get("api-key", None)
        self.build_report_repo = self.config.get("github", {}).get(
            "build-report-repo", "QubesOS/updates-status"
        )
        self.logs_repo = self.config.get("github", {}).get(
            "logs-repo", "QubesOS/build-logs"
        )

        self.env = os.environ.copy()
        self.env.update(
            {
                "PYTHONPATH": builder_dir,
                "GITHUB_API_KEY": self.api_key,
                "GITHUB_BUILD_REPORT_REPO": self.build_report_repo,
            }
        )

    def get_build_log_url(self, log_file):
        return f"https://github.com/{self.logs_repo}/tree/master/{log_file}"

    @staticmethod
    def display_head_info(args):
        pass

    def make_with_log(self, func, *args):
        with subprocess.Popen(
            ["qrexec-client-vm", "dom0", "qubesbuilder.BuildLog"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        ) as p:
            qrexec_stream = logging.StreamHandler(stream=p.stdin)

            log.addHandler(qrexec_stream)
            log.debug("> starting build with log")
            self.display_head_info(args)
            try:
                func(*args)
                log.debug("> done")
            except PluginError as e:
                p.stdin.close()
                p.wait()
                log_file = list(p.stdout)
                log_file = log_file[0].rstrip("\n")
                raise AutoBuildError(log_file=log_file) from e
            else:
                p.stdin.close()
                p.wait()
                log_file = list(p.stdout)
                log_file = log_file[0].rstrip("\n")
            finally:
                log.removeHandler(qrexec_stream)
            return log_file

    def run(self):
        try:
            subprocess.run(
                [str(PROJECT_PATH / "update-qubes-builder"), str(self.builder_dir)],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise AutoBuildError(
                f"Failed to update Qubes Builder at {self.builder_dir}."
            ) from exc


class AutoBuild(BaseAutoBuild):
    def __init__(self, builder_dir, component_name, state_dir):
        super().__init__(builder_dir, state_dir)

        self.components = self.config.get_components([component_name])
        self.distributions = self.config.get_distributions()

        if not self.components:
            raise AutoBuildError(f"No such component '{component_name}'.")

        self.repository_publish = self.config.get("repository-publish", {}).get(
            "components", None
        )
        if not self.repository_publish:
            raise AutoBuildError(f"No repository defined for component publication.")

    def run_stages(self, stages):
        for stage in stages:
            _component_stage(
                stage_name=stage,
                config=self.config,
                components=self.components,
                distributions=self.distributions,
            )

    def notify_build_status(self, dist, status, log_file=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/notify-issues",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        notify_issues_cmd += [
            "build",
            self.qubes_release,
            str(self.components[0].source_dir),
            self.components[0].name,
            dist.distribution,
            status,
        ]

        try:
            subprocess.run(notify_issues_cmd, env=self.env)
        except subprocess.CalledProcessError as e:
            msg = f"{self.components[0].name}:{dist}: Failed to notify GitHub: {str(e)}"
            log.error(msg)

    def notify_upload_status(self, dist, log_file=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/notify-issues",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        component = self.components[0]

        state_file = (
            self.state_dir
            / f"{self.qubes_release}-{component.name}-{dist.package_set}-{dist.name}-{self.repository_publish}"
            # type: ignore
        )
        stable_state_file = (
            self.state_dir
            / f"{self.qubes_release}-{component.name}-{dist.package_set}-{dist.name}-current"
            # type: ignore
        )
        notify_issues_cmd += [
            "upload",
            self.qubes_release,
            str(component.source_dir),
            component.name,
            dist.distribution,
            str(self.repository_publish),
            str(state_file),
            str(stable_state_file),
        ]

        try:
            subprocess.run(notify_issues_cmd, env=self.env)
        except subprocess.CalledProcessError as e:
            msg = f"{component.name}:{dist}: Failed to notify GitHub: {str(e)}"
            log.error(msg)

    def display_head_info(self, args):
        log.debug(f">> args:")
        log.debug(f">>   {args}")
        log.debug(f">> component:")
        log.debug(f">>   {self.components[0]}")
        try:
            log.debug(
                f">>     commit-hash: {self.components[0].get_source_commit_hash()}"
            )
            log.debug(f">>     source-hash: {self.components[0].get_source_hash()}")
        except ComponentError:
            # we may have not yet source (like calling fetch stage)
            pass
        log.debug(f">> distributions:")
        log.debug(f">>   {self.distributions}")

    def run(self):
        # Update Qubes Builder
        super().run()

        self.make_with_log(
            _component_stage, self.config, self.components, self.distributions, "fetch"
        )

        built_for_dist = []

        for dist in self.distributions:
            release_status = _check_release_status_for_component(
                config=self.config,
                components=self.components,
                distributions=[dist],
                abort_no_version=True,
                abort_on_empty=True,
                # no_print_version=True,
            )

            if (
                release_status.get(self.components[0].name, {})
                .get(dist.distribution, {})
                .get("status", None)
                == "not released"
            ):
                try:
                    self.notify_build_status(
                        dist,
                        "building",
                    )

                    build_log_file = self.make_with_log(
                        self.run_stages,
                        ["prep", "build"],
                    )

                    self.make_with_log(
                        self.run_stages,
                        ["sign", "publish", "upload"],
                    )

                    self.notify_upload_status(dist, build_log_file)

                    built_for_dist.append(dist)
                except AutoBuildError as autobuild_exc:
                    self.notify_build_status(
                        dist, "failed", log_file=autobuild_exc.log_file
                    )
                    pass
                except Exception as exc:
                    self.notify_build_status(dist, "failed")
                    log.error(str(exc))
                    pass

        if not built_for_dist:
            log.warning(
                "Nothing was built, something gone wrong or version tag was not found."
            )


class AutoBuildTemplate(BaseAutoBuild):
    def __init__(self, builder_dir, template_name, template_timestamp, state_dir):
        super().__init__(builder_dir, state_dir)

        self.templates = self.config.get_templates([template_name])
        self.template_timestamp = template_timestamp

        if not self.templates:
            raise AutoBuildError(f"No such template '{template_name}'.")

        self.repository_publish = self.config.get("repository-publish", {}).get(
            "templates", None
        )
        if not self.repository_publish:
            raise AutoBuildError(f"No repository defined for template publication.")

    def run_stages(self, stages):
        for stage in stages:
            _template_stage(
                stage_name=stage,
                config=self.config,
                templates=self.templates,
                template_timestamp=self.template_timestamp,
            )

    def notify_build_status(self, status, log_file=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/notify-issues",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        template = self.templates[0]
        package_name = f"qubes-template-{template.name}-{TEMPLATE_VERSION}-{self.template_timestamp}"

        notify_issues_cmd += [
            "build",
            self.qubes_release,
            str(self.builder_dir),
            package_name,
            template.distribution.distribution,
            status,
        ]

        try:
            subprocess.run(notify_issues_cmd, env=self.env)
        except subprocess.CalledProcessError as e:
            msg = f"{template}: Failed to notify GitHub: {str(e)}"
            log.error(msg)

    def notify_upload_status(self, log_file=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/notify-issues",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        template = self.templates[0]
        package_name = f"qubes-template-{template.name}-{TEMPLATE_VERSION}-{self.template_timestamp}"

        state_file = (
            self.state_dir
            / f"{self.qubes_release}-template-vm-{template.distribution.name}-{self.repository_publish}"
            # type: ignore
        )
        stable_state_file = (
            self.state_dir
            / f"{self.qubes_release}-template-vm-{template.distribution.name}-current"
            # type: ignore
        )
        notify_issues_cmd += [
            "upload",
            self.qubes_release,
            str(self.builder_dir),
            package_name,
            template.distribution.distribution,
            str(self.repository_publish),
            str(state_file),
            str(stable_state_file),
        ]

        try:
            subprocess.run(notify_issues_cmd, env=self.env)
        except subprocess.CalledProcessError as e:
            msg = f"{template}: Failed to notify GitHub: {str(e)}"
            log.error(msg)

    def run(self):
        # Update Qubes Builder
        super().run()

        timestamp_file = (
            self.config.get_artifacts_dir()
            / "templates"
            / f"build_timestamp_{self.templates[0].name}"
        )
        if timestamp_file.exists():
            try:
                timestamp_existing = datetime.datetime.strptime(
                    timestamp_file.read_text().rstrip("\n"), "%Y%m%d%H%MZ"
                )
                template_timestamp = datetime.datetime.strptime(
                    self.template_timestamp, "%Y%m%d%H%MZ"
                )
            except (OSError, ValueError) as exc:
                raise AutoBuildError(
                    f"Failed to read timestamp file: {str(exc)}"
                ) from exc
            if template_timestamp < timestamp_existing:
                log.info(
                    f"Newer template ({timestamp_existing.strftime('%Y%m%d%H%MZ')}) already built."
                )
                return

        release_status = _check_release_status_for_template(
            config=self.config, templates=self.templates
        )

        if (
            release_status.get(self.templates[0].name, {}).get("status", None)
            == "not released"
        ):
            try:
                self.notify_build_status(
                    "building",
                )

                build_log_file = self.make_with_log(
                    self.run_stages,
                    ["prep", "build"],
                )

                self.make_with_log(
                    self.run_stages,
                    ["sign", "publish", "upload"],
                )

                self.notify_upload_status(build_log_file)

            except AutoBuildError as autobuild_exc:
                self.notify_build_status("failed", log_file=autobuild_exc.log_file)
                pass
            except Exception as exc:
                self.notify_build_status("failed")
                log.error(str(exc))
                pass


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    # component parser
    component_parser = subparsers.add_parser("component")
    component_parser.set_defaults(command="component")
    component_parser.add_argument("builder_dir")
    component_parser.add_argument("component_name")
    component_parser.add_argument(
        "--state-dir", default=Path.home() / "github-notify-state"
    )

    # template parser
    template_parser = subparsers.add_parser("template")
    template_parser.set_defaults(command="template")
    template_parser.add_argument("builder_dir")
    template_parser.add_argument("template_name")
    template_parser.add_argument("template_timestamp")
    template_parser.add_argument(
        "--state-dir", default=Path.home() / "github-notify-state"
    )

    args = parser.parse_args()

    if args.command == "component":
        cli = AutoBuild(
            builder_dir=args.builder_dir,
            component_name=args.component_name,
            state_dir=args.state_dir,
        )
    else:
        cli = AutoBuildTemplate(
            builder_dir=args.builder_dir,
            template_name=args.template_name,
            template_timestamp=args.template_timestamp,
            state_dir=args.state_dir,
        )
    cli.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(str(e))
        sys.exit(1)
