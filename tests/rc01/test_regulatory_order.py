"""Exercise actual ath12k requests and cfg80211 enqueue code before deferred publication.

No RF or regulatory enforcement is emulated. The harness controls the worker
boundary and runs the actual publication prefix; channel updates after that
prefix are outside this request-order test.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_group_teardown import function

ROOT = Path(__file__).resolve().parents[2]
WIRELESS = os.environ.get("RC01_WIRELESS_SOURCE")


@unittest.skipUnless(WIRELESS and shutil.which("cc"), "prepared wireless source and host cc required")
class RegulatoryRequestOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="rc01-reg-order-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        source = Path(WIRELESS)
        reg = source / "drivers/net/wireless/ath/ath12k/reg.c"
        cfg = (source / "net/wireless/reg.c").read_text()
        prefix = function(cfg, "reg_process_self_managed_hint")
        prefix = prefix[:prefix.index("\n\tfor (band =")] + "\n}\n"
        common = (function(cfg, "__regulatory_set_wiphy_regd") +
                  function(cfg, "regulatory_set_wiphy_regd") + prefix)
        cls.current = cls.build(reg.read_text(), common, "current")
        # Reverse the carried patch to recover the exact rejected upstream
        # current-only guard, rather than inventing a simplified negative.
        previous = cls.work / "old/drivers/net/wireless/ath/ath12k/reg.c"
        previous.parent.mkdir(parents=True)
        previous.write_bytes(reg.read_bytes())
        patch = ROOT / "package/kernel/mac80211/patches/ath12k/922-rc01-preserve-regulatory-request-order.patch"
        subprocess.run(["patch", "--batch", "--fuzz=0", "-R", "-p1", "-i", str(patch)],
                       cwd=cls.work / "old", capture_output=True, text=True, check=True)
        cls.old = cls.build(previous.read_text(), common, "old")

    @classmethod
    def build(cls, reg, common, name):
        code = common + function(reg, "ath12k_copy_regd")
        if "static bool ath12k_regd_is_current(" in reg:
            code += function(reg, "ath12k_regd_is_current")
        code += function(reg, "ath12k_regd_update")
        directory = cls.work / name
        directory.mkdir(exist_ok=True)
        (directory / "prepared-regulatory.h").write_text(code)
        binary = directory / "reg-order"
        subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-unused-variable", "-I", str(directory),
                        str(ROOT / "tests/rc01/regulatory_order_harness.c"), "-o", str(binary)],
                       capture_output=True, text=True, check=True)
        return binary

    def test_a_b_a_before_worker_publishes_final_a(self):
        subprocess.run([str(self.current), "aba"], check=True, capture_output=True)

    def test_original_current_only_guard_loses_final_a(self):
        result = subprocess.run([str(self.old), "aba"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"published", result.stderr)

    def test_latest_request_wins_and_invalid_request_does_not_replace_it(self):
        for scenario in ("latest", "invalid"):
            with self.subTest(scenario=scenario):
                subprocess.run([str(self.current), scenario], check=True, capture_output=True)
