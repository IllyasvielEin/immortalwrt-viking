#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
cd "$(dirname "$0")/.."
# The source lock covers the immutable feed, exact pre/postimages and patch
# bytes. A matching HEAD alone does not make a changed recipe trustworthy.
python3 -B - "${1:-feeds/daede}" <<'PY'
from pathlib import Path
import hashlib
import json
import subprocess
import sys

feed = Path(sys.argv[1]).resolve()
patch_dir = Path("config/rc01/patches/daede")
lock = json.loads((patch_dir / "sources.json").read_text())
revision = subprocess.check_output(["git", "-C", str(feed), "rev-parse", "HEAD"], text=True).strip()
if revision != lock["feed_commit"]:
    raise SystemExit("Unexpected daede revision; review the source lock and local patches before updating")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


patches = lock["patches"]
if sorted(item["name"] for item in patches) != sorted(path.name for path in patch_dir.glob("*.patch")):
    raise SystemExit("Unexpected daede patch set; review the source lock")
for item in patches:
    if sha256(patch_dir / item["name"]) != item["sha256"]:
        raise SystemExit(f"Daede patch differs from source lock: {item['name']}")
for name, expected in lock["files"].items():
    if sha256(feed / name) not in (expected["before"], expected["after"]):
        raise SystemExit(f"Daede source differs from reviewed pre/postimage: {name}")
for item in patches:
    patch = str((patch_dir / item["name"]).resolve())
    command = ["git", "-C", str(feed), "apply"]
    if subprocess.run(command + ["--reverse", "--check", patch],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        continue
    subprocess.run(command + ["--check", patch], check=True)
    subprocess.run(command + [patch], check=True)
for name, expected in lock["files"].items():
    if sha256(feed / name) != expected["after"]:
        raise SystemExit(f"Daede prepared source differs from source lock: {name}")
print("RC01 daede source and lifecycle patches verified")
PY
