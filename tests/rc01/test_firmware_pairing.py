"""Reject stale firmware/module/package combinations before accepting images."""
import copy
import hashlib
import importlib.util
import gzip
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def module(name, script):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / script)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


artifacts = module("rc01_wireless_artifacts", "verify-rc01-wireless-artifacts.py")
metadata = module("rc01_source_metadata", "rc01-source-metadata.py")


class FirmwarePairing(unittest.TestCase):
    def setUp(self):
        self.sources = json.loads((ROOT / "scripts/rc01-platform-sources.json").read_text())
        # Small fixture blobs exercise validation without downloading firmware.
        # Real payload hashes are enforced separately in the post-build image.
        self.amss = b"fixture WBE.1.0-02903 payload"
        self.m3 = b"fixture m3 payload"
        self.driver = b"module ath12k_wifi7_ds_hw1_compat WBE.1.0-02903"
        for name, blob in (("amss.bin", self.amss), ("m3.bin", self.m3)):
            self.sources["ath12k_firmware"]["hw1"]["files"][name] = {
                "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        self.packages = {"ath12k-firmware-qcn9274-ddwrt": "2026.11.07~774f0cca-r1",
                         "kmod-ath12k": "6.18.60.7.2-r47", "kmod-mac80211": "6.18.60.7.2-r47"}

    def verify(self, **changes):
        args = {"amss": self.amss, "m3": self.m3, "module": self.driver,
                "packages": self.packages, "sources": self.sources}
        args.update(changes)
        return artifacts.verify_payloads(**args)

    def test_correct_pair_passes_and_mismatched_firmware_or_driver_fails(self):
        self.verify()
        for changes in ({"amss": self.amss + b"wrong"}, {"m3": self.m3[:-1]},
                        {"module": self.driver.replace(b"02903", b"00159")},
                        {"module": self.driver + b" WBE.1.0.r4-00159"},
                        {"module": b"WBE.1.0-02903 without DS code"}):
            with self.subTest(changes=changes), self.assertRaises(artifacts.images.VerificationError):
                self.verify(**changes)

    def test_stale_public_ci_package_versions_are_rejected(self):
        for package in self.packages:
            packages = self.packages | {package: self.packages[package].replace("r47", "r44").replace("r1", "r0")}
            with self.subTest(package=package), self.assertRaises(artifacts.images.VerificationError):
                self.verify(packages=packages)

    @unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs") and shutil.which("zstd"),
                         "squashfs-tools and zstd are required for actual image extraction")
    def test_actual_squashfs_apk_and_compressed_module_extraction(self):
        with tempfile.TemporaryDirectory(prefix="rc01-squashfs-pairing-") as temporary:
            work = Path(temporary)
            root = work / "root"
            firmware = root / "lib/firmware/ath12k/QCN9274/hw1.0"
            firmware.mkdir(parents=True)
            (firmware / "amss.bin").write_bytes(self.amss)
            (firmware / "m3.bin").write_bytes(self.m3)
            modules = root / "lib/modules/6.18.60"
            modules.mkdir(parents=True)
            database = root / "lib/apk/db/installed"
            database.parent.mkdir(parents=True)
            correct = "\n\n".join(f"P:{name}\nV:{version}\nF:lib\nR:ignored" for name, version in self.packages.items()) + "\n\n"
            database.write_text(correct)
            for index, (suffix, blob) in enumerate((("", self.driver), (".gz", gzip.compress(self.driver)),
                         (".zst", subprocess.check_output(["zstd", "-q", "-c"], input=self.driver)))):
                with self.subTest(module=suffix or "plain"):
                    for previous in modules.iterdir():
                        previous.unlink()
                    (modules / ("ath12k_wifi7.ko" + suffix)).write_bytes(blob)
                    squashfs = work / f"root-{index}.squashfs"
                    subprocess.run(["mksquashfs", str(root), str(squashfs), "-noappend", "-processors", "1", "-quiet"],
                                   check=True, capture_output=True)
                    upgrade = work / "sysupgrade.bin"
                    with tarfile.open(upgrade, "w") as archive:
                        payloads = {"kernel": b"fixture kernel", "root": squashfs.read_bytes()}
                        for name, data in payloads.items():
                            entry = tarfile.TarInfo("sysupgrade-xiaomi_be10000/" + name)
                            entry.size = len(data)
                            archive.addfile(entry, io.BytesIO(data))
                    result = artifacts.verify_image(upgrade, self.sources)
                    self.assertEqual(result["ath12k_wifi7.ko"]["sha256"], hashlib.sha256(self.driver).hexdigest())
            # A real installed DB drift must be rejected by the full extractor.
            database.write_text(correct.replace("6.18.60.7.2-r47", "6.18.60.7.2-r44"))
            squashfs = work / "wrong-packages.squashfs"
            subprocess.run(["mksquashfs", str(root), str(squashfs), "-noappend", "-processors", "1", "-quiet"],
                           check=True, capture_output=True)
            with tarfile.open(upgrade, "w") as archive:
                for name, data in (("kernel", b"fixture kernel"), ("root", squashfs.read_bytes())):
                    entry = tarfile.TarInfo("sysupgrade-xiaomi_be10000/" + name)
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
            with self.assertRaises(artifacts.images.VerificationError):
                artifacts.verify_image(upgrade, self.sources)

    def test_firmware_source_recipe_is_locked_beyond_mac80211(self):
        expected = json.loads((ROOT / "scripts/rc01-platform-sources.json").read_text())["ath12k_firmware"]
        metadata.firmware_metadata(expected)
        for key in ("url", "commit", "sha256", "date", "release"):
            changed = copy.deepcopy(expected)
            changed[key] = "unreviewed"
            with self.subTest(key=key), self.assertRaises(ValueError):
                metadata.firmware_metadata(changed)

    def test_optional_dae_btf_provenance_reads_the_actual_kernel_section(self):
        with tempfile.TemporaryDirectory(prefix="rc01-btf-provenance-") as temporary:
            root = Path(temporary)
            kernel = root / "build_dir/target-test/linux-qualcommbe_ipq95xx/linux-6.18.60/vmlinux"
            kernel.parent.mkdir(parents=True)
            kernel.with_name(".config").write_text("CONFIG_DEBUG_INFO_BTF=y\n")
            names = b"\0.shstrtab\0.BTF\0"
            section = b"\x9f\xeb\x01\x00" + bytes(20)
            blob = bytearray(288 + len(section))
            blob[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<Q", blob, 40, 64)
            struct.pack_into("<HHH", blob, 58, 64, 3, 1)
            struct.pack_into("<IIQQQQIIQQ", blob, 128, 1, 3, 0, 0, 256, len(names), 0, 0, 1, 0)
            struct.pack_into("<IIQQQQIIQQ", blob, 192, 11, 1, 0, 0, 288, len(section), 0, 0, 1, 0)
            blob[256:256 + len(names)] = names
            blob[288:] = section
            kernel.write_bytes(blob)
            original = metadata.ROOT
            metadata.ROOT = root
            try:
                result = metadata.kernel_btf_metadata()
                self.assertEqual(result["btf_sha256"], hashlib.sha256(section).hexdigest())
                self.assertFalse(result["shadow_vmlinux_btf"])
                kernel.write_bytes(blob[:-1])
                with self.assertRaises(ValueError):
                    metadata.kernel_btf_metadata()
                kernel.write_bytes(blob.replace(b".BTF", b".XYZ"))
                with self.assertRaises(ValueError):
                    metadata.kernel_btf_metadata()
            finally:
                metadata.ROOT = original


if __name__ == "__main__":
    unittest.main()
