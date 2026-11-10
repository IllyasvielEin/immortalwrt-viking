#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Match the built Go compiler to this build's verified software source lock."""

import argparse
import hashlib
import json
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
SHA256 = re.compile(r"[0-9a-f]{64}")


class GoVersionError(ValueError):
    pass


def field(recipe, name):
    values = re.findall(rf"^{re.escape(name)}[ \t]*:?=[ \t]*([^\n]*)$", recipe, re.M)
    if len(values) != 1:
        raise GoVersionError(f"missing or duplicate Go recipe field: {name}")
    return values[0].strip()


def expected_version(root, lock):
    if lock.get("schema") != 1 or lock.get("prepared") is not True or lock.get("mode") not in ("latest", "locked"):
        raise GoVersionError("Go acceptance requires a complete software source lock")
    go = lock.get("go", {})
    version = go.get("version", "")
    if not re.fullmatch(r"1\.27\.[0-9]+", version) or go.get("bootstrap") != "1.24.13":
        raise GoVersionError("Go compiler major/minor or bootstrap differs from the reviewed chain")
    recipes = {}
    for name in (COMPILER, BOOTSTRAP, VALUES):
        path = root / name
        expected = lock.get("inputs", {}).get(name, "")
        if not SHA256.fullmatch(expected) or path.is_symlink() or not path.is_file():
            raise GoVersionError(f"missing or unsafe locked Go input: {name}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise GoVersionError(f"Go recipe differs from this build's source lock: {name}")
        recipes[name] = payload.decode()
    if go.get("recipe_sha256") != lock["inputs"][COMPILER]:
        raise GoVersionError("Go version provenance differs from the locked compiler recipe")
    compiler, bootstrap = recipes[COMPILER], recipes[BOOTSTRAP]
    if (field(compiler, "GO_VERSION_MAJOR_MINOR") + "." + field(compiler, "GO_VERSION_PATCH") != version
            or field(compiler, "GO_VERSION_RC") or field(compiler, "GO_BOOTSTRAP_VERSION") != "bootstrap"
            or field(recipes[VALUES], "GO_DEFAULT_VERSION") != "1.27"
            or field(bootstrap, "GO_VERSION_MAJOR_MINOR") + "." + field(bootstrap, "GO_VERSION_PATCH") != "1.24.13"):
        raise GoVersionError("locked Go recipe version or bootstrap selection does not match its provenance")
    return version


def check(root, lock):
    version = expected_version(root, lock)
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
        raise GoVersionError("built Go compiler version/platform does not match this build's source lock")
    identity = probe("env", "GOROOT", "GOHOSTOS", "GOHOSTARCH").splitlines()
    if (len(identity) != 3 or identity[1:] != ["linux", "amd64"]
            or not Path(identity[0]).is_absolute()
            or Path(identity[0]).resolve() != binary.parent.parent.resolve()):
        raise GoVersionError("built Go compiler root or host platform differs from its installation")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        lock = json.loads((args.lock or root / ".rc01-sources/lock.json").read_text())
        print(check(root, lock))
    except (GoVersionError, OSError, json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"RC01 Go compiler acceptance failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
