"""Compile the prepared QMI memory code with observable DMA/DT fault injection.

The allocator and kernel APIs are stubs; the ownership, request parsing and
cleanup functions under test are extracted verbatim from the prepared driver.
This is host validation, not a kernel build or a hardware lifetime test.
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


@unittest.skipUnless(WIRELESS and shutil.which("cc"), "set RC01_WIRELESS_SOURCE to a prepared tree; host cc required")
class FirmwareMemoryOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="rc01-dma-memory-")
        cls.addClassCleanup(cls.temporary.cleanup)
        work = Path(cls.temporary.name)
        driver = Path(WIRELESS) / "drivers/net/wireless/ath/ath12k"
        qmi_h, core_h = (driver / "qmi.h").read_text(), (driver / "core.h").read_text()
        declarations = []
        for source, kind, name in ((qmi_h, "struct", "target_mem_chunk"),
                                   (qmi_h, "struct", "ath12k_qmi_fixed_mem"),
                                   (core_h, "struct", "ath12k_mlo_memory"),
                                   (qmi_h, "enum", "ath12k_qmi_target_mem")):
            declarations.append(re.search(rf"{kind} {name} \{{.*?^\}};", source, re.S | re.M)[0])
        # Copy the used QMI members as well: omitting the borrower field in
        # qmi.h must fail this probe, rather than being hidden by a test stub.
        members = []
        for name in ("target_mem", "fixed_mem", "mem_seg_count", "target_mem_delayed", "mlo_mem_ref"):
            members.append(re.search(rf"^\t(?:struct \w+|u32|bool) {name}(?:\[[^\]]+\])?;", qmi_h, re.M)[0])
        declarations.append("struct ath12k_qmi {\n" + "\n".join(members) + "\n};")
        # Keep resource bounds in step with the real prepared driver.
        for macro in ("ATH12K_QMI_WLANFW_MAX_NUM_MEM_SEG_V01", "AFC_SLOT_SIZE", "AFC_MAX_SLOT", "AFC_MEM_SIZE"):
            declarations.insert(0, re.search(rf"^#define {macro}\s+[^\n]+", qmi_h, re.M)[0])
        (work / "prepared-types.h").write_text("\n".join(declarations) + "\n")
        qmi_c = (driver / "qmi.c").read_text()
        start = qmi_c.index("void ath12k_qmi_reset_mlo_mem(")
        end = qmi_c.index("/* clang stack usage explodes", start)
        chunk_bound = re.search(r"^#define ATH12K_QMI_MAX_CHUNK_SIZE\s+[^\n]+", qmi_c, re.M)[0]
        (work / "prepared-functions.h").write_text(chunk_bound + "\n" + qmi_c[start:end])
        cls.binary = work / "memory-test"
        result = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Werror", "-I", str(work),
                                 str(HERE / "dma_memory_harness.c"), "-o", str(cls.binary)],
                                text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_coherent_reuse_type_resize_tail_unknown_and_double_free(self):
        self.scenario("coherent")

    def test_allocation_failure_and_small_chunk_retry_leave_no_owners(self):
        self.scenario("allocation-failure")

    def test_fixed_dynamic_transitions_and_mapping_failure(self):
        self.scenario("fixed")

    def test_named_region_errors_and_host_ddr_bounds(self):
        self.scenario("dt-errors")

    def test_pre_start_partner_failure_preserves_the_shared_pool(self):
        self.scenario("pre-start-partner")

    def test_shared_pool_boundaries_resize_and_last_borrower_release(self):
        self.scenario("shared-pool")

    def test_reserved_mlo_pool_is_shared_without_dma_free(self):
        self.scenario("fixed-mlo")

    def test_mlo_total_overflow_and_afc_bounds(self):
        self.scenario("request-bounds")
