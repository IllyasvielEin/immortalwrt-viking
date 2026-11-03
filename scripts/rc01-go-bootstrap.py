#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install the pinned official Go bootstrap, or validate RC01's selection."""

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request


# Official release metadata: https://go.dev/dl/?mode=json&include=all
VERSION = "1.24.13"
HOST_OS = "linux"
HOST_ARCH = "amd64"
ARCHIVE_URL = f"https://go.dev/dl/go{VERSION}.{HOST_OS}-{HOST_ARCH}.tar.gz"
ARCHIVE_SHA256 = "1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730"
ARCHIVE_SIZE = 78709986
BUILD_SYMBOL = "CONFIG_GOLANG_BUILD_BOOTSTRAP"
ROOT_SYMBOL = "CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT"


class BootstrapError(ValueError):
    pass


def safe_path(value):
    """Reject shell/Make/Kconfig metacharacters, aliases and relative paths."""
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        raise BootstrapError("bootstrap path must be an absolute safe path")
    path = Path(value)
    if path == Path("/") or str(path.resolve()) != value:
        raise BootstrapError("bootstrap path must be canonical and contain no symlinks")
    return path


def check_structure(root):
    for name in ("bin", "pkg", "src"):
        directory = root / name
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            raise BootstrapError(f"missing or unsafe bootstrap directory: {name}")
    executable = root / "bin/go"
    if (not executable.is_file() or not os.access(executable, os.X_OK)
            or not executable.resolve().is_relative_to(root)):
        raise BootstrapError("bootstrap bin/go must be an executable within its root")
    return executable


def check(value):
    root = safe_path(str(value))
    executable = check_structure(root)
    env = os.environ.copy()
    # Probe the binary's actual identity and root, without inherited overrides.
    for name in ("GOROOT", "GOHOSTOS", "GOHOSTARCH", "GOOS", "GOARCH", "GOFLAGS", "GODEBUG"):
        env.pop(name, None)
    env.update(GOTOOLCHAIN="local", GOENV="off", GOWORK="off")

    def probe(*arguments):
        result = subprocess.run([str(executable), *arguments], env=env, cwd=root,
                                capture_output=True, text=True, check=True, timeout=30)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.stdout.strip()

    expected = f"go version go{VERSION} {HOST_OS}/{HOST_ARCH}"
    if probe("version") != expected:
        raise BootstrapError(f"bootstrap must report exactly: {expected}")
    identity = probe("env", "GOROOT", "GOHOSTOS", "GOHOSTARCH").splitlines()
    if (len(identity) != 3 or identity[1:] != [HOST_OS, HOST_ARCH]
            or not Path(identity[0]).is_absolute()
            or Path(identity[0]).resolve() != root):
        raise BootstrapError("bootstrap GOROOT or host platform does not match its installation")
    return root


def download(archive):
    print(f"Downloading {ARCHIVE_URL}", file=sys.stderr)
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > ARCHIVE_SIZE:
                raise BootstrapError("bootstrap archive exceeds its pinned size")
            digest.update(chunk)
            output.write(chunk)
    if size != ARCHIVE_SIZE or digest.hexdigest() != ARCHIVE_SHA256:
        raise BootstrapError("bootstrap archive size or SHA256 does not match the official release")


def extract(archive, destination):
    """Preflight every member, then apply Python's data filter during extraction."""
    def within_go(member, directory):
        target = Path(directory) / member.name
        go_root = Path(directory) / "go"
        if not target.resolve().is_relative_to(go_root):
            raise BootstrapError(f"archive member escapes Go root: {member.name}")
        if member.issym() or member.islnk():
            base = target.parent if member.issym() else Path(directory)
            if not (base / member.linkname).resolve().is_relative_to(go_root):
                raise BootstrapError(f"archive link escapes Go root: {member.name}")
        return tarfile.data_filter(member, directory)

    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or not path.parts or path.parts[0] != "go"
                    or ".." in path.parts or "\\" in member.name or str(path) in seen):
                raise BootstrapError(f"unsafe or duplicate archive member: {member.name}")
            seen.add(str(path))
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise BootstrapError(f"unsupported archive member: {member.name}")
            if member.issym() or member.islnk():
                target = member.linkname
                if member.issym():
                    target = posixpath.join(posixpath.dirname(member.name), target)
                target_path = PurePosixPath(posixpath.normpath(target))
                if (target_path.is_absolute() or not target_path.parts
                        or target_path.parts[0] != "go" or "\\" in member.linkname):
                    raise BootstrapError(f"archive link escapes Go root: {member.name}")
            within_go(member, str(destination))
        # The filter runs again as links are created; this also blocks link chains
        # which would redirect a later member outside the extraction directory.
        source.extractall(destination, members=members, filter=within_go)


def require_empty(destination):
    if not destination.is_dir() or any(destination.iterdir()):
        raise BootstrapError("install destination must be a dedicated empty directory")


def install(value):
    destination = safe_path(str(value))
    if not destination.exists():
        destination.mkdir(mode=0o700)
    require_empty(destination)
    # Keep unverified downloads and incomplete extraction outside the destination.
    with tempfile.TemporaryDirectory(prefix=".rc01-go-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        archive = staging / "bootstrap.tar.gz"
        download(archive)
        unpacked = staging / "unpacked"
        unpacked.mkdir()
        extract(archive, unpacked)
        check_structure(unpacked / "go")
        require_empty(destination)
        root = destination / "go"
        (unpacked / "go").rename(root)
        try:
            return check(root)
        except Exception:
            shutil.rmtree(root)
            raise


def selected_lines(root):
    return {
        BUILD_SYMBOL: f"# {BUILD_SYMBOL} is not set" if root else f"{BUILD_SYMBOL}=y",
        ROOT_SYMBOL: f'{ROOT_SYMBOL}="{root}"',
    }


def is_symbol(line, symbol):
    return line.startswith(symbol + "=") or line.startswith("# " + symbol + " is not set")


def configure(config, requested_root):
    root = str(check(requested_root)) if requested_root else ""
    path = Path(config)
    lines = path.read_text().splitlines()
    selection = selected_lines(root)
    retained = [line for line in lines if not any(is_symbol(line, symbol) for symbol in selection)]
    path.write_text("\n".join(retained + list(selection.values())) + "\n")


def validate_config(config, requested_root):
    root = str(safe_path(requested_root)) if requested_root else ""
    lines = Path(config).read_text().splitlines()
    for symbol, expected in selected_lines(root).items():
        if [line for line in lines if is_symbol(line, symbol)] != [expected]:
            raise BootstrapError(f"normalized bootstrap selection does not match the request: {symbol}")
    if root:
        check(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "check"):
        commands.add_parser(name).add_argument("root")
    for name in ("configure", "validate-config"):
        commands.add_parser(name).add_argument("config")
    arguments = parser.parse_args()
    try:
        if arguments.command in ("install", "check"):
            print(globals()[arguments.command](arguments.root))
        else:
            action = configure if arguments.command == "configure" else validate_config
            action(arguments.config, os.environ.get("RC01_GO_BOOTSTRAP_ROOT", ""))
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError, urllib.error.URLError) as error:
        print(f"RC01 Go bootstrap: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
