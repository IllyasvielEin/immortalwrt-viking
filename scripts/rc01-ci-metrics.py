#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Record bounded build/cache observations; never dump the runner environment."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def run(args, env=None):
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace",
                                timeout=60, env=env)
        return {"exit_code": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}


def identity(command):
    resolved = shutil.which(command) if command else None
    if not resolved:
        return {"command": command, "status": "not-built-or-not-found"}
    executable = Path(resolved).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    return {"command": command, "resolved": str(executable), "sha256": digest,
            "version": run([str(executable), "--version"])}


def main():
    phase, directory = sys.argv[1:]
    if phase not in ("prepare", "report"):
        raise SystemExit("expected prepare or report")
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    stats = destination / "ccache-stats.log"
    data = {"phase": phase, "recorded_at_unix": int(time.time()),
            "ccache_compiler_check": os.environ.get("CCACHE_COMPILERCHECK"),
            "ccache_max_size": os.environ.get("CCACHE_MAXSIZE"),
            "cache_restore": {name: os.environ.get(name) for name in (
                "RC01_COLD_CACHE", "RC01_DOWNLOADS_MATCHED_KEY", "RC01_COMPILER_MATCHED_KEY",
                "RC01_DOWNLOADS_CACHE_OUTCOME", "RC01_COMPILER_CACHE_OUTCOME")}}
    if phase == "prepare":
        stats.write_text("")
        # Actions must pass an expanded absolute path, not a literal $RUNNER_TEMP.
        with Path(os.environ["GITHUB_ENV"]).open("a") as output:
            output.write(f"CCACHE_STATSLOG={stats.resolve()}\n")
        data["runner"] = {"image_os": os.environ.get("ImageOS"),
                          "image_version": os.environ.get("ImageVersion"),
                          "cpu_count": os.cpu_count(),
                          "memory": Path("/proc/meminfo").read_text().splitlines()[:3],
                          "disk": run(["df", "-h", "."])}
    else:
        ccache = Path("staging_dir/host/bin/ccache")
        data["ccache"] = (run([str(ccache.resolve()), "--show-log-stats", "--verbose"])
                          if ccache.is_file() else {"status": "build-did-not-reach-ccache"})
        resolved = run(["make", "-s", "--no-print-directory", "-f",
                        "scripts/rc01-ci-toolchain.mk", "rc01-ci-toolchain"])
        data["toolchain_resolution"] = resolved
        data["tools"] = {}
        if resolved.get("exit_code") == 0:
            for line in resolved["stdout"].splitlines():
                name, separator, command = line.partition("=")
                if separator and name in ("host_cc", "target_cc", "bpf_clang", "bpf_llvm_strip"):
                    data["tools"][name] = identity(command)
    encoded = json.dumps(data, indent=2) + "\n"
    (destination / f"{phase}.json").write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
