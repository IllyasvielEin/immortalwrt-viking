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


def verify_nand(kernel: Path) -> dict:
    """Check the prepared entry correction without weakening other IDs."""
    core = (kernel / "drivers/mtd/nand/spi/core.c").read_text()
    match = function(core, "spinand_match_and_init")
    require("memcmp(id + 1, info->devid.id, info->devid.len)" in match and
            "min(3, info->devid.len)" not in core,
            "SPI NAND matching must compare the full per-entry device ID")
    require(core.index("&esmt_c8_spinand_manufacturer") < core.index("&gigadevice_spinand_manufacturer"),
            "ESMT must match before GigaDevice's shorter ID")
    esmt = (kernel / "drivers/mtd/nand/spi/esmt.c").read_text()
    entry = re.search(r'SPINAND_INFO\("F50D2G41KA",(.*?)SPINAND_ECCINFO', esmt, re.S)
    require(entry is not None, "F50D2G41KA entry missing")
    normalized = re.sub(r"\s+", "", entry[1])
    for value in ("SPINAND_ID(SPINAND_READID_METHOD_OPCODE_ADDR,0x51,0x7f,0x7f)",
                  "NAND_MEMORG(1,2048,128,64,2048,40,1,1,1)", "NAND_ECCREQ(8,512)"):
        require(value in normalized, "F50D2G41KA ID/geometry/ECC changed")
    require(re.search(r"#define\s+SPINAND_MFR_ESMT_C8\s+0xc8\b", esmt) and
            re.search(r"esmt_c8_spinand_manufacturer\s*=\s*\{\s*\.id\s*=\s*SPINAND_MFR_ESMT_C8", esmt),
            "F50D2G41KA manufacturer is not 0xc8")
    diagnostic = re.search(r'"rc01-nand: matched model=[^"]+",(.*?)\);', match, re.S)
    require(diagnostic is not None, "NAND match diagnostic missing")
    arguments = re.sub(r"\s+", "", diagnostic[1])
    require(arguments == "info->model,(unsignedint)rdid_method,(unsignedint)info->devid.len,"
                         "(unsignedint)info->devid.len,(unsignedlonglong)nanddev_size(nand)",
            "NAND diagnostic must report the actual table/comparison length")
    qpic = (kernel / "drivers/spi/spi-qpic-snand.c").read_text()
    for marker in ("rc01-nand: matched model=", "rc01-nand: QPIC READID", "rc01-nand: ECC strength="):
        require(marker in core + qpic, f"0412 probe diagnostic missing: {marker}")
    return {"nand": "F50D2G41KA, 256MiB, ECC8/512, three device ID bytes"}


def verify_kernel(kernel: Path) -> dict:
    version = (kernel / "Makefile").read_text()
    for key, value in (("VERSION", "6"), ("PATCHLEVEL", "18"), ("SUBLEVEL", "60")):
        require(re.search(rf"^{key}\s*=\s*{value}$", version, re.M), "expected prepared Linux 6.18.60")
    for path, name, pointer in (("net/ipv4/af_inet.c", "inet_sock_destruct", "inet->inet_opt"),
                                ("net/ipv4/tcp_ipv4.c", "tcp_v4_reqsk_destructor", "inet_rsk(req)->ireq_opt")):
        body = function((kernel / path).read_text(), name)
        require(f"kfree_rcu(rcu_dereference_protected({pointer}, 1), rcu);" in body,
                f"CVE-2026-98432 fix missing in {name}")
    nand = verify_nand(kernel)
    pcs_source = (kernel / "drivers/net/pcs/pcs-qcom-ipq9574.c").read_text()
    pcs = function(pcs_source, "ipq_pcs_config_mode")
    require(pcs.count("qpcs->interface = interface;") == 1, "invalid PCS state commit")
    require(pcs.index("clk_set_rate(qpcs->qpcs_mii[0]->rx_clk, rate)") <
            pcs.index("reset_control_deassert(qpcs->xpcs_rstc)") <
            pcs.index("qpcs->interface = interface;") < pcs.index("err_clk:"),
            "PCS clocks/reset/state commit order changed")
    require(pcs.index("WRITE_ONCE(qpcs->clock_rate, rate)") <
            pcs.index("clk_set_rate(qpcs->rx_hw.clk, rate)"),
            "PCS parent rate must be published before CCF recalculation")
    require("READ_ONCE(qpcs->clock_rate)" in function(pcs_source, "ipq_pcs_clk_rate_get"),
            "PCS clock provider must not depend on the late successful-mode cache")
    require(pcs.index("qpcs->interface = PHY_INTERFACE_MODE_NA") <
            pcs.index("reset_control_assert(qpcs->xpcs_rstc)"),
            "PCS reconfiguration must invalidate the previous mode before reset")
    return {"kernel": "6.18.60", "cve_2026_98432_destructors": 2,
            "pcs_state_commit": "after-success", "pcs_parent_clock": "published-before-CCF"} | nand


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
    tx = function((ath / "wifi7/dp_tx.c").read_text(), "ath12k_wifi7_dp_tx")
    require("(skb->protocol == cpu_to_be16(ETH_P_PAE) &&\n\t\t     (skb_cb->flags & ATH12K_SKB_CIPHER_SET))" in tx and
            "msdu_ext_desc = true;" in tx, "native-WiFi encrypted EAPOL conversion missing")
    rx = function((ath / "dp_rx.c").read_text(), "ath12k_dp_rx_h_undecap_nwifi")
    require("ieee80211_has_a4(hdr->frame_control) && hdr_len % 4" in rx and
            "msdu->len >= ALIGN(hdr_len, 4) + 6" in rx and
            "!get_unaligned_le16(msdu->data + hdr_len)" in rx and
            '"\\xaa\\xaa\\x03\\x00\\x00"' in rx,
            "HW1 four-address padding must be conditional and bounded")
    ds = (ath / "wifi7/ppe_ds.c").read_text()
    gate = function(ds, "ath12k_wifi7_ds_hw1_compat")
    require("ab->qmi.target.fw_version == 0x10098b57" in gate and
            'strstr(ab->qmi.target.fw_build_id, "WBE.1.0-02903")' in gate and
            "ATH12K_HW_QCN9274_HW10" in gate and "00159" not in gate,
            "02903 firmware must be paired with its exact HW1 DS gate")
    setup = function(ds, "ath12k_wifi7_ds_setup")
    require("!ath12k_wifi7_ds_hw1_compat(dp->ab)" in setup and
            setup.index("!ath12k_wifi7_ds_hw1_compat(dp->ab)") < setup.index("kzalloc"),
            "unknown HW1 firmware must retain CPU fallback")
    profile = function(core, "ath12k_core_pre_init")
    require('"qcom,rx-skb-slab"' in profile and '"qcom,ath12k-low-memory"' in profile and
            profile.index('"qcom,rx-skb-slab"') < profile.index('"qcom,ath12k-low-memory"') and
            "ab->low_memory_rx = true;" in profile,
            "independent slab selection must preserve old low-memory semantics")
    bind = function(ds, "ath12k_ds_bind_vif")
    require("nf_register_net_hook" in bind and "if (ret)\n\t\treturn ret;" in bind,
            "control-port ingress hook registration/error propagation missing")
    control = function(ds, "ath12k_ds_control_rx")
    require(control.index("cfg80211_rx_control_port") < control.index("NF_STOLEN") and
            "READ_ONCE(peer->authorized)" in control and "ether_addr_equal(eth->h_dest, dev->dev_addr)" in control,
            "control-port hook must preserve ownership and authorization")
    cleanup = function(ds, "ath12k_wifi7_ds_cleanup")
    require(cleanup.index("ath12k_ds_control_unregister") < cleanup.index("dev_put"),
            "control hook must be removed before releasing interface references")
    update = function(ds, "ath12k_ds_peer_update")
    require("ATH12K_DS_FALLBACK_4ADDR" in update and "ath12k_ds_peer_cpu(ds, peer)" in update and
            "peer_fault:" in update and "ath12k_ds_fault(ds)" in update,
            "four-address/hook failures must fall back through fault cleanup")
    halt = function((ath / "wifi7/hal.c").read_text(), "ath12k_wifi7_hal_ds_set_indices")
    require("read_poll_timeout" in halt and "1000, 100000, false" in halt and
            halt.count("ath12k_hif_write32(ab, control_reg, control);") == 2,
            "nonzero ring restoration needs bounded HALT and original control restoration")
    start = function(ds, "ath12k_ds_start_indices")
    require(start.count("if (ret)\n\t\treturn ret;") == 2 and
            start.index("ds->hw1_reo = reo;") > start.rindex("return ret;"),
            "both ring restoration failures must propagate before success state")
    pci = (ath / "pci.c").read_text()
    down = function(pci, "ath12k_pci_power_down")
    require("if (!test_bit(ATH12K_MHI_INIT, &ab_pci->mhi_state))" in down and
            "if (!test_bit(ATH12K_PCI_FLAG_INIT_DONE" not in down,
            "partially started MHI must be powered down")
    remove = function(pci, "ath12k_pci_remove")
    require(remove.index("&ab->qmi.event_work") < remove.index("&ab->restart_work"),
            "QMI event work must drain before restart work")
    allocation = function(core, "ath12k_core_alloc")
    require("ath12k_qmi_init_work(ab);" in allocation and
            "INIT_WORK(&ab->qmi.event_work" not in function(qmi, "ath12k_qmi_init_service"),
            "QMI work must be initialized once before a successful early probe")
    destroy = function(core, "ath12k_core_hw_group_destroy")
    lifecycle = "&ath12k_hw_group_mutex"
    for name in ("ath12k_core_hw_group_assign", "ath12k_core_hw_group_unassign",
                 "ath12k_core_hw_group_free", "ath12k_core_hw_group_destroy"):
        require(f"lockdep_assert_held({lifecycle});" in function(core, name),
                f"group lifetime helper lacks its common lock contract: {name}")
    for name in ("ath12k_core_init", "ath12k_core_deinit", "ath12k_core_hw_group_cleanup"):
        body = function(core, name)
        require(f"mutex_lock({lifecycle});" in body and f"mutex_unlock({lifecycle});" in body,
                f"group lifecycle entry lacks serialization: {name}")
    creation = function(core, "ath12k_core_init")
    require(creation.index("ATH12K_GROUP_FLAG_UNREGISTER") < creation.index("ath12k_core_hw_group_create(ag)"),
            "creation must reject a group already exiting")
    stop = destroy.index("ath12k_qmi_stop_service(ab);")
    release = destroy.index("ath12k_core_soc_destroy(ab);")
    require(destroy.index("mutex_unlock(&ag->mutex);") < stop <
            destroy.rindex("mutex_lock(&ag->mutex);") < release,
            "group teardown must stop workers outside ag lock before locked release")
    deinit = function(qmi, "ath12k_qmi_deinit_service")
    require("lockdep_assert_held(&ab->ag->mutex);" in deinit and
            "cancel_work_sync" not in deinit and "qmi_handle_release" not in deinit,
            "QMI memory release must be locked without waiting for workers")
    return {"wireless": "backports-7.2", "hw1_mlo": False, "dynamic_afc_limit": 8192,
            "hw1_ds_abi": "0x10098b57/WBE.1.0-02903", "rx_allocator": "slab; original resource profile"}


def verify_kernel_config(config: Path) -> dict:
    text = config.read_text()
    for symbol in ("NETFILTER", "NETFILTER_INGRESS", "NET_INGRESS"):
        require(re.search(rf"^CONFIG_{symbol}=y$", text, re.M), f"final kernel lacks CONFIG_{symbol}=y")
    require(re.search(r"^CONFIG_NF_FLOW_TABLE=[ym]$", text, re.M), "final kernel lacks flowtable dependency")
    return {"control_port_ingress_config": "verified in final kernel .config"}


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
    if (args.kernel / ".config").exists():
        result.update(verify_kernel_config(args.kernel / ".config"))
    else:
        result["control_port_ingress_config"] = "not-built; source-only validation"
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
