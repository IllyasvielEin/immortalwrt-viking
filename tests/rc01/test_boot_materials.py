"""Frozen boot-material identity and adversarial container tests; no device I/O."""
import base64
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rc01_boot_materials", ROOT / "scripts/verify-rc01-boot-materials.py")
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


def synthetic_uboot():
    """New ELF and DTB fixture containing hardware facts, no candidate code."""
    strings = bytearray()
    offsets = {}
    def align(value):
        return value + bytes((-len(value)) % 4)
    def cells(*values):
        return struct.pack(">" + "I" * len(values), *values)
    def node(name, properties, children=()):
        result = cells(1) + align(name.encode() + b"\0")
        for key, value in properties.items():
            if key not in offsets:
                offsets[key] = len(strings)
                strings.extend(key.encode() + b"\0")
            result += cells(3, len(value), offsets[key]) + align(value)
        result += b"".join(children) + cells(2)
        return result
    ports = [node("port@" + str(i), {"phy_address": cells(i + 1), "phy_type": cells(6 if i < 4 else 255)}) for i in range(6)]
    switch = node("ess-switch", {"switch_mac_mode0": cells(21), "switch_mac_mode1": cells(255),
        "switch_mac_mode2": cells(255), "mdc_mdio_gpio": cells(38, 39), "qca808x_gpio": cells(60)},
        [node("port_phyinfo", {}, ports)])
    gpio = node("tlmm-gpio", {}, [node("key_gpio", {}, [node("reset", {"gpio": cells(45)})])])
    structure = node("", {"model": b"Xiaomi BE10000\0", "machid": cells(0x08050301),
        "config_name": b"config@al02-c4\0"}, [switch, gpio]) + cells(9)
    start = 56
    header = struct.pack(">10I", 0xD00DFEED, start + len(structure) + len(strings), start,
        start + len(structure), 40, 17, 16, 0, len(strings), len(structure))
    dtb = header + bytes(16) + structure + strings
    result = bytearray(4096)
    result[:16] = b"\x7fELF\x01\x01\x01" + bytes(9)
    struct.pack_into("<HHIIIIIHHHHHH", result, 16, 2, 40, 1, 0x4A400080, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    struct.pack_into("<8I", result, 52, 1, 0, 0x4A400000, 0x4A400000, len(result), len(result), 5, 4096)
    compressed = gzip.compress(bytes(16) + dtb, mtime=0)
    result[256:256 + len(compressed)] = compressed
    return bytes(result)


def private_fixture(material, data, compressed=None):
    result = copy.deepcopy(material)
    compressed = gzip.compress(data, mtime=0) if compressed is None else compressed
    encoded = base64.b64encode(compressed).decode("ascii")
    maximum = 128
    values = [encoded[i:i + maximum] for i in range(0, len(encoded), maximum)]
    result["private_input"] = {"codec": "gzip+base64", "prefix": "RC01_UBOOT_R1_", "chunks": len(values),
        "compressed_bytes": len(compressed), "compressed_sha256": hashlib.sha256(compressed).hexdigest(), "chunk_max_bytes": maximum}
    environ = {"RC01_UBOOT_R1_" + f"{i + 1:02d}": value for i, value in enumerate(values)}
    return result, environ


class BootMaterials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / "config/rc01/boot"
        cls.layout = json.loads((ROOT / "config/rc01/layout.json").read_text())
        cls.materials = json.loads((cls.directory / "materials.json").read_text())
        cls.mibib = (cls.directory / cls.materials["mibib"]["file"]).read_bytes()
        cls.uboot = synthetic_uboot()
        cls.fixture_material = copy.deepcopy(cls.materials["uboot"])
        cls.fixture_material.update(bytes=len(cls.uboot), sha256=hashlib.sha256(cls.uboot).hexdigest(),
            dtb_gzip_offset=256, dtb_offset=16, provenance="synthetic test fixture; no candidate code")
        cls.fixture_material, cls.fixture_environ = private_fixture(cls.fixture_material, cls.uboot)

    def repair(self, blob):
        for base in (0, 131072):
            struct.pack_into("<I", blob, base + 6156, boot.checksum(blob[base:base + 6144]))

    def test_selected_materials_and_explicit_scope(self):
        with tempfile.TemporaryDirectory(prefix="rc01-boot-fixture-") as temporary:
            directory = Path(temporary)
            materials = copy.deepcopy(self.materials)
            materials["uboot"] = self.fixture_material
            (directory / "materials.json").write_text(json.dumps(materials))
            (directory / materials["mibib"]["file"]).write_bytes(self.mibib)
            result = boot.verify(boot_dir=directory, environ=self.fixture_environ)
        self.assertEqual(result["layout_id"], self.layout["layout_id"])
        self.assertEqual(result["runtime"], "not-performed")
        self.assertEqual(result["physical_acceptance"], "not-performed")
        self.assertEqual(result["uboot"]["configured_copper_ports"], 4)
        self.assertIn("does not execute", result["uboot"]["environment_contract"]["evidence"])

    def test_crc_known_answer(self):
        self.assertEqual(boot.checksum(b"123456789"), 0x89A1897F)
        self.assertEqual([c["crc"] for c in boot.decode_mibib(self.mibib)], ["2747a2e4", "8ab1c279"])

    def test_all_records_match_single_contract(self):
        actual = boot.decode_mibib(self.mibib)[0]["physical"]
        self.assertEqual(actual, sorted(self.layout["partitions"].values(), key=lambda p: p["index"]))
        self.assertEqual([p["name"] for p in actual[-3:]], ["0:LICENSE", "bdata", "rootfs"])
        self.assertEqual(actual[-2]["offset"] + actual[-2]["bytes"], actual[-1]["offset"])

    def test_valid_crc_mirror_disagreement(self):
        data = bytearray(self.mibib)
        for at in (0x810, 0x1010):
            data[131072 + at + 24 * 28] = ord("x")
        self.repair(data)
        with self.assertRaisesRegex(ValueError, "mirror mismatch"):
            boot.decode_mibib(data)

    def test_valid_crc_wrong_mapping(self):
        data = bytearray(self.mibib)
        # Keep internal tables contiguous and internally valid, but consume an
        # extra rootfs block. The single selected layout must still reject it.
        for base in (0, 131072):
            struct.pack_into("<I", data, base + 0x810 + 25 * 28 + 20, 1843)
            struct.pack_into("<I", data, base + 0x1010 + 25 * 28 + 16, 1843 * 128)
        self.repair(data)
        boot.decode_mibib(data)
        with self.assertRaisesRegex(ValueError, "selected layout mismatch"):
            boot.verify_mibib(data, self.layout, self.materials["mibib"], check_identity=False)

    def test_valid_crc_identity_partition_attribute_change(self):
        data = bytearray(self.mibib)
        for base in (0, 131072):
            data[base + 0x810 + 23 * 28 + 24] = 254
            data[base + 0x1010 + 23 * 28 + 24] = 254
        self.repair(data)
        boot.decode_mibib(data)
        with self.assertRaisesRegex(ValueError, "selected layout mismatch"):
            boot.verify_mibib(data, self.layout, self.materials["mibib"], check_identity=False)

    def test_valid_crc_stale_user_table(self):
        data = bytearray(self.mibib)
        for base in (0, 131072):
            struct.pack_into("<I", data, base + 0x1010 + 25 * 28 + 16, 1024)
        self.repair(data)
        with self.assertRaisesRegex(ValueError, "physical/user mismatch"):
            boot.decode_mibib(data)

    def test_mibib_crc_and_tail_corruption(self):
        for at in (0x180C, len(self.mibib) - 1):
            with self.subTest(offset=at):
                data = bytearray(self.mibib)
                data[at] ^= 1
                with self.assertRaises(ValueError):
                    boot.decode_mibib(data)

    def test_uboot_program_bounds(self):
        data = bytearray(self.uboot)
        struct.pack_into("<I", data, 28, 0xFFFFFFF0)
        with self.assertRaisesRegex(ValueError, "program table bounds"):
            boot.decode_elf(data)

    def test_uboot_actual_embedded_board_mismatch(self):
        data = bytearray(self.uboot)
        start = self.fixture_material["dtb_gzip_offset"]
        raw = zlib.decompress(data[start:], 31)
        self.assertIn(b"Xiaomi BE10000", raw)
        changed = raw.replace(b"Xiaomi BE10000", b"Xiaomi BE20000")
        compressed = gzip.compress(changed, mtime=0)
        self.assertLess(start + len(compressed), len(data))
        data[start:start + len(compressed)] = compressed
        with self.assertRaisesRegex(ValueError, "board identity"):
            boot.verify_uboot(data, self.fixture_material, check_identity=False)

    def test_uboot_frozen_identity_cannot_be_replaced_by_valid_structure(self):
        data = bytearray(self.uboot)
        data[100] ^= 1
        # Offset 100 is outside the embedded DTB and executable header; the
        # structure alone is insufficient to accept another candidate.
        boot.verify_uboot(data, self.fixture_material, check_identity=False)
        with self.assertRaisesRegex(ValueError, "frozen identity mismatch"):
            boot.verify_uboot(data, self.fixture_material)

    def test_metadata_names_do_not_expose_local_sources(self):
        name = self.materials["mibib"]["file"]
        self.assertEqual(Path(name).name, name)
        self.assertNotIn("..", name)
        self.assertNotIn("file", self.materials["uboot"])
        self.assertFalse(list(self.directory.glob("*.mbn")))


    def test_private_input_roundtrip_in_memory(self):
        result = boot.load_uboot_input(self.fixture_material, environ=self.fixture_environ)
        self.assertEqual(result, self.uboot)

    def test_private_missing_chunk(self):
        environ = dict(self.fixture_environ)
        environ.pop(next(iter(environ)))
        with self.assertRaisesRegex(ValueError, "missing or malformed"):
            boot.load_uboot_input(self.fixture_material, environ=environ)

    def test_private_bad_encoding_does_not_echo_input(self):
        environ = dict(self.fixture_environ)
        name = next(iter(environ))
        value = "!" * len(environ[name])
        environ[name] = value
        with self.assertRaisesRegex(ValueError, "encoding invalid") as raised:
            boot.load_uboot_input(self.fixture_material, environ=environ)
        self.assertNotIn(value, str(raised.exception))

    def test_private_compressed_identity(self):
        material = copy.deepcopy(self.fixture_material)
        material["private_input"]["compressed_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "compressed identity"):
            boot.load_uboot_input(material, environ=self.fixture_environ)

    def test_private_chunk_contract_and_lengths(self):
        for field, value in (("chunks", 0), ("chunks", 17), ("chunk_max_bytes", 48001), ("compressed_bytes", 1048577)):
            material = copy.deepcopy(self.fixture_material)
            material["private_input"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                boot.load_uboot_input(material, environ=self.fixture_environ)
        environ = dict(self.fixture_environ)
        name = next(iter(environ))
        environ[name] += "A"
        with self.assertRaisesRegex(ValueError, "missing or malformed"):
            boot.load_uboot_input(self.fixture_material, environ=environ)

    def test_private_gzip_truncated_trailing_and_oversized(self):
        original = gzip.compress(self.uboot, mtime=0)
        for compressed in (original[:-1], original + b"trailing", original + gzip.compress(b"second", mtime=0),
                           gzip.compress(self.uboot + b"excess", mtime=0)):
            material, environ = private_fixture(self.fixture_material, self.uboot, compressed)
            with self.subTest(compressed_length=len(compressed)), self.assertRaisesRegex(ValueError, "gzip truncated, trailing or oversized"):
                boot.load_uboot_input(material, environ=environ)

    def test_private_decoded_frozen_identity(self):
        data = bytearray(self.uboot)
        data[-1] ^= 1
        material, environ = private_fixture(self.fixture_material, self.uboot, gzip.compress(data, mtime=0))
        with self.assertRaisesRegex(ValueError, "frozen identity"):
            boot.load_uboot_input(material, environ=environ)

    def test_private_regular_local_file_and_environment_path(self):
        with tempfile.TemporaryDirectory(prefix="rc01-boot-file-") as temporary:
            path = Path(temporary) / "fixture.elf"
            path.write_bytes(self.uboot)
            self.assertEqual(boot.load_uboot_input(self.fixture_material, path, environ={}), self.uboot)
            self.assertEqual(boot.load_uboot_input(self.fixture_material, environ={"RC01_UBOOT_FILE": str(path)}), self.uboot)
            link = Path(temporary) / "symlink"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "unavailable or unsupported"):
                boot.load_uboot_input(self.fixture_material, link, environ={})
            with self.assertRaisesRegex(ValueError, "bounded regular file"):
                boot.load_uboot_input(self.fixture_material, Path(temporary), environ={})

    def test_private_fifo_rejected_without_waiting(self):
        import os
        with tempfile.TemporaryDirectory(prefix="rc01-boot-fifo-") as temporary:
            path = Path(temporary) / "fifo"
            os.mkfifo(path)
            with self.assertRaisesRegex(ValueError, "bounded regular file"):
                boot.load_uboot_input(self.fixture_material, path, environ={})


if __name__ == "__main__":
    unittest.main(verbosity=2)
