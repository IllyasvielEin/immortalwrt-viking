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
            "dae": {"config": {"enabled": "0"}},
            "firewall": {"defaults": {".type": "defaults", "flow_offloading": "0", "flow_offloading_hw": "0"}},
            "wireless": {"radio0": {".type": "wifi-device", ".name": "radio0", "disabled": "1"}},
        }
        self.write("tmp/sysinfo/board_name", "xiaomi,be10000")
        self.write("proc/sys/kernel/osrelease", "6.18.55")
        layout = json.loads((ROOT / 'config/rc01/layout.json').read_text())
        self.write('usr/share/rc01/layout.json', json.dumps(layout))
        for key, part in layout['partitions'].items():
            idx = {'rootfs': 41, 'appsblenv': 12}.get(key, part['index'] + 100)
            for field, value in {"name": part['name'], "offset": part['offset'], "size": part['bytes'], "erasesize": 131072,
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
        self.write("sys/kernel/debug/ppe/active_flows", "0 0 0/4096")
        self.write("usr/libexec/mihomo", "test executable placeholder")
        self.write("proc/uptime", "1000.00 900.00")

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + "\n")
        if name in ("usr/libexec/mihomo", "usr/bin/dae", "usr/bin/daed"):
            path.chmod(0o755)

    def run_script(self, body):
        source = f'import {{ collect, assess }} from {json.dumps(str(MODULE))};\n'
        source += "let values = " + json.dumps(self.config) + ";\n"
        source += '''let uci = {
          get: (c, s, k) => values[c]?.[s]?.[k],
          foreach: (c, t, fn) => { for (let s, v in values[c] || {}) if (v['.type'] == t) fn(v); }
        };\n'''
        source += f"let root = {json.dumps(str(self.root))};\n" + body
        result = subprocess.run([UCODE, "-"], input=source, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def run_status(self, target="hardware"):
        source = f'import {{ collect, assess }} from {json.dumps(str(MODULE))};\n'
        source += "let values = " + json.dumps(self.config) + ";\n"
        source += '''let uci = {
          get: (c, s, k) => values[c]?.[s]?.[k],
          foreach: (c, t, fn) => { for (let s, v in values[c] || {}) if (v['.type'] == t) fn(v); }
        };\n'''
        source += f"let state = collect({json.dumps(str(self.root))}, uci);\n"
        source += f"print(sprintf('%J', {{ state, result: assess(state, {json.dumps(target)}) }}));\n"
        result = subprocess.run([UCODE, "-"], input=source, capture_output=True, text=True, timeout=10)
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

    def test_moved_identity_and_rootfs_offsets_are_required(self):
        for node in ['mtd124', 'mtd41']:
            with self.subTest(node=node):
                path = self.root / f'sys/class/mtd/{node}/offset'
                before = path.read_text()
                self.write(f'sys/class/mtd/{node}/offset', 0)
                self.assertEqual(self.levels(self.run_status())['layout'], 'error')
                path.write_text(before)

    def test_missing_contract_fails_closed(self):
        (self.root / 'usr/share/rc01/layout.json').unlink()
        self.assertEqual(self.levels(self.run_status())['layout'], 'error')

    def test_identity_ecc_is_not_reported_healthy(self):
        self.write('sys/class/mtd/mtd123/ecc_failures', 1)
        self.assertEqual(self.levels(self.run_status())['identity-storage'], 'error')

    def test_missing_rootfs_ecc_cannot_pass(self):
        (self.root / 'sys/class/mtd/mtd41/ecc_failures').unlink()
        result = self.run_status()
        self.assertEqual(self.levels(result)['nand-ecc'], 'error')
        self.assertFalse(result['result']['passed'])

    def test_read_only_mtd_alias_is_not_a_second_partition(self):
        shutil.copytree(self.root / "sys/class/mtd/mtd41", self.root / "sys/class/mtd/mtd41ro")
        self.assertTrue(self.run_status()["result"]["passed"])

    def mtd_copy(self, source, target, name):
        shutil.copytree(self.root / f"sys/class/mtd/{source}", self.root / f"sys/class/mtd/{target}")
        if name is None:
            (self.root / f"sys/class/mtd/{target}/name").unlink()
        else:
            self.write(f"sys/class/mtd/{target}/name", name)

    def test_duplicate_or_case_colliding_names_are_ambiguous_not_overwritten(self):
        cases = [("mtd123", "0:LICENSE", "license", "identity-storage"),
                 ("mtd123", "0:license", "license", "identity-storage"),
                 ("mtd124", "BData", "bdata", "identity-storage"),
                 ("mtd41", "rootfs", "rootfs", "nand-ecc")]
        for source, name, key, check in cases:
            with self.subTest(name=name):
                self.mtd_copy(source, "mtd200", name)
                result = self.run_status()
                self.assertIsNone(result["state"]["storage"][key])
                self.assertEqual(self.levels(result)[check], "error")
                self.assertEqual(self.levels(result)["layout"], "error")
                shutil.rmtree(self.root / "sys/class/mtd/mtd200")

    def test_partition_count_uses_real_nodes(self):
        for name in ["extra", None]:
            with self.subTest(name=name):
                self.mtd_copy("mtd100", "mtd200", name)
                result = self.run_status()
                self.assertEqual(self.levels(result)["layout"], "error")
                self.assertEqual(self.levels(result)["identity-storage"], "ok")
                shutil.rmtree(self.root / "sys/class/mtd/mtd200")
        (self.root / "sys/class/mtd/mtd100/name").unlink()
        self.assertEqual(self.levels(self.run_status())["layout"], "error")

    def test_missing_mtd_fields_stay_unknown(self):
        for node, field, key, attribute, check in [
                ("mtd123", "bad_blocks", "license", "bad_blocks", "identity-storage"),
                ("mtd124", "ecc_failures", "bdata", "uncorrectable_ecc", "identity-storage"),
                ("mtd41", "bad_blocks", "rootfs", "bad_blocks", "nand-reserve"),
                ("mtd41", "offset", "rootfs", "offset", "layout")]:
            for value in [None, "unreadable"]:
                with self.subTest(node=node, field=field, value=value):
                    path = self.root / f"sys/class/mtd/{node}/{field}"
                    before = path.read_text()
                    if value is None:
                        path.unlink()
                    else:
                        path.write_text(value + "\n")
                    result = self.run_status()
                    self.assertIsNone(result["state"]["storage"][key][attribute])
                    self.assertEqual(self.levels(result)[check], "error")
                    path.write_text(before)

    def test_every_layout_geometry_field_is_compared(self):
        for node in ["mtd110", "mtd41"]:
            for field, value in [("size", 131072), ("erasesize", 262144), ("writesize", 4096)]:
                with self.subTest(node=node, field=field):
                    path = self.root / f"sys/class/mtd/{node}/{field}"
                    before = path.read_text()
                    self.write(f"sys/class/mtd/{node}/{field}", value)
                    self.assertEqual(self.levels(self.run_status())["layout"], "error")
                    path.write_text(before)

    def test_environment_map_follows_the_actual_device(self):
        (self.root / "sys/class/mtd/mtd12").rename(self.root / "sys/class/mtd/mtd13")
        result = self.run_status()
        self.assertEqual(result["state"]["storage"]["environment"]["device"], "/dev/mtd13")
        self.assertEqual(self.levels(result)["environment-map"], "error")
        self.assertEqual(self.levels(result)["layout"], "ok")
        self.write("etc/fw_env.config", "/dev/mtd13 0x0 0x10000 0x20000 2")
        self.assertTrue(self.run_status()["result"]["passed"])

    def test_each_collect_rereads_mtd_nodes(self):
        mtd = json.dumps(str(self.root / "sys/class/mtd"))
        state = self.run_script(f'''import {{ writefile, rename }} from 'fs';
let mtd = {mtd};
let first = collect(root, uci).storage;
writefile(mtd + '/mtd123/ecc_failures', '1\\n');
rename(mtd + '/mtd41', mtd + '/mtd42');
writefile(root + '/sys/class/ubi/ubi0/mtd_num', '42\\n');
let second = collect(root, uci).storage;
print(sprintf('%J', [ first, second ]));''')
        first, second = state
        self.assertEqual((first["license"]["uncorrectable_ecc"], first["rootfs"]["device"]), (0, "/dev/mtd41"))
        self.assertEqual((second["license"]["uncorrectable_ecc"], second["rootfs"]["device"]), (1, "/dev/mtd42"))
        self.assertTrue(second["layout_matches"] and second["ubi_root_matches"])

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

    def test_dae_preflight_requires_btf_and_detects_nikki_and_daed(self):
        self.write("usr/bin/dae", "test executable placeholder")
        self.assertEqual(self.levels(self.run_status("dae"))["btf"], "error")
        self.write("sys/kernel/btf/vmlinux", "test BTF placeholder")
        self.assertTrue(self.run_status("dae")["result"]["passed"])
        self.config["nikki"]["config"]["enabled"] = "1"
        self.assertEqual(self.levels(self.run_status("dae"))["other-proxy"], "error")
        self.config["nikki"]["config"]["enabled"] = "0"
        self.write("proc/124/comm", "daed")
        self.assertEqual(self.levels(self.run_status("dae"))["other-proxy"], "error")

    def test_dae_enabled_blocks_nikki_even_before_process_start(self):
        self.config["dae"]["config"]["enabled"] = "1"
        self.assertEqual(self.levels(self.run_status("nikki"))["other-proxy"], "error")

    def test_daed_requires_btf_but_nikki_does_not(self):
        self.write("usr/bin/daed", "test executable placeholder")
        self.assertEqual(self.levels(self.run_status("daed"))["btf"], "error")
        self.assertTrue(self.run_status("nikki")["result"]["passed"])

    def test_missing_ppe_counter_is_unknown_and_not_a_driver_failure(self):
        (self.root / "sys/kernel/debug/ppe/active_flows").unlink()
        result = self.run_status()
        self.assertTrue(result["result"]["passed"])
        self.assertIsNone(result["state"]["ppe"]["active_flows"])

    def test_ppe_kernel_counters_include_total_and_capacity(self):
        result = self.run_status()["state"]["ppe"]["active_flows"]
        self.assertEqual(result, {"ipv4": 0, "ipv6": 0, "total": 0, "capacity": 4096})
        self.write("sys/kernel/debug/ppe/active_flows", "12 3 15/4096")
        self.assertEqual(self.run_status()["state"]["ppe"]["active_flows"],
                         {"ipv4": 12, "ipv6": 3, "total": 15, "capacity": 4096})

    def test_legacy_ppe_counter_formats_remain_readable(self):
        for text, expected in [("15", {"total": 15}),
                               ("12 3 15", {"ipv4": 12, "ipv6": 3, "total": 15})]:
            with self.subTest(text=text):
                self.write("sys/kernel/debug/ppe/active_flows", text)
                self.assertEqual(self.run_status()["state"]["ppe"]["active_flows"], expected)

    def test_malformed_ppe_counts_are_unknown_without_failing_hardware(self):
        for text in ("", "bad", "0 0 bad/4096", "0 0 0/0", "0 0 4097/4096",
                     "0 0 0/", "0 0 0/4096/1", "-1 0 0/4096"):
            with self.subTest(text=text):
                self.write("sys/kernel/debug/ppe/active_flows", text)
                result = self.run_status()
                self.assertIsNone(result["state"]["ppe"]["active_flows"])
                self.assertTrue(result["result"]["passed"])

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


PPE_RESOURCES = (
    "host active:3 capacity:2048 flow active:12/3 capacity:4096\n"
    "failure stage:core_role enoent:0 enospc:18446744073709551615 enomem:0 eio:0 timeout:0 other:0 last_errno:-28\n"
    "failure stage:nexthop enoent:0 enospc:0 enomem:0 eio:0 timeout:0 other:0 last_errno:0\n"
    "nexthop active:2 capacity:128 alloc:5 reuse:9 free:3\n")
PPE_DS = ("node:0 ring:1 queue:2 running:1 faulted:0 down_desc:9223372036854775807 up_desc:1 refill:0 "
          "release:0 sync:0 refill_fail:7 poll_retry:0 refill_wake:0 tx_deferred:0\n"
          "node:1 unregistered\n"
          "node:2 ring:3 queue:4 running:0 faulted:1 down_desc:0 up_desc:0 refill:0 release:0 sync:0 "
          "refill_fail:0 poll_retry:0 refill_wake:0 tx_deferred:0\n")
@unittest.skipUnless(UCODE, "ucode is required for the real status collector")
class AccelerationDiagnostics(Status):
    def setUp(self):
        super().setUp()
        self.write("sys/kernel/debug/ppe/resource_stats", PPE_RESOURCES)
        self.write("sys/kernel/debug/ppe/direct_switch", PPE_DS)

    def ppe(self):
        return self.run_status()["state"]["ppe"]

    def test_native_counters_keep_64_bit_precision_and_sources(self):
        ppe = self.ppe()
        self.assertEqual(ppe["sampled_at"], 1000.0)
        self.assertEqual(ppe["resources"]["status"], "ok")
        self.assertEqual(ppe["resources"]["failures"]["core_role"]["enospc"], "18446744073709551615")
        self.assertEqual(ppe["resources"]["failures"]["core_role"]["last_errno"], "-28")
        self.assertEqual(ppe["resources"]["flow"], {"ipv4_active": "12", "ipv6_active": "3", "capacity": "4096"})
        self.assertEqual(ppe["direct_switch"]["nodes"][0]["counters"]["down_desc"], "9223372036854775807")
        self.assertEqual([n["registered"] for n in ppe["direct_switch"]["nodes"]], [True, False, True])
        self.assertTrue(ppe["direct_switch"]["nodes"][2]["faulted"])
    def test_absent_debugfs_is_unavailable_not_zero(self):
        shutil.rmtree(self.root / "sys/kernel/debug")
        result = self.run_status()
        ppe = result["state"]["ppe"]
        self.assertFalse(ppe["debugfs_accessible"])
        self.assertIsNone(ppe["active_flows"])
        for key in ("resources", "direct_switch"):
            self.assertEqual(ppe[key], {"source": f"debugfs:ppe/{key if key == 'direct_switch' else 'resource_stats'}",
                                        "status": "missing"})
        self.assertEqual(ppe["detail"]["flows"]["status"], "not-collected")
        self.assertEqual(ppe["active_flows_source"]["status"], "missing")
        self.assertTrue(result["result"]["passed"])

    def test_malformed_sources_withhold_all_values(self):
        cases = {
            "resource_stats": ["host active:x capacity:1\n", "failure stage:a enoent:1\nfailure stage:a enoent:2\n",
                               "host active:1 active:2\n", "garbage\x00\n", "\n"],
            "direct_switch": ["node:0 ring:1 running:2 faulted:0\n", "node:x unregistered\n", "\n"],
        }
        for name, texts in cases.items():
            for text in texts:
                with self.subTest(name=name, text=text):
                    (self.root / f"sys/kernel/debug/ppe/{name}").write_text(text)
                    ppe = self.ppe()
                    record = {"resource_stats": ppe["resources"], "direct_switch": ppe["direct_switch"]}[name]
                    self.assertEqual(record["status"], "malformed")
                    self.assertEqual(set(record), {"source", "status"})
            self.setUp()

    def test_oversized_lightweight_output_withholds_values(self):
        self.write("sys/kernel/debug/ppe/resource_stats", PPE_RESOURCES * 100)
        record = self.ppe()["resources"]
        self.assertEqual(record["status"], "truncated")
        self.assertEqual(set(record), {"source", "status"})

    def test_hardware_table_views_are_never_opened(self):
        # An open for reading would block: the FIFO deliberately has no writer.
        # The collector subprocess has a timeout so a regression fails the test.
        for name in ("flows", "reason_counters"):
            path = self.root / "sys/kernel/debug/ppe" / name
            os.mkfifo(path)
        ppe = self.ppe()
        self.assertEqual(ppe["resources"]["status"], "ok")
        for key, name in (("flows", "flows"), ("reasons", "reason_counters")):
            self.assertEqual(ppe["detail"][key], {
                "source": f"debugfs:ppe/{name}", "status": "not-collected"})

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses file permissions")
    def test_unreadable_source_is_distinct_from_missing(self):
        (self.root / "sys/kernel/debug/ppe/resource_stats").chmod(0)
        self.assertEqual(self.ppe()["resources"]["status"], "unreadable")

@unittest.skipUnless(UCODE, "ucode is required for the real status collector")
class FanStatus(Status):
    def fan(self):
        return self.run_status()["state"]

    def test_readings_that_the_guard_rejects_are_never_healthy(self):
        hw = "sys/class/hwmon/hwmon8/"
        cases = [({"fan1_input": 0}, "stopped"), ({"fan1_fault": 1}, "fault"),
                 ({"fan1_fault": "invalid"}, "unreadable"), ({"fan1_input": "x"}, "unreadable"),
                 ({"pwm1": 50}, "pwm-out-of-range")]
        for values, expected in cases:
            with self.subTest(values=values):
                self.setUp()
                for name, value in values.items():
                    self.write(hw + name, value)
                self.assertEqual(self.fan()["fans"][0]["health"], expected)
        self.setUp()
        (self.root / hw / "fan1_fault").unlink()
        self.assertEqual(self.fan()["fans"][0]["health"], "ok-no-fault-flag")
        self.setUp()
        self.assertEqual(self.fan()["fans"][0]["health"], "ok")

    def test_temperature_and_controller_unavailability_are_distinct(self):
        self.assertEqual(self.fan()["fan"]["temperature"], {"status": "ok", "celsius": 45.0})
        self.write("sys/class/thermal/thermal_zone7/temp", 200000)
        self.assertEqual(self.fan()["fan"]["temperature"]["status"], "invalid")
        self.write("sys/class/thermal/thermal_zone7/temp", 0)
        self.assertEqual(self.fan()["fan"]["temperature"]["status"], "out-of-range")
        self.write("sys/class/thermal/thermal_zone3/type", "nss_top_thermal")
        self.assertEqual(self.fan()["fan"]["temperature"]["status"], "ambiguous")
        shutil.rmtree(self.root / "sys/class/thermal")
        self.assertEqual(self.fan()["fan"]["temperature"]["status"], "missing")
        shutil.rmtree(self.root / "sys/class/hwmon")
        state = self.fan()
        self.assertEqual(state["fan"]["controller"], "missing")
        self.assertIsNone(state["fan"]["trend"]["samples"][0]["rpm"])

    def test_preflight_temperature_thresholds_for_every_target(self):
        def levels():
            return self.run_script('''let state = collect(root, uci);
print(sprintf('%J', map([ 'hardware', 'nikki', 'dae', 'daed' ], t =>
  filter(assess(state, t).checks, c => c.code == 'temperature')[0].level)));''')
        cases = [(None, "error"), (0, "error"), (999, "error"), (1000, "ok"), (59999, "ok"),
                 (60000, "warn"), (74999, "warn"), (75000, "error"), (130000, "error"),
                 (130001, "error"), (150000, "error"), (150001, "error")]
        for milli, expected in cases:
            with self.subTest(milli=milli):
                self.write("sys/class/thermal/thermal_zone7/temp", "invalid" if milli is None else milli)
                self.assertEqual(levels(), [expected] * 4)
        self.write("sys/class/thermal/thermal_zone7/temp", 45000)
        self.write("sys/class/thermal/thermal_zone3/type", "nss_top_thermal")
        self.write("sys/class/thermal/thermal_zone3/temp", 45000)
        self.assertEqual(levels(), ["error"] * 4)
        shutil.rmtree(self.root / "sys/class/thermal")
        self.assertEqual(levels(), ["error"] * 4)

    def test_guard_process_presence_is_reported_alone(self):
        self.assertEqual(self.fan()["fan"]["guard_process"], "absent")
        self.write("proc/77/comm", "rc01-fan-guard")
        self.assertEqual(self.fan()["fan"]["guard_process"], "running")
        self.write("proc/77/status", "Name:\trc01-fan-guard\nPPid:\t1\n")
        # A command-substitution subshell forked by the guard keeps its name.
        self.write("proc/79/comm", "rc01-fan-guard")
        self.write("proc/79/status", "Name:\trc01-fan-guard\nPPid:\t77\n")
        state = self.fan()
        self.assertEqual(state["fan"]["guard_process"], "running")
        self.assertEqual([p["pid"] for p in state["processes"]], [77])
        self.write("proc/78/comm", "rc01-fan-guard")
        self.write("proc/78/status", "Name:\trc01-fan-guard\nPPid:\t1\n")
        self.assertEqual(self.fan()["fan"]["guard_process"], "multiple")

    BOOT = "0b3d6c1e-5a7f-4c2e-9d10-2f6a8b4c7e91"

    def guard(self, pid=77, ticks=4242, ppid=1):
        self.write(f"proc/{pid}/comm", "rc01-fan-guard")
        self.write(f"proc/{pid}/status", f"Name:\trc01-fan-guard\nPPid:\t{ppid}\n")
        self.write(f"proc/{pid}/stat", f"{pid} (rc01-fan-guard) S {ppid} " + " ".join(["0"] * 17) + f" {ticks} 0 0")

    def guard_record(self, **changes):
        record = {"schema": 1, "boot_id": self.BOOT, "pid": 77, "start_ticks": 4242, "updated_uptime": 996.0,
                  "sampled_uptime": 996.0, "phase": "monitoring", "event": "sample", "sample_reason": "ok",
                  "failures": 0, "latched": True, "latch_reason": "consecutive-failures:rpm-zero"} | changes
        self.write("var/run/rc01-fan-guard.json", json.dumps(record))

    def protection(self):
        return self.fan()["fan"]["protection"]

    def test_guard_record_is_current_only_for_the_running_instance(self):
        self.write("proc/sys/kernel/random/boot_id", self.BOOT)
        self.guard(); self.guard_record()
        current = self.protection()
        self.assertEqual(current, {"status": "current", "detail": None, "age": 4.0, "sample_age": 4.0,
                                   "phase": "monitoring", "event": "sample", "sample_reason": "ok", "failures": 0,
                                   "latched": True, "latch_reason": "consecutive-failures:rpm-zero"})
        # A child subshell of the guard is not another instance.
        self.guard(pid=79, ppid=77)
        self.assertEqual(self.protection()["status"], "current")
        self.guard_record(updated_uptime=990.0, sampled_uptime=980.0)
        self.assertEqual(self.protection()["status"], "current")
        cases = [
            (dict(updated_uptime=989.5, sampled_uptime=989.5), "stale", "expired"),
            (dict(updated_uptime=1001.0, sampled_uptime=1001.0), "stale", "age-unknown"),
            (dict(start_ticks=4243), "stale", "other-instance"),
            (dict(pid=78), "stale", "other-instance"),
            (dict(boot_id="11111111-2222-3333-4444-555555555555"), "stale", "previous-boot"),
            (dict(phase="exited", event="signal-stop"), "exited", None),
        ]
        for changes, status, detail in cases:
            with self.subTest(changes=changes):
                self.guard_record(**changes)
                result = self.protection()
                self.assertEqual((result["status"], result["detail"]), (status, detail))
                # Older records keep their values so the page can label them.
                self.assertEqual((result["failures"], result["latched"]), (0, True))
        self.guard_record(boot_id="11111111-2222-3333-4444-555555555555")
        self.assertIsNone(self.protection()["age"])
        self.guard_record(sampled_uptime=None, phase="starting", event="startup", sample_reason="not-sampled",
                          latched=False, latch_reason=None)
        starting = self.protection()
        self.assertEqual((starting["status"], starting["sample_age"], starting["latched"]), ("current", None, False))

    def test_guard_record_without_matching_process_is_not_current(self):
        self.write("proc/sys/kernel/random/boot_id", self.BOOT)
        self.guard_record()
        self.assertEqual(self.protection()["detail"], "no-guard-process")
        self.guard(); self.guard(pid=78)
        self.assertEqual(self.protection()["detail"], "multiple-guard-processes")
        shutil.rmtree(self.root / "proc/78")
        (self.root / "proc/77/stat").unlink()
        self.assertEqual((self.protection()["status"], self.protection()["failures"]), ("unknown", None))
        self.guard()
        (self.root / "proc/sys/kernel/random/boot_id").unlink()
        self.assertEqual((self.protection()["status"], self.protection()["detail"]), ("unknown", "identity-unavailable"))

    def test_missing_or_invalid_guard_record_is_unknown_not_zero(self):
        self.write("proc/sys/kernel/random/boot_id", self.BOOT)
        self.guard()
        unknown = {"status": "unknown", "detail": "missing", "age": None, "sample_age": None, "phase": None,
                   "event": None, "sample_reason": None, "failures": None, "latched": None, "latch_reason": None}
        self.assertEqual(self.protection(), unknown)
        bad = [dict(schema=2), dict(failures=-1), dict(failures="0"), dict(failures=None), dict(latched="true"),
               dict(latched=False), dict(latched=True, latch_reason=None), dict(latch_reason="startup:ok"),
               dict(latch_reason="consecutive-failures:bogus"), dict(sample_reason="hot"), dict(event="other"),
               dict(phase="running"), dict(updated_uptime="996"), dict(sampled_uptime=997.0), dict(pid=0),
               dict(boot_id="<script>"), dict(start_ticks=None)]
        for changes in bad:
            with self.subTest(changes=changes):
                self.guard_record(**changes)
                self.assertEqual(self.protection(), unknown | {"detail": "malformed"})
        path = self.root / "var/run/rc01-fan-guard.json"
        for content, detail in (('{"schema":1,"boot_id"', "malformed"), ("", "malformed"),
                                (" " * 5000, "truncated"), ("[]", "malformed")):
            with self.subTest(content=content[:20]):
                path.write_text(content)
                self.assertEqual(self.protection()["detail"], detail)
        path.write_text("{}")
        path.chmod(0)
        if os.geteuid() != 0:
            self.assertEqual(self.protection()["detail"], "unreadable")
        path.chmod(0o644)

    def test_trend_is_bounded_rate_limited_and_marks_gaps(self):
        script = r"""import { writefile } from 'fs';
        let cache = {}, out = [];
        function at(t) { writefile(root + '/proc/uptime', t + '.00 0\n'); return collect(root, uci, cache).fan.trend; }
        at(100); at(105);
        let a = at(110);
        push(out, map(a.samples, s => s.t));
        writefile(root + '/sys/class/hwmon/hwmon8/fan1_input', '0\n');
        let b = at(200);
        push(out, map(b.samples, s => [ s.t, s.gap_before, s.health ]));
        for (let t = 210; t <= 2000; t += 10) at(t);
        let c = at(2010);
        push(out, [ length(c.samples), c.samples[0].t, c.samples[-1].age, c.window ]);
        let d = at(50);
        push(out, map(d.samples, s => s.t));
        print(sprintf('%J', out));
        """
        result = self.run_script(script)
        self.assertEqual(result[0], [100, 110])
        self.assertEqual(result[1], [[100, False, "ok"], [110, False, "ok"], [200, True, "stopped"]])
        # 90 samples, oldest inside the 900 s window, newest age zero.
        self.assertEqual(result[2], [90, 1120, 0, 900])
        # Uptime moving backwards (reboot or clock reset) discards history.
        self.assertEqual(result[3], [50])


def band(*freqs):
    """One nl80211 band: freqs given as MHz or (MHz, flags) as the ucode module returns them."""
    entries = []
    for f in freqs:
        freq, flags = (f, ()) if isinstance(f, int) else f
        entries.append({"freq": freq, **{flag: True for flag in flags}})
    return {"freqs": entries}


# Three independent phys as on RC01, shaped like an nl80211 GET_WIPHY dump
# (wiphy_bands is indexed by band type, so absent bands are null).
WIPHY_DUMP = [
    {"wiphy": 0, "wiphy_name": "phy0", "wiphy_bands": [band(2412, 2437, 2462, (2467, ["disabled"]), (2472, ["disabled"]))]},
    {"wiphy": 1, "wiphy_name": "phy1", "wiphy_bands": [None, band(5180, (5260, ["radar", "no_ir"]), 5745, (5865, ["disabled"]))]},
    {"wiphy": 2, "wiphy_name": "phy2", "wiphy_bands": [None, None, None, band(5955, 5975, 6115)]},
]


@unittest.skipUnless(UCODE, "ucode is required for the real status collector")
class ChannelHints(Status):
    def setUp(self):
        super().setUp()
        self.paths = {"phy0": "platform/soc@0/pcie-a/0000:01:00.0", "phy1": "platform/soc@0/pcie-b/0000:02:00.0",
                      "phy2": "platform/soc@0/pcie-c/0000:03:00.0"}
        for idx, (phy, path) in enumerate(self.paths.items()):
            device = self.root / "sys/devices" / path
            device.mkdir(parents=True)
            self.write(f"sys/class/ieee80211/{phy}/index", idx)
            self.write(f"sys/class/ieee80211/{phy}/macaddress", f"02:00:00:00:00:0{idx}")
            (self.root / f"sys/class/ieee80211/{phy}/device").symlink_to(device)
        self.config["wireless"] = {}
        self.radio("radio0", "2g", "6", path=self.paths["phy0"])
        self.radio("radio1", "5g", "36", path=self.paths["phy1"])
        self.radio("radio2", "6g", "1", path=self.paths["phy2"])

    def radio(self, name, band_name, channel, **options):
        section = {".type": "wifi-device", ".name": name, "disabled": "1", "type": "mac80211", **options}
        if band_name is not None:
            section["band"] = band_name
        if channel is not None:
            section["channel"] = channel
        self.config["wireless"][name] = section

    def hints(self, dump=WIPHY_DUMP, query=True):
        call = f"collect(root, uci, null, () => {json.dumps(dump)})" if query else "collect(root, uci, null)"
        state = self.run_script(f"print(sprintf('%J', {call}.wireless));")
        return state, {h["radio"]: h for h in state["hints"]}

    def test_valid_fixed_channels_match_their_own_phy(self):
        state, hints = self.hints()
        self.assertEqual(state["status"], "ok")
        self.assertEqual([p["name"] for p in state["phys"]], ["phy0", "phy1", "phy2"])
        self.assertEqual({r: (h["phy"], h["frequency"], h["status"]) for r, h in hints.items()},
                         {"radio0": ("phy0", 2437, "ok"), "radio1": ("phy1", 5180, "ok"), "radio2": ("phy2", 5955, "ok")})

    def test_same_channel_number_is_judged_by_band_frequency(self):
        # Channel 1 is 2412 MHz in 2.4 GHz and 5955 MHz in 6 GHz.
        self.radio("radio0", "2g", "1", path=self.paths["phy0"])
        _, hints = self.hints([{**WIPHY_DUMP[0], "wiphy_bands": [band(2412)]}, *WIPHY_DUMP[1:]])
        self.assertEqual((hints["radio0"]["frequency"], hints["radio0"]["status"]), (2412, "ok"))
        self.assertEqual((hints["radio2"]["frequency"], hints["radio2"]["status"]), (5955, "ok"))
        # A 6 GHz number on the 2.4 GHz phy is not "valid because 1 exists somewhere".
        self.radio("radio2", "6g", "1", path=self.paths["phy0"])
        _, hints = self.hints()
        self.assertEqual(hints["radio2"]["status"], "band-unsupported")
        self.assertEqual(hints["radio2"]["supported_bands"], ["2g"])

    def test_clear_mismatches(self):
        self.radio("radio0", "2g", "36", path=self.paths["phy0"])     # no such 2.4 GHz channel
        self.radio("radio1", "5g", "165", path=self.paths["phy1"])    # 5825 MHz not listed
        self.radio("radio2", "6g", "1", path=self.paths["phy1"])      # 6 GHz configured on the 5 GHz phy
        self.radio("radio3", "2g", "13", path=self.paths["phy0"])     # listed but disabled
        self.radio("radio4", "5g", "173", path=self.paths["phy1"])    # 5865 MHz disabled
        self.radio("radio5", "7g", "1", path=self.paths["phy0"])
        _, hints = self.hints()
        self.assertEqual({r: h["status"] for r, h in hints.items()}, {
            "radio0": "channel-invalid", "radio1": "channel-unsupported", "radio2": "band-unsupported",
            "radio3": "channel-disabled", "radio4": "channel-disabled", "radio5": "band-invalid"})

    def test_kernel_flags_are_reported_without_judging_legality(self):
        self.radio("radio1", "5g", "52", path=self.paths["phy1"])
        _, hints = self.hints()
        self.assertEqual((hints["radio1"]["status"], hints["radio1"]["flags"]), ("ok", ["radar", "no_ir"]))

    def test_automatic_channel_checks_only_band(self):
        self.radio("radio0", "2g", "auto", path=self.paths["phy0"])
        self.radio("radio1", "5g", None, path=self.paths["phy1"])
        self.radio("radio2", "6g", "auto", path=self.paths["phy0"])
        _, hints = self.hints()
        self.assertEqual({r: (h["status"], h["frequency"]) for r, h in hints.items()},
                         {"radio0": ("auto", None), "radio1": ("auto", None), "radio2": ("band-unsupported", None)})
        _, hints = self.hints([{**WIPHY_DUMP[0], "wiphy_bands": [band((2412, ["disabled"]))]}])
        self.assertEqual(hints["radio0"]["status"], "band-disabled")

    def test_missing_phy_or_capabilities_is_undetermined(self):
        # Driver loaded only one phy: the others are not ready, not wrong.
        _, hints = self.hints(WIPHY_DUMP[:1])
        self.assertEqual({r: h["status"] for r, h in hints.items()},
                         {"radio0": "ok", "radio1": "not-ready", "radio2": "not-ready"})
        # Query failure: nothing is asserted, and no phy list is shown.
        state, hints = self.hints(None)
        self.assertEqual((state["status"], state["phys"]), ("unavailable", None))
        self.assertEqual({h["status"] for h in hints.values()}, {"capability-unavailable"})
        # Band missing in configuration is undetermined as well.
        self.radio("radio0", None, "6", path=self.paths["phy0"])
        _, hints = self.hints()
        self.assertEqual(hints["radio0"]["status"], "band-missing")

    def test_without_nl80211_module_status_is_module_missing(self):
        # The host ucode has no nl80211 plugin, exercising the real require() path.
        state, hints = self.hints(query=False)
        self.assertEqual(state["status"], "module-missing")
        self.assertEqual({h["status"] for h in hints.values()}, {"capability-unavailable"})

    def test_phy_lookup_follows_wifi_scripts_order(self):
        # Two phys below one device: "+1" selects the second by phy index.
        shared = self.paths["phy1"]
        (self.root / "sys/class/ieee80211/phy2/device").unlink()
        (self.root / "sys/class/ieee80211/phy2/device").symlink_to(self.root / "sys/devices" / shared)
        self.radio("radio1", "5g", "36", path=shared)
        self.radio("radio2", "6g", "1", path=shared + "+1")
        # MAC address, then plain phy name, when there is no path.
        self.radio("radio0", "2g", "6", macaddr="02:00:00:00:00:00")
        self.radio("radio3", "2g", "1", phy="phy0")
        self.radio("radio4", "2g", "1", path="platform/missing", phy="phy9")
        _, hints = self.hints()
        self.assertEqual({r: h["phy"] for r, h in hints.items()},
                         {"radio0": "phy0", "radio1": "phy1", "radio2": "phy2", "radio3": "phy0", "radio4": None})
        self.assertEqual(hints["radio4"]["status"], "not-ready")

    def test_failed_query_after_success_keeps_no_old_capabilities(self):
        script = f"""let cache = {{}}, dumps = [ {json.dumps(WIPHY_DUMP)}, null ];
        let a = collect(root, uci, cache, () => dumps[0]).wireless;
        let b = collect(root, uci, cache, () => dumps[1]).wireless;
        print(sprintf('%J', [ a.hints[0].status, b.status, b.phys, map(b.hints, h => [ h.status, h.phy ]) ]));"""
        result = self.run_script(script)
        self.assertEqual(result[0], "ok")
        self.assertEqual(result[1:3], ["unavailable", None])
        self.assertEqual(result[3], [["capability-unavailable", None]] * 3)

    def test_hints_do_not_write_configuration_or_files(self):
        def fingerprint():
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        before, config = fingerprint(), json.dumps(self.config, sort_keys=True)
        # The UCI stub has only get/foreach; any write would raise.
        self.radio("radio1", "5g", "165", path=self.paths["phy1"])
        config = json.dumps(self.config, sort_keys=True)
        self.hints()
        self.assertEqual(before, fingerprint())
        self.assertEqual(config, json.dumps(self.config, sort_keys=True))


if __name__ == "__main__":
    unittest.main()


# Shaped like the Linux 6.18 sfp driver debugfs "state" file (sfp_debug_state_show).
def sfp_debug_state(moddef0, module="empty"):
    return (f"Module state: {module}\nModule probe attempts: 0 0\nDevice state: up\nMain state: down\n"
            "Fault recovery remaining retries: 5\nPHY probe remaining retries: 12\nSignalling rate: 0 kBd\n"
            f"Rate select threshold: 0 kBd\nmoddef0: {moddef0}\nrx_los: 0\ntx_fault: 0\ntx_disable: 1\nrs0: 0\nrs1: 0")


@unittest.skipUnless(UCODE, "ucode is required for the real status collector")
class SfpStatus(Status):
    def setUp(self):
        super().setUp()
        driver = self.root / "sys/bus/platform/drivers/sfp"
        driver.mkdir(parents=True)
        for entry in ("bind", "unbind", "uevent"):
            (driver / entry).write_text("")
        cage = self.root / "sys/devices/platform/sfp"
        cage.mkdir(parents=True)
        (driver / "sfp").symlink_to(cage)
        self.write("sys/kernel/debug/sfp/state", sfp_debug_state(0))

    def sfp(self):
        return self.run_status()["state"]["sfp"]

    def module(self, moddef0, carrier, module="present"):
        self.write("sys/kernel/debug/sfp/state", sfp_debug_state(moddef0, module))
        self.write("sys/class/net/sfp/carrier", carrier)

    def test_three_basic_states(self):
        for moddef0, carrier, status in ((0, 0, "not-detected"), (1, 0, "detected-no-link"), (1, 1, "link")):
            with self.subTest(status=status):
                self.module(moddef0, carrier)
                state = self.run_status()["state"]
                sfp = state["sfp"]
                self.assertEqual(sfp["status"], status)
                self.assertEqual(sfp["presence"]["status"], "ok")
                self.assertEqual(sfp["presence"]["detected"], moddef0 == 1)
                self.assertEqual(sfp["presence"]["source"], "debugfs:sfp/state")
                self.assertEqual(sfp["presence"]["method"], "i2c-inferred")
                self.assertEqual(sfp["presence"]["module_state"], "present")
                self.assertEqual(sfp["carrier"], carrier)
                self.assertEqual(sfp["sampled_at"], 1000.0)
                # The net device exists in every case; it is not a module.
                self.assertTrue([p for p in state["ports"] if p["name"] == "sfp"][0]["present"])

    def test_unavailable_presence_is_unknown_not_empty(self):
        cases = {
            "driver-missing": lambda: shutil.rmtree(self.root / "sys/bus/platform/drivers/sfp"),
            "missing": lambda: shutil.rmtree(self.root / "sys/kernel/debug/sfp"),
            "malformed": lambda: self.write("sys/kernel/debug/sfp/state", "Module state: empty\nmoddef0: maybe"),
            "truncated": lambda: self.write("sys/kernel/debug/sfp/state", sfp_debug_state(1) + "\n" + "x" * 5000),
            "ambiguous": lambda: (self.root / "sys/bus/platform/drivers/sfp/sfp2").symlink_to(self.root / "sys/devices/platform/sfp"),
        }
        if os.geteuid() != 0:
            cases["unreadable"] = lambda: (self.root / "sys/kernel/debug/sfp/state").chmod(0)
        for expected, damage in cases.items():
            with self.subTest(expected=expected):
                self.setUp()
                self.write("sys/class/net/sfp/carrier", 1)
                damage()
                sfp = self.sfp()
                self.assertEqual(sfp["status"], "unknown")
                self.assertEqual(sfp["presence"]["status"], expected)
                self.assertIsNone(sfp["presence"]["detected"])
                # Link is still reported on its own.
                self.assertEqual(sfp["carrier"], 1)

    def test_unreadable_carrier_is_not_no_link(self):
        self.module(1, "")
        (self.root / "sys/class/net/sfp/carrier").write_text("")
        sfp = self.sfp()
        self.assertEqual((sfp["status"], sfp["carrier"], sfp["carrier_status"]), ("detected-link-unknown", None, "unreadable"))
        (self.root / "sys/class/net/sfp/carrier").unlink()
        self.assertEqual(self.sfp()["status"], "detected-link-unknown")
        self.module(0, "")
        (self.root / "sys/class/net/sfp/carrier").unlink()
        self.assertEqual(self.sfp()["status"], "not-detected")

    def test_incomplete_or_bad_module_record_withholds_presence(self):
        complete = sfp_debug_state(1, "present") + "\n"
        cases = {
            "moddef0-only": "moddef0: 1\n",
            "module-state-missing": complete.split("\n", 1)[1],
            "module-state-invalid": complete.replace("Module state: present", "Module state: ???"),
            "short-after-moddef0": complete.split("rx_los:")[0],
            "short-final-line": complete[:-2],
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                (self.root / "sys/kernel/debug/sfp/state").write_text(data)
                sfp = self.sfp()
                self.assertEqual(sfp["status"], "unknown")
                self.assertEqual(sfp["presence"]["status"], "malformed")
                self.assertIsNone(sfp["presence"]["detected"])
                self.assertIsNone(sfp["presence"]["module_state"])

    def test_missing_interface_is_distinct(self):
        self.module(1, 1)
        shutil.rmtree(self.root / "sys/class/net/sfp")
        sfp = self.sfp()
        self.assertEqual((sfp["status"], sfp["interface_present"], sfp["carrier_status"]),
                         ("interface-missing", False, "interface-missing"))
        self.assertTrue(sfp["presence"]["detected"])

    def test_led_mode_and_controller_presence(self):
        led = self.sfp()["led"]
        self.assertEqual(led, {"presence_mode": False, "controller": "absent"})
        self.config["system"] = {"led_sfp": {".type": "led", "rc01_presence": "1"}}
        self.write("proc/90/comm", "rc01-sfp-led")
        self.write("proc/90/status", "Name:\trc01-sfp-led\nPPid:\t1\n")
        self.write("proc/91/comm", "rc01-sfp-led")
        self.write("proc/91/status", "Name:\trc01-sfp-led\nPPid:\t90\n")
        self.assertEqual(self.sfp()["led"], {"presence_mode": True, "controller": "running"})
        self.write("proc/92/comm", "rc01-sfp-led")
        self.write("proc/92/status", "Name:\trc01-sfp-led\nPPid:\t1\n")
        self.assertEqual(self.sfp()["led"]["controller"], "multiple")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to render the collected state")
    def test_collected_states_render_on_the_page(self):
        view = ROOT / "package/emortal/luci-app-rc01/htdocs/luci-static/resources/view/status/rc01.js"
        program = r"""
const fs = require('fs');
const E = (tag, attrs, children) => ({ tag, attrs, children });
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, { declare: () => () => null }, { add: () => {} }, {}, E, v => v);
process.stdout.write(JSON.stringify(api.render(JSON.parse(fs.readFileSync(0, 'utf8')))));
"""
        for moddef0, carrier, text in ((0, 0, "No module detected by the kernel"), (1, 0, "Module detected, no link"),
                                       (1, 1, "Module detected, link up"), (None, 1, "Unknown: module detection unavailable")):
            with self.subTest(text=text):
                if moddef0 is None:
                    shutil.rmtree(self.root / "sys/kernel/debug/sfp")
                else:
                    self.module(moddef0, carrier)
                result = self.run_status()
                state = result["state"]
                state["preflight"] = [result["result"]]
                page = subprocess.run([shutil.which("node"), "-e", program, str(view)], input=json.dumps(state),
                                      capture_output=True, text=True, timeout=10)
                self.assertEqual(page.returncode, 0, page.stderr)
                self.assertIn(text, page.stdout)

    def test_collection_does_not_modify_the_fixture(self):
        self.module(1, 1)
        def fingerprint():
            return sorted((str(p.relative_to(self.root)), p.stat().st_mode, p.read_bytes() if p.is_file() and not p.is_symlink() else b"")
                          for p in self.root.rglob("*"))
        before = fingerprint()
        self.sfp()
        self.assertEqual(before, fingerprint())
