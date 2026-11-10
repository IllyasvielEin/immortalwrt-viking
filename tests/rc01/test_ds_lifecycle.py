"""Host fault injection into actual prepared DS/HAL functions; no hardware I/O."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WIRELESS = os.environ.get("RC01_WIRELESS_SOURCE")


def declaration(source, name, record=False):
    pattern = (r"(?m)^(?:struct|enum) " + re.escape(name) + r"\s*\{.*?^\};"
               if record else
               r"(?m)^(?:static )?(?:struct \w+ \*|unsigned int|int|void|bool|u16)\s*"
               + re.escape(name) + r"\s*\([^;]*?\)\s*\{")
    match = re.search(pattern, source, re.S)
    if match is None:
        raise AssertionError(f"missing prepared definition {name}")
    if record:
        return match[0] + "\n"
    depth = 1
    for end in range(match.end(), len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[match.start():end + 1] + "\n"
    raise AssertionError(f"unterminated prepared definition {name}")


def macro(source, name):
    match = re.search(r"(?m)^#define " + re.escape(name)
                      + r"(?=[\s(])(?:[^\n]*\\\n)*[^\n]*", source)
    if not match:
        raise AssertionError(f"missing prepared macro {name}")
    return match[0] + "\n"


@unittest.skipUnless(WIRELESS, "set RC01_WIRELESS_SOURCE to the prepared backports tree")
class DirectSwitchLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="rc01-ds-lifecycle-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.path = Path(cls.temp.name)
        ath = Path(WIRELESS) / "drivers/net/wireless/ath/ath12k"
        ds = (ath / "wifi7/ppe_ds.c").read_text()
        hal = (ath / "wifi7/hal.c").read_text()
        header = (ath / "wifi7/hal.h").read_text()
        tx = (ath / "wifi7/hal_tx.h").read_text()
        mhi = (ath / "mhi.c").read_text()
        pci = (ath / "pci.c").read_text()
        qmi = (ath / "qmi.c").read_text()
        enums = declaration((ath / "mhi.h").read_text(), "ath12k_mhi_state", True)
        enums += declaration((ath / "qmi.h").read_text(), "ath12k_qmi_event_type", True)
        types = "".join(declaration(ds, name, True) for name in
                        ("ath12k_ds_fallback", "ath12k_ds_vp", "ath12k_ds_peer", "ath12k_ds"))
        definitions = "".join(macro(ds, name) for name in ("ATH12K_DS_VP_COUNT", "ATH12K_DS_VP_TABLE"))
        for name in ("HAL_SEQ_WCSS_UMAC_TCL_REG", "HAL_SEQ_WCSS_UMAC_REO_REG",
                     "HAL_TCL1_RING_CMN_CTRL_REG", "HAL_TCL_PPE2TCL_RING_HALT",
                     "HAL_TCL_PPE2TCL_RING_HALT_STATUS", "HAL_TCL1_RING_MISC",
                     "HAL_TCL1_RING_BASE_LSB", "HAL_TCL1_RING_MISC_OFFSET",
                     "HAL_REO1_RING_MISC", "HAL_REO1_RING_BASE_LSB",
                     "HAL_REO1_RING_HP", "HAL_REO1_RING_TP", "HAL_REO1_RING_TP_OFFSET",
                     "HAL_REO1_DEST_RING_CTRL_IX_0", "HAL_TCL_DS_RBM_MAPPING_OFFSET",
                     "HAL_TCL_DS_RBM_MAPPING_SHIFT", "HAL_WBM2SW_DS_RBM_ID",
                     "HAL_REO2PPE_DEST_INFO", "HAL_REO2PPE_DEST_INFO_VALUE"):
            definitions += macro(header, name)
        for name in ("HAL_TX_BANK_CONFIG_ENCAP_TYPE", "HAL_TX_BANK_CONFIG_VDEV_ID_CHECK_EN",
                     "HAL_TX_BANK_CONFIG_PMAC_ID"):
            definitions += macro(tx, name)
        definitions += macro(mhi, "MHI_TIMEOUT_DEFAULT_MS")
        functions = "".join(declaration(hal, name) for name in
                            ("ath12k_wifi7_hal_reo1_ring_misc_offset", "ath12k_wifi7_hal_ds_disable",
                             "ath12k_wifi7_hal_ds_set_indices"))
        for name in ("ath12k_ds_srng", "ath12k_ds_start_indices", "ath12k_ds_read_index",
                     "ath12k_ds_tcl_cons", "ath12k_ds_fault", "ath12k_ds_fault_work",
                     "ath12k_ds_start", "ath12k_ds_control_rx", "ath12k_ds_control_unregister",
                     "ath12k_ds_bind_vif", "ath12k_ds_route_vif", "ath12k_ds_vif_can_direct",
                     "ath12k_ds_peer_disable", "ath12k_ds_peer_cpu", "ath12k_ds_peer_update",
                     "ath12k_wifi7_ds_vif_remove", "ath12k_wifi7_ds_stop",
                     "ath12k_wifi7_ds_quarantine", "ath12k_wifi7_ds_cleanup"):
            functions += declaration(ds, name)
        for name in ("ath12k_mhi_set_state_bit", "ath12k_mhi_check_state_bit",
                     "ath12k_mhi_set_state", "ath12k_mhi_start", "ath12k_mhi_stop",
                     "ath12k_mhi_unregister"):
            functions += declaration(mhi, name)
        functions += declaration(pci, "ath12k_pci_power_down")
        functions += declaration(pci, "ath12k_pci_remove")
        functions += declaration(qmi, "ath12k_qmi_driver_event_work")
        functions += declaration(qmi, "ath12k_qmi_init_work")
        (cls.path / "prepared_enums.inc").write_text(enums)
        (cls.path / "prepared_types.inc").write_text(definitions + types)
        (cls.path / "prepared_functions.inc").write_text(functions)
        cls.executable = cls.path / "ds-lifecycle"
        compiler = shutil.which("cc")
        if not compiler:
            raise AssertionError("DS failure regression requires host cc")
        result = subprocess.run(
            [compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
             "-Wno-unused-parameter", "-I", str(cls.path),
             str(ROOT / "tests/rc01/ds_lifecycle_harness.c"), "-o", str(cls.executable)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.executable), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "host-ds-lifecycle:ok")

    def test_new_peer_hook_failure_stop_cleanup_and_rebuild(self):
        self.case("new-hook-failure")

    def test_existing_peer_hook_failure_has_same_fault_cleanup(self):
        self.case("existing-hook-failure")

    def test_quarantine_releases_software_refs_and_retains_hardware_leases(self):
        self.case("quarantine")

    def test_hook_failure_then_stop_failure_keeps_its_unregistered_port_reserved(self):
        self.case("hook-failure-quarantine")

    def test_unregister_failure_quarantines_without_releasing_hardware_leases(self):
        self.case("unregister-failure")

    def test_vif_removal_unregisters_and_synchronizes_before_final_put(self):
        self.case("vif-remove")

    def test_control_port_consumes_only_delivered_authorized_matching_peer(self):
        self.case("control-rx")

    def test_nonzero_indices_halt_both_rings_then_restore_control(self):
        self.case("indices-success")

    def test_zero_indices_skip_halt_and_reset_pointers(self):
        self.case("indices-zero")

    def test_zero_tcl_with_nonzero_reo_halts_only_for_the_nonzero_ring(self):
        self.case("indices-mixed")

    def test_first_halt_timeout_preserves_indices_and_published_reo(self):
        self.case("indices-first-timeout")

    def test_second_halt_timeout_does_not_publish_unconfirmed_reo(self):
        self.case("indices-second-timeout")

    def test_mhi_never_started_partial_failed_and_repeated_stop(self):
        self.case("mhi-stop")

    def test_mhi_suspend_keeps_channel_device_until_final_unregister(self):
        self.case("mhi-suspend")

    def test_pci_power_down_uses_mhi_init_before_datapath_init_done(self):
        self.case("pci-power-down")

    def test_normal_pci_remove_quiesces_events_before_restart_and_teardown(self):
        self.case("pci-remove")

    def test_qmi_failed_pci_remove_also_quiesces_before_power_down(self):
        self.case("pci-qmi-fail-remove")

    def test_fw_ready_refuses_unregistering_between_outer_and_inner_checks(self):
        self.case("qmi-fw-ready")


if __name__ == "__main__":
    unittest.main()
