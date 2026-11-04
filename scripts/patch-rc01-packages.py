#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Apply the reviewed RC01 source recipes to the exact locked feeds, offline.

Run after prepare-rc01.sh's Mihomo SOURCE_DATE_EPOCH guard. Both revisions,
complete recipe preimages and patched outputs are checked before either feed
is changed. An exact postimage is accepted on subsequent runs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "config/rc01/patches/packages"
TIMESTAMP = b'PKG_BUILD_TIME:=$(shell date -u -d "@$(SOURCE_DATE_EPOCH)" -Iseconds)'


class RecipeError(ValueError):
    pass


def git(directory, *arguments):
    return subprocess.run(["git", "-C", str(directory), *arguments],
                          check=True, capture_output=True, text=True).stdout.strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def apply(packages, nikki):
    feeds = {"packages": Path(packages).resolve(), "nikki": Path(nikki).resolve()}
    recipes = json.loads((PATCHES / "manifest.json").read_text())["packages"]
    pending = []
    for recipe in recipes:
        feed = feeds[recipe["feed"]]
        if (Path(git(feed, "rev-parse", "--show-toplevel")).resolve() != feed
                or git(feed, "rev-parse", "HEAD") != recipe["feed_revision"]):
            raise RecipeError(f"Unexpected {recipe['feed']} revision; review the RC01 package patches")
        path = feed / recipe["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(feed):
            raise RecipeError(f"Unsafe recipe path: {path}")
        before = path.read_bytes()
        if recipe["feed"] == "nikki" and before.splitlines().count(TIMESTAMP) != 1:
            raise RecipeError("Mihomo SOURCE_DATE_EPOCH timestamp guard must run before package updates")
        digest = sha256(before)
        if digest == recipe["applied_sha256"]:
            continue
        if digest != recipe["baseline_sha256"]:
            raise RecipeError(f"Unexpected recipe baseline: {path}; refusing to overwrite local changes")
        # Apply the real patch in isolation, then verify its entire output. No
        # live recipe is written if either feed or either patch is unexpected.
        with tempfile.TemporaryDirectory(prefix="rc01-package-patch-") as temporary:
            work = Path(temporary)
            staged = work / recipe["path"]
            staged.parent.mkdir(parents=True)
            staged.write_bytes(before)
            patch = str(PATCHES / recipe["patch"])
            git(work, "apply", "--check", patch)
            git(work, "apply", patch)
            after = staged.read_bytes()
        if sha256(after) != recipe["applied_sha256"]:
            raise RecipeError(f"Unexpected patched recipe: {recipe['patch']}")
        pending.append((path, before, after))
    # Recheck all pending preimages before writing, in case another preparer
    # changed a recipe during validation. Feed preparation must not run in parallel.
    for path, before, _ in pending:
        if path.is_symlink() or path.read_bytes() != before:
            raise RecipeError(f"Recipe changed during validation: {path}")
    for path, _, after in pending:
        path.write_bytes(after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="?", type=Path, default=ROOT / "feeds/packages")
    parser.add_argument("nikki", nargs="?", type=Path, default=ROOT / "feeds/nikki")
    args = parser.parse_args()
    try:
        apply(args.packages, args.nikki)
    except (OSError, RecipeError, subprocess.CalledProcessError) as error:
        print(f"RC01 package update failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, end="", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
