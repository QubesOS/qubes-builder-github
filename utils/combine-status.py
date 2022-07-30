#!/usr/bin/python3

import re
import sys
import traceback
from pathlib import Path

import yaml
from jinja2 import Template

HTML_TEMPLATE = """
<html>
  <head>
    <title>Packages status</title>
<style>
body { font-family: sans-serif; }
td.current { background-color: green; }
td.current-testing { background-color: yellow; }
td.templates-itl-testing { background-color: yellow; }
td.templates-community-testing { background-color: yellow; }
td.unstable { background-color: blue; }
td.unreleased { background-color: red; }
td.built-unreleased { background-color: white; }
td.templates-itl { background-color: green; }
td.templates-community { background-color: green; }
td.days-testing { background-color: yellow; }
td.days-stable { background-color: green; }
td.no-version { background-color: red; font-weight: bold; }
td, th { border: solid 2px #dcdcdc; padding: 4px; }
table { border-collapse: collapse; }
</style>
  </head>
  <body>
{%- for release in status.keys() -%}
    <h1>Release {{release}}</h1>
{%- for distribution in status[release].keys() %}
      <h2>Packages for <span class="dist">{{distribution}}</span></h2>
        <table><tr><th>Component</th><th>Version</th><th>Status</th></tr>
{%- for component, component_status in status[release][distribution].items() -%}
          <tr>
            <td>{{component}}</td><td>{{component_status["tag"]}}</td>
{%- if component_status["status"] is iterable and (component_status["status"] is not string and component_status["status"] is not mapping) %}
{%- for repo in component_status["status"] %}
            <td class="{{color(repo["repo"])}}">{{repo["repo"]}}</td>
            <td class="{{color(repo["days"])}}">{{repo["days"]}}</td>
{%- endfor -%}
{%- endif -%}
          </tr>
{%- endfor %}
        </table>
{%- endfor -%}
{%- endfor -%}
  </body>
</html>
"""


def color(input_string):
    if input_string == "no version tag":
        tag = "no-version"
    elif isinstance(input_string, int):
        if int(input_string) > 5:
            tag = "days-stable"
        else:
            tag = "days-testing"
    elif input_string == "built, not released":
        tag = "built-unreleased"
    elif input_string == "not released":
        tag = "unreleased"
    elif input_string == "":
        tag = "no-version"
    elif input_string == "":
        tag = "no-version"
    elif input_string in (
        "current",
        "current-testing",
        "security-testing",
        "unstable",
        "templates-itl",
        "templates-community",
        "templates-itl-testing",
        "templates-community-testing",
    ):
        tag = input_string
    else:
        raise ValueError(f"Unknown input string: {input_string}")
    return tag


def main(input_dir: Path, output_dir: Path):
    release_files = {}  # type: ignore
    for f in input_dir.glob("builder-*-status.yml"):
        release = re.match(r".*builder-(.*)-.*-status.yml", str(f))
        if not release:
            continue
        release_files.setdefault(release.group(1), [])
        release_files[release.group(1)].append(f)

    status = {}  # type: ignore
    for release in release_files:
        for f in release_files[release]:
            content = yaml.safe_load(f.read_text())
            for component in content:
                for distribution in content[component]:
                    status.setdefault(release, {})
                    status[release].setdefault(distribution, {})
                    status[release][distribution][component] = content[component][
                        distribution
                    ]

    template = Template(HTML_TEMPLATE)
    template.globals.update(color=color)
    html = template.render(status=status)

    with open(output_dir / "status.yml", "w") as f:
        f.write(yaml.dump(status))

    with open(output_dir / "status.html", "w") as f:
        f.write(html)


if __name__ == "__main__":
    try:
        main(
            Path(sys.argv[1]).expanduser().resolve(),
            Path(sys.argv[2]).expanduser().resolve(),
        )
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
