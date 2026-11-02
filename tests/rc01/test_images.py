"""Boot/flash regression fixtures; no router access or firmware flashing."""
import gzip
import hashlib
import importlib.util
from pathlib import Path
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rc01_images", ROOT / "scripts/verify-rc01-images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
DTC = shutil.which("dtc")

DTB_SOURCE = '''/dts-v1/;
/ {
    #address-cells = <1>; #size-cells = <0>;
    compatible = "xiaomi,be10000", "qcom,ipq9574";
    controller: fan-controller@2f {
        compatible = "microchip,emc2301", "microchip,emc2305";
        reg = <0x2f>;
        #address-cells = <1>; #size-cells = <0>; #pwm-cells = <3>;
        fan: fan@0 { reg = <0>; #cooling-cells = <2>; };
    };
    thermal-zones {
        nss-top-thermal {
            polling-delay = <1000>;
            trips {
                nss-top-critical { temperature = <125000>; type = "critical"; };
                base: fan-base { temperature = <0>; type = "active"; };
                warm: fan-warm { temperature = <50000>; type = "active"; };
                hot: fan-hot { temperature = <60000>; type = "active"; };
                full: fan-full { temperature = <70000>; type = "active"; };
            };
            cooling-maps {
                fan-base { trip = <&base>; cooling-device = <&fan 3 3>; };
                fan-warm { trip = <&warm>; cooling-device = <&fan 5 5>; };
                fan-hot { trip = <&hot>; cooling-device = <&fan 7 7>; };
                fan-full { trip = <&full>; cooling-device = <&fan 10 10>; };
            };
        };
    };
};
'''


def rootfs(size=4096):
    blob = bytearray(size)
    blob[:4] = b"hsqs"
    struct.pack_into("<Q", blob, 40, size)
    return bytes(blob)


@unittest.skipUnless(DTC, "dtc is required for FIT fixtures")
class Images(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def compile(self, source, name):
        dts, dtb = self.work / (name + ".dts"), self.work / (name + ".dtb")
        dts.write_text(source)
        subprocess.run([DTC, "-I", "dts", "-O", "dtb", "-o", str(dtb), str(dts)],
                       check=True, capture_output=True)
        return dtb

    def fit(self, *, alias=True, load="0x41000000", board="xiaomi,be10000", extra=""):
        dtb = self.compile(DTB_SOURCE.replace("xiaomi,be10000", board), "board")
        kernel = bytearray(4096)
        kernel[56:60] = b"ARM\x64"
        struct.pack_into("<QQ", kernel, 8, 0, 0x200000)
        compressed = self.work / "Image.gz"
        compressed.write_bytes(gzip.compress(bytes(kernel), mtime=0))
        its = self.work / "image.its"
        cmd = ["sh", str(ROOT / "scripts/mkits.sh"), "-A", "arm64", "-C", "gzip",
               "-a", load, "-e", load, "-v", "6.18.55", "-k", str(compressed),
               "-d", str(dtb), "-D", "xiaomi_be10000", "-c", "config@al02-c6", "-o", str(its)]
        if alias:
            cmd += ["-b", "config@al02-c4"]
        subprocess.run(cmd, check=True, capture_output=True)
        source = its.read_text()
        # mkimage fills these values in production. Fixtures use independently
        # computed hashes so malformed FITs can also be constructed deliberately.
        for name, payload in (("kernel-1", compressed.read_bytes()), ("fdt-1", dtb.read_bytes())):
            for suffix, value in (("1", struct.pack(">I", zlib.crc32(payload))),
                                  ("2", hashlib.sha1(payload).digest())):
                source += f'\n&{{/images/{name}/hash-{suffix}}} {{ value = [{value.hex(" ")}]; }};\n'
        return self.compile(source + extra, "fit").read_bytes()

    def test_aliases_boot_same_payload(self):
        result = images.verify_fit(self.fit())
        self.assertEqual(result["fit_configs"], list(images.CONFIGS))

    def test_c6_only_is_rejected(self):
        with self.assertRaisesRegex(images.VerificationError, "config@al02-c4"):
            images.verify_fit(self.fit(alias=False))

    def test_alias_cannot_select_another_dtb(self):
        blob = self.fit(extra='\n&{/configurations/config@al02-c4} { fdt = "another-board"; };\n')
        with self.assertRaisesRegex(images.VerificationError, "different payloads"):
            images.verify_fit(blob)

    def test_wrong_board_is_rejected(self):
        with self.assertRaisesRegex(images.VerificationError, "not RC01"):
            images.verify_fit(self.fit(board="xiaomi,be10000-pro"))

    def test_wrong_arm64_alignment_is_rejected(self):
        with self.assertRaisesRegex(images.VerificationError, "alignment"):
            images.verify_fit(self.fit(load="0x41080000"))

    def test_hash_corruption_is_rejected(self):
        blob = self.fit()
        digest = images.Fdt(blob).prop("/images/kernel-1/hash-2", "value")
        pos = blob.index(digest)
        damaged = blob[:pos] + bytes([blob[pos] ^ 1]) + blob[pos + 1:]
        with self.assertRaisesRegex(images.VerificationError, "hash mismatch"):
            images.verify_fit(damaged)

    def test_small_image_keeps_writable_budget(self):
        result = images.image_budget(self.fit(), rootfs())
        self.assertGreaterEqual(result["minimum_estimated_data_leb_bytes"], images.MIN_DATA_BYTES)

    def test_oversized_image_is_rejected(self):
        with self.assertRaisesRegex(images.VerificationError, "32 MiB"):
            images.image_budget(b"x" * 1000000, rootfs(100 * 1024 * 1024))

    def test_duplicate_alias_is_rejected_by_generator(self):
        result = subprocess.run(["sh", str(ROOT / "scripts/mkits.sh"), "-A", "arm64", "-C", "gzip",
                                 "-a", "0x41000000", "-e", "0x41000000", "-v", "test", "-k", "/dev/null",
                                 "-c", "config@al02-c6", "-b", "config@al02-c6", "-o", str(self.work / "bad.its")],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate", result.stderr)

    def factory(self, kernel, root):
        table = bytearray(b"\xff" * images.LEB)
        for number, name in enumerate((b"kernel", b"rootfs", b"rootfs_data")):
            record = bytearray(172)
            struct.pack_into(">III", record, 0, 1, 1, 0)
            record[12] = 1
            struct.pack_into(">H", record, 14, len(name))
            record[16:16 + len(name)] = name
            record[144] = int(number == 2)
            struct.pack_into(">I", record, 168, images.ubi_crc(record[:168]))
            table[number * 172:(number + 1) * 172] = record

        def block(vol_id, number, payload):
            out = bytearray(b"\xff" * images.PEB)
            ec = bytearray(64); ec[:4] = b"UBI#"; ec[4] = 1
            struct.pack_into(">II", ec, 16, 2048, 4096)
            struct.pack_into(">I", ec, 60, images.ubi_crc(ec[:60]))
            vid = bytearray(64); vid[:4] = b"UBI!"; vid[4] = 1; vid[5] = 1
            struct.pack_into(">II", vid, 8, vol_id, number)
            struct.pack_into(">I", vid, 60, images.ubi_crc(vid[:60]))
            out[:64] = ec; out[2048:2112] = vid; out[4096:4096 + len(payload)] = payload
            return bytes(out)

        payload = block(0x7FFFEFFF, 0, table) + block(0x7FFFEFFF, 1, table)
        payload += block(0, 0, kernel) + block(1, 0, root)
        path = self.work / "factory.ubi"; path.write_bytes(payload)
        return path

    def test_factory_and_sysupgrade_match(self):
        kernel, root = self.fit(), rootfs()
        result = images.verify_factory(self.factory(kernel, root), kernel, root)
        self.assertEqual(result["ubi_volume_ids"]["rootfs"], 1)

    def test_factory_crc_failure_is_rejected(self):
        kernel, root = self.fit(), rootfs()
        path = self.factory(kernel, root)
        blob = bytearray(path.read_bytes()); blob[10] ^= 1; path.write_bytes(blob)
        with self.assertRaisesRegex(images.VerificationError, "EC CRC"):
            images.verify_factory(path, kernel, root)

    def test_factory_payload_mismatch_is_rejected(self):
        kernel, root = self.fit(), rootfs()
        path = self.factory(kernel, root)
        with self.assertRaisesRegex(images.VerificationError, "payload mismatch"):
            images.verify_factory(path, kernel, root[:-1] + b"x")

    def test_sysupgrade_zero_padding_is_distinct_from_ubi_erased_padding(self):
        kernel, root = self.fit(), rootfs(4097)
        path = self.factory(kernel, root)
        padded = root + b"\0" * 1023
        images.verify_factory(path, kernel, padded)
        with self.assertRaisesRegex(images.VerificationError, "rootfs padding"):
            images.verify_factory(path, kernel, padded[:-1] + b"x")
        # Never strip meaningful trailing zeroes from the filesystem itself.
        damaged = bytearray(path.read_bytes())
        damaged[3 * images.PEB + 4096 + len(root) - 1] = 1
        path.write_bytes(damaged)
        with self.assertRaisesRegex(images.VerificationError, "payload mismatch"):
            images.verify_factory(path, kernel, padded)

    @unittest.skipUnless(shutil.which("ubinize"), "ubinize is required for real packaging regression")
    def test_real_ubinize_and_sysupgrade_scripts_match_for_unaligned_rootfs(self):
        kernel = self.fit()
        kernel_path = self.work / "kernel"
        kernel_path.write_bytes(kernel)
        env = os.environ | {"TOPDIR": str(ROOT), "SOURCE_DATE_EPOCH": "1793618500"}
        for size in [4097, images.LEB + 1]:
            root = rootfs(size)
            root_path = self.work / "root"
            root_path.write_bytes(root)
            factory = self.work / "real-factory.ubi"
            upgrade = self.work / "real-sysupgrade.bin"
            for command in [
                ["sh", str(ROOT / "scripts/ubinize-image.sh"), "--kernel", str(kernel_path),
                 "--rootfs", str(root_path), str(factory), "-p", "128KiB", "-m", "2048"],
                ["sh", str(ROOT / "scripts/sysupgrade-tar.sh"), "--board", "xiaomi_be10000",
                 "--kernel", str(kernel_path), "--rootfs", str(root_path), str(upgrade)],
            ]:
                subprocess.run(command, env=env, check=True, capture_output=True)
            actual_kernel, actual_root = images.read_sysupgrade(upgrade)
            self.assertEqual(actual_root, root + b"\0" * ((-size) % 1024))
            images.verify_factory(factory, actual_kernel, actual_root)


if __name__ == "__main__":
    unittest.main()
