#!/usr/bin/env python3
"""Record reviewed selection and actual prepared software recipes; fail on drift."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def value(path, key):
    match = re.search(r"^" + re.escape(key) + r"\s*:?=\s*(.+)$", (ROOT / path).read_text(), re.M)
    if not match:
        raise ValueError(f"missing {key} in {path}")
    return match[1].strip()


def firmware_metadata(expected):
    recipe = "package/firmware/ath12k-firmware/Makefile"
    for key, field in (("PKG_SOURCE_DATE", "date"), ("PKG_SOURCE_VERSION", "commit"),
                       ("PKG_SOURCE_URL", "url"), ("PKG_MIRROR_HASH", "sha256"),
                       ("PKG_RELEASE", "release")):
        if value(recipe, key) != str(expected[field]):
            raise ValueError(f"ath12k firmware differs from reviewed metadata: {key}")
    return expected


def local_patchset():
    # Hash actual tracked inputs, including local context/ownership fixes.
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                                     "package/kernel/mac80211/patches", "target/linux/generic/*-6.18",
                                     "target/linux/qualcommbe", "package/network/config/netifd", "package/network/config/firewall4",
                                     "package/emortal/luci-app-router-tool", "config/rc01/patches/daede",
                                     "scripts/patch-rc01-daede.sh"], cwd=ROOT, text=True).splitlines()
    records = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
               for p in sorted(set(paths)) if (ROOT / p).is_file()}
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"sha256": digest, "files": records}


def kernel_btf_metadata():
    kernels = list(ROOT.glob("build_dir/target-*/linux-qualcommbe_ipq95xx/linux-6.18.60/vmlinux"))
    if len(kernels) != 1:
        raise ValueError("dae provenance requires one actual built RC01 vmlinux")
    kernel = kernels[0]
    data = kernel.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("expected actual ELF64 little-endian vmlinux")
    offset = struct.unpack_from("<Q", data, 40)[0]
    size, count, strings = struct.unpack_from("<HHH", data, 58)
    if size < 64 or not 0 < strings < count or offset + size * count > len(data):
        raise ValueError("invalid vmlinux section table")
    entries = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(count)]
    shstr = entries[strings]
    names = data[shstr[4]:shstr[4] + shstr[5]]
    matches = [e for e in entries if names[e[0]:].split(b"\0", 1)[0] == b".BTF"]
    if len(matches) != 1 or not 0 < matches[0][5] <= len(data) - matches[0][4]:
        raise ValueError("actual compiled kernel has no bounded BTF section")
    section = data[matches[0][4]:matches[0][4] + matches[0][5]]
    return {"source": str(kernel.relative_to(ROOT)), "vmlinux_sha256": hashlib.sha256(data).hexdigest(),
            "config_sha256": hashlib.sha256(kernel.with_name(".config").read_bytes()).hexdigest(),
            "btf_bytes": len(section), "btf_sha256": hashlib.sha256(section).hexdigest(),
            "shadow_vmlinux_btf": False}


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
    spec = importlib.util.spec_from_file_location("rc01_software_sources", ROOT / "scripts/rc01-software-sources.py")
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    source_lock = resolver.verify()
    source_lock_path = ROOT / ".rc01-sources/lock.json"
    result["software_resolution"] = {"mode": source_lock["mode"],
        "resolved_at": source_lock["resolved_at"], "replay": source_lock.get("replay", False),
        "source_lock_sha256": hashlib.sha256(source_lock_path.read_bytes()).hexdigest(),
        "platform_inputs": source_lock["platform_inputs"]}
    result["software"] = {}
    result["software"]["ath12k-firmware"] = firmware_metadata(result["ath12k_firmware"])
    result["local_patchset"] = local_patchset()
    dae_lock = source_lock.get("optional_dae") or json.loads((ROOT / "config/rc01/patches/daede/sources.json").read_text())
    enabled = bool(re.search(r"^CONFIG_PACKAGE_dae=y$", (ROOT / ".config").read_text(), re.M))
    if enabled != source_lock["with_dae"]:
        raise ValueError("actual daemon selection differs from this build's source lock")
    result["optional_dae"] = {"enabled": enabled, "source_lock_sha256": hashlib.sha256(json.dumps(dae_lock, sort_keys=True).encode()).hexdigest(),
                              "source": dae_lock, "kernel_btf": "actual-built-kernel; checked in opt-in CI"}
    if enabled:
        for name, expected in dae_lock["files"].items():
            if hashlib.sha256((ROOT / "feeds/daede" / name).read_bytes()).hexdigest() != expected["after"]:
                raise ValueError(f"prepared dae source differs from lock: {name}")
        result["optional_dae"]["kernel_btf"] = kernel_btf_metadata()
    manifest = source_lock["package_manifest"]
    for entry in manifest["packages"]:
        path = f"feeds/{entry['feed']}/{entry['path']}"
        if value(path, "PKG_VERSION") != entry["version"] or hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != entry["applied_sha256"]:
            raise ValueError(f"prepared package differs from reviewed manifest: {path}")
        result["software"][entry["path"].split("/")[-2]] = entry
    result["software"]["nikki"] = {"version": value("feeds/nikki/nikki/Makefile", "PKG_VERSION"),
                                   "release": value("feeds/nikki/nikki/Makefile", "PKG_RELEASE"),
                                   "stable_source": source_lock.get("nikki")}
    result["software"]["go"] = {"version": value("feeds/packages/lang/golang/golang1.27/Makefile", "GO_VERSION_MAJOR_MINOR") + "." + value("feeds/packages/lang/golang/golang1.27/Makefile", "GO_VERSION_PATCH"),
                                "bootstrap": "1.24.13"}
    result["software"]["dnsmasq"] = {"version": value("package/network/services/dnsmasq/Makefile", "PKG_UPSTREAM_VERSION"),
                                     "release": value("package/network/services/dnsmasq/Makefile", "PKG_RELEASE"),
                                     "zero_length_fix": re.search(r"^From ([0-9a-f]{40}) ", (ROOT / "package/network/services/dnsmasq/patches/010-Fix-zero-length-blockdata-allocation.patch").read_text(), re.M)[1]}
    ipq = "package/firmware/ipq-wifi/Makefile"
    result["software"]["ipq-wifi"] = {"source_commit": value(ipq, "PKG_SOURCE_VERSION"), "release": value(ipq, "PKG_RELEASE"), "source_sha256": value(ipq, "PKG_MIRROR_HASH")}
    result["feeds"] = {}
    for line in (ROOT / ".rc01-sources/feeds.conf").read_text().splitlines():
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
