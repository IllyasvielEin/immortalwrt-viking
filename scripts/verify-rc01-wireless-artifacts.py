#!/usr/bin/env python3
"""Check the firmware/DS pairing in the actual sysupgrade SquashFS payload."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rc01_images", ROOT / "scripts/verify-rc01-images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


def verify_payloads(amss, m3, module, packages, sources):
    expected = sources["ath12k_firmware"]["hw1"]
    result = {}
    for name, data in (("amss.bin", amss), ("m3.bin", m3)):
        digest = hashlib.sha256(data).hexdigest()
        images.require(len(data) == expected["files"][name]["bytes"] and
                       digest == expected["files"][name]["sha256"], f"wrong HW1 firmware payload: {name}")
        result[name] = {"bytes": len(data), "sha256": digest}
    images.require(expected["build_id"].encode() in amss, "HW1 AMSS version marker missing")
    images.require(expected["build_id"].encode() in module and b"WBE.1.0.r4-00159" not in module,
                   "driver in image does not use the paired 02903 HW1 DS gate")
    images.require(b"ath12k_wifi7_ds_hw1_compat" in module, "actual DS compatibility code missing")
    fw = sources["ath12k_firmware"]
    version = fw["date"].replace("-", ".") + "~" + fw["commit"][:8] + "-r" + str(fw["release"])
    images.require(packages.get("ath12k-firmware-qcn9274-ddwrt") == version,
                   "firmware package version differs from paired source")
    expected_driver = sources["kernel"]["version"] + "." + sources["wireless"]["version"] + "-r" + str(sources["wireless"]["release"])
    for name in ("kmod-ath12k", "kmod-mac80211"):
        images.require(packages.get(name) == expected_driver, f"wrong image package version: {name}")
    result["ath12k_wifi7.ko"] = {"bytes": len(module), "sha256": hashlib.sha256(module).hexdigest()}
    result["packages"] = {name: packages[name] for name in
                          ("ath12k-firmware-qcn9274-ddwrt", "kmod-ath12k", "kmod-mac80211")}
    result["physical_acceptance"] = "not-performed"
    return result


def verify_image(image, sources):
    _, root = images.read_sysupgrade(image)
    tool = shutil.which("unsquashfs") or shutil.which("unsquashfs4")
    if not tool:
        candidate = ROOT / "staging_dir/host/bin/unsquashfs4"
        if candidate.is_file():
            tool = str(candidate)
    images.require(tool is not None, "install squashfs-tools or prepare host unsquashfs4")
    with tempfile.TemporaryDirectory(prefix="rc01-wireless-image-") as temporary:
        squashfs = Path(temporary) / "root.squashfs"
        squashfs.write_bytes(root)

        def cat(path):
            return subprocess.check_output([tool, "-cat", str(squashfs), path])

        listing = subprocess.check_output([tool, "-ls", str(squashfs), "lib/modules"], text=True)
        modules = re.findall(r"^squashfs-root/(lib/modules/[^\n ]*/ath12k_wifi7\.ko(?:\.(?:gz|zst))?)$", listing, re.M)
        images.require(len(modules) == 1, "expected one actual ath12k_wifi7 module in image")
        module = cat(modules[0])
        if modules[0].endswith(".gz"):
            module = gzip.decompress(module)
        elif modules[0].endswith(".zst"):
            module = subprocess.check_output(["zstd", "-dc"], input=module)
        packages = {}
        for record in cat("lib/apk/db/installed").decode().split("\n\n"):
            fields = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
            if "P" in fields and "V" in fields:
                packages[fields["P"]] = fields["V"]
        firmware = "lib/firmware/ath12k/QCN9274/hw1.0/"
        return verify_payloads(cat(firmware + "amss.bin"), cat(firmware + "m3.bin"), module, packages, sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", type=Path)
    args = parser.parse_args()
    candidates = list(args.target_dir.glob("*xiaomi_be10000-squashfs-sysupgrade.bin"))
    images.require(len(candidates) == 1, "expected one RC01 sysupgrade image")
    sources = json.loads((ROOT / "scripts/rc01-platform-sources.json").read_text())
    print(json.dumps(verify_image(candidates[0], sources), indent=2))


if __name__ == "__main__":
    main()
