"""Run the production ucode collector against sysfs/proc fixtures, without a router."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "package/emortal/rc01-platform/files/platform.uc"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")


@unittest.skipUnless(UCODE, "ucode is required for the real status collector")
class Status(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-status-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {
            "router-tool": {"main": {"auto_network": "0"}},
            "nikki": {"config": {"enabled": "0"}},
            "daed": {"config": {"enabled": "0"}},
            "firewall": {"defaults": {".type": "defaults", "flow_offloading": "0", "flow_offloading_hw": "0"}},
            "wireless": {"radio0": {".type": "wifi-device", ".name": "radio0", "disabled": "1"}},
        }
        self.write("tmp/sysinfo/board_name", "xiaomi,be10000")
        self.write("proc/sys/kernel/osrelease", "6.18.55")
        for idx, name, size in [(12, "0:APPSBLENV", 524288), (41, "rootfs", 125829120)]:
            for field, value in {"name": name, "size": size, "erasesize": 131072,
                                 "writesize": 2048, "bad_blocks": 0, "ecc_failures": 0}.items():
                self.write(f"sys/class/mtd/mtd{idx}/{field}", value)
        self.write("etc/fw_env.config", "/dev/mtd12 0x0 0x10000 0x20000 2 # verified map")
        self.write("sys/class/ubi/ubi0/mtd_num", 41)
        for idx, name in enumerate(["kernel", "rootfs", "rootfs_data"]):
            self.write(f"sys/class/ubi/ubi0_{idx}/name", name)
        self.write("sys/class/thermal/thermal_zone7/type", "nss-top-thermal")
        self.write("sys/class/thermal/thermal_zone7/temp", 45000)
        for field, value in {"name": "emc2305", "pwm1": 77, "fan1_input": 960, "fan1_fault": 0}.items():
            self.write("sys/class/hwmon/hwmon8/" + field, value)
        self.fan_device = self.root / "sys/devices/i2c-4/4-002f"
        self.fan_device.mkdir(parents=True)
        (self.root / "sys/class/hwmon/hwmon8/device").symlink_to(self.fan_device)
        for port in ["lan1", "lan2", "lan3", "lan4", "sfp", "wan"]:
            self.write(f"sys/class/net/{port}/carrier", 0)
            self.write(f"sys/class/net/{port}/speed", -1)
            self.write(f"sys/class/net/{port}/operstate", "down")
        self.write("sys/module/qcom_ppe/refcnt", 1)
        self.write("sys/kernel/debug/ppe/active_flows", "0 0 0")
        self.write("usr/libexec/mihomo", "test executable placeholder")

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + "\n")
        if name in ("usr/libexec/mihomo", "usr/bin/daed"):
            path.chmod(0o755)

    def run_status(self, target="hardware"):
        source = f'import {{ collect, assess }} from {json.dumps(str(MODULE))};\n'
        source += "let values = " + json.dumps(self.config) + ";\n"
        source += '''let uci = {
          get: (c, s, k) => values[c]?.[s]?.[k],
          foreach: (c, t, fn) => { for (let s, v in values[c] || {}) if (v['.type'] == t) fn(v); }
        };\n'''
        source += f"let state = collect({json.dumps(str(self.root))}, uci);\n"
        source += f"print(sprintf('%J', {{ state, result: assess(state, {json.dumps(target)}) }}));\n"
        result = subprocess.run([UCODE, "-"], input=source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def levels(self, result):
        return {entry["code"]: entry["level"] for entry in result["result"]["checks"]}

    def test_healthy_renumbered_device_and_disconnected_ports_pass(self):
        result = self.run_status()
        self.assertTrue(result["result"]["passed"])
        self.assertEqual(result["state"]["fans"][0]["hwmon"], "hwmon8")
        self.assertEqual(result["state"]["storage"]["rootfs"]["device"], "/dev/mtd41")
        self.assertIsNone(result["state"]["ports"][0]["speed_mbps"])
        self.assertFalse(result["state"]["storage"]["environment_crc_checked"])

    def test_wrong_environment_length_fails(self):
        self.write("etc/fw_env.config", "/dev/mtd12 0 0x40000 0x20000 2")
        self.assertEqual(self.levels(self.run_status())["environment-map"], "error")

    def test_read_only_mtd_alias_is_not_a_second_partition(self):
        shutil.copytree(self.root / "sys/class/mtd/mtd41", self.root / "sys/class/mtd/mtd41ro")
        self.assertTrue(self.run_status()["result"]["passed"])

    def test_duplicate_fan_is_not_silently_selected(self):
        target = self.root / "sys/class/hwmon/hwmon9"
        shutil.copytree(self.root / "sys/class/hwmon/hwmon8", target, symlinks=True)
        self.assertEqual(self.levels(self.run_status())["fan"], "error")

    def test_invalid_fault_reading_is_not_healthy(self):
        self.write("sys/class/hwmon/hwmon8/fan1_fault", "invalid")
        self.assertEqual(self.levels(self.run_status())["fan"], "error")

    def test_missing_or_hot_sensor_fails(self):
        self.write("sys/class/thermal/thermal_zone7/temp", "invalid")
        self.assertEqual(self.levels(self.run_status())["temperature"], "error")
        self.write("sys/class/thermal/thermal_zone7/temp", 76000)
        self.assertEqual(self.levels(self.run_status())["temperature"], "error")

    def test_dual_proxy_processes_are_rejected(self):
        self.write("proc/123/comm", "mihomo")
        self.write("proc/124/comm", "daed")
        self.write("proc/123/status", "VmRSS:\t12345 kB")
        result = self.run_status("nikki")
        self.assertEqual(self.levels(result)["proxy-conflict"], "error")
        self.assertEqual(self.levels(result)["other-proxy"], "error")
        self.assertEqual(result["state"]["processes"][0]["rss_kib"], 12345)

    def test_configured_other_proxy_is_rejected_without_a_process(self):
        self.config["nikki"]["config"]["enabled"] = "1"
        self.write("usr/bin/daed", "test executable placeholder")
        self.write("sys/kernel/btf/vmlinux", "test BTF placeholder")
        self.assertEqual(self.levels(self.run_status("daed"))["other-proxy"], "error")

    def test_daed_requires_btf_but_nikki_does_not(self):
        self.write("usr/bin/daed", "test executable placeholder")
        self.assertEqual(self.levels(self.run_status("daed"))["btf"], "error")
        self.assertTrue(self.run_status("nikki")["result"]["passed"])

    def test_missing_ppe_counter_is_unknown_and_not_a_driver_failure(self):
        (self.root / "sys/kernel/debug/ppe/active_flows").unlink()
        result = self.run_status()
        self.assertTrue(result["result"]["passed"])
        self.assertIsNone(result["state"]["ppe"]["active_flows"])

    def test_non_executable_proxy_is_not_reported_installed(self):
        (self.root / "usr/libexec/mihomo").chmod(0o644)
        self.assertEqual(self.levels(self.run_status("nikki"))["proxy-installed"], "error")

    def test_unreadable_offload_settings_are_not_reported_disabled(self):
        self.config["firewall"] = {}
        result = self.run_status("nikki")
        self.assertIsNone(result["state"]["proxy"]["offload"]["hardware"])
        self.assertEqual(self.levels(result)["offload-baseline"], "warn")

    def test_status_does_not_export_secrets_or_modify_the_fixture(self):
        secret = "NOT-FOR-STATUS-OUTPUT-SECRET"
        for path in ["etc/nikki/profile.yaml", "etc/daed/wing.db", "dev/mtd12", "proc/123/cmdline"]:
            self.write(path, secret)
        self.config["nikki"]["config"]["subscription"] = secret
        self.write("proc/123/comm", "mihomo")
        def fingerprint():
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.root.rglob("*") if p.is_file()}
        before = fingerprint()
        self.assertNotIn(secret, json.dumps(self.run_status("nikki")))
        self.assertEqual(before, fingerprint())


if __name__ == "__main__":
    unittest.main()
