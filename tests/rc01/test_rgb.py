"""RC01 RGB light bar: effect logic, service lifecycle, init script, LuCI and DTB.

Everything runs against fixture directories; nothing touches the host's
/sys, LEDs, PWM or syslog. Fan, status, network and SFP LEDs plus the fan
PWM are present in the fixtures so that writes to them would be caught.

The init script runs under dash and BusyBox ash (BUSYBOX=/path/to/busybox,
else busybox from PATH); a BUSYBOX that is not an executable file is an
error. The service itself is ucode, not shell.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "package/emortal/rc01-rgb"
FILES = PKG / "files"
MODULE = FILES / "rgb.uc"
DAEMON = FILES / "rc01-rgb"
INIT = FILES / "rc01-rgb.init"
LUCI = ROOT / "package/emortal/luci-app-rc01-rgb"
VIEW = LUCI / "htdocs/luci-static/resources/view/system/rc01-rgb.js"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")


def busybox():
    path = os.environ.get("BUSYBOX")
    if not path:
        return shutil.which("busybox")
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        raise RuntimeError(f"BUSYBOX={path!r} is not an executable file")
    return os.path.abspath(path)


BUSYBOX = busybox()
# Shell name -> command line that runs it.
SHELL_COMMANDS = {name: command for name, command in (
    ("dash", [shutil.which("dash")]), ("busybox", [BUSYBOX, "sh"])) if command[0]}
SHELLS = list(SHELL_COMMANDS) or ["sh"]
SHELL_COMMANDS.setdefault("sh", ["sh"])
KERNEL = os.environ.get("RC01_KERNEL_SOURCE")
LUCI_VALIDATION = Path(os.environ.get("RC01_LUCI_VALIDATION_SOURCE") or
                       ROOT / "feeds/luci/modules/luci-base/htdocs/luci-static/resources/validation.js")

RGB = ["red:ambient", "green:ambient", "blue:ambient"]
# LEDs and controls that must never be written by the RGB service.
FOREIGN = {
    "sys/class/leds/white:status/brightness": "1",
    "sys/class/leds/white:status/trigger": "none [default-on]",
    "sys/class/leds/amber:network/brightness": "0",
    "sys/class/leds/white:network/brightness": "1",
    "sys/class/leds/green:sfp/trigger": "none [netdev]",
    "sys/class/leds/90000.mdio-1:01:green:lan-1/trigger": "none [netdev]",
    "sys/class/hwmon/hwmon8/pwm1": "77",
    "sys/class/hwmon/hwmon8/pwm1_enable": "2",
}

spec = importlib.util.spec_from_file_location("rc01_images_rgb", ROOT / "scripts/verify-rc01-images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-rgb-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "root"
        for led in RGB:
            self.write(f"sys/class/leds/{led}/brightness", "0")
            self.write(f"sys/class/leds/{led}/trigger", "[none] timer heartbeat default-on")
        for name, value in FOREIGN.items():
            self.write(name, value)
        self.write("var/run/.keep", "")

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{value}\n")

    def read(self, name):
        return (self.root / name).read_text().strip()

    def leds(self):
        return [int(self.read(f"sys/class/leds/{led}/brightness")) for led in RGB]

    def assert_foreign_untouched(self):
        for name, value in FOREIGN.items():
            self.assertEqual(self.read(name), value, name)


@unittest.skipUnless(UCODE, "ucode is required for the RGB service")
class Logic(Fixture):
    def uc(self, body, cfg=None):
        source = f'import * as rgb from {json.dumps(str(MODULE))};\n'
        source += f'let root = {json.dumps(str(self.root))};\n'
        source += f'let cfg = rgb.config({json.dumps(cfg or {})});\n' + body
        result = subprocess.run([UCODE, "-"], input=source, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def frames(self, cfg, steps, minute=720, device="wan"):
        """Frames at the given elapsed times (ms), sharing one context."""
        return self.uc(f'''let ctx = rgb.create(root, {json.dumps(device)}), out = [];
            for (let t in {json.dumps(steps)}) push(out, rgb.frame(ctx, cfg, {minute}, t));
            print(sprintf("%J", out));''', cfg)

    def test_defaults_are_off_and_match_the_shipped_config(self):
        shipped = dict(re.findall(r"^\s*option (\w+) '([^']*)'$", (FILES / "rc01_rgb.config").read_text(), re.M))
        self.assertEqual(shipped["enabled"], "0")
        self.assertEqual(self.uc('print(sprintf("%J", cfg));'), self.uc('print(sprintf("%J", cfg));', shipped))
        cfg = self.uc('print(sprintf("%J", cfg));')
        self.assertFalse(cfg["enabled"])
        self.assertEqual((cfg["mode"], cfg["brightness"], cfg["color"], cfg["problems"]), ("static", 96, [0, 0x66, 0xFF], []))

    def test_invalid_input_falls_back_per_option_and_is_reported(self):
        bad = {"mode": "disco", "color": "blue", "second_color": "#12345", "brightness": "300",
               "minimum_brightness": "-1", "speed": "0", "sensitivity": "1.5", "on_time": "24:00",
               "off_time": "7:00", "night_start": "", "night_brightness": "x",
               "traffic_interface": "eth0;reboot"}
        cfg = self.uc('print(sprintf("%J", cfg));', bad)
        self.assertEqual(cfg["mode"], "static")
        self.assertEqual(cfg["color"], [0, 0x66, 0xFF])
        self.assertEqual((cfg["brightness"], cfg["minimum_brightness"], cfg["speed"], cfg["sensitivity"]), (96, 8, 5, 5))
        self.assertEqual((cfg["on_time"], cfg["off_time"], cfg["night_brightness"]), (7 * 60, 23 * 60 + 30, 24))
        self.assertEqual(cfg["traffic_interface"], "wan")
        self.assertEqual(sorted(cfg["problems"]), sorted(set(bad) - {"night_start"}))  # empty = default
        good = self.uc('print(sprintf("%J", cfg));', {"brightness": "0", "speed": "10", "color": "#A0b1C2",
                                                      "on_time": "00:00", "off_time": "23:59", "enabled": "1"})
        self.assertEqual((good["brightness"], good["speed"], good["color"], good["problems"]), (0, 10, [0xA0, 0xB1, 0xC2], []))
        self.assertTrue(good["enabled"])

    def level(self, cfg, minute):
        return self.uc(f'print(sprintf("%J", rgb.level(cfg, {minute})));', cfg)

    def test_schedule_turns_off_outside_its_window_including_across_midnight(self):
        day = {"schedule_enabled": "1", "on_time": "07:00", "off_time": "23:30", "brightness": "200"}
        self.assertEqual(self.level(day, 6 * 60 + 59)["period"], "schedule-off")
        self.assertEqual(self.level(day, 7 * 60)["level"], 200)
        self.assertEqual(self.level(day, 23 * 60 + 29)["level"], 200)
        self.assertEqual(self.level(day, 23 * 60 + 30)["level"], 0)
        evening = dict(day, on_time="18:00", off_time="02:00")
        self.assertEqual([self.level(evening, m)["level"] for m in (17 * 60, 18 * 60, 0, 119, 120)], [0, 200, 200, 200, 0])
        self.assertEqual(self.level(dict(day, off_time="07:00"), 3 * 60)["level"], 200)  # equal: whole day
        self.assertEqual(self.level(dict(day, schedule_enabled="0"), 3 * 60)["level"], 200)

    def test_night_only_lowers_and_never_lights_a_scheduled_off_bar(self):
        cfg = {"brightness": "200", "night_enabled": "1", "night_start": "22:00", "night_end": "07:00",
               "night_brightness": "24"}
        self.assertEqual(self.level(cfg, 23 * 60), {"level": 24, "period": "night"})
        self.assertEqual(self.level(cfg, 12 * 60), {"level": 200, "period": "day"})
        self.assertEqual(self.level(dict(cfg, night_brightness="250"), 23 * 60)["level"], 200)
        both = dict(cfg, schedule_enabled="1", on_time="07:00", off_time="23:00")
        self.assertEqual(self.level(both, 22 * 60 + 30)["level"], 24)       # lit, night
        self.assertEqual(self.level(both, 23 * 60 + 30)["period"], "schedule-off")  # schedule wins
        self.assertEqual(self.level(dict(cfg, night_brightness="0"), 23 * 60)["level"], 0)

    def test_static_colour_scaling_and_off(self):
        f = self.frames({"color": "#ff8000", "brightness": "255"}, [0])[0]
        self.assertEqual((f["rgb"], f["delay"]), ([255, 128, 0], 1000))
        f = self.frames({"color": "#ff8000", "brightness": "128"}, [0])[0]
        self.assertEqual(f["rgb"], [128, 64, 0])
        f = self.frames({"color": "#ffffff", "brightness": "0"}, [0])[0]
        self.assertEqual((f["rgb"], f["level"]), ([0, 0, 0], 0))

    def test_breathe_stays_between_minimum_and_brightness_at_ten_hertz(self):
        cfg = {"mode": "breathe", "color": "#ffffff", "brightness": "200", "minimum_brightness": "20", "speed": "5"}
        out = self.frames(cfg, list(range(0, 12000, 100)))
        reds = [f["rgb"][0] for f in out]
        self.assertTrue(all(f["delay"] == 100 for f in out))
        self.assertEqual(min(reds), 20)
        self.assertEqual(max(reds), 200)
        self.assertEqual(reds[0], 20)
        self.assertEqual(reds[50], 200)          # speed 5: 10 s cycle, peak at 5 s
        # A minimum above the ceiling (e.g. at night) is capped to it.
        flat = self.frames(dict(cfg, minimum_brightness="250"), [0, 2500, 5000])
        self.assertEqual({tuple(f["rgb"]) for f in flat}, {(200, 200, 200)})
        fast = self.frames(dict(cfg, speed="10"), [2500])[0]["rgb"][0]
        self.assertEqual(fast, 200)              # speed 10: 5 s cycle

    def test_duotone_and_rainbow_reach_their_colours(self):
        duo = self.frames({"mode": "duotone", "color": "#ff0000", "second_color": "#0000ff", "brightness": "255"},
                          [0, 5000, 10000])
        self.assertEqual([f["rgb"] for f in duo], [[255, 0, 0], [0, 0, 255], [255, 0, 0]])
        wheel = self.frames({"mode": "rainbow", "brightness": "255"}, [0, 1667, 3334, 5000, 6667, 8334])
        self.assertEqual([f["rgb"] for f in wheel][::3], [[255, 0, 0], [0, 255, 255]])
        self.assertTrue(all(f["delay"] == 100 for f in wheel))

    def test_temperature_uses_the_nss_sensor_and_falls_back_when_missing_or_invalid(self):
        cfg = {"mode": "temperature", "brightness": "255", "color": "#123456"}
        self.write("sys/class/thermal/thermal_zone0/type", "cpu0-thermal")
        self.write("sys/class/thermal/thermal_zone0/temp", "90000")
        self.write("sys/class/thermal/thermal_zone7/type", "nss-top-thermal")
        self.write("sys/class/thermal/thermal_zone7/temp", "30000")
        f = self.frames(cfg, [0])[0]
        self.assertEqual((f["rgb"], f["detail"], f["fallback"], f["delay"]), ([0, 255, 80], "30 C", False, 1000))
        self.write("sys/class/thermal/thermal_zone7/temp", "80000")
        self.assertEqual(self.frames(cfg, [0])[0]["rgb"], [255, 0, 0])
        for value in ("200000", "garbage", "0"):
            self.write("sys/class/thermal/thermal_zone7/temp", value)
            f = self.frames(cfg, [0])[0]
            self.assertEqual((f["rgb"], f["fallback"], f["detail"]), ([0x12, 0x34, 0x56], True, "source unavailable"))
        shutil.rmtree(self.root / "sys/class/thermal/thermal_zone7")
        self.assertTrue(self.frames(cfg, [0])[0]["fallback"])

    def test_cpu_needs_two_samples_and_reports_an_unreadable_source(self):
        cfg = {"mode": "cpu", "brightness": "255", "color": "#010203"}
        stat = self.root / "proc/stat"
        stat.parent.mkdir(parents=True)
        stat.write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\n")
        body = f'''let ctx = rgb.create(root, "wan"), out = [];
            push(out, rgb.frame(ctx, cfg, 720, 0));
            writefile({json.dumps(str(stat))}, "cpu  600 0 100 800 0 0 0 0 0 0\\n");
            push(out, rgb.frame(ctx, cfg, 720, 1000));
            writefile({json.dumps(str(stat))}, "cpu  600 0 100 1800 0 0 0 0 0 0\\n");
            push(out, rgb.frame(ctx, cfg, 720, 2000));
            print(sprintf("%J", out));'''
        first, busy, idle = self.uc("import { writefile } from 'fs';\n" + body, cfg)
        self.assertEqual((first["fallback"], first["detail"], first["rgb"]), (True, "sampling", [1, 2, 3]))
        self.assertEqual((busy["fallback"], busy["detail"], busy["rgb"]), (False, "100%", [255, 0, 0]))
        self.assertEqual((idle["detail"], idle["rgb"]), ("0%", [0, 180, 80]))
        stat.unlink()
        f = self.frames(cfg, [0])[0]
        self.assertEqual((f["fallback"], f["detail"]), (True, "source unavailable"))

    def test_cpu_detail_is_real_load_while_sensitivity_changes_colour(self):
        stat = self.root / "proc/stat"
        frames = []
        for sensitivity in ("1", "5", "10"):
            with self.subTest(sensitivity=sensitivity):
                self.write("proc/stat", "cpu 100 0 0 100 0 0 0 0 0 0")
                frame = self.uc(f'''import {{ writefile }} from 'fs';
                    let ctx = rgb.create(root, "wan");
                    rgb.frame(ctx, cfg, 720, 0);
                    writefile({json.dumps(str(stat))}, "cpu 150 0 0 150 0 0 0 0 0 0\\n");
                    print(sprintf("%J", rgb.frame(ctx, cfg, 720, 1000)));''',
                    {"mode": "cpu", "brightness": "255", "sensitivity": sensitivity})
                self.assertEqual(frame["detail"], "50%")
                self.assertFalse(frame["fallback"])
                frames.append(frame["rgb"])
        self.assertEqual(len({tuple(rgb) for rgb in frames}), 3)

    @unittest.skipUnless(shutil.which("node") and LUCI_VALIDATION.is_file(),
                         "Node and the pinned LuCI validation source (feed or RC01_LUCI_VALIDATION_SOURCE) are required")
    def test_page_integer_validation_agrees_with_real_backend(self):
        # Run the actual view's datatypes through the real feed validator;
        # only the form/translation scaffolding is stubbed.
        program = r'''
const fs = require('fs');
String.prototype.format = function(...args) { let i = 0; return this.replace(/%[dfs]/g, () => args[i++]); };
const options = {};
const Value = { extend: () => function() {} };
const form = { Value, Map: function() {
    this.section = () => ({ option: (type, name) => options[name] = { depends() {}, value() {} } });
    this.render = () => options;
} };
const api = new Function('view', 'form', 'fs', 'rpc', 'uci', '_', fs.readFileSync(process.argv[1], 'utf8'))(
    { extend: value => value }, form, {}, { declare: () => () => ({}) }, {}, value => value);
api.render([null, {}, null]);
const validation = new Function('baseclass', '_', 'L', fs.readFileSync(process.argv[2], 'utf8'))(
    { extend: value => value }, value => value, { raise: (type, message) => { throw new Error(message); } });
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const results = cases.map(item => {
    const stack = validation.compile(options[item.option].datatype);
    const context = { value: item.value, factory: validation, assert: value => !!value };
    return { ...item, accepted: stack[0].apply(context, stack[1]) };
});
process.stdout.write(JSON.stringify(results));
'''
        cases = []
        for option, low, high in (("brightness", 0, 255), ("minimum_brightness", 0, 255),
                                  ("night_brightness", 0, 255), ("speed", 1, 10), ("sensitivity", 1, 10)):
            for value, accepted in ((str(low), True), (str(high), True), (str((low + high) // 2), True),
                                    (f"{low}.5", False), (str(low - 1), False), (str(high + 1), False)):
                cases.append({"option": option, "value": value, "expected": accepted})
        run = subprocess.run([shutil.which("node"), "-e", program, str(VIEW), str(LUCI_VALIDATION)],
                             input=json.dumps(cases), text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        for case in json.loads(run.stdout):
            with self.subTest(option=case["option"], value=case["value"]):
                self.assertEqual(case["accepted"], case["expected"])
                if case["accepted"]:
                    config = self.uc('print(sprintf("%J", cfg));', {case["option"]: case["value"]})
                    self.assertEqual(config[case["option"]], int(case["value"]))
                    self.assertNotIn(case["option"], config["problems"])

    def test_traffic_rate_device_resolution_and_counter_reset(self):
        cfg = {"mode": "traffic", "brightness": "255", "sensitivity": "5", "traffic_interface": "lan"}
        resolved = self.uc('print(sprintf("%J", [rgb.traffic_device(cfg, n => n == "lan" ? "br-lan" : null),'
                           ' rgb.traffic_device(cfg, n => null), rgb.traffic_device(cfg, n => "bad;name")]));', cfg)
        self.assertEqual(resolved, ["br-lan", "lan", "lan"])
        stats = self.root / "sys/class/net/br-lan/statistics"
        stats.mkdir(parents=True)

        def counters(rx, tx):
            return (f'writefile({json.dumps(str(stats / "rx_bytes"))}, "{rx}\\n"); '
                    f'writefile({json.dumps(str(stats / "tx_bytes"))}, "{tx}\\n");')
        body = f'''import {{ writefile }} from 'fs';
            let ctx = rgb.create(root, "br-lan"), out = [];
            {counters(1000, 1000)} push(out, rgb.frame(ctx, cfg, 720, 0));
            {counters(1001000, 1000)} push(out, rgb.frame(ctx, cfg, 720, 1000));
            {counters(6001000, 1000)} push(out, rgb.frame(ctx, cfg, 720, 2000));
            {counters(5, 5)} push(out, rgb.frame(ctx, cfg, 720, 3000));
            print(sprintf("%J", out));'''
        first, low, full, reset = self.uc(body, cfg)
        self.assertEqual((first["fallback"], first["detail"]), (True, "sampling"))
        self.assertEqual((low["fallback"], low["detail"]), (False, "40%"))     # 1 MB/s of 2.5 MB/s
        self.assertEqual((full["detail"], full["rgb"]), ("100%", [255, 0, 180]))
        self.assertEqual((reset["fallback"], reset["detail"]), (True, "sampling"))
        f = self.frames(cfg, [0, 1000], device="missing0")[1]
        self.assertEqual((f["fallback"], f["detail"]), (True, "source unavailable"))

    def test_writes_only_changed_channels_and_retries_failures(self):
        red = self.root / "sys/class/leds/red:ambient/brightness"
        out = self.uc(f'''import {{ readfile, writefile }} from 'fs';
            let ctx = rgb.create(root, "wan"), out = [];
            push(out, rgb.show(ctx, [10, 20, 30]));
            writefile({json.dumps(str(red))}, "99\\n");
            push(out, rgb.show(ctx, [10, 20, 30]), trim(readfile({json.dumps(str(red))})));
            push(out, rgb.show(ctx, [11, 20, 30]), trim(readfile({json.dumps(str(red))})));
            print(sprintf("%J", out));''')
        self.assertEqual(out, [True, True, "99", True, "11"])
        red.chmod(0o444)
        if os.access(red, os.W_OK):
            self.skipTest("running as root; cannot simulate a write failure")
        out = self.uc(f'''import {{ chmod }} from 'fs';
            let ctx = rgb.create(root, "wan"), out = [];
            push(out, rgb.show(ctx, [5, 5, 5]), [...ctx.last]);
            chmod({json.dumps(str(red))}, 0o644);
            push(out, rgb.show(ctx, [5, 5, 5]), [...ctx.last]);
            print(sprintf("%J", out));''')
        self.assertEqual(out, [False, [-1, 5, 5], True, [5, 5, 5]])
        self.assertEqual(self.leds(), [5, 5, 5])

    def test_missing_led_is_reported_without_touching_others(self):
        shutil.rmtree(self.root / "sys/class/leds/green:ambient")
        missing = self.uc('print(sprintf("%J", rgb.create(root, "wan").missing));')
        self.assertEqual(missing, ["green:ambient"])


@unittest.skipUnless(UCODE, "ucode is required for the RGB service")
class Service(Fixture):
    def setUp(self):
        super().setUp()
        self.lib = Path(self.temporary.name) / "lib/rc01"
        self.lib.mkdir(parents=True)
        (self.lib / "rgb.uc").symlink_to(MODULE)
        self.config = Path(self.temporary.name) / "config.json"
        self.log = Path(self.temporary.name) / "stderr"

    def start(self, section, network=None):
        self.config.write_text(json.dumps({"main": section, "network": network or {}}))
        env = {"PATH": os.environ["PATH"], "RC01_ROOT": str(self.root), "RC01_RGB_CONFIG": str(self.config),
               "TZ": "UTC"}
        command = [UCODE, "-L", str(self.lib.parent) + "/*.uc", str(DAEMON)]
        process = subprocess.Popen(command, env=env, stderr=self.log.open("w"), stdout=subprocess.DEVNULL)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def status(self):
        try:
            return json.loads(self.read("var/run/rc01-rgb.status"))
        except (FileNotFoundError, ValueError):
            return None

    def stop(self, process, sig=signal.SIGTERM):
        process.send_signal(sig)
        return process.wait(timeout=5)

    def test_runs_claims_only_its_leds_and_turns_off_on_each_stop_signal(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(sig=sig):
                process = self.start({"enabled": "1", "mode": "static", "color": "#ff8000", "brightness": "255"})
                self.assertTrue(self.wait_for(lambda: self.leds() == [255, 128, 0]))
                self.assertTrue(self.wait_for(lambda: self.status() is not None))
                self.assertEqual(self.status()["mode"], "static")
                for led in RGB:
                    self.assertEqual(self.read(f"sys/class/leds/{led}/trigger"), "none")
                self.assertEqual(self.stop(process, sig), 0)
                self.assertEqual(self.leds(), [0, 0, 0])
                self.assertFalse((self.root / "var/run/rc01-rgb.status").exists())
                self.assert_foreign_untouched()

    def test_static_is_not_rewritten_every_tick(self):
        process = self.start({"enabled": "1", "color": "#00ff00", "brightness": "100"})
        green = self.root / "sys/class/leds/green:ambient/brightness"
        self.assertTrue(self.wait_for(lambda: self.leds() == [0, 100, 0]))
        stamp = green.stat().st_mtime_ns
        time.sleep(2.2)
        self.assertEqual(green.stat().st_mtime_ns, stamp)
        self.stop(process)

    def test_animation_runs_and_stops_cleanly(self):
        process = self.start({"enabled": "1", "mode": "rainbow", "speed": "10", "brightness": "255"})
        seen = set()
        self.wait_for(lambda: seen.add(tuple(self.leds())) or len(seen) > 5, timeout=3)
        self.assertGreater(len(seen), 5)
        self.assertIsNone(self.status()["rgb"])     # frames are not logged to the status file
        self.assertEqual(self.stop(process), 0)
        self.assertEqual(self.leds(), [0, 0, 0])
        self.assert_foreign_untouched()

    def test_restart_with_new_settings_and_schedule_off(self):
        process = self.start({"enabled": "1", "color": "#0000ff", "brightness": "200"})
        self.assertTrue(self.wait_for(lambda: self.leds() == [0, 0, 200]))
        self.stop(process)
        now = time.gmtime()
        later = lambda h: f"{(now.tm_hour + h) % 24:02d}:{now.tm_min:02d}"
        process = self.start({"enabled": "1", "color": "#0000ff", "brightness": "200",
                              "schedule_enabled": "1", "on_time": later(2), "off_time": later(3)})
        self.assertTrue(self.wait_for(lambda: (self.status() or {}).get("period") == "schedule-off"))
        self.assertEqual(self.leds(), [0, 0, 0])
        self.stop(process)

    def test_missing_source_falls_back_to_primary_colour(self):
        process = self.start({"enabled": "1", "mode": "temperature", "color": "#102030", "brightness": "255"})
        self.assertTrue(self.wait_for(lambda: (self.status() or {}).get("fallback") is True))
        self.assertEqual(self.leds(), [0x10, 0x20, 0x30])
        self.assertEqual(self.status()["detail"], "source unavailable")
        self.stop(process)

    def test_invalid_options_are_logged_and_shown(self):
        process = self.start({"enabled": "1", "mode": "disco", "brightness": "999"})
        self.assertTrue(self.wait_for(lambda: self.status() is not None))
        self.assertEqual(sorted(self.status()["ignored_options"]), ["brightness", "mode"])
        self.stop(process)
        self.assertIn("invalid option(s) replaced by defaults", self.log.read_text())

    def test_missing_led_exits_without_writing(self):
        shutil.rmtree(self.root / "sys/class/leds/blue:ambient")
        self.write("sys/class/leds/red:ambient/brightness", "7")
        process = self.start({"enabled": "1", "brightness": "255"})
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertEqual(self.read("sys/class/leds/red:ambient/brightness"), "7")
        self.assertIn("LED not found: blue:ambient", self.log.read_text())
        self.assert_foreign_untouched()

    def test_write_failure_is_reported_once_and_recovers(self):
        red = self.root / "sys/class/leds/red:ambient/brightness"
        red.chmod(0o444)
        if os.access(red, os.W_OK):
            self.skipTest("running as root; cannot simulate a write failure")
        process = self.start({"enabled": "1", "color": "#ffffff", "brightness": "50"})
        self.assertTrue(self.wait_for(lambda: (self.status() or {}).get("led_write_failed") is True))
        time.sleep(1.2)
        red.chmod(0o644)
        self.assertTrue(self.wait_for(lambda: self.leds() == [50, 50, 50]))
        self.assertTrue(self.wait_for(lambda: (self.status() or {}).get("led_write_failed") is False))
        self.stop(process)
        self.assertEqual(self.log.read_text().count("LED write failed"), 1)


# Stubs for rc.common, procd and the UCI helpers.
INIT_STUBS = r'''
uci() { [ "$*" = "-q get system.@system[-1].leds_off" ] && [ -n "$LEDS_OFF" ] && echo "$LEDS_OFF"; }
logger() { echo "logger $*"; }
config_load() { [ "$1" = rc01_rgb ]; }
config_get_bool() { eval "$1=\${CFG_$3-\$4}"; }
procd_open_instance() { echo open; }
procd_set_param() { echo "param $*"; }
procd_close_instance() { echo close; }
procd_add_reload_trigger() { echo "reload $*"; }
'''


class Init(Fixture):
    shell = SHELLS[0]

    def run_init(self, action, **values):
        env = {"PATH": os.environ["PATH"], "RC01_SYSFS": str(self.root / "sys"), **values}
        script = f'{INIT_STUBS}\n. "{INIT}"\n{action}'
        result = subprocess.run(SHELL_COMMANDS[self.shell] + ["-c", script],
                                env={k: v for k, v in env.items() if v is not None},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, f"{self.shell}: {result.stderr}")
        return result.stdout.splitlines()

    def use(self, shell):
        """Fresh fixture for one shell's run of a test body."""
        self.setUp()
        self.shell = shell

    def test_disabled_by_default_and_with_system_leds_off(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.use(shell)
                self.assertNotIn("open", self.run_init("start_service"))
                self.assertNotIn("open", self.run_init("start_service", CFG_enabled="1", LEDS_OFF="1"))

    def test_enabled_runs_one_restartable_instance(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.use(shell)
                out = self.run_init("start_service\nservice_triggers", CFG_enabled="1")
                self.assertEqual(out.count("open"), 1)
                self.assertIn("param command /usr/libexec/rc01-rgb", out)
                self.assertIn("param file /etc/config/rc01_rgb", out)     # config change restarts it
                self.assertIn("param respawn 3600 5 5", out)
                self.assertIn("reload rc01_rgb system", out)

    def test_missing_led_is_logged_and_not_started(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.use(shell)
                shutil.rmtree(self.root / "sys/class/leds/red:ambient")
                out = self.run_init("start_service", CFG_enabled="1")
                self.assertNotIn("open", out)
                self.assertTrue(any("red:ambient not found" in line for line in out))

    def test_stopped_turns_only_the_rgb_bar_off(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.use(shell)
                for led in RGB:
                    self.write(f"sys/class/leds/{led}/brightness", "200")
                self.run_init("service_stopped")
                self.assertEqual(self.leds(), [0, 0, 0])
                self.assert_foreign_untouched()
                shutil.rmtree(self.root / "sys/class/leds/green:ambient")
                self.run_init("service_stopped")          # a missing LED is not an error

    def test_shell_syntax(self):
        for shell in SHELLS:
            result = subprocess.run(SHELL_COMMANDS[shell] + ["-n", str(INIT)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{shell}: {result.stderr}")


class Package(unittest.TestCase):
    def test_package_installs_its_files_and_no_fan_control(self):
        makefile = (PKG / "Makefile").read_text()
        for source in re.findall(r"\./files/([\w.-]+)", makefile):
            self.assertTrue((FILES / source).is_file(), source)
        for line in ("$(INSTALL_BIN) ./files/rc01-rgb.init $(1)/etc/init.d/rc01-rgb",
                     "$(INSTALL_BIN) ./files/rc01-rgb $(1)/usr/libexec/rc01-rgb",
                     "$(INSTALL_DATA) ./files/rgb.uc $(1)/usr/share/ucode/rc01/rgb.uc",
                     "$(INSTALL_CONF) ./files/rc01_rgb.config $(1)/etc/config/rc01_rgb"):
            self.assertIn(line, makefile)
        self.assertRegex(makefile, r"/etc/config/rc01_rgb\nendef")    # conffile survives upgrades
        depends = re.search(r"DEPENDS:=(.*)", makefile).group(1).split()
        self.assertEqual(sorted(depends), sorted(["@TARGET_qualcommbe_ipq95xx", "+ucode", "+ucode-mod-fs", "+ucode-mod-uci"]))
        self.assertIn("7c3057979a9e1767fed5e12401ae7d1948223f16", makefile)
        self.assertIn("GPL-2.0-or-later", makefile)
        # The RGB code never refers to fan, hwmon or other LEDs.
        for path in FILES.iterdir():
            text = path.read_text()
            for word in ("hwmon", "pwm1", "fancontrol", "emc230", "rc01-fan", ":status", ":network", ":sfp"):
                self.assertNotIn(word, text, f"{path.name}: {word}")
        self.assertEqual(re.findall(r"'([a-z]+:ambient)'", MODULE.read_text()), RGB)

    def test_image_selects_the_packages(self):
        mk = (ROOT / "target/linux/qualcommbe/image/ipq95xx.mk").read_text()
        self.assertIn("DEVICE_PACKAGES += ipq-wifi-xiaomi_be10000 rc01-platform rc01-rgb", mk)
        defconfig = (ROOT / "defconfig/qualcommbe-rc01.config").read_text().splitlines()
        validator = (ROOT / "scripts/validate-rc01-config.sh").read_text()
        for symbol in ("rc01-rgb", "luci-app-rc01-rgb", "luci-i18n-rc01-rgb-zh-cn"):
            self.assertIn(f"CONFIG_PACKAGE_{symbol}=y", defconfig)
            self.assertIn(f"CONFIG_PACKAGE_{symbol} ", validator + " ")

    def test_daemon_is_executable_ucode(self):
        self.assertTrue(DAEMON.read_text().startswith("#!/usr/bin/ucode\n"))
        self.assertTrue(INIT.read_text().startswith("#!/bin/sh /etc/rc.common\n"))
        # After /etc/init.d/led (START=96) so a boot-time LED policy cannot override it.
        self.assertGreater(int(re.search(r"^START=(\d+)$", INIT.read_text(), re.M).group(1)), 96)


class Luci(unittest.TestCase):
    def test_acl_writes_only_the_rgb_config(self):
        acl = json.loads((LUCI / "root/usr/share/rpcd/acl.d/luci-app-rc01-rgb.json").read_text())
        self.assertEqual(list(acl), ["luci-app-rc01-rgb"])
        grant = acl["luci-app-rc01-rgb"]
        self.assertEqual(grant["write"], {"uci": ["rc01_rgb"]})
        self.assertEqual(grant["read"], {"file": {"/var/run/rc01-rgb.status": ["read"]},
                                         "ubus": {"rc": ["list"]}, "uci": ["rc01_rgb"]})
        # The existing read-only RC01 status RPC stays read-only.
        status_acl = json.loads((ROOT / "package/emortal/luci-app-rc01/root/usr/share/rpcd/acl.d/luci-app-rc01.json").read_text())
        self.assertNotIn("write", status_acl["luci-app-rc01"])

    def test_menu_points_at_the_view(self):
        menu = json.loads((LUCI / "root/usr/share/luci/menu.d/luci-app-rc01-rgb.json").read_text())
        entry = menu["admin/system/rc01-rgb"]
        self.assertEqual(entry["action"], {"type": "view", "path": "system/rc01-rgb"})
        self.assertEqual(entry["depends"]["acl"], ["luci-app-rc01-rgb"])
        self.assertTrue(VIEW.is_file())

    def test_view_options_match_the_service(self):
        view = VIEW.read_text()
        options = set(re.findall(r"s\.option\(\w+(?:\.\w+)?, '([a-z_]+)'", view)) - {"_status"}
        shipped = set(re.findall(r"^\s*option (\w+) ", (FILES / "rc01_rgb.config").read_text(), re.M))
        self.assertEqual(options, shipped)
        modes = re.findall(r"o\.value\('([a-z]+)'", view)
        service_modes = re.search(r"MODES = \[([^\]]*)\]", MODULE.read_text()).group(1)
        self.assertEqual(modes, re.findall(r"'([a-z]+)'", service_modes))
        self.assertNotRegex(view, r"callInit|rc', *method: 'init'|fs\.exec|fs\.write")

    def test_translations_cover_the_view(self):
        view = VIEW.read_text()
        used = set(re.findall(r"_\('((?:[^'\\]|\\.)*)'\)", view))
        po = (LUCI / "po/zh_Hans/rc01-rgb.po").read_text()
        ids = set(re.findall(r'^msgid "(.+)"$', po, re.M))
        self.assertEqual(used, ids)
        self.assertEqual(len(re.findall(r'^msgid ', po, re.M)), len(ids) + 1)

    @unittest.skipUnless(shutil.which("node"), "node is required for the syntax check")
    def test_view_syntax(self):
        # LuCI evaluates views as a function body.
        script = "new Function(require('fs').readFileSync(process.argv[1], 'utf8'))"
        result = subprocess.run(["node", "-e", script, str(VIEW)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class Dts(unittest.TestCase):
    PATCH = ROOT / "target/linux/generic/pending-6.18/830-pwm-ipq-fix-period-calculation.patch"

    def test_source_wiring(self):
        dts = (ROOT / "target/linux/qualcommbe/dts/ipq9570-be10000.dts").read_text()
        bar = re.search(r"rgb-leds \{(.*?)\n\t\};", dts, re.S).group(1)
        self.assertIn('compatible = "pwm-leds";', bar)
        found = re.findall(r"color = <LED_COLOR_ID_(\w+)>;\s*function = \"ambient\";\s*pwms = <&pwm (\d) 51000 0>;"
                           r"\s*max-brightness = <255>;\s*default-state = \"off\";", bar)
        self.assertEqual(found, [("RED", "3"), ("GREEN", "2"), ("BLUE", "1")])
        self.assertRegex(dts, r'pwm_pins: pwm-state \{\s*pins = "gpio31", "gpio32", "gpio33";\s*function = "pwm";')
        self.assertRegex(dts, r"&pwm \{\s*pinctrl-0 = <&pwm_pins>;\s*pinctrl-names = \"default\";\s*status = \"okay\";")
        # Unchanged neighbours: status/network/SFP LEDs stay on GPIO, fan on EMC2301.
        for line in ("gpios = <&tlmm 40 GPIO_ACTIVE_HIGH>;", "gpios = <&tlmm 62 GPIO_ACTIVE_HIGH>;",
                     "pwms = <&rc01_fan_controller 26000 0 1>;", "perst-gpios = <&tlmm 23 GPIO_ACTIVE_LOW>;"):
            self.assertIn(line, dts)

    def test_period_is_exact_with_fine_duty_on_the_patched_driver(self):
        """Model of the pinned pwm-ipq period search (830 patch) at 100 MHz."""
        patch = self.PATCH.read_text()
        for fragment in ("pre_div = div64_u64(period_rate,", "if (pre_div > pwm_div)",
                         "if (pwm_div > IPQ_PWM_MAX_DIV - 1)", "hi_dur = DIV64_U64_ROUND_CLOSEST"):
            self.assertIn(fragment, patch)
        clk, period, max_div = 100_000_000, 51000, 0xFFFF
        rate = period * clk
        best, best_diff = (max_div, max_div), rate
        pre = rate // (10**9 * (max_div + 1))
        while pre <= max_div:
            pwm, rem = divmod(rate, 10**9 * (pre + 1))
            pwm -= 1
            if pre > pwm:
                break
            if pwm <= max_div - 1 and rem < best_diff:
                best, best_diff = (pre, pwm), rem
                if rem == 0:
                    break
            pre += 1
        pre, pwm = best
        self.assertEqual(((pre + 1) * (pwm + 1) * 10**9) // clk, period)
        # leds-pwm duty = period * brightness / 255; every level maps to a distinct high time.
        highs = [min(round(period * b // 255 * clk / ((pre + 1) * 10**9)), pwm + 1) for b in range(256)]
        self.assertEqual(highs[0], 0)
        self.assertEqual(highs[255], pwm + 1)
        self.assertEqual(len(set(highs)), 256)


@unittest.skipUnless(KERNEL and shutil.which("dtc") and shutil.which("cpp"), "set RC01_KERNEL_SOURCE to a prepared kernel tree")
class Dtb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("rc01_platform_rgb", ROOT / "scripts/validate-rc01-platform.py")
        platform = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(platform)
        cls.temp = tempfile.TemporaryDirectory(prefix="rc01-rgb-dtb-")
        cls.path = Path(cls.temp.name) / "rc01.dtb"
        platform.compile_dtb(Path(KERNEL), cls.path)
        cls.source = subprocess.run(["dtc", "-I", "dtb", "-O", "dts", str(cls.path)],
                                    check=True, capture_output=True, text=True).stdout

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def recompile(self, source):
        result = subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-"], input=source.encode(),
                                check=True, capture_output=True)
        return result.stdout

    def test_final_dtb_wiring(self):
        result = images.verify_platform_dtb(self.path.read_bytes())
        self.assertIn("red ch3, green ch2, blue ch1", result["rgb"])
        images.verify_thermal_dtb(self.path.read_bytes())

    def test_checker_rejects_wrong_wiring(self):
        pwm = re.search(r"pwm@1941010 \{.*?\n\t\t\};", self.source, re.S).group(0)
        red = re.search(r"pwms = <(0x[0-9a-f]+) 0x03 0xc738 0x00>;", self.source).group(0)
        cases = {
            "pwm disabled": self.source.replace(pwm, pwm.replace('status = "okay"', 'status = "disabled"')),
            "red on ch1": self.source.replace(red, red.replace(" 0x03 ", " 0x01 ")),
            "lit at boot": re.sub(r'(function = "ambient";[^}]*default-state = )"off"', r'\1"keep"', self.source, count=1),
            "pin muxed twice": self.source.replace('pins = "gpio46", "gpio64";', 'pins = "gpio46", "gpio64", "gpio32";'),
            "pin used as GPIO": re.sub(r"(gpios = <0x[0-9a-f]+) 0x2a ", r"\1 0x1f ", self.source, count=1),
        }
        for name, source in cases.items():
            with self.subTest(name):
                self.assertNotEqual(source, self.source)
                with self.assertRaisesRegex(images.VerificationError, r"(?i)pwm|RGB|gpio31-33"):
                    images.verify_platform_dtb(self.recompile(source))


if __name__ == "__main__":
    unittest.main()
