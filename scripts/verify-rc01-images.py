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
        magic, total, start, strings, reserve, version, _, _, string_size, size = struct.unpack_from(">10I", blob)
        require(magic == 0xD00DFEED and version >= 17, "unsupported FDT header")
        require(40 <= total <= len(blob), "invalid FDT total size")
        require(40 <= start < start + size <= total, "invalid FDT structure bounds")
        require(40 <= strings <= strings + string_size <= total, "invalid FDT string bounds")
        require(40 <= reserve <= total - 16 and reserve % 8 == 0, "invalid FDT reserve map")
        self.reservations = []
        cursor = reserve
        while True:
            require(cursor + 16 <= total, "unterminated FDT reserve map")
            address, length = struct.unpack_from(">QQ", blob, cursor)
            cursor += 16
            if address == length == 0:
                break
            require(length > 0 and address + length <= 1 << 64, "invalid FDT reserve range")
            self.reservations.append((address, length))
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


def verify_enabled_path(tree: Fdt, node: str) -> None:
    parts = node.split("/")
    for count in range(1, len(parts) + 1):
        parent = "/".join(parts[:count]) or "/"
        require(tree.nodes[parent].get("status", b"okay\0") in (b"okay\0", b"ok\0"),
                f"device path is disabled: {parent}")


def verify_ppe_dtb(blob: bytes) -> None:
    tree = Fdt(blob)
    devices = [node for node, props in tree.nodes.items()
               if "compatible" in props and "qcom,ipq9574-ppe" in tree.strings(node, "compatible")]
    require(len(devices) == 1, "expected one IPQ9574 PPE controller")
    edma = devices[0] + "/ethernet-dma"
    names = tree.strings(edma, "interrupt-names")
    interrupts = tree.cells(edma, "interrupts")
    require(len(set(names)) == len(names) and len(interrupts) == 3 * len(names),
            "invalid PPE EDMA IRQ names or cells")
    for prefix, first_irq in (("rxdesc", 332), ("rxfill", 356), ("txcmpl", 364)):
        for ring in (1, 2, 3):
            name = f"{prefix}_{ring}"
            require(name in names, f"missing PPE DS IRQ {name}")
            offset = 3 * names.index(name)
            require(interrupts[offset:offset + 3] == (0, first_irq + ring - 1, 4),
                    f"wrong PPE DS IRQ {name}")
    verify_enabled_path(tree, edma)


def verify_thermal_dtb(blob: bytes) -> None:
    tree = Fdt(blob)
    require("xiaomi,be10000" in tree.strings("/", "compatible"), "DTB is not RC01")
    controllers = [node for node, props in tree.nodes.items()
                   if "compatible" in props and "microchip,emc2301" in tree.strings(node, "compatible")]
    require(len(controllers) == 1, "expected one EMC2301 controller")
    controller = controllers[0]
    require(tree.strings(controller, "compatible") == ["microchip,emc2301", "microchip,emc2305"],
            "unexpected fan-controller compatible fallback")
    require(tree.cells(controller, "reg") == (0x2F,), "wrong fan-controller address")
    require(tree.cells(controller, "#pwm-cells") == (3,), "fan-controller requires three PWM cells")
    controller_phandle = tree.cells(controller, "phandle")
    require(len(controller_phandle) == 1, "invalid fan-controller phandle")
    fan = controller + "/fan@0"
    require(tree.cells(fan, "reg") == (0,), "fan must use channel zero")
    require(tree.cells(fan, "#cooling-cells") == (2,), "fan requires two cooling cells")
    require(tree.cells(fan, "pwms") == (controller_phandle[0], 26000, 0, 1),
            "fan PWM must reference its controller with arguments 26000/normal/push-pull")
    verify_enabled_path(tree, fan)
    phandle = tree.cells(fan, "phandle")
    require(len(phandle) == 1, "invalid fan phandle")
    zone = "/thermal-zones/nss-top-thermal"
    require(tree.cells(zone, "polling-delay") == (1000,), "fan temperature polling is missing")
    for name, temperature, state in (("base", 0, 3), ("warm", 50000, 5), ("hot", 60000, 7), ("full", 70000, 10)):
        trip = zone + "/trips/fan-" + name
        require(tree.cells(trip, "temperature") == (temperature,), "unexpected fan trip")
        require(tree.string(trip, "type") == "active", "fan trip must be active")
        require(tree.cells(trip, "hysteresis") == (0 if name == "base" else 3000,), "unexpected fan hysteresis")
        mapping = zone + "/cooling-maps/fan-" + name
        require(tree.cells(mapping, "trip") == tree.cells(trip, "phandle"), "fan trip reference mismatch")
        require(tree.cells(mapping, "cooling-device") == (phandle[0], state, state), "fan cooling-state mismatch")
    critical = zone + "/trips/nss-top-critical"
    require(tree.string(critical, "type") == "critical", "original critical trip missing")


def resolve_phandle(tree: Fdt, value: int) -> str:
    nodes = [node for node, props in tree.nodes.items()
             if props.get("phandle") == struct.pack(">I", value)]
    require(len(nodes) == 1, f"invalid or duplicate phandle {value}")
    return nodes[0]


def reserved_regions(tree: Fdt) -> list[tuple[str, int, int]]:
    regions = [("FDT reserve map", address, size) for address, size in tree.reservations]
    path = "/reserved-memory"
    require(path in tree.nodes, "missing reserved-memory")
    address_cells = tree.cells(path, "#address-cells")
    size_cells = tree.cells(path, "#size-cells")
    require(address_cells in ((1,), (2,)) and size_cells in ((1,), (2,)),
            "unsupported reserved-memory cells")
    ac, sc = address_cells[0], size_cells[0]
    for node, props in tree.nodes.items():
        if not node.startswith(path + "/") or "/" in node[len(path) + 1:] or "reg" not in props:
            continue
        if props.get("status", b"okay\0") == b"disabled\0":
            continue
        cells = tree.cells(node, "reg")
        require(bool(cells) and len(cells) % (ac + sc) == 0, "invalid reserved-memory reg")
        for offset in range(0, len(cells), ac + sc):
            address = int.from_bytes(struct.pack(f">{ac}I", *cells[offset:offset + ac]), "big")
            size = int.from_bytes(struct.pack(f">{sc}I", *cells[offset + ac:offset + ac + sc]), "big")
            require(size > 0 and address + size <= 1 << 64, "invalid reserved-memory range")
            regions.append((node, address, size))
    return regions


def verify_kernel_memory(tree: Fdt, load: int, size: int) -> None:
    require(size > 0 and load + size <= 1 << 64, "invalid kernel footprint")
    for node, address, length in reserved_regions(tree):
        require(load + size <= address or address + length <= load,
                f"kernel footprint overlaps reserved memory: {node}")


def verify_platform_dtb(blob: bytes) -> dict:
    """Check final compiled RC01 wiring, references and allocation policy."""
    tree = Fdt(blob)
    require("xiaomi,be10000" in tree.strings("/", "compatible"), "DTB is not RC01")
    args = " ".join(tree.nodes.get("/chosen", {}).get(key, b"").decode().rstrip("\0")
                    for key in ("bootargs", "bootargs-append"))
    require([arg for arg in args.split() if arg.startswith("cma=")] == ["cma=320M"],
            "RC01 must request cma=320M")
    regions = reserved_regions(tree)
    tz = [(address, size) for node, address, size in regions if node.endswith("/tzapp@49b00000")]
    require(tz == [(0x49B00000, 0x600000)], "RC01 TrustZone application reservation changed")
    require("no-map" in tree.nodes["/reserved-memory/tzapp@49b00000"], "tzapp must be no-map")
    for node, address, size in regions:
        require(address not in (0x50000000, 0x54000000, 0x58000000, 0x5C000000),
                f"obsolete WLAN/MLO carveout: {node}")
    require("/reserved-memory/tzapp@4d600000" not in tree.nodes, "obsolete tzapp reservation")
    require(all(address + size <= 0x4AB00000 or address >= 0x4D600000
                for _, address, size in regions), "unused PCIe-board Q6 reservation remains")

    def one(predicate, description):
        nodes = [node for node in tree.nodes if predicate(node)]
        require(len(nodes) == 1, f"expected one {description}")
        return nodes[0]

    def compatible(node, value):
        return "compatible" in tree.nodes[node] and value in tree.strings(node, "compatible")

    remoteproc = one(lambda n: compatible(n, "qcom,ipq9574-wcss-sec-pil"), "WCSS remote processor")
    require(tree.string(remoteproc, "status") == "disabled" and
            "memory-region" not in tree.nodes[remoteproc],
            "PCIe-only RC01 must disable WCSS and unbind its Q6 memory")
    require(not any(compatible(n, "qcom,ipq9574-wifi") and
                    tree.nodes[n].get("status", b"okay\0") != b"disabled\0"
                    for n in tree.nodes), "unexpected enabled integrated radio")

    art = one(lambda n: tree.nodes[n].get("label") == b"0:art\0", "ART partition")
    require("read-only" in tree.nodes[art], "ART must be read-only")
    base = {}
    for offset in (0, 6):
        cell = art + f"/nvmem-layout/macaddr@{offset:x}"
        require(tree.cells(cell, "reg") == (offset, 6) and
                tree.cells(cell, "#nvmem-cell-cells") == (1,), "invalid ART MAC base")
        require(tree.string(cell, "compatible") == "mac-base", "MAC base decoder missing")
        base[offset] = tree.cells(cell, "phandle")[0]

    def mac(node, offset, increment):
        require(tree.strings(node, "nvmem-cell-names") == ["mac-address"] and
                tree.cells(node, "nvmem-cells") == (base[offset], increment),
                f"incorrect ART MAC reference: {node}")
        require("local-mac-address" not in tree.nodes[node] and "mac-address" not in tree.nodes[node],
                "device-specific MAC must not be embedded")
        verify_enabled_path(tree, node)

    for label in ("lan1", "lan2", "lan3", "lan4", "sfp", "wan"):
        node = one(lambda n: tree.nodes[n].get("label") == label.encode() + b"\0", label + " port")
        mac(node, 0 if label == "wan" else 6, 0)
    radios = [n for n in tree.nodes if compatible(n, "pci17cb,1109")]
    require(len(radios) == 3, "expected three independent PCIe radios")
    for board_id, increment, pcie in ((1, 2, "pcie@10000000"), (4, 3, "pcie@20000000"), (2, 4, "pcie@18000000")):
        node = one(lambda n: n in radios and tree.cells(n, "qcom,board-id") == (board_id,), "radio board ID")
        # Check card-to-controller identity in addition to the MAC increment.
        require(pcie in node.split("/"), "radio is on the wrong PCIe controller")
        mac(node, 6, increment)
        require(tree.cells(node, "qcom,num-radios") == (1,), "unexpected PHY count")
        require(tree.string(node, "qcom,calibration-variant") == "Xiaomi-BE10000", "wrong BDF variant")
        require("qcom,force-unique-qrtr-id" in tree.nodes[node], "QRTR IDs must remain independent")
        require("memory-region" not in tree.nodes[node] and "memory-region-names" not in tree.nodes[node],
                "PCIe firmware must allocate through DMA/CMA")
        require("qcom,rx-skb-slab" in tree.nodes[node], "PCIe radio must use slab RX buffers")
        require("qcom,ath12k-fw-memory-mode" not in tree.nodes[node],
                "memory-mode experiment must remain separate from the default candidate")

    phy = one(lambda n: compatible(n, "ethernet-phy-ieee802.3-c45"), "Aquantia PHY")
    reset = tree.cells(phy, "reset-gpios")
    require(len(reset) == 3 and reset[1:] == (61, 1), "Aquantia requires GPIO61 active-low reset")
    require(compatible(resolve_phandle(tree, reset[0]), "qcom,ipq9574-tlmm"), "PHY reset is not on TLMM")
    for name in ("reset-assert-us", "reset-deassert-us"):
        require(tree.cells(phy, name) == (100000,), "Aquantia reset delay must be 100 ms")
    require(tree.strings(phy, "nvmem-cell-names") == ["firmware"], "Aquantia firmware reference missing")
    firmware = tree.cells(phy, "nvmem-cells")
    require(len(firmware) == 1, "invalid PHY firmware reference")
    cell = resolve_phandle(tree, firmware[0])
    require(tree.cells(cell, "reg") == (0x28, 0x60002), "Aquantia firmware header/CRC bounds changed")
    partition = cell.rsplit("/", 2)[0]
    require(tree.string(partition, "label") == "0:ethphyfw" and "read-only" in tree.nodes[partition],
            "PHY firmware must use the new device's read-only ethphyfw")
    verify_enabled_path(tree, phy)

    pcie3 = one(lambda n: n.endswith("/pcie@18000000"), "PCIe3 controller")
    pins = tree.cells(pcie3, "pinctrl-0")
    require(len(pins) == 1, "invalid PCIe3 reset pinctrl")
    pin = resolve_phandle(tree, pins[0])
    require(tree.strings(pin, "pins") == ["gpio23"] and "output-low" in tree.nodes[pin],
            "PCIe3 reset must use GPIO23 output-low")
    perst = tree.cells(pcie3, "perst-gpios")
    require(len(perst) == 3 and perst[1:] == (23, 1), "PCIe3 PERST must be GPIO23 active-low")
    require(compatible(resolve_phandle(tree, perst[0]), "qcom,ipq9574-tlmm"), "PCIe3 reset is not on TLMM")
    verify_enabled_path(tree, pcie3)
    nand = one(lambda n: compatible(n, "spi-nand"), "SPI NAND")
    require(tree.cells(nand, "nand-ecc-strength") == (8,) and
            tree.cells(nand, "nand-ecc-step-size") == (512,), "NAND must use ECC8/512")
    verify_enabled_path(tree, nand)
    return {"cma_mib": 320, "radio_board_ids": [1, 4, 2],
            "mac_source": "device-local ART: WAN+0, LAN base6, WiFi base6+2/+3/+4",
            "reserved_regions": [{"node": n, "address": hex(a), "bytes": s} for n, a, s in regions]}


def verify_fit(blob: bytes) -> dict:
    fit = Fdt(blob)
    require(fit.string("/configurations", "default") == CONFIGS[0], "FIT default must be C4")
    configs = [node for node in fit.nodes if node.startswith("/configurations/") and
               "/" not in node[len("/configurations/"):]]
    require(set(configs) == {"/configurations/" + name for name in CONFIGS}, "expected exactly config@al02-c4 and config@al02-c6")
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
    require(image_size >= len(image), "kernel footprint is smaller than Image")
    verify_kernel_memory(Fdt(dtb), load[0], image_size)
    verify_thermal_dtb(dtb)
    verify_ppe_dtb(dtb)
    return {"fit_configs": list(CONFIGS), "load_address": hex(load[0]), "kernel_memory_bytes": image_size,
            "ppe_ds_irq_rings": 3}


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
    # mksquashfs uses -nopad. sysupgrade-tar.sh adds zeroes to a 1 KiB
    # boundary, whereas ubinize stores the raw filesystem followed by 0xff.
    require(len(root) >= 96 and root[:4] == b"hsqs", "rootfs is not SquashFS")
    used = struct.unpack_from("<Q", root, 40)[0]
    require(96 <= used <= len(root), "invalid SquashFS byte count")
    require(len(root) == (used + 1023) // 1024 * 1024,
            "unexpected sysupgrade rootfs alignment")
    require(root[used:] == b"\0" * (len(root) - used),
            "unexpected sysupgrade rootfs padding")
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
    for index, expected in ((0, kernel), (1, root[:used])):
        blocks = volumes.get(index, {})
        require(sorted(blocks) == list(range(len(blocks))) and bool(blocks), "incomplete UBI payload")
        actual = b"".join(blocks[number] for number in sorted(blocks))
        name = "kernel" if index == 0 else "rootfs"
        require(actual[:len(expected)] == expected, f"factory/sysupgrade {name} payload mismatch")
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
    fit = Fdt(kernel)
    ref = fit.string("/configurations/" + CONFIGS[0], "fdt")
    result.update(verify_platform_dtb(fit.prop("/images/" + ref, "data")))
    result["physical_acceptance"] = "not-performed"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
