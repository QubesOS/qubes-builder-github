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

Configuration
-------------

To use this plugin you need to enable it in  `builder.conf` by appending it to
`BUILDER_PLUGINS` variable. It is important to have it **after**
distribution-specific plugin (like `builder-fedora` or `builder-debian`).

Then you need to add some additional settings:
 * `GITHUB_API_KEY` - GitHub API key
 * `GITHUB_STATE_DIR` - directory for plugin state

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

Each file is actually message template, which can contain following placeholders:
 * `@DIST@` - code name of the target distribution
 * `@PACKAGE_SET@` - either `dom0` or `vm`
 * `@PACKAGE_NAME@` - primary package name, including the version being
   uploaded; in case of multiple packages being build from the same component,
   only the first one is listed
 * `@COMPONENT@` - Qubes component name (as listed in `COMPONENTS` setting of `builder.conf`)
 * `@REPOSITORY@` - either `testing` or `stable`
 * `@RELEASE_NAME@` - name of target Qubes release (`r2`, `r3.0` etc)


Ideally the message should include instrution how to install the update.
