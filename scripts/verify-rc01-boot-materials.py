#!/usr/bin/env python3
"""Validate frozen RC01 boot materials offline; never executes or flashes them."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import stat
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rc01_boot_images", ROOT / "scripts/verify-rc01-images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
layout_spec = importlib.util.spec_from_file_location("rc01_boot_layout", ROOT / "scripts/rc01-layout.py")
layout_contract = importlib.util.module_from_spec(layout_spec)
layout_spec.loader.exec_module(layout_contract)


class VerificationError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise VerificationError(message)


def checksum(data):
    """Non-reflected CRC-32, initial/final xor zero, polynomial long division."""
    remainder = 0
    for byte in data + bytes(4):
        remainder = (remainder << 8) | byte
        for bit in range(39, 31, -1):
            if remainder & (1 << bit):
                remainder ^= 0x104C11DB7 << (bit - 32)
    return remainder


def decode_mibib(data):
    require(len(data) == 262144, "MIBIB container length")
    copies = []
    for index in range(2):
        block = data[index * 131072:(index + 1) * 131072]
        def word(at, width=4):
            return int.from_bytes(block[at:at + width], "little")
        require([word(i) for i in (0, 4, 8, 12)] == [0xFE569FAC, 0xCD7F127A, 4, index], "MIBIB header/age")
        require(block[16:2048] == b"\xff" * 2032, "MIBIB header padding")
        tables = []
        for at, magic in ((2048, (0x55EE73AA, 0xE35EBDDB)), (4096, (0xAA7D1B9A, 0x1F7D48BC))):
            require((word(at), word(at + 4), word(at + 8)) == (*magic, 4), "MIBIB table header")
            count = word(at + 12)
            require(1 <= count <= 32, "MIBIB table count")
            require(block[at + 16 + 28 * count:at + 912] == bytes((32 - count) * 28), "MIBIB unused slots")
            require(block[at + 912:at + 2048] == b"\xff" * 1136, "MIBIB table padding")
            rows = []
            for i in range(count):
                pos = at + 16 + 28 * i
                raw = block[pos:pos + 16]
                name = raw.split(b"\0", 1)[0]
                require(name and raw == name.ljust(16, b"\0"), "MIBIB name padding")
                try:
                    label = name.decode("ascii")
                except UnicodeDecodeError as error:
                    raise VerificationError("MIBIB partition name encoding") from error
                row = {"index": i, "name": label}
                if at == 2048:
                    row.update(offset=word(pos + 16) * 131072, bytes=word(pos + 20) * 131072, attributes=word(pos + 24))
                else:
                    row.update(image_kib=word(pos + 16), padding_kib=word(pos + 20, 2), which_flash=word(pos + 22, 2),
                               attr1=block[pos + 24], attr2=block[pos + 25], attr3=block[pos + 26], reserved=block[pos + 27])
                rows.append(row)
            tables.append(rows)
        physical, user = tables
        require(len(physical) == len(user), "MIBIB table counts differ")
        end, names = 0, set()
        for p, u in zip(physical, user):
            require(p["name"] not in names, "MIBIB duplicate name")
            names.add(p["name"])
            require(p["offset"] == end and p["bytes"] > 0, "MIBIB geometry gap/overlap/zero size")
            end = p["offset"] + p["bytes"]
            require(end <= 256 * 1048576, "MIBIB exceeds NAND")
            require(p["name"] == u["name"] and p["bytes"] == 1024 * (u["image_kib"] + u["padding_kib"]), "MIBIB physical/user mismatch")
            attributes = u["attr1"] | u["attr2"] << 8 | u["attr3"] << 16 | u["which_flash"] << 24
            require(u["which_flash"] <= 255 and attributes == p["attributes"] and u["reserved"] == 255, "MIBIB user attributes")
        require([word(i) for i in (6144, 6148, 6152)] == [0x9D41BEA1, 0xF1DED2EA, 1], "MIBIB CRC header")
        require(word(6156) == checksum(block[:6144]), "MIBIB CRC mismatch")
        require(block[6160:] == b"\xff" * (131072 - 6160), "MIBIB block tail")
        copies.append({"age": index, "crc": f"{word(6156):08x}", "physical": physical, "user": user})
    require(copies[0]["physical"] == copies[1]["physical"] and copies[0]["user"] == copies[1]["user"], "MIBIB mirror mismatch")
    return copies


def verify_mibib(data, layout, material, *, check_identity=True):
    copies = decode_mibib(data)
    require(len(copies[0]["physical"]) == 26, "MIBIB selected layout count")
    for copy in copies:
        for key in ("license", "bdata", "rootfs"):
            expected = layout["partitions"][key]
            require(copy["physical"][expected["index"]] == dict(index=expected["index"], name=expected["name"],
                    offset=expected["offset"], bytes=expected["bytes"], attributes=expected["attributes"]), "MIBIB selected layout mismatch: " + key)
        require(copy["physical"] == sorted(layout["partitions"].values(), key=lambda row: row["index"]), "MIBIB complete selected layout mismatch")
    for base in (0, 131072):
        for key, at in (("preserved_physical_records_sha256", 0x810), ("preserved_user_records_sha256", 0x1010)):
            require(hashlib.sha256(data[base + at:base + at + 24 * 28]).hexdigest() == material[key], "MIBIB preserved records changed")
    if check_identity:
        verify_identity(data, material, "MIBIB")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "copies": [{"age": c["age"], "crc": c["crc"]} for c in copies],
            "partition_count": 26, "layout_id": layout["layout_id"]}


def verify_identity(data, material, label):
    require(len(data) == material["bytes"] and hashlib.sha256(data).hexdigest() == material["sha256"], label + " frozen identity mismatch")


def decode_elf(data):
    require(len(data) >= 52 and data[:7] == b"\x7fELF\x01\x01\x01", "U-Boot must be little-endian ELF32")
    fields = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    kind, machine, version, entry, phoff, _, _, ehsize, phsize, phnum, _, _, _ = fields
    require(kind in (2, 3) and machine == 40 and version == 1 and ehsize == 52, "U-Boot ELF identity")
    require(phsize == 32 and 1 <= phnum <= 64 and phoff >= 52 and phoff + phsize * phnum <= len(data), "U-Boot program table bounds")
    loads = []
    for i in range(phnum):
        kind, offset, virtual, physical, filesz, memsz, flags, alignment = struct.unpack_from("<8I", data, phoff + i * phsize)
        require(offset + filesz <= len(data), "U-Boot program file bounds")
        if kind == 1:
            require(0 < memsz <= 256 * 1048576 and filesz <= memsz and virtual + memsz <= 1 << 32, "U-Boot LOAD bounds")
            require(alignment in (0, 1) or alignment & (alignment - 1) == 0, "U-Boot LOAD alignment")
            loads.append((virtual, memsz, flags))
    require(loads and any(start <= (entry & ~1) < start + size and flags & 1 for start, size, flags in loads), "U-Boot entry outside executable LOAD")
    return {"class": 32, "machine": "ARM", "load_segments": len(loads), "entry": entry}


def verify_uboot(data, material, *, check_identity=True):
    elf = decode_elf(data)
    start = material["dtb_gzip_offset"]
    require(0 <= start < len(data) and data[start:start + 3] == b"\x1f\x8b\x08", "U-Boot compressed DTB location")
    decompressor = zlib.decompressobj(31)
    try:
        raw = decompressor.decompress(data[start:], 1048577)
    except zlib.error as error:
        raise VerificationError("U-Boot DTB gzip corruption") from error
    require(decompressor.eof and len(raw) <= 1048576, "U-Boot DTB gzip size or truncation")
    at = material["dtb_offset"]
    require(0 <= at <= len(raw) - 40, "U-Boot DTB bounds")
    size = struct.unpack_from(">I", raw, at + 4)[0]
    require(40 <= size <= len(raw) - at, "U-Boot DTB size")
    tree = images.Fdt(raw[at:at + size])
    require(tree.string("/", "model") == "Xiaomi BE10000" and tree.cells("/", "machid") == (0x08050301,), "U-Boot board identity")
    require("config@al02-c4" in tree.strings("/", "config_name"), "U-Boot C4 FIT configuration")
    switch = "/ess-switch"
    require(tuple(tree.cells(switch, "switch_mac_mode" + str(i))[0] for i in range(3)) == (0x15, 0xFF, 0xFF), "U-Boot switch modes")
    for i in range(6):
        node = switch + "/port_phyinfo/port@" + str(i)
        require(tree.cells(node, "phy_type") == ((6,) if i < 4 else (255,)), "U-Boot PHY type")
        if i < 4:
            require(tree.cells(node, "phy_address") == (i + 1,), "U-Boot PHY address")
    require(tree.cells(switch, "mdc_mdio_gpio") == (38, 39) and tree.cells(switch, "qca808x_gpio") == (60,), "U-Boot MDIO/reset GPIO")
    require(tree.cells("/tlmm-gpio/key_gpio/reset", "gpio") == (45,), "U-Boot button GPIO")
    if check_identity:
        verify_identity(data, material, "U-Boot")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "elf": elf, "model": "Xiaomi BE10000", "fit_config": "config@al02-c4",
            "configured_copper_ports": 4, "environment_contract": material["environment"], "runtime": "not-performed"}



def load_uboot_input(material, path=None, environ=None):
    """Read a bounded regular local file or decode private CI chunks in memory."""
    environ = os.environ if environ is None else environ
    path = path if path is not None else environ.get("RC01_UBOOT_FILE")
    expected = material["bytes"]
    require(type(expected) is int and 0 < expected <= 1048576, "U-Boot input size contract")
    if path is not None:
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_size == expected, "U-Boot input must be a bounded regular file")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = None
                data = source.read(expected + 1)
        except OSError:
            raise VerificationError("U-Boot input file unavailable or unsupported") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
    else:
        transport = material["private_input"]
        require(transport["codec"] == "gzip+base64" and transport["prefix"] == "RC01_UBOOT_R1_", "U-Boot private codec contract")
        count, maximum, compressed_bytes = transport["chunks"], transport["chunk_max_bytes"], transport["compressed_bytes"]
        require(all(type(value) is int for value in (count, maximum, compressed_bytes)) and
                1 <= count <= 16 and 1 <= maximum <= 48000 and 0 < compressed_bytes <= 1048576,
                "U-Boot private transport bounds")
        encoded_bytes = ((compressed_bytes + 2) // 3) * 4
        require(count == (encoded_bytes + maximum - 1) // maximum, "U-Boot private chunk count contract")
        chunks = []
        for index in range(count):
            value = environ.get(transport["prefix"] + f"{index + 1:02d}")
            size = min(maximum, encoded_bytes - maximum * index)
            require(isinstance(value, str) and len(value) == size and value.isascii(), "U-Boot private input missing or malformed")
            chunks.append(value)
        encoded = "".join(chunks)
        try:
            compressed = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise VerificationError("U-Boot private input encoding invalid") from None
        require(len(compressed) == compressed_bytes and base64.b64encode(compressed).decode("ascii") == encoded,
                "U-Boot private input encoding or length mismatch")
        require(hashlib.sha256(compressed).hexdigest() == transport["compressed_sha256"], "U-Boot private compressed identity mismatch")
        decompressor = zlib.decompressobj(31)
        try:
            data = decompressor.decompress(compressed, expected + 1)
        except zlib.error:
            raise VerificationError("U-Boot private gzip invalid") from None
        require(decompressor.eof and not decompressor.unused_data and not decompressor.unconsumed_tail and len(data) == expected,
                "U-Boot private gzip truncated, trailing or oversized")
    verify_identity(data, material, "U-Boot")
    return data


def verify(layout_path=ROOT / "config/rc01/layout.json", boot_dir=ROOT / "config/rc01/boot", uboot_path=None, environ=None):
    layout = layout_contract.load_layout(layout_path)
    layout_contract.check_generated(layout)
    directory = Path(boot_dir)
    materials = json.loads((directory / "materials.json").read_text())
    require(materials["uboot"]["environment"]["bytes"] == layout["environment"]["bytes"], "U-Boot ENV contract mismatch")
    result = {"layout_id": layout["layout_id"], "scope": "static-candidate-validation", "runtime": "not-performed", "physical_acceptance": "not-performed"}
    result["mibib"] = verify_mibib((directory / materials["mibib"]["file"]).read_bytes(), layout, materials["mibib"])
    result["uboot"] = verify_uboot(load_uboot_input(materials["uboot"], uboot_path, environ), materials["uboot"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=ROOT / "config/rc01/layout.json")
    parser.add_argument("--boot-dir", type=Path, default=ROOT / "config/rc01/boot")
    parser.add_argument("--uboot", type=Path, help="private local candidate; otherwise use private CI inputs")
    args = parser.parse_args()
    print(json.dumps(verify(args.layout, args.boot_dir, args.uboot), indent=2))
