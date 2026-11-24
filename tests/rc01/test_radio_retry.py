"""Execute the production PHY hotplug retry in mac80211.uc with bounded bus/PHY fixtures."""
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_router import ROOT, UCODE, nl_band, run_wifi_config  # noqa: E402

WIFI = ROOT / "package/network/config/wifi-scripts/files"
PATHS = {"/sys/devices/pci/a": "/stable/a",
         "/sys/class/ieee80211/renamed-phy/device": "/stable/a",
         "/sys/class/ieee80211/other-phy/device": "/stable/b"}
# A configured PHY, so the retry follows a generator pass that commits nothing.
PHYS = {"phy0": {"path": "pci-a", "bands": {"5G": nl_band("5G")}}}
WIRELESS = {"radio0": {".type": "wifi-device", ".name": "radio0", "path": "pci-a"}}


class HotplugWiring(unittest.TestCase):
    def test_phy_add_runs_wifi_config_hotplug_for_every_driver(self):
        # Upstream replaced the ath12k-only 11-ath12k-trigger with this hook;
        # the retry no longer depends on the PHY's driver.
        hook = (WIFI / "etc/hotplug.d/ieee80211/10-wifi-detect").read_text()
        self.assertIn('[ "${ACTION}" = "add" ]', hook)
        self.assertIn("/sbin/wifi config --hotplug", hook)
        self.assertFalse((WIFI / "etc/hotplug.d/ieee80211/11-ath12k-trigger").exists())
        result = subprocess.run(["sh", "-c", 'sed -n "/^wifi_config()/,/^}/p" "$0"', str(WIFI / "sbin/wifi")],
                                capture_output=True, text=True)
        self.assertIn('ucode /lib/wifi/mac80211.uc "$1"', result.stdout)


@unittest.skipUnless(UCODE, "real ucode required for radio retry regression")
class RadioRetry(unittest.TestCase):
    def run_retry(self, status, devpath="/devices/pci/a/ieee80211/old-phy", paths=None, args=("--hotplug",)):
        result = run_wifi_config(PHYS, wireless=WIRELESS, args=args, devpath=devpath,
                                 status=status, realpaths=PATHS if paths is None else paths)
        self.assertEqual((result["code"], result["commit"]), (0, None), result["stderr"])
        for obj, method, _ in result["calls"]:
            # Only network.wireless status/retry; never reload or stop the network.
            self.assertEqual(obj, "network.wireless")
            self.assertIn(method, ("status", "retry"))
        return [args for _, method, args in result["calls"] if method == "retry"]

    def down(self, **changes):
        return {"autostart": True, "config": {"phy": "renamed-phy"}, **changes}

    def test_renamed_phy_retries_only_radios_on_the_same_device(self):
        self.assertEqual(self.run_retry({"radio0": self.down(), "radio1": self.down(config={"phy": "other-phy"}),
                                         "radio2": self.down(config={}), "radio3": self.down()}),
                         [{"devices": ["radio0", "radio3"]}])

    def test_running_disabled_and_manually_stopped_radios_are_untouched(self):
        # netifd's retry_setup() itself queues a retry for radios in setup or
        # teardown, so the hook no longer filters pending/failed states.
        for change in ({"up": True}, {"disabled": True}, {"autostart": False}):
            with self.subTest(change=change):
                self.assertEqual(self.run_retry({"radio0": self.down(**change)}), [])

    def test_unrelated_device_does_not_retry(self):
        self.assertEqual(self.run_retry({"radio0": self.down(config={"phy": "other-phy"})}), [])
        self.assertEqual(self.run_retry({}), [])

    def test_unresolvable_event_retries_all_radios(self):
        # Upstream retries every radio when the event cannot be matched to a
        # parent device; netifd skips running and stopped radios.
        self.assertEqual(self.run_retry({"radio0": self.down()}, devpath="/devices/no-phy"), [{}])
        self.assertEqual(self.run_retry({"radio0": self.down()}, paths={}), [{}])

    def test_plain_wifi_config_does_not_retry(self):
        self.assertEqual(self.run_retry({"radio0": self.down()}, args=()), [])


if __name__ == "__main__":
    unittest.main()
