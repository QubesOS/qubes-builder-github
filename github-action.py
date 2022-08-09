#!/usr/bin/python3
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

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
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import List

from qubesbuilder.cli.cli_package import _component_stage
from qubesbuilder.cli.cli_template import _template_stage
from qubesbuilder.cli.cli_repository import (
    _publish,
    _upload,
    _check_release_status_for_component,
    _check_release_status_for_template,
)
from qubesbuilder.config import Config
from qubesbuilder.log import init_logging
from qubesbuilder.component import ComponentError
from qubesbuilder.plugins import PluginError
from qubesbuilder.plugins.template import TEMPLATE_VERSION

PROJECT_PATH = Path(__file__).resolve().parent

log = init_logging(level="DEBUG")
log.name = "github-action"


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


class AutoActionError(Exception):
    def __init__(self, *args, log_file=None):
        self.args = args
        self.log_file = log_file


class AutoActionTimeout(Exception):
    pass


class BaseAutoAction:
    def __init__(
        self,
        builder_dir,
        state_dir,
        builder_conf=None,
        commit_sha=None,
        repository_publish=None,
        local_log_file=None,
        dry_run=False,
    ):
        self.builder_dir = Path(builder_dir).resolve()
        self.builder_conf = builder_conf or self.builder_dir / "builder.yml"
        self.state_dir = Path(state_dir).resolve()
        self.config = Config(self.builder_conf)
        self.timeout = 21600
        self.qubes_release = self.config.get("qubes-release")
        self.commit_sha = commit_sha
        self.repository_publish = repository_publish
        self.dry_run = False

        if not self.builder_dir.exists():
            raise AutoActionError(
                f"No such directory for builder '{self.builder_dir}'."
            )

        self.state_dir.mkdir(exist_ok=True, parents=True)
        self.local_log_file = local_log_file
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
        if self.local_log_file:
            return self.make_with_log_local(func, *args)
        else:
            return self.make_with_log_qrexec(func, *args)

    def make_with_log_local(self, func, *args):
        log_fh = logging.FileHandler(self.local_log_file)
        log.addHandler(log_fh)
        log.debug("> starting build with log")
        self.display_head_info(args)
        try:
            func(*args)
            log.debug("> done")
        except PluginError as e:
            raise AutoActionError(e.args, log_file=self.local_log_file) from e
        finally:
            log.removeHandler(log_fh)
        return self.local_log_file

    def make_with_log_qrexec(self, func, *args):
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
                raise AutoActionError(e.args, log_file=log_file) from e
            else:
                p.stdin.close()
                p.wait()
                log_file = list(p.stdout)
                log_file = log_file[0].rstrip("\n")
            finally:
                log.removeHandler(qrexec_stream)
            return log_file

    def run(self):
        pass


class AutoAction(BaseAutoAction):
    def __init__(
        self,
        builder_dir,
        builder_conf,
        component_name,
        state_dir,
        commit_sha,
        repository_publish,
        local_log_file,
    ):
        super().__init__(
            builder_dir=builder_dir,
            builder_conf=builder_conf,
            state_dir=state_dir,
            commit_sha=commit_sha,
            repository_publish=repository_publish,
            local_log_file=local_log_file,
        )

        self.components = self.config.get_components([component_name])
        self.distributions = self.config.get_distributions()

        if not self.components:
            raise AutoActionError(f"No such component '{component_name}'.")

        self.repository_publish = repository_publish or self.config.get(
            "repository-publish", {}
        ).get("components", None)
        if not self.repository_publish:
            raise AutoActionError(f"No repository defined for component publication.")

        self.timeout = self.components[0].timeout

        self.built_for_dist = []

    def run_stages(self, dist, stages):
        for stage in stages:
            _component_stage(
                stage_name=stage,
                config=self.config,
                components=self.components,
                distributions=[dist],
            )

    def publish_and_upload(self, repository_publish: str, distributions: List):
        _publish(
            config=self.config,
            repository_publish=repository_publish,
            components=self.components,
            distributions=distributions,
            templates=[],
        )
        _upload(
            config=self.config,
            repository_publish=repository_publish,
            distributions=distributions,
            templates=[],
        )

    def notify_build_status_on_timeout(self):
        for dist in self.distributions:
            if dist.name not in self.built_for_dist:
                self.notify_build_status(dist, "failed", additional_info="Timeout")

    def notify_build_status(self, dist, status, log_file=None, additional_info=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/utils/notify-issues",
            f"--days={self.config.get('min-age-days', 5)}",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        if additional_info:
            notify_issues_cmd += [f"--additional-info={str(additional_info)}"]

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

    def notify_upload_status(self, dist, log_file=None, additional_info=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/utils/notify-issues",
            f"--days={self.config.get('min-age-days', 5)}",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        if additional_info:
            notify_issues_cmd += [f"--additional-info={str(additional_info)}"]

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

    def build(self):
        self.make_with_log(
            _component_stage, self.config, self.components, self.distributions, "fetch"
        )

        for dist in self.distributions:
            release_status = _check_release_status_for_component(
                config=self.config, components=self.components, distributions=[dist]
            )

            if (
                release_status.get(self.components[0].name, {})
                .get(dist.distribution, {})
                .get("status", None)
                == "not released"
                and release_status.get(self.components[0].name, {})
                .get(dist.distribution, {})
                .get("tag", None)
                != "no version tag"
            ):
                try:
                    self.notify_build_status(
                        dist,
                        "building",
                    )

                    build_log_file = self.make_with_log(
                        self.run_stages,
                        dist,
                        ["prep", "build"],
                    )

                    self.make_with_log(
                        self.run_stages,
                        dist,
                        ["sign", "publish", "upload"],
                    )

                    self.notify_upload_status(dist, build_log_file)

                    self.built_for_dist.append(dist)
                except AutoActionError as autobuild_exc:
                    log.error(str(autobuild_exc.args))
                    self.notify_build_status(
                        dist,
                        "failed",
                        log_file=autobuild_exc.log_file,
                        additional_info=autobuild_exc.args,
                    )
                    pass
                except TimeoutError as timeout_exc:
                    raise AutoActionTimeout(
                        "Timeout reached for build!"
                    ) from timeout_exc
                except Exception as exc:
                    self.notify_build_status(
                        dist,
                        "failed",
                        additional_info=f"Internal error: '{str(exc.__class__.__name__)}'",
                    )
                    log.error(str(exc))
                    pass

        if not self.built_for_dist:
            log.warning(
                "Nothing was built, something gone wrong or version tag was not found."
            )

    def upload(self):
        actual_commit_sha = self.components[0].get_source_commit_hash()
        if self.commit_sha != actual_commit_sha:
            raise AutoActionError(
                f"Source have changed in the meantime (current: {actual_commit_sha})"
            )
        for dist in self.distributions:
            try:
                upload_log_file = self.make_with_log(
                    self.publish_and_upload, self.repository_publish, [dist]
                )
                self.notify_upload_status(dist, upload_log_file)
            except AutoActionError as autobuild_exc:
                self.notify_build_status(
                    dist, "failed", log_file=autobuild_exc.log_file
                )
                pass
            except TimeoutError as timeout_exc:
                raise AutoActionTimeout("Timeout reached for upload!") from timeout_exc
            except Exception as exc:
                self.notify_build_status(
                    dist,
                    "failed",
                    additional_info=f"Internal error: '{str(exc.__class__.__name__)}'",
                )
                log.error(str(exc))
                pass


class AutoActionTemplate(BaseAutoAction):
    def __init__(
        self,
        builder_dir,
        builder_conf,
        template_name,
        template_timestamp,
        state_dir,
        commit_sha,
        repository_publish,
        local_log_file,
    ):
        super().__init__(
            builder_dir=builder_dir,
            builder_conf=builder_conf,
            state_dir=state_dir,
            commit_sha=commit_sha,
            repository_publish=repository_publish,
            local_log_file=local_log_file,
        )

        self.templates = self.config.get_templates([template_name])
        self.template_timestamp = template_timestamp

        if not self.templates:
            raise AutoActionError(f"No such template '{template_name}'.")

        self.repository_publish = repository_publish or self.config.get(
            "repository-publish", {}
        ).get("templates", None)
        if not self.repository_publish:
            raise AutoActionError(f"No repository defined for template publication.")

        self.timeout = self.templates[0].timeout

    def run_stages(self, stages):
        for stage in stages:
            _template_stage(
                stage_name=stage,
                config=self.config,
                templates=self.templates,
                template_timestamp=self.template_timestamp,
            )

    def publish_and_upload(self, repository_publish: str):
        _publish(
            config=self.config,
            repository_publish=repository_publish,
            templates=self.templates,
            components=[],
            distributions=[],
        )
        _upload(
            config=self.config,
            repository_publish=repository_publish,
            templates=self.templates,
            distributions=[],
        )

    def notify_build_status_on_timeout(self):
        self.notify_build_status("failed", additional_info="Timeout")

    def notify_build_status(self, status, log_file=None, additional_info=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/utils/notify-issues",
            f"--days={self.config.get('min-age-days', 5)}",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        if additional_info:
            notify_issues_cmd += [f"--additional-info={str(additional_info)}"]

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

    def notify_upload_status(self, log_file=None, additional_info=None):
        notify_issues_cmd = [
            f"{str(PROJECT_PATH)}/utils/notify-issues",
            f"--days={self.config.get('min-age-days', 5)}",
            f"--message-templates-dir={str(PROJECT_PATH)}/templates",
        ]

        if log_file:
            notify_issues_cmd += [
                f"--build-log={self.get_build_log_url(log_file=log_file)}"
            ]

        if additional_info:
            notify_issues_cmd += [f"--additional-info={str(additional_info)}"]

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

    def build(self):
        timestamp_file = (
            self.config.get_artifacts_dir()
            / "templates"
            / f"build_timestamp_{self.templates[0].name}"
        )
        if timestamp_file.exists():
            try:
                timestamp_existing = datetime.datetime.strptime(
                    timestamp_file.read_text().rstrip("\n"), "%Y%m%d%H%M"
                )
                template_timestamp = datetime.datetime.strptime(
                    self.template_timestamp, "%Y%m%d%H%M"
                )
            except (OSError, ValueError) as exc:
                raise AutoActionError(
                    f"Failed to read or parse timestamp: {str(exc)}"
                ) from exc
            if template_timestamp < timestamp_existing:
                log.info(
                    f"Newer template ({timestamp_existing.strftime('%Y%m%d%H%M')}) already built."
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

            except AutoActionError as autobuild_exc:
                self.notify_build_status("failed", log_file=autobuild_exc.log_file)
                pass
            except TimeoutError as timeout_exc:
                raise AutoActionTimeout("Timeout reached for build!") from timeout_exc
            except Exception as exc:
                self.notify_build_status(
                    "failed",
                    additional_info=f"Internal error: '{str(exc.__class__.__name__)}'",
                )
                log.error(str(exc))
                pass

    def upload(self):
        timestamp_file = (
            self.config.get_artifacts_dir()
            / "templates"
            / f"build_timestamp_{self.templates[0].name}"
        )
        if not timestamp_file.exists():
            raise AutoActionError("Cannot upload template, no build timestamp found.")
        try:
            timestamp_existing = datetime.datetime.strptime(
                timestamp_file.read_text().rstrip("\n"), "%Y%m%d%H%M"
            ).strftime("%Y%m%d%H%M")
        except (OSError, ValueError) as exc:
            raise AutoActionError(
                f"Failed to read or parse timestamp: {str(exc)}"
            ) from exc
        if self.commit_sha != f"{TEMPLATE_VERSION}-{timestamp_existing}":
            raise AutoActionError(
                f"Different template was built in the meantime (current: {TEMPLATE_VERSION}-{timestamp_existing})"
            )
        try:
            upload_log_file = self.make_with_log(
                self.publish_and_upload,
                self.repository_publish,
            )
            self.notify_upload_status(upload_log_file)
        except AutoActionError as autobuild_exc:
            self.notify_build_status("failed", log_file=autobuild_exc.log_file)
            pass
        except TimeoutError as timeout_exc:
            raise AutoActionTimeout("Timeout reached for upload!") from timeout_exc
        except Exception as exc:
            self.notify_build_status(
                "failed",
                additional_info=f"Internal error: '{str(exc.__class__.__name__)}'",
            )
            log.error(str(exc))
            pass


def main():
    parser = argparse.ArgumentParser()

    signer = parser.add_mutually_exclusive_group()
    signer.add_argument(
        "--no-signer-github-command-check",
        action="store_true",
        default=False,
        help="Don't check signer fingerprint.",
    )
    signer.add_argument(
        "--signer-fpr",
        help="Signer GitHub command fingerprint.",
    )
    parser.add_argument("--state-dir", default=Path.home() / "github-notify-state")
    parser.add_argument(
        "--local-log-file",
        help="Use local log file instead of qubesbuilder.BuildLog RPC.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    # build component parser
    build_component_parser = subparsers.add_parser("build-component")
    build_component_parser.set_defaults(command="build-component")
    build_component_parser.add_argument("builder_dir")
    build_component_parser.add_argument("builder_conf")
    build_component_parser.add_argument("component_name")

    # upload component parser
    upload_component_parser = subparsers.add_parser("upload-component")
    upload_component_parser.set_defaults(command="upload-component")
    upload_component_parser.add_argument("builder_dir")
    upload_component_parser.add_argument("builder_conf")
    upload_component_parser.add_argument("component_name")
    upload_component_parser.add_argument("commit_sha")
    upload_component_parser.add_argument("repository_publish")
    upload_component_parser.add_argument("--distribution", nargs="+", default=[])

    # build template parser
    build_template_parser = subparsers.add_parser("build-template")
    build_template_parser.set_defaults(command="build-template")
    build_template_parser.add_argument("builder_dir")
    build_template_parser.add_argument("builder_conf")
    build_template_parser.add_argument("template_name")
    build_template_parser.add_argument("template_timestamp")

    # upload template parser
    template_parser = subparsers.add_parser("upload-template")
    template_parser.set_defaults(command="upload-template")
    template_parser.add_argument("builder_dir")
    template_parser.add_argument("builder_conf")
    template_parser.add_argument("template_name")
    template_parser.add_argument("template_sha")
    template_parser.add_argument("repository_publish")

    args = parser.parse_args()

    commit_sha = None
    template_timestamp = None
    if args.command == "upload-component":
        commit_sha = args.commit_sha
    elif args.command == "upload-template":
        commit_sha = args.template_sha
        template_timestamp = commit_sha.split("-")[-1]

    if args.command in ("upload-component", "upload-template"):
        repository_publish = args.repository_publish
    else:
        repository_publish = None

    if args.local_log_file:
        local_log_file = Path(args.local_log_file).resolve()
    else:
        local_log_file = None

    if args.command in ("build-component", "upload-component"):
        cli = AutoAction(
            builder_dir=args.builder_dir,
            builder_conf=args.builder_conf,
            component_name=args.component_name,
            state_dir=args.state_dir,
            commit_sha=commit_sha,
            repository_publish=repository_publish,
            local_log_file=local_log_file,
        )
        supported_distributions = [
            d.distribution for d in cli.config.get_distributions()
        ]
        supported_components = [c.name for c in cli.config.get_components()]

        # check if requested component name exists
        if args.component_name not in supported_components:
            return

        # maintainers checks
        if not args.no_signer_github_command_check:
            # maintainers components filtering
            allowed_components = (
                cli.config.get("github", {})
                .get("maintainers", {})
                .get(args.signer_fpr, {})
                .get("components", [])
            )
            if allowed_components == "_all_":
                allowed_components = supported_components
            if args.component_name not in allowed_components:
                log.info("Cannot find any allowed components.")
                return

            # maintainers distributions filtering (only supported for upload)
            if args.command == "upload-component":
                allowed_distributions = (
                    cli.config.get("github", {})
                    .get("maintainers", {})
                    .get(args.signer_fpr, {})
                    .get("distributions", [])
                )
                if allowed_distributions == "_all_":
                    allowed_distributions = supported_distributions
                if args.distribution == ["all"]:
                    args.distribution = supported_distributions
                # we may have multiple distribution provided
                filtered_distributions = set(args.distribution).intersection(
                    set(allowed_distributions)
                )
                if not filtered_distributions:
                    log.info("Cannot find any allowed distributions.")
                    return
                cli.distributions = cli.config.get_distributions(filtered_distributions)
    elif args.command in ("build-template", "upload-template"):
        cli = AutoActionTemplate(
            builder_dir=args.builder_dir,
            builder_conf=args.builder_conf,
            template_name=args.template_name,
            template_timestamp=template_timestamp,
            state_dir=args.state_dir,
            commit_sha=commit_sha,
            repository_publish=repository_publish,
            local_log_file=local_log_file,
        )
        supported_templates = [t.name for t in cli.config.get_templates()]
        # check if requested template name exists
        if args.template_name not in supported_templates:
            return
        # maintainers checks
        if not args.no_signer_github_command_check:
            allowed_templates = (
                cli.config.get("github", {})
                .get("maintainers", {})
                .get(args.signer_fpr, {})
                .get("templates", [])
            )
            if allowed_templates == "_all_":
                allowed_templates = supported_templates
            if args.template_name not in allowed_templates:
                return
    else:
        return

    if cli.config.get("github", {}).get("dry-run", False):
        # Dry-run mode (for tests only)
        time.sleep(1)
        return

    with timeout(cli.timeout):
        try:
            if args.command in ("build-component", "build-template"):
                cli.build()
            elif args.command in ("upload-component", "upload-template"):
                cli.upload()
            else:
                return
        except AutoActionTimeout as autobuild_exc:
            cli.notify_build_status_on_timeout()
            raise AutoActionTimeout(str(autobuild_exc))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        log.error(str(e))
        sys.exit(1)
