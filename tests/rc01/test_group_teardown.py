"""Probe actual ath12k core/QMI creation and teardown entrypoints on the host.

Kernel I/O is fault-injected; worker cancellation rejects uninitialized work or
held group/core locks. Shared allocations use the prepared 914 implementation.
This exercises the real outer deinit/create rollback and alloc/find/assign/free
functions. Pthread barriers reproduce probe/remove lookup and publication
interleavings; no test wrapper takes lifecycle locks for these entrypoints.
The host scheduler and I/O stubs do not replace a kernel lockdep/runtime test.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

WIRELESS = os.environ.get("RC01_WIRELESS_SOURCE")
HERE = Path(__file__).resolve().parent


def function(source, name):
    match = re.search(r"^[^\n{};]*\b" + name + r"\([^;{}]*\)\n\{", source, re.M)
    if match is None:
        raise AssertionError(f"missing prepared function {name}")
    start = match.start()
    previous = source[:start].rsplit("\n", 2)
    if len(previous) >= 2 and previous[-2].strip() in ("static inline", "static int", "static void"):
        start -= len(previous[-2]) + 1
    end = source.index("\n}", match.end()) + 2
    return source[start:end] + "\n"


def structure(source, name):
    return re.search(rf"struct {name} \{{.*?^\}};", source, re.S | re.M)[0]


@unittest.skipUnless(WIRELESS and shutil.which("cc"), "set RC01_WIRELESS_SOURCE to prepared tree; host cc required")
class GroupTeardown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="rc01-group-teardown-")
        cls.addClassCleanup(cls.temporary.cleanup)
        work = Path(cls.temporary.name)
        driver = Path(WIRELESS) / "drivers/net/wireless/ath/ath12k"
        core, qmi = (driver / "core.c").read_text(), (driver / "qmi.c").read_text()
        core_h, qmi_h = (driver / "core.h").read_text(), (driver / "qmi.h").read_text()
        core_names = ("ath12k_core_soc_create", "ath12k_core_soc_destroy",
                      "ath12k_core_hw_group_destroy", "ath12k_core_hw_group_create",
                      "ath12k_core_hw_group_cleanup", "ath12k_core_hw_group_unassign",
                      "ath12k_core_hw_group_alloc", "ath12k_core_hw_group_find_by_dt",
                      "ath12k_core_hw_group_assign", "ath12k_core_hw_group_free",
                      "ath12k_core_free_wsi_info", "ath12k_core_get_wsi_index",
                      "ath12k_core_hw_group_create_ready", "ath12k_core_init",
                      "ath12k_core_deinit", "ath12k_core_alloc", "ath12k_core_free")
        qmi_names = ("ath12k_qmi_init_work", "ath12k_qmi_init_service",
                     "ath12k_qmi_stop_service", "ath12k_qmi_deinit_service", "ath12k_qmi_free_resource",
                     "ath12k_qmi_driver_event_post")
        core_code = "\n".join(function(core, name) for name in core_names)
        qmi_code = "\n".join(function(qmi, name) for name in qmi_names)
        declarations = [structure(qmi_h, "target_mem_chunk"), structure(qmi_h, "ath12k_qmi_fixed_mem"),
                        structure(core_h, "ath12k_mlo_memory"),
                        re.search(r"enum ath12k_qmi_target_mem \{.*?^\};", qmi_h, re.S | re.M)[0]]
        for macro in ("ATH12K_QMI_WLANFW_MAX_NUM_MEM_SEG_V01", "AFC_SLOT_SIZE", "AFC_MAX_SLOT", "AFC_MEM_SIZE"):
            declarations.insert(0, re.search(rf"^#define {macro}\s+[^\n]+", qmi_h, re.M)[0])
        # Use the actual members referenced by the selected functions. Keeping
        # only their declarations avoids stubbing unrelated QMI protocol types.
        qmi_struct = structure(qmi_h, "ath12k_qmi")
        memory_start = qmi.index("void ath12k_qmi_reset_mlo_mem(")
        memory_end = qmi.index("/* clang stack usage explodes", memory_start)
        memory = qmi[memory_start:memory_end]
        members = []
        referenced = set(re.findall(r"ab->qmi\.(\w+)", memory + core_code + qmi_code))
        for name in sorted(referenced):
            line = re.search(rf"^\t(?:struct \w+|spinlock_t|bool|u32|u8|unsigned int)\s+\*?{name}(?:\[[^\]]+\])?;", qmi_struct, re.M)
            if line is None:
                raise AssertionError(f"missing prepared QMI member {name}")
            members.append(line[0])
        declarations.append("struct ath12k_qmi {\n" + "\n".join(members) + "\n};")
        (work / "prepared-memory-types.h").write_text("\n".join(declarations) + "\n")
        # New lifecycle state declarations must be taken from the real headers.
        for name, header, output, skip in (
            ("ath12k_hw_group", core_h, "prepared-group-extra.h", {"mutex", "flags", "id", "ab", "list", "wsi_node", "mlo_capable", "mlo_mem", "num_devices", "num_probed", "num_started"}),
            ("ath12k_base", core_h, "prepared-base-extra.h", {"qmi", "dev", "ag", "core_lock", "dev_flags", "hif", "fw_mode", "target_mem_mode", "device_id", "workqueue", "workqueue_aux", "driver_recovery", "base_lock", "reset_complete", "peer_mapping_wq", "wmi_ab", "restart_work", "reset_work", "rfkill_work", "dump_work", "update_11d_work", "rx_replenish_retry", "htc_suspend", "restart_completed", "wow", "single_chip_mlo_support", "hw_group_ref", "hw_rev", "wsi_info"})):
            variable = "ag" if name == "ath12k_hw_group" else "ab"
            text = structure(header, name)
            extra = []
            for field in sorted(set(re.findall(rf"{variable}->(\w+)", core_code)) - skip):
                line = re.search(rf"^\t(?:bool|unsigned int|u32|struct mutex)\s+{field};", text, re.M)
                if line is not None:
                    extra.append(line[0])
            (work / output).write_text("\n".join(extra) + "\n")
        chunk_bound = re.search(r"^#define ATH12K_QMI_MAX_CHUNK_SIZE\s+[^\n]+", qmi, re.M)[0]
        (work / "prepared-memory-functions.h").write_text(chunk_bound + "\n" + memory)
        for code, output in ((qmi_code, "prepared-qmi-functions.h"), (core_code, "prepared-core-functions.h")):
            prototypes = []
            for name in qmi_names if output.startswith("prepared-qmi") else core_names:
                original = function(qmi if output.startswith("prepared-qmi") else core, name)
                prototypes.append(original[:original.index("\n{")] + ";\n")
            (work / output).write_text("\n".join(prototypes) + "\n" + code)
        cls.binary = work / "group-teardown-test"
        flags = ["-fsanitize=address", "-fno-omit-frame-pointer", "-g", "-O1", "-no-pie"] if os.environ.get("RC01_TEARDOWN_ASAN") == "1" else []
        result = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Werror", "-pthread", *flags, "-I", str(work),
                                 str(HERE / "group_teardown_harness.c"), "-o", str(cls.binary)],
                                text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_normal_outer_deinit_serializes_and_releases_last_prestart_borrower(self):
        self.scenario("normal")

    def test_early_remove_before_group_create_ready_only_cancels_initialized_work(self):
        self.scenario("early-remove")

    def test_partial_group_create_failures_use_unlocked_stop_then_locked_free(self):
        for stage in ("handle", "workqueue", "lookup", "power-up"):
            with self.subTest(stage=stage):
                self.scenario("fail-" + stage)

    def test_failed_request_and_explicit_free_resource_keep_the_lock_contract(self):
        self.scenario("free-resource")

    def test_late_partner_event_is_rejected_after_stop_and_destroy(self):
        self.scenario("late-event")

    def test_unassign_validation_paths_release_the_group_lock(self):
        self.scenario("unassign-errors")

    def test_probe_publication_cannot_overlap_remove_and_recreate_stopped_members(self):
        self.scenario("probe-published-remove")

    def test_find_exit_check_and_member_publication_pin_the_group_until_create(self):
        self.scenario("probe-found-remove")

    def test_multiple_remove_waiters_free_the_group_once_after_the_last_member(self):
        self.scenario("remove-waiters")

    def test_probe_after_group_exit_builds_a_new_independent_group(self):
        self.scenario("probe-after-exit")
