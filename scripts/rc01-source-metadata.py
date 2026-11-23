#!/usr/bin/env python3
"""Record platform provenance and the prepared pinned-feed recipes; fail on drift."""
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
                                     "package/emortal/luci-app-router-tool", "config/rc01/patches",
                                     "scripts/prepare-rc01.sh", "include/netfilter.mk",
                                     "package/kernel/linux/modules/netfilter.mk"], cwd=ROOT, text=True).splitlines()
    records = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
               for p in sorted(set(paths)) if (ROOT / p).is_file()}
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"sha256": digest, "files": records}


def integration_metadata(result, root=None):
    """Bind a merge claim to real Git ancestry and the complete review set.

    Schema 1 remains readable for historical source selections. Schema 2
    records imported history separately from local overrides and acceptance.
    """
    root = ROOT if root is None else root
    if result.get("schema") == 1:
        return {"mode": "legacy-selection", "ancestry_claim": False}
    if result.get("schema") != 2:
        raise ValueError("unsupported platform provenance schema")
    integration = result["integration"]
    if integration.get("mode") != "upstream-merges-with-local-overrides":
        raise ValueError("unsupported integration mode")
    upstream = integration["imported_head"]
    local = result["review"]["local_base"]
    for sha in (upstream, local):
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("integration references must be full commit IDs")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    head = git("rev-parse", "HEAD")
    for ancestor in (upstream, local):
        if subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, head],
                          cwd=root, capture_output=True).returncode:
            raise ValueError("build HEAD does not contain the declared integration ancestry")
    if git("ls-files", "--unmerged"):
        raise ValueError("cannot record provenance for an unresolved merge")
    review = result["review"]
    path = (root / review["inventory"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("review inventory must be inside the source tree")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != review["inventory_sha256"]:
        raise ValueError("review inventory digest differs")
    inventory = json.loads(data)
    if inventory["local"] != local or inventory["upstream"] != upstream:
        raise ValueError("review inventory endpoints differ")
    records = inventory["records"]
    shas = [record["sha"] for record in records]
    expected = set(git("rev-list", f"{local}..{upstream}").splitlines())
    if len(set(shas)) != len(shas) or set(shas) != expected:
        raise ValueError("review inventory has duplicate, missing or extra commits")
    if review["completed_unique_commits"] != len(expected):
        raise ValueError("review count differs from actual commit set")
    for record in records:
        if record.get("review_status") != "complete" or not record.get("review_depth"):
            raise ValueError("review inventory contains incomplete records")
    legacy = result["legacy_selection"]
    legacy_path = (root / legacy["path"]).resolve()
    if not legacy_path.is_relative_to(root.resolve()):
        raise ValueError("legacy provenance must be inside the source tree")
    if hashlib.sha256(legacy_path.read_bytes()).hexdigest() != legacy["sha256"]:
        raise ValueError("legacy provenance digest differs")
    return {"mode": integration["mode"], "source_head": head,
            "imported_head": upstream, "ancestry_verified": True,
            "reviewed_unique_commits": len(expected),
            "review_inventory_sha256": review["inventory_sha256"],
            "physical_acceptance": "not-performed"}


def metadata():
    result = json.loads((ROOT / "scripts/rc01-platform-sources.json").read_text())
    result["integration_proof"] = integration_metadata(result)
    kernel = result["kernel"]
    actual = "6.18" + value("target/linux/generic/kernel-6.18", "LINUX_VERSION-6.18")
    if actual != kernel["version"] or value("target/linux/generic/kernel-6.18", f"LINUX_KERNEL_HASH-{actual}") != kernel["sha256"]:
        raise ValueError("kernel source differs from reviewed metadata")
    wireless = result["wireless"]
    for key, expected in (("PKG_SOURCE_VERSION", wireless["version"]), ("PKG_RELEASE", wireless["release"]), ("PKG_HASH", wireless["sha256"])):
        if value("package/kernel/mac80211/Makefile", key) != str(expected):
            raise ValueError(f"wireless source differs: {key}")
    feeds_conf = (ROOT / "config/rc01/feeds.conf").read_text()
    result["software_resolution"] = {"mode": "pinned-feeds",
                                     "feeds_conf_sha256": hashlib.sha256(feeds_conf.encode()).hexdigest()}
    result["software"] = {}
    result["software"]["ath12k-firmware"] = firmware_metadata(result["ath12k_firmware"])
    result["local_patchset"] = local_patchset()
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
    for line in feeds_conf.splitlines():
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
    parser.add_argument("--summary", action="store_true", help="print verified build metadata as public key=value lines")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.summary:
        result = json.loads(args.output.read_text())
        proof = result["integration_proof"]
        merged = proof.get("ancestry_verified", False)
        values = {
            "viking_integration": proof["mode"],
            "viking_full_merge": str(merged).lower(),
            "viking_imported_head": proof.get("imported_head", "not-merged"),
            "viking_reviewed_commits": proof.get("reviewed_unique_commits", 0),
            "kernel": result["kernel"]["version"],
            "kernel_source_sha256": result["kernel"]["sha256"],
            "mac80211": f'{result["wireless"]["version"]}-r{result["wireless"]["release"]} ({proof["mode"]})',
            "ipq_wifi": f'{result["software"]["ipq-wifi"]["release"]}@{result["software"]["ipq-wifi"]["source_commit"]}',
            "dnsmasq": f'{result["software"]["dnsmasq"]["version"]}-r{result["software"]["dnsmasq"]["release"]}@{result["software"]["dnsmasq"]["zero_length_fix"]}',
        }
        for key, value in values.items():
            print(f"{key}={value}")
        return
    args.output.write_text(json.dumps(metadata(), indent=2) + "\n")


if __name__ == "__main__":
    main()
