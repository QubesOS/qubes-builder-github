Description
-----------

This is Qubes builder plugin which reports to github issues when package
containing a fix is uploaded to the repository. Reporting is done using a
comment and additionally a label, so it is easy to check if the issue was
uploaded somewhere (including backports!).

The plugin will report only when uploading to standard repositories, using
`update-repo-*` targets, and when `LINUX_REPO_BASEDIR` setting points at
specific Qubes release (not `current-release` symlink). Only `current` and
`current-testing` repositories are taken into account, others (for example
`unstable` or `security-testing`) are ignored.

Optionally additional repository may be configured to have dedicated issues
created for the sole purpose of tracking uploaded updates (regardless of
comments in issues mentioned in git log). One issue will be used for multiple
target templates (Debian, Fedora etc).

Configuration
-------------

To use this plugin you need to enable it in  `builder.conf` by appending it to
`BUILDER_PLUGINS` variable. It is important to have it **after**
distribution-specific plugin (like `builder-fedora` or `builder-debian`).

Then you need to add some additional settings:

 * `GITHUB_API_KEY` - GitHub API key
 * `GITHUB_STATE_DIR` - directory for plugin state

Optional:

 * `GITHUB_BUILD_REPORT_REPO` - repository in which every uploaded package
   should have issue created (regardless of commenting issues mentioned in git
   log).

Comments text
=============

Comment messages can be configured in `message-*` files. Available files:
 * `message-stable-dom0`, `message-testing-dom0` - when the package is uploaded to
   dom0 repository
 * `message-stable-vm`, `message-testing-vm` - when the package is uploaded to
   VM repository
 * `message-stable-vm-DIST`, `message-testing-vm-DIST` (where `DIST` is code
   name of target distribution) - if exists, it is used instead of
   corresponding `message-stable-vm` or `message-testing-vm`
 * `message-build-report` - template for issue description (if
   `GITHUB_BUILD_REPORT_REPO` set)

Each file is actually message template, which can contain following placeholders:
 * `@DIST@` - code name of the target distribution
 * `@PACKAGE_SET@` - either `dom0` or `vm`
 * `@PACKAGE_NAME@` - primary package name, including the version being
   uploaded; in case of multiple packages being build from the same component,
   only the first one is listed
 * `@COMPONENT@` - Qubes component name (as listed in `COMPONENTS` setting of `builder.conf`)
 * `@REPOSITORY@` - either `testing` or `stable`
 * `@RELEASE_NAME@` - name of target Qubes release (`r2`, `r3.0` etc)
 * `@GIT_LOG@` - `git log --pretty=oneline previous_commit..current_commit` with github-like commits refrences
 * `@GIT_LOG_URL@` - Github URL to commits between previous version and the current one. "compare" github feature.
 * `@COMMIT_SHA@` - Commit SHA used to build the package.

Ideally the message should include instrution how to install the update.
