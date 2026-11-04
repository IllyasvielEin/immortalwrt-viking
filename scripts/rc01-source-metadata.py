#!/usr/bin/env python3
"""Record reviewed selection and actual prepared software recipes; fail on drift."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def value(path, key):
    match = re.search(r"^" + re.escape(key) + r"\s*:?=\s*(.+)$", (ROOT / path).read_text(), re.M)
    if not match:
        raise ValueError(f"missing {key} in {path}")
    return match[1].strip()


def metadata():
    result = json.loads((ROOT / "scripts/rc01-platform-sources.json").read_text())
    kernel = result["kernel"]
    actual = "6.18" + value("target/linux/generic/kernel-6.18", "LINUX_VERSION-6.18")
    if actual != kernel["version"] or value("target/linux/generic/kernel-6.18", f"LINUX_KERNEL_HASH-{actual}") != kernel["sha256"]:
        raise ValueError("kernel source differs from reviewed metadata")
    wireless = result["wireless"]
    for key, expected in (("PKG_SOURCE_VERSION", wireless["version"]), ("PKG_RELEASE", wireless["release"]), ("PKG_HASH", wireless["sha256"])):
        if value("package/kernel/mac80211/Makefile", key) != str(expected):
            raise ValueError(f"wireless source differs: {key}")
    result["software"] = {}
    manifest = json.loads((ROOT / "config/rc01/patches/packages/manifest.json").read_text())
    for entry in manifest["packages"]:
        path = f"feeds/{entry['feed']}/{entry['path']}"
        if value(path, "PKG_VERSION") != entry["version"] or hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != entry["applied_sha256"]:
            raise ValueError(f"prepared package differs from reviewed manifest: {path}")
        result["software"][entry["path"].split("/")[-2]] = entry
    result["software"]["nikki"] = {"version": value("feeds/nikki/nikki/Makefile", "PKG_VERSION"),
                                   "release": value("feeds/nikki/nikki/Makefile", "PKG_RELEASE")}
    result["software"]["go"] = {"version": value("feeds/packages/lang/golang/golang1.27/Makefile", "GO_VERSION_MAJOR_MINOR") + "." + value("feeds/packages/lang/golang/golang1.27/Makefile", "GO_VERSION_PATCH"),
                                "bootstrap": "1.24.13"}
    result["software"]["dnsmasq"] = {"version": value("package/network/services/dnsmasq/Makefile", "PKG_UPSTREAM_VERSION"),
                                     "release": value("package/network/services/dnsmasq/Makefile", "PKG_RELEASE"),
                                     "zero_length_fix": re.search(r"^From ([0-9a-f]{40}) ", (ROOT / "package/network/services/dnsmasq/patches/010-Fix-zero-length-blockdata-allocation.patch").read_text(), re.M)[1]}
    ipq = "package/firmware/ipq-wifi/Makefile"
    result["software"]["ipq-wifi"] = {"source_commit": value(ipq, "PKG_SOURCE_VERSION"), "release": value(ipq, "PKG_RELEASE"), "source_sha256": value(ipq, "PKG_MIRROR_HASH")}
    result["feeds"] = {}
    for line in (ROOT / "config/rc01/feeds.conf").read_text().splitlines():
        if not line.startswith("src-git "):
            continue
        _, name, url = line.split()
        revision = subprocess.check_output(["git", "-C", str(ROOT / "feeds" / name), "rev-parse", "HEAD"], text=True).strip()
        if revision != url.rsplit("^", 1)[1]:
            raise ValueError(f"feed is not at its locked revision: {name}")
        result["feeds"][name] = revision
    result["physical_acceptance"] = "not-performed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(metadata(), indent=2) + "\n")


if __name__ == "__main__":
    main()
