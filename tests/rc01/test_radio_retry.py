"""Execute the production retry ucode with bounded bus/PHY fixtures."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "package/network/config/wifi-scripts/files/etc/hotplug.d/ieee80211/11-ath12k-trigger"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")


class DriverGate(unittest.TestCase):
    def test_actual_driver_expression_accepts_wifi7_but_not_other_drivers(self):
        expression = re.search(r"grep -qsE '([^']+)'", SCRIPT.read_text())[1]
        for driver, expected in (("ath12k_pci", True), ("ath12k_ahb", True),
                                 ("ath12k_wifi7_pci", True), ("ath12k_wifi7_ahb", True),
                                 ("ath11k_pci", False), ("ath12k_pci_extra", False)):
            with self.subTest(driver=driver):
                result = subprocess.run(["grep", "-qE", expression],
                                        input=f"DRIVER={driver}\n", text=True)
                self.assertEqual(result.returncode == 0, expected)


@unittest.skipUnless(UCODE, "real ucode required for radio retry regression")
class RadioRetry(unittest.TestCase):
    def run_retry(self, status, devpath="/devices/pci/a/ieee80211/old-phy", paths=None):
        # Only replace imports; run the unchanged production control flow.
        body = SCRIPT.read_text().split("ucode - <<'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0]
        body = re.sub(r"^import .*?;\n", "", body, flags=re.M)
        paths = paths or {"/sys/devices/pci/a": "/stable/a",
                          "/sys/class/ieee80211/renamed-phy/device": "/stable/a",
                          "/sys/class/ieee80211/other-phy/device": "/stable/b"}
        prelude = "let status_data = " + json.dumps(status) + ";\n"
        prelude += "let paths = " + json.dumps(paths) + ";\n"
        prelude += r'''
let calls = [];
function realpath(path) { return paths[path]; }
function find_phy(config, retry) {
    if (retry !== true) die('retry must allow a renamed PHY');
    return config?.phy;
}
function connect() {
    return { call: function(object, method, args) {
        if (object != 'network.wireless') die('unexpected bus object');
        if (method == 'status') return status_data;
        if (method != 'retry') die('retry must not reload or stop the network');
        push(calls, args);
    }};
}
'''
        result = subprocess.run([UCODE, "-"], input=prelude + body + "\nprint(sprintf('%J', calls));\n",
                                env={**os.environ, "DEVPATH": devpath}, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout or "[]")

    def failed(self, **changes):
        return {"autostart": True, "retry_setup_failed": True,
                "config": {"phy": "renamed-phy"}, **changes}

    def test_renamed_phy_retries_only_the_matching_failed_radio(self):
        self.assertEqual(self.run_retry({"radio0": self.failed(),
                                        "radio1": self.failed(config={"phy": "other-phy"}),
                                        "radio2": self.failed(config={})}),
                         [{"device": "radio0"}])

    def test_running_pending_disabled_manual_and_successful_radios_are_untouched(self):
        for change in ({"up": True}, {"pending": True}, {"disabled": True},
                       {"autostart": False}, {"retry_setup_failed": False}):
            with self.subTest(change=change):
                self.assertEqual(self.run_retry({"radio0": self.failed(**change)}), [])

    def test_unrelated_or_unresolvable_event_does_not_retry(self):
        self.assertEqual(self.run_retry({"radio0": self.failed()}, devpath="/devices/no-phy"), [])
        self.assertEqual(self.run_retry({"radio0": self.failed()}, paths={"unrelated": "/stable/b"}), [])
        self.assertEqual(self.run_retry({}), [])
