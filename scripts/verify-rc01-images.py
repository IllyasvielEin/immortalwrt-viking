#!/usr/bin/env python3
"""Verify RC01 FIT aliases, thermal DTB, UBI volumes and writable-space budget."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import tarfile
import zlib

PEB = 131072
LEB = 126976
PARTITION_PEBS = 960
RESERVE_PEBS = 44  # Whole-NAND bad-block budget plus UBI overhead.
MIN_DATA_BYTES = 32 * 1024 * 1024
CONFIGS = ("config@al02-c4", "config@al02-c6")


class VerificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


class Fdt:
    """Small, bounded reader for inline-data FITs and their embedded DTBs."""

    def __init__(self, blob: bytes):
        require(len(blob) >= 40, "truncated FDT header")
        magic, total, start, strings, _, version, _, _, string_size, size = struct.unpack_from(">10I", blob)
        require(magic == 0xD00DFEED and version >= 17, "unsupported FDT header")
        require(40 <= total <= len(blob), "invalid FDT total size")
        require(40 <= start < start + size <= total, "invalid FDT structure bounds")
        require(40 <= strings <= strings + string_size <= total, "invalid FDT string bounds")
        self.nodes: dict[str, dict[str, bytes]] = {}
        names = blob[strings:strings + string_size]
        pos, end, stack, finished = start, start + size, [], False
        while pos + 4 <= end:
            token = struct.unpack_from(">I", blob, pos)[0]
            pos += 4
            if token == 1:
                nul = blob.find(b"\0", pos, end)
                require(nul >= 0, "unterminated FDT node")
                name = blob[pos:nul].decode("ascii")
                require("/" not in name and (bool(stack) or name == ""), "invalid FDT node name")
                stack.append(name)
                path = "/" + "/".join(stack[1:])
                require(path not in self.nodes, "duplicate FDT node")
                self.nodes[path] = {}
                pos = (nul + 4) & ~3
            elif token == 2:
                require(bool(stack), "unbalanced FDT node")
                stack.pop()
            elif token == 3:
                require(bool(stack) and pos + 8 <= end, "invalid FDT property")
                length, name_offset = struct.unpack_from(">II", blob, pos)
                pos += 8
                require(pos + length <= end and name_offset < len(names), "FDT property out of bounds")
                nul = names.find(b"\0", name_offset)
                require(nul >= 0, "unterminated FDT property name")
                name = names[name_offset:nul].decode("ascii")
                props = self.nodes["/" + "/".join(stack[1:])]
                require(name not in props, "duplicate FDT property")
                props[name] = blob[pos:pos + length]
                pos = (pos + length + 3) & ~3
            elif token == 4:
                continue
            elif token == 9:
                require(not stack, "unclosed FDT node")
                finished = True
                break
            else:
                raise VerificationError(f"unknown FDT token {token}")
        require(finished, "FDT has no end token")

    def prop(self, path: str, name: str) -> bytes:
        try:
            return self.nodes[path][name]
        except KeyError as error:
            raise VerificationError(f"missing FDT property {path}:{name}") from error

    def strings(self, path: str, name: str) -> list[str]:
        value = self.prop(path, name)
        require(value.endswith(b"\0"), f"invalid string property {path}:{name}")
        return value[:-1].decode("utf-8").split("\0")

    def string(self, path: str, name: str) -> str:
        values = self.strings(path, name)
        require(len(values) == 1, f"expected one string at {path}:{name}")
        return values[0]

    def cells(self, path: str, name: str) -> tuple[int, ...]:
        value = self.prop(path, name)
        require(len(value) % 4 == 0, f"invalid cell property {path}:{name}")
        return struct.unpack(f">{len(value) // 4}I", value)


def verify_hashes(fdt: Fdt, path: str, data: bytes) -> None:
    hashes = [node for node in fdt.nodes if node.startswith(path + "/hash") and "/" not in node[len(path) + 1:]]
    require(bool(hashes), f"no hashes for {path}")
    crypto = False
    for node in hashes:
        algorithm = fdt.string(node, "algo")
        if algorithm == "crc32":
            expected = struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)
        elif algorithm in ("sha1", "sha256", "sha384", "sha512"):
            expected = hashlib.new(algorithm, data).digest()
            crypto = True
        else:
            raise VerificationError(f"unsupported FIT hash {algorithm}")
        require(fdt.prop(node, "value") == expected, f"FIT hash mismatch: {node}")
    require(crypto, f"no cryptographic hash for {path}")


def verify_thermal_dtb(blob: bytes) -> None:
    tree = Fdt(blob)
    require("xiaomi,be10000" in tree.strings("/", "compatible"), "DTB is not RC01")
    controllers = [node for node, props in tree.nodes.items()
                   if "compatible" in props and "microchip,emc2301" in tree.strings(node, "compatible")]
    require(len(controllers) == 1, "expected one EMC2301 controller")
    controller = controllers[0]
    require(tree.cells(controller, "reg") == (0x2F,), "wrong fan-controller address")
    fan = controller + "/fan@0"
    phandle = tree.cells(fan, "phandle")
    require(len(phandle) == 1, "invalid fan phandle")
    zone = "/thermal-zones/nss-top-thermal"
    require(tree.cells(zone, "polling-delay") == (1000,), "fan temperature polling is missing")
    for name, temperature, state in (("base", 0, 3), ("warm", 50000, 5), ("hot", 60000, 7), ("full", 70000, 10)):
        trip = zone + "/trips/fan-" + name
        require(tree.cells(trip, "temperature") == (temperature,), "unexpected fan trip")
        require(tree.string(trip, "type") == "active", "fan trip must be active")
        mapping = zone + "/cooling-maps/fan-" + name
        require(tree.cells(mapping, "trip") == tree.cells(trip, "phandle"), "fan trip reference mismatch")
        require(tree.cells(mapping, "cooling-device") == (phandle[0], state, state), "fan cooling-state mismatch")
    critical = zone + "/trips/nss-top-critical"
    require(tree.string(critical, "type") == "critical", "original critical trip missing")


def verify_fit(blob: bytes) -> dict:
    fit = Fdt(blob)
    require(fit.string("/configurations", "default") in CONFIGS, "unexpected FIT default")
    refs = []
    for config in CONFIGS:
        path = "/configurations/" + config
        refs.append((fit.string(path, "kernel"), fit.string(path, "fdt")))
    require(refs[0] == refs[1], "C4/C6 aliases reference different payloads")
    kernel_path, dtb_path = ("/images/" + ref for ref in refs[0])
    require(fit.string(kernel_path, "arch") == "arm64", "kernel architecture is not arm64")
    require(fit.string(kernel_path, "compression") == "gzip", "expected gzip Image")
    kernel = fit.prop(kernel_path, "data")
    dtb = fit.prop(dtb_path, "data")
    verify_hashes(fit, kernel_path, kernel)
    verify_hashes(fit, dtb_path, dtb)
    with gzip.GzipFile(fileobj=io.BytesIO(kernel)) as stream:
        image = stream.read(64 * 1024 * 1024 + 1)
    require(64 <= len(image) <= 64 * 1024 * 1024, "unexpected decompressed kernel size")
    require(image[56:60] == b"ARM\x64", "missing ARM64 Image header")
    text_offset, image_size = struct.unpack_from("<QQ", image, 8)
    load = fit.cells(kernel_path, "load")
    entry = fit.cells(kernel_path, "entry")
    require(len(load) == 1 and entry == load, "unexpected kernel entry")
    require(load[0] >= 0x40000000 and (load[0] - text_offset) % 0x200000 == 0, "ARM64 alignment mismatch")
    require(image_size >= len(image) and load[0] + image_size <= 0x4A100000, "kernel footprint overlaps bootloader memory")
    verify_thermal_dtb(dtb)
    return {"fit_configs": list(CONFIGS), "load_address": hex(load[0]), "kernel_memory_bytes": image_size}


def image_budget(kernel: bytes, root: bytes) -> dict:
    require(len(root) >= 96 and root[:4] == b"hsqs", "rootfs is not SquashFS")
    used = struct.unpack_from("<Q", root, 40)[0]
    require(96 <= used <= len(root), "invalid SquashFS byte count")
    payload_pebs = (len(kernel) + LEB - 1) // LEB + (len(root) + LEB - 1) // LEB
    data_bytes = (PARTITION_PEBS - RESERVE_PEBS - payload_pebs) * LEB
    require(data_bytes >= MIN_DATA_BYTES, "image leaves less than 32 MiB of rootfs_data LEBs")
    return {"kernel_bytes": len(kernel), "rootfs_bytes": len(root), "minimum_estimated_data_leb_bytes": data_bytes}


def read_sysupgrade(path: Path) -> tuple[bytes, bytes]:
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        require(len(names) == len(set(names)), "duplicate sysupgrade member")
        kernels = [member for member in members if member.name.startswith("sysupgrade-") and
                   member.name.count("/") == 1 and member.name.endswith("/kernel")]
        require(len(kernels) == 1, "expected exactly one kernel in sysupgrade")
        kernel_member = kernels[0]
        root_member = archive.getmember(kernel_member.name.rsplit("/", 1)[0] + "/root")
        require(kernel_member.isfile() and root_member.isfile(), "payload must be a regular file")
        require(0 < kernel_member.size <= 16 * 1024 * 1024 and 0 < root_member.size <= 120 * 1024 * 1024,
                "unexpected sysupgrade payload size")
        return archive.extractfile(kernel_member).read(), archive.extractfile(root_member).read()


def ubi_crc(data: bytes) -> int:
    return zlib.crc32(data) ^ 0xFFFFFFFF


def verify_factory(path: Path, kernel: bytes, root: bytes) -> dict:
    blob = path.read_bytes()
    require(bool(blob) and len(blob) % PEB == 0, "factory UBI is not PEB-aligned")
    require(len(blob) <= (PARTITION_PEBS - RESERVE_PEBS) * PEB, "factory UBI exceeds NAND budget")
    volumes: dict[int, dict[int, bytes]] = {}
    tables = []
    for offset in range(0, len(blob), PEB):
        block = blob[offset:offset + PEB]
        if block == b"\xff" * PEB:
            continue
        require(block[:4] == b"UBI#" and block[4] == 1, "bad UBI EC header")
        require(ubi_crc(block[:60]) == struct.unpack_from(">I", block, 60)[0], "UBI EC CRC mismatch")
        vid, data_offset = struct.unpack_from(">II", block, 16)
        require((vid, data_offset) == (2048, 4096), "unexpected UBI page geometry")
        header = block[vid:vid + 64]
        if header == b"\xff" * 64:
            continue
        require(header[:4] == b"UBI!" and header[4] == 1, "bad UBI VID header")
        require(ubi_crc(header[:60]) == struct.unpack_from(">I", header, 60)[0], "UBI VID CRC mismatch")
        vol_id, lnum = struct.unpack_from(">II", header, 8)
        data = block[data_offset:]
        if vol_id == 0x7FFFEFFF:
            tables.append(data)
            continue
        require(vol_id in (0, 1, 2), "unexpected factory UBI volume ID")
        blocks = volumes.setdefault(vol_id, {})
        require(lnum not in blocks, "duplicate UBI logical block")
        blocks[lnum] = data
    require(len(tables) == 2, "expected two UBI volume tables")
    for table in tables:
        for index, name in enumerate((b"kernel", b"rootfs", b"rootfs_data")):
            record = table[index * 172:(index + 1) * 172]
            require(ubi_crc(record[:168]) == struct.unpack_from(">I", record, 168)[0], "UBI volume-table CRC mismatch")
            name_length = struct.unpack_from(">H", record, 14)[0]
            require(record[16:16 + name_length] == name, "UBI kernel/rootfs/data IDs changed")
            if index == 2:
                require(record[144] & 1, "rootfs_data must autoresize")
    require(tables[0][:3 * 172] == tables[1][:3 * 172], "UBI volume-table copies differ")
    for index, expected in ((0, kernel), (1, root)):
        blocks = volumes.get(index, {})
        require(sorted(blocks) == list(range(len(blocks))) and bool(blocks), "incomplete UBI payload")
        actual = b"".join(blocks[number] for number in sorted(blocks))
        require(actual[:len(expected)] == expected, "factory/sysupgrade payload mismatch")
        require(actual[len(expected):].strip(b"\xff") == b"", "unexpected data after UBI payload")
    return {"factory_bytes": len(blob), "ubi_volume_ids": {"kernel": 0, "rootfs": 1, "rootfs_data": 2}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", type=Path)
    args = parser.parse_args()
    factories = list(args.target_dir.glob("*xiaomi_be10000-squashfs-factory.ubi"))
    upgrades = list(args.target_dir.glob("*xiaomi_be10000-squashfs-sysupgrade.bin"))
    require(len(factories) == len(upgrades) == 1, "expected exactly one RC01 factory and sysupgrade image")
    kernel, root = read_sysupgrade(upgrades[0])
    result = verify_fit(kernel) | image_budget(kernel, root) | verify_factory(factories[0], kernel, root)
    result["physical_acceptance"] = "not-performed"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
