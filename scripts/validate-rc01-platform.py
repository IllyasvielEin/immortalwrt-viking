#!/usr/bin/env python3
"""Validate prepared RC01 kernel/wireless sources and compile the project DTB."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rc01_images", ROOT / "scripts/verify-rc01-images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
require = images.require


def function(source: str, name: str) -> str:
    match = re.search(r"\b" + re.escape(name) + r"\([^;]*?\)\s*\{", source, re.S)
    require(match is not None, f"missing function {name}")
    depth = 1
    for end in range(match.end(), len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[match.end():end]
    raise images.VerificationError(f"unterminated function {name}")


def verify_kernel(kernel: Path) -> dict:
    version = (kernel / "Makefile").read_text()
    for key, value in (("VERSION", "6"), ("PATCHLEVEL", "18"), ("SUBLEVEL", "60")):
        require(re.search(rf"^{key}\s*=\s*{value}$", version, re.M), "expected prepared Linux 6.18.60")
    for path, name, pointer in (("net/ipv4/af_inet.c", "inet_sock_destruct", "inet->inet_opt"),
                                ("net/ipv4/tcp_ipv4.c", "tcp_v4_reqsk_destructor", "inet_rsk(req)->ireq_opt")):
        body = function((kernel / path).read_text(), name)
        require(f"kfree_rcu(rcu_dereference_protected({pointer}, 1), rcu);" in body,
                f"CVE-2026-98432 fix missing in {name}")
    core = (kernel / "drivers/mtd/nand/spi/core.c").read_text()
    require("memcmp(id + 1, info->devid.id, min(3, info->devid.len))" in core, "0411 NAND ID workaround missing")
    require(core.index("&esmt_c8_spinand_manufacturer") < core.index("&gigadevice_spinand_manufacturer"),
            "ESMT must match before GigaDevice's shorter ID")
    esmt = (kernel / "drivers/mtd/nand/spi/esmt.c").read_text()
    entry = re.search(r'SPINAND_INFO\("F50D2G41KA",(.*?)SPINAND_ECCINFO', esmt, re.S)
    require(entry is not None, "F50D2G41KA entry missing")
    normalized = re.sub(r"\s+", "", entry[1])
    for value in ("SPINAND_ID(SPINAND_READID_METHOD_OPCODE_ADDR,0x51,0x7f,0x7f,0x7f)",
                  "NAND_MEMORG(1,2048,128,64,2048,40,1,1,1)", "NAND_ECCREQ(8,512)"):
        require(value in normalized, "F50D2G41KA ID/geometry/ECC changed")
    # Reproduce the controller's four returned bytes and the three-byte match.
    require(bytes.fromhex("c8517f7f")[1:] == bytes.fromhex("517f7f7f")[:3], "QPIC ID prefix mismatch")
    qpic = (kernel / "drivers/spi/spi-qpic-snand.c").read_text()
    for marker in ("rc01-nand: matched model=", "rc01-nand: QPIC READID", "rc01-nand: ECC strength="):
        require(marker in core + qpic, f"0412 probe diagnostic missing: {marker}")
    pcs = function((kernel / "drivers/net/pcs/pcs-qcom-ipq9574.c").read_text(), "ipq_pcs_config_mode")
    require(pcs.count("qpcs->interface = interface;") == 1, "invalid PCS state commit")
    require(pcs.index("clk_set_rate(qpcs->qpcs_mii[0]->rx_clk, rate)") <
            pcs.index("reset_control_deassert(qpcs->xpcs_rstc)") <
            pcs.index("qpcs->interface = interface;") < pcs.index("err_clk:"),
            "PCS clocks/reset/state commit order changed")
    return {"kernel": "6.18.60", "cve_2026_98432_destructors": 2,
            "nand": "F50D2G41KA, 256MiB, ECC8/512, three device ID bytes", "pcs_state_commit": "after-success"}


def verify_wireless(wireless: Path) -> dict:
    ath = wireless / "drivers/net/wireless/ath/ath12k"
    core = (ath / "core.c").read_text()
    group = function(core, "ath12k_core_hw_group_assign")
    guard = group.index("ab->hw_rev == ATH12K_HW_QCN9274_HW10")
    require(guard < group.index("ath12k_core_hw_group_find_by_dt(ab)"), "HW1 was grouped before MLO guard")
    require("ab->hif.bus == ATH12K_BUS_PCI" in group and "ATH12K_FW_FEATURE_MLO" in group,
            "PCIe firmware MLO feature guard missing")
    capability = function(core, "ath12k_core_hw_group_set_mlo_capable")
    require("ag->mlo_capable = false;" in capability and "ATH12K_HW_QCN9274_HW10" in capability,
            "HW1 still advertises MLO")
    require("ab->hif.bus == ATH12K_BUS_AHB && !ab->fw.fw_features_valid" in capability,
            "AHB split firmware MLO path was lost")
    qmi = (ath / "qmi.c").read_text()
    dynamic = function(qmi, "ath12k_qmi_alloc_target_mem_chunk")
    require("case AFC_REGION_TYPE:" in dynamic and "chunk->size > AFC_MEM_SIZE" in dynamic and
            "ath12k_qmi_alloc_chunk(ab, chunk)" in dynamic, "dynamic AFC allocation/bound missing")
    header = (ath / "qmi.h").read_text()
    require(re.search(r"#define\s+AFC_SLOT_SIZE\s+0x1000\b", header) and
            re.search(r"#define\s+AFC_MAX_SLOT\s+2\b", header) and
            re.search(r"#define\s+AFC_MEM_SIZE\s+\(AFC_SLOT_SIZE \* AFC_MAX_SLOT\)", header),
            "AFC bound is not 8192 bytes")
    require("QCN9274 MHI selection:" not in (ath / "mhi.c").read_text() and
            "QCN9274 TCSR hardware version:" not in (ath / "wifi7/pci.c").read_text(),
            "temporary wireless diagnostics remain")
    require("WMI_VDEV_TYPE_STA" in function((ath / "mac.c").read_text(), "ath12k_mac_fill_reg_tpc_info"),
            "909 station TPE guard missing")
    return {"wireless": "backports-7.2", "hw1_mlo": False, "dynamic_afc_limit": 8192}


def compile_dtb(kernel: Path, output: Path) -> None:
    source = ROOT / "target/linux/qualcommbe/dts"
    command = ["cpp", "-nostdinc", "-undef", "-D__DTS__", "-x", "assembler-with-cpp",
               "-I", str(source), "-I", str(kernel / "arch/arm64/boot/dts/qcom"),
               "-I", str(kernel / "include"), str(source / "ipq9570-be10000.dts")]
    dts = subprocess.run(command, check=True, capture_output=True).stdout
    subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(output), "-"],
                   input=dts, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("wireless", type=Path)
    args = parser.parse_args()
    result = verify_kernel(args.kernel) | verify_wireless(args.wireless)
    with tempfile.TemporaryDirectory(prefix="rc01-dtb-") as temporary:
        output = Path(temporary) / "rc01.dtb"
        compile_dtb(args.kernel, output)
        blob = output.read_bytes()
        images.verify_thermal_dtb(blob)
        images.verify_ppe_dtb(blob)
        result.update(images.verify_platform_dtb(blob))
    result["physical_acceptance"] = "not-performed"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
