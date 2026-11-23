#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Match the built Go compiler to the Go recipes of the pinned packages feed."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPILER = "feeds/packages/lang/golang/golang1.27/Makefile"
BOOTSTRAP = "feeds/packages/lang/golang/golang-bootstrap/Makefile"
VALUES = "feeds/packages/lang/golang/golang-values.mk"
BINARY = "staging_dir/hostpkg/lib/go-1.27/bin/go"


class GoVersionError(ValueError):
    pass


def field(recipe, name):
    values = re.findall(rf"^{re.escape(name)}[ \t]*:?=[ \t]*([^\n]*)$", recipe, re.M)
    if len(values) != 1:
        raise GoVersionError(f"missing or duplicate Go recipe field: {name}")
    return values[0].strip()


def expected_version(root):
    """Read the compiler version from the Go recipes of the pinned packages feed."""
    recipes = {}
    for name in (COMPILER, BOOTSTRAP, VALUES):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise GoVersionError(f"missing or unsafe Go recipe: {name}")
        recipes[name] = path.read_text()
    compiler, bootstrap = recipes[COMPILER], recipes[BOOTSTRAP]
    version = field(compiler, "GO_VERSION_MAJOR_MINOR") + "." + field(compiler, "GO_VERSION_PATCH")
    if (not re.fullmatch(r"1\.27\.[0-9]+", version) or field(compiler, "GO_VERSION_RC")
            or field(compiler, "GO_BOOTSTRAP_VERSION") != "bootstrap"
            or field(recipes[VALUES], "GO_DEFAULT_VERSION") != "1.27"
            or field(bootstrap, "GO_VERSION_MAJOR_MINOR") + "." + field(bootstrap, "GO_VERSION_PATCH") != "1.24.13"):
        raise GoVersionError("Go recipe version or bootstrap selection differs from the reviewed chain")
    return version


def check(root):
    version = expected_version(root)
    binary = root / BINARY
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise GoVersionError("the built Go compiler is missing or not executable")
    env = os.environ.copy()
    for name in ("GOROOT", "GOHOSTOS", "GOHOSTARCH", "GOOS", "GOARCH", "GOFLAGS", "GODEBUG"):
        env.pop(name, None)
    env.update(GOTOOLCHAIN="local", GOENV="off", GOWORK="off")

    def probe(*arguments):
        result = subprocess.run([str(binary), *arguments], cwd=root, env=env,
                                text=True, capture_output=True, check=True, timeout=30)
        return result.stdout.strip()

    actual = probe("version")
    if actual != f"go version go{version} linux/amd64":
        raise GoVersionError("built Go compiler version/platform does not match the pinned Go recipe")
    identity = probe("env", "GOROOT", "GOHOSTOS", "GOHOSTARCH").splitlines()
    if (len(identity) != 3 or identity[1:] != ["linux", "amd64"]
            or not Path(identity[0]).is_absolute()
            or Path(identity[0]).resolve() != binary.parent.parent.resolve()):
        raise GoVersionError("built Go compiler root or host platform differs from its installation")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        print(check(root))
    except (GoVersionError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"RC01 Go compiler acceptance failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
