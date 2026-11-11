"""SFP presence/link LED service and its init script, against a fake LED class directory.

The fake trigger file behaves like the kernel's: writing a trigger name
selects it (shown in brackets), and only netdev exposes its attributes.
Nothing here touches /sys or the host syslog.
"""
from pathlib import Path
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "package/emortal/rc01-platform/files"
SERVICE = FILES / "rc01-sfp-led"
INIT = FILES / "rc01-sfp-led.init"
SHELLS = [s for s in ("dash", "busybox") if shutil.which(s)]
PRESENT = "sfp-sfp:present"

# Emulates the kernel trigger file and logs every trigger write.
FAKE_KERNEL = r'''
logger() { printf '%s\n' "$*" >> "$WORK/syslog"; }
rc01_sfp_set_trigger() {
	local t out=
	case " $(cat "$WORK/available") " in *" $1 "*) ;; *) return 1 ;; esac
	for t in $(cat "$WORK/available"); do
		[ "$t" = "$1" ] && t="[$t]"
		out="$out $t"
	done
	printf '%s\n' "${out# }" > "$RC01_SFP_LED/trigger"
	rm -f "$RC01_SFP_LED/device_name" "$RC01_SFP_LED/link" "$RC01_SFP_LED/tx" "$RC01_SFP_LED/rx" "$RC01_SFP_LED/interval"
	if [ "$1" = netdev ]; then
		: > "$RC01_SFP_LED/device_name"
		for t in link tx rx interval; do echo 0 > "$RC01_SFP_LED/$t"; done
	fi
	printf '%s\n' "$1" >> "$WORK/writes"
}
'''


class SfpLed(unittest.TestCase):
    shell = SHELLS[0] if SHELLS else "sh"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-sfp-led-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.led = self.work / "sys/class/leds/green:sfp"
        self.led.mkdir(parents=True)
        self.available("none timer netdev sfp-sfp:los " + PRESENT)
        # The state /etc/init.d/led leaves: netdev on sfp, link tx rx.
        self.kernel_select("netdev")
        (self.led / "device_name").write_text("sfp\n")
        for mode in ("link", "tx", "rx"):
            (self.led / mode).write_text("1\n")
        (self.led / "interval").write_text("50\n")
        self.carrier(0)
        self.env = {"PATH": os.environ["PATH"], "WORK": str(self.work), "RC01_SYSFS": str(self.work / "sys"),
                    "RC01_SFP_LIBRARY": "1", "RC01_SFP_INTERVAL": "0.1"}

    def available(self, triggers):
        (self.work / "available").write_text(triggers + "\n")

    def kernel_select(self, trigger):
        words = (self.work / "available").read_text().split()
        (self.led / "trigger").write_text(" ".join(f"[{w}]" if w == trigger else w for w in words) + "\n")

    def carrier(self, value):
        path = self.work / "sys/class/net/sfp/carrier"
        path.parent.mkdir(parents=True, exist_ok=True)
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(f"{value}\n")

    def selected(self):
        return re.search(r"\[([^]]+)\]", (self.led / "trigger").read_text()).group(1)

    def writes(self):
        path = self.work / "writes"
        return path.read_text().split() if path.exists() else []

    def run_shell(self, body, shell=None):
        command = [shutil.which(shell or self.shell)] + (["sh"] if (shell or self.shell) == "busybox" else [])
        script = f'. "{SERVICE}"\n{FAKE_KERNEL}\nset -f\nrc01_sfp_init green:sfp sfp 50 link tx rx\n{body}'
        result = subprocess.run(command + ["-c", script], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def step(self):
        self.run_shell("rc01_sfp_step")

    def assert_netdev_policy(self):
        self.assertEqual(self.selected(), "netdev")
        self.assertEqual((self.led / "device_name").read_text().strip(), "sfp")
        self.assertEqual([(self.led / m).read_text().strip() for m in ("link", "tx", "rx", "interval")], ["1", "1", "1", "50"])

    def test_no_carrier_hands_the_led_to_the_kernel_present_trigger(self):
        # The present trigger is off with no module and on with one; both
        # "not detected" and "detected, no link" therefore map to it.
        self.step()
        self.assertEqual(self.selected(), PRESENT)
        self.step()
        self.assertEqual(self.writes(), [PRESENT])

    def test_carrier_switches_to_netdev_and_back(self):
        self.step()
        self.carrier(1)
        self.step()
        self.assert_netdev_policy()
        self.step()
        self.carrier(0)
        self.step()
        self.assertEqual(self.selected(), PRESENT)
        self.assertEqual(self.writes(), [PRESENT, "netdev", PRESENT])

    def test_link_keeps_the_existing_netdev_policy_untouched(self):
        self.carrier(1)
        self.step()
        self.assertEqual(self.writes(), [])
        self.assert_netdev_policy()

    def test_unreadable_carrier_is_treated_as_no_link(self):
        self.carrier(None)
        self.step()
        self.assertEqual(self.selected(), PRESENT)
        (self.work / "sys/class/net/sfp/carrier").write_text("")
        self.step()
        self.assertEqual(self.selected(), PRESENT)

    def test_without_present_trigger_the_link_led_stays(self):
        for triggers in ("none timer netdev", f"none netdev {PRESENT} sfp-sfp2:present"):
            with self.subTest(triggers=triggers):
                self.setUp()
                self.available(triggers)
                self.kernel_select("netdev")
                self.step()
                self.assertEqual(self.writes(), [])
                self.assert_netdev_policy()

    def test_present_trigger_lost_falls_back_to_netdev(self):
        self.step()
        self.assertEqual(self.selected(), PRESENT)
        # e.g. sfp module unloaded: the kernel drops the trigger (LED reads "none").
        self.available("none timer netdev")
        self.kernel_select("none")
        self.step()
        self.assertEqual(self.writes(), [PRESENT])
        self.assertEqual(self.selected(), "none")

    def test_user_trigger_is_left_alone_until_the_policy_returns(self):
        self.step()
        self.kernel_select("timer")
        for _ in range(3):
            self.step()
        self.assertEqual(self.selected(), "timer")
        self.assertEqual(self.writes(), [PRESENT])
        self.assertEqual(len((self.work / "syslog").read_text().splitlines()), 3)

    def test_yield_is_logged_once_per_process(self):
        self.step()
        self.kernel_select("timer")
        self.run_shell("rc01_sfp_step; rc01_sfp_step; rc01_sfp_step")
        self.assertEqual(len((self.work / "syslog").read_text().splitlines()), 1)

    def test_user_netdev_on_another_device_is_left_alone(self):
        (self.led / "device_name").write_text("lan1\n")
        self.step()
        self.assertEqual(self.writes(), [])
        self.assertEqual((self.led / "device_name").read_text().strip(), "lan1")

    def test_restore_puts_back_netdev_only_from_its_own_trigger(self):
        self.step()
        self.run_shell("rc01_sfp_restore")
        self.assert_netdev_policy()
        self.assertEqual(self.writes(), [PRESENT, "netdev"])
        self.kernel_select("timer")
        self.run_shell("rc01_sfp_restore")
        self.assertEqual(self.selected(), "timer")
        self.kernel_select("netdev")
        self.run_shell("rc01_sfp_restore")
        self.assertEqual(self.writes(), [PRESENT, "netdev"])

    def service(self, shell):
        command = [shutil.which(shell)] + (["sh"] if shell == "busybox" else [])
        script = f'. "{SERVICE}"\n{FAKE_KERNEL}\nrc01_sfp_main green:sfp sfp 50 link tx rx'
        return subprocess.Popen(command + ["-c", script], env=self.env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return False

    def test_service_lifecycle_follows_state_and_restores_on_stop(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.setUp()
                process = self.service(shell)
                try:
                    self.assertTrue(self.wait_for(lambda: self.selected() == PRESENT))
                    self.carrier(1)
                    self.assertTrue(self.wait_for(lambda: self.selected() == "netdev"))
                    self.carrier(0)
                    self.assertTrue(self.wait_for(lambda: self.selected() == PRESENT))
                    process.send_signal(signal.SIGTERM)
                    self.assertEqual(process.wait(timeout=5), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                self.assert_netdev_policy()
                # A restart picks up the current state again.
                process = self.service(shell)
                try:
                    self.assertTrue(self.wait_for(lambda: self.selected() == PRESENT))
                    self.kernel_select("timer")
                    time.sleep(0.4)
                    process.send_signal(signal.SIGTERM)
                    self.assertEqual(process.wait(timeout=5), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                # Stopping while the user owns the LED does not take it back.
                self.assertEqual(self.selected(), "timer")
                self.assertEqual(self.writes(), [PRESENT, "netdev", PRESENT, "netdev", PRESENT])

    def test_missing_led_exits_with_an_error(self):
        shutil.rmtree(self.led)
        command = [shutil.which(self.shell)] + (["sh"] if self.shell == "busybox" else [])
        script = f'. "{SERVICE}"\n{FAKE_KERNEL}\nrc01_sfp_main green:sfp sfp 50 link tx rx'
        result = subprocess.run(command + ["-c", script], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.led.exists())


# Stubs for rc.common, procd and the UCI config helpers.
INIT_STUBS = r'''
board_name() { echo "$BOARD"; }
uci() { [ "$*" = "-q get system.@system[-1].leds_off" ] && [ -n "$LEDS_OFF" ] && echo "$LEDS_OFF"; }
logger() { echo "logger $*"; }
config_load() { [ "$1" = system ]; }
config_get() { eval "$1=\${CFG_$3-\$4}"; }
config_get_bool() { config_get "$@"; }
procd_open_instance() { echo open; }
procd_set_param() { echo "param $*"; }
procd_close_instance() { echo close; }
procd_add_reload_trigger() { echo "reload $*"; }
'''


class SfpLedInit(unittest.TestCase):
    def start(self, **values):
        env = {"PATH": os.environ["PATH"], "BOARD": "xiaomi,be10000", "CFG_sysfs": "green:sfp",
               "CFG_trigger": "netdev", "CFG_dev": "sfp", "CFG_mode": "link tx rx", **values}
        script = f'{INIT_STUBS}\n. "{INIT}"\nstart_service\nservice_triggers'
        result = subprocess.run(["sh", "-c", script], env={k: v for k, v in env.items() if v is not None},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_disabled_by_default(self):
        self.assertNotIn("open", self.start())

    def test_enabled_runs_one_instance_with_the_led_policy(self):
        for board in ("xiaomi,be10000", "xiaomi,be10000-es"):
            out = self.start(BOARD=board, CFG_rc01_presence="1")
            self.assertEqual(out.count("open"), 1)
            self.assertIn("param command /usr/libexec/rc01-sfp-led green:sfp sfp 50 link tx rx", out)
            self.assertIn("reload system", out)
        out = self.start(CFG_rc01_presence="1", CFG_interval="100", CFG_mode="link")
        self.assertIn("param command /usr/libexec/rc01-sfp-led green:sfp sfp 100 link", out)

    def test_not_started_where_it_would_override_a_choice(self):
        cases = [dict(CFG_trigger="timer"), dict(CFG_trigger=None), dict(CFG_dev=None), dict(CFG_sysfs=None),
                 dict(LEDS_OFF="1"), dict(BOARD="xiaomi,be7000")]
        for case in cases:
            with self.subTest(case=case):
                self.assertNotIn("open", self.start(CFG_rc01_presence="1", **case))


class SfpLedPackage(unittest.TestCase):
    def test_package_installs_the_service(self):
        makefile = (ROOT / "package/emortal/rc01-platform/Makefile").read_text()
        self.assertIn("$(INSTALL_BIN) ./files/rc01-sfp-led.init $(1)/etc/init.d/rc01-sfp-led", makefile)
        self.assertIn("$(INSTALL_BIN) ./files/rc01-sfp-led $(1)/usr/libexec/rc01-sfp-led", makefile)
        for source in re.findall(r"\./files/([\w.-]+)", makefile):
            self.assertTrue((FILES / source).is_file(), source)
        self.assertTrue(INIT.read_text().startswith("#!/bin/sh /etc/rc.common\n"))
        # Must start after /etc/init.d/led (START=96) applied the netdev policy.
        self.assertEqual(re.search(r"^START=(\d+)$", INIT.read_text(), re.M).group(1), "97")

    def test_default_led_policy_is_unchanged(self):
        leds = (ROOT / "target/linux/qualcommbe/ipq95xx/base-files/etc/board.d/01_leds").read_text()
        self.assertIn('ucidef_set_led_netdev "sfp" "SFP+ port" "green:sfp" "sfp" "link tx rx"', leds)

    def test_shell_syntax(self):
        for shell in SHELLS or ["sh"]:
            command = [shutil.which(shell)] + (["sh"] if shell == "busybox" else [])
            for path in (SERVICE, INIT):
                result = subprocess.run(command + ["-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{shell} {path}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
