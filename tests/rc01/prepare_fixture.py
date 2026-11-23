# SPDX-License-Identifier: GPL-2.0-only
"""A disposable project that runs the real scripts/prepare-rc01.sh offline.

Only the external collaborators are replaced: scripts/feeds and make log what
they were called with, and the feeds are tiny local Git repositories at the
commits pinned in the fixture's feeds.conf. The controlled patches are the real
ones. The Go preimages are the pristine fixtures in tests/rc01/fixtures; the
tailscale and mihomo recipes are rebuilt from each patch's own old-side lines,
which checks the patch/manifest contract but not the upstream file content.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/rc01/fixtures/go/lang/golang"
PATCHES = ROOT / "config/rc01/patches"
GIT_ENV = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}


def preimage_from_patch(patch, path):
    """Old-side lines of every hunk of `path`, placed at their line numbers."""
    section = patch.split(f"--- a/{path}\n", 1)[1]
    lines = {}
    number = 0
    for line in section.splitlines():
        header = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
        if header:
            number = int(header[1])
        elif line.startswith("diff "):
            break
        elif line[:1] in (" ", "-"):
            lines[number] = line[1:]
            number += 1
        elif line == "" and number:
            lines[number] = ""
            number += 1
    return "".join(lines.get(n, f"# fixture filler {n}") + "\n" for n in range(1, max(lines) + 1))


def git(repository, *arguments):
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, text=True,
                          capture_output=True, env=os.environ | GIT_ENV).stdout.strip()


def executable(path, content):
    path.write_text("#!/bin/sh\nset -eu\n" + content)
    path.chmod(0o755)


def make_feed(project, name, files):
    feed = project / "feeds" / name
    for relative, text in files.items():
        target = feed / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git(project, "init", "--quiet", str(feed))
    git(feed, "add", "-A")
    git(feed, "commit", "--quiet", "-m", "pristine")
    return git(feed, "rev-parse", "HEAD")


def make_project(work, defconfig="", extra_scripts=("rc01-go-bootstrap.py",)):
    project = Path(work) / "project"
    for directory in ("scripts", "config/rc01/files/etc/board.d", "config/rc01/files/etc/uci-defaults",
                      "defconfig", "test-bin"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    for name in ("prepare-rc01.sh", "rc01-layout.py", *extra_scripts):
        shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
    for relative in ("config/rc01/layout.json", "config/rc01/layout.mk",
                     "package/emortal/rc01-platform/files/rc01-layout.sh"):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copytree(PATCHES, project / "config/rc01/patches")
    for name in ("board.d/99-rc01-wireless", "uci-defaults/89-rc01-bypass"):
        (project / "config/rc01/files/etc" / name).write_text("fixture\n")
    (project / "defconfig/qualcommbe-rc01.config").write_text(defconfig)

    patch = lambda relative: (PATCHES / relative).read_text()
    manifest = {item["feed"]: item for item in json.loads((PATCHES / "packages/manifest.json").read_text())["packages"]}
    pins = {
        "packages": make_feed(project, "packages", {
            "lang/golang/golang-compiler.mk": (FIXTURES / "golang-compiler.mk").read_text(),
            "lang/golang/golang-host-build.mk": (FIXTURES / "golang-host-build.mk").read_text(),
            manifest["packages"]["path"]: preimage_from_patch(
                patch("packages/" + manifest["packages"]["patch"]), manifest["packages"]["path"]),
        }),
        "nikki": make_feed(project, "nikki", {
            manifest["nikki"]["path"]: preimage_from_patch(
                patch("packages/" + manifest["nikki"]["patch"]), manifest["nikki"]["path"]),
        }),
    }
    (project / "config/rc01/feeds.conf").write_text(
        "".join(f"src-git {name} https://example.invalid/{name}.git^{sha}\n" for name, sha in pins.items()))

    executable(project / "scripts/feeds", 'echo "feeds $*" >> "$RC01_TEST_EVENTS"\n')
    executable(project / "scripts/validate-rc01-config.sh",
               'echo validate >> "$RC01_TEST_EVENTS"\n'
               'exec "$RC01_TEST_PYTHON" -B scripts/rc01-go-bootstrap.py validate-config "$1"\n')
    executable(project / "test-bin/python3", 'exec "$RC01_TEST_PYTHON" -B "$@"\n')
    executable(project / "test-bin/make", '''case "$*" in
    defconfig)
        "$RC01_TEST_PYTHON" -B scripts/rc01-go-bootstrap.py validate-config .config
        echo defconfig >> "$RC01_TEST_EVENTS"
        if [ "${RC01_TEST_MANGLE_CONFIG:-0}" = 1 ]; then
            echo CONFIG_GOLANG_BUILD_BOOTSTRAP=y >> .config
        fi ;;
    *) echo recipe-parse >> "$RC01_TEST_EVENTS" ;;
esac
''')
    return project


def run_prepare(project, python, extra_env=None):
    events = project / "events"
    events.unlink(missing_ok=True)
    env = os.environ | {"PATH": str(project / "test-bin") + os.pathsep + os.environ["PATH"],
                        "RC01_TEST_PYTHON": python, "RC01_TEST_EVENTS": str(events)} | (extra_env or {})
    result = subprocess.run(["sh", "scripts/prepare-rc01.sh"], cwd=project, env=env,
                            capture_output=True, text=True)
    return result, (events.read_text().splitlines() if events.exists() else [])
