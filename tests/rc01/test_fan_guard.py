"""Trace rc01-fan-guard protection I/O on fresh fixtures and check its status export.

The guard runs under BusyBox ash with PATH limited to a stub directory, so a
missing stub never falls back to a host tool. An LD_PRELOAD tracer records
sysfs reads and every PWM write together with the writing process, so the
order of protection writes, sleeps and log calls is checked directly rather
than inferred from the final PWM value (the EXIT trap always writes 255).
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = "package/emortal/rc01-platform/files/rc01-fan-guard"
GUARD = ROOT / GUARD_PATH
# Guard before the reason export (modules 01-05 integrated).
BASELINE = "a9ac7c6e3841b795e0b292232b308d72dea98929"
BUSYBOX = os.environ.get("BUSYBOX") or shutil.which("busybox")
CC = shutil.which("cc")
UCODE = os.environ.get("UCODE") or shutil.which("ucode")
PLATFORM = ROOT / "package/emortal/rc01-platform/files/platform.uc"
BOOT_ID = "0b3d6c1e-5a7f-4c2e-9d10-2f6a8b4c7e91"
START_TICKS = 4242

SLEEP_STUB = r'''#!@BUSYBOX@ sh
# Fixture sleep: log, optionally wait until the guard's last export has
# landed (fixture-side only), apply this tick's fixture change, advance the
# fixture uptime, and fail after the scenario's last tick to end the loop.
live_children() {
	local s line
	for s in /proc/[0-9]*/stat; do
		read -r line 2>/dev/null < "$s" || continue
		set -- ${line##*) }
		[ "$2" = "$PPID" ] && [ "$s" != "/proc/$$/stat" ] && [ "$1" != Z ] && return 0
	done
	return 1
}
read n 2>/dev/null < "$TICKS/count" || n=0
n=$((n + 1)); echo "$n" > "$TICKS/count"
echo "sleep $*" >> "$RC01_TRACE_LOG"
read up rest < "$RC01_PROC/uptime"; up=${up%%.*}
case "$up" in ''|*[!0-9]*) up= ;; esac
if [ -f "$TICKS/settle" ]; then
	i=0
	while [ $i -lt 300 ]; do
		if "$BUSYBOX" grep -q "\"updated_uptime\":$up.00," "$RC01_FAN_STATE" 2>/dev/null && ! live_children; then
			"$BUSYBOX" cp "$RC01_FAN_STATE" "$TICKS/snap.$n"; break
		fi
		"$BUSYBOX" usleep 10000; i=$((i + 1))
	done
fi
[ ! -f "$TICKS/$n.sh" ] || GUARD_PID=$PPID "$BUSYBOX" env -u LD_PRELOAD "$BUSYBOX" sh "$TICKS/$n.sh"
[ ! -f "$TICKS/$n.signal" ] || { read sig < "$TICKS/$n.signal"; kill -s "$sig" "$PPID"; }
[ -z "$up" ] || echo "$((up + ${1%%.*})).00 0" > "$RC01_PROC/uptime"
read last < "$TICKS/last"
[ "$n" -le "$last" ]
'''
LOGGER_STUB = '#!@BUSYBOX@ sh\necho "logger $*" >> "$RC01_TRACE_LOG"\n'
SLOW_MV = '#!@BUSYBOX@ sh\necho start >> "$TICKS/mv.log"\nread gate < "$TICKS/gate"\nexec "$BUSYBOX" mv "$@"\n'
FAILING_MV = '#!@BUSYBOX@ sh\necho start >> "$TICKS/mv.log"\nexit 1\n'
# Fixture changes, run by BusyBox sh without the tracer.
RPM0, RPM_OK = 'echo 0 > "$HW/fan1_input"', 'echo 960 > "$HW/fan1_input"'
READ_ONLY_PWM = '"$BUSYBOX" chmod 444 "$HW/pwm1"'


def temp(value):
    return f'echo {value} > "$TZ/temp"'


# name: initial fixture change, per-tick changes, successful ticks, signals.
# Tick 1 is the 3 s startup wait; later ticks are 2 s loop waits.
SCENARIOS = {
    "normal": dict(last=2),
    "startup-failure": dict(setup=RPM0, last=2),
    "one-two-failures-then-success-clears": dict(ticks={2: RPM0, 4: RPM_OK, 5: RPM0, 7: RPM_OK}, last=8),
    "third-failure-latches-and-recovery-keeps-it": dict(ticks={2: RPM0, 5: RPM_OK}, last=6),
    "over-temperature-boundary": dict(ticks={2: temp(74999), 3: temp(75000), 4: temp(45000)}, last=4),
    "startup-at-threshold-writes-floor-first": dict(setup=temp(75000), last=2),
    "hot-with-invalid-rpm-counts-failure": dict(ticks={2: temp(80000) + "; echo x > \"$HW/fan1_input\""}, last=3),
    "latched-startup-then-failures": dict(setup=RPM0, last=4),
    "controller-missing": dict(setup='"$BUSYBOX" rm -r "$RC01_SYSFS/class/hwmon/hwmon8"', last=2),
    "controller-ambiguous": dict(setup='"$BUSYBOX" mkdir -p "$RC01_SYSFS/class/hwmon/hwmon9" && echo emc2305 > '
                                 '"$RC01_SYSFS/class/hwmon/hwmon9/name" && "$BUSYBOX" ln -s "$RC01_SYSFS/devices/i2c-5/5-002f" '
                                 '"$RC01_SYSFS/class/hwmon/hwmon9/device"', last=2),
    "initial-pwm-write-fails": dict(setup=READ_ONLY_PWM, last=2),
    "startup-floor-write-fails": dict(ticks={1: READ_ONLY_PWM}, last=2),
    "latch-write-fails": dict(ticks={2: RPM0, 4: READ_ONLY_PWM}, last=6),
    "term-in-loop": dict(signals={3: "TERM"}, last=6),
    "int-during-startup": dict(signals={1: "INT"}, last=6),
    "term-after-latch": dict(ticks={2: RPM0}, signals={6: "TERM"}, last=8),
}
# First failing check of a sample, in the guard's order.
REASONS = [
    ("", "ok"),
    ('echo cpu-thermal > "$TZ/type"', "sensor-missing"),
    (temp("x"), "sensor-invalid"),
    ('"$BUSYBOX" rm "$TZ/temp"', "sensor-invalid"),
    (temp(999), "sensor-invalid"),
    (temp(130001), "sensor-invalid"),
    (temp(1000), "ok"),
    (temp(130000), "ok"),
    ('echo x > "$HW/fan1_input"; echo y > "$HW/pwm1"', "rpm-invalid"),
    ('"$BUSYBOX" rm "$HW/fan1_input"', "rpm-invalid"),
    ('echo y > "$HW/pwm1"; echo 0 > "$HW/fan1_input"', "pwm-invalid"),
    ('echo 76 > "$HW/pwm1"; echo 1 > "$HW/fan1_fault"', "pwm-out-of-range"),
    ('echo 256 > "$HW/pwm1"', "pwm-out-of-range"),
    ('echo 255 > "$HW/pwm1"', "ok"),
    ('echo 1 > "$HW/fan1_fault"; echo 0 > "$HW/fan1_input"', "fault"),
    ('echo bad > "$HW/fan1_fault"', "fault"),
    ('"$BUSYBOX" rm "$HW/fan1_fault"', "ok"),
    ('"$BUSYBOX" rm "$HW/fan1_fault"; echo 0 > "$HW/fan1_input"', "rpm-zero"),
    ('"$BUSYBOX" chmod 000 "$HW/fan1_fault"', "ok"),
    ('"$BUSYBOX" mkdir -p "$TZ/../thermal_zone9" && echo nss_top_thermal > "$TZ/../thermal_zone9/type" && '
     'echo 50000 > "$TZ/../thermal_zone9/temp"', "ok"),
    ('"$BUSYBOX" mkdir -p "$TZ/../thermal_zone9" && echo nss_top_thermal > "$TZ/../thermal_zone9/type" && '
     'echo 50 > "$TZ/../thermal_zone9/temp"', "sensor-invalid"),
]


@unittest.skipUnless(BUSYBOX and CC and os.geteuid() != 0, "BusyBox, a host C compiler and a non-root user are required")
class FanGuardTrace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="rc01-fan-trace-build-")
        cls.preload = Path(cls.build.name) / "trace.so"
        subprocess.run([CC, "-shared", "-fPIC", "-O2", "-o", str(cls.preload),
                        str(Path(__file__).with_name("fan_trace_preload.c")), "-ldl"], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-fan-guard-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.bin, self.ticks = self.work / "bin", self.work / "ticks"
        self.bin.mkdir(); self.ticks.mkdir(); (self.work / "run").mkdir()
        for tool in ("cat", "readlink", "rm"):
            (self.bin / tool).symlink_to(BUSYBOX)
        self.stub("sleep", SLEEP_STUB); self.stub("logger", LOGGER_STUB)
        self.mv("normal")
        hw = self.work / "sys/class/hwmon/hwmon8"
        for name, value in {"name": "emc2305", "pwm1": 77, "fan1_input": 960, "fan1_fault": 0}.items():
            self.write(hw / name, value)
        for bus in ("i2c-4/4-002f", "i2c-5/5-002f"):
            (self.work / "sys/devices" / bus).mkdir(parents=True)
        (hw / "device").symlink_to(self.work / "sys/devices/i2c-4/4-002f")
        self.write(self.work / "sys/class/thermal/thermal_zone7/type", "nss-top-thermal")
        self.write(self.work / "sys/class/thermal/thermal_zone7/temp", 45000)
        self.write(self.work / "proc/sys/kernel/random/boot_id", BOOT_ID)
        self.write(self.work / "proc/uptime", "100.00 0")
        self.state = self.work / "run/rc01-fan-guard.json"
        self.env = {"PATH": str(self.bin), "BUSYBOX": BUSYBOX, "TICKS": str(self.ticks),
                    "RC01_SYSFS": str(self.work / "sys"), "RC01_PROC": str(self.work / "proc"),
                    "RC01_FAN_STATE": str(self.state), "HW": str(hw),
                    "TZ": str(self.work / "sys/class/thermal/thermal_zone7"),
                    "RC01_TRACE_LOG": str(self.work / "trace.log"), "RC01_TRACE_SYS": str(self.work / "sys"),
                    "RC01_TRACE_PWM": str(hw / "pwm1")}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{value}\n")

    def stub(self, name, body):
        path = self.bin / name
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_text(body.replace("@BUSYBOX@", BUSYBOX))
        path.chmod(0o755)

    def mv(self, mode):
        if mode == "normal":
            if (self.bin / "mv").exists():
                (self.bin / "mv").unlink()
            (self.bin / "mv").symlink_to(BUSYBOX)
        else:
            self.stub("mv", SLOW_MV if mode == "slow" else FAILING_MV)
            if mode == "slow":
                os.mkfifo(self.ticks / "gate")

    def fixture(self, script):
        result = subprocess.run([BUSYBOX, "sh", "-c", script], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_guard(self, scenario, guard=GUARD, settle=True):
        """Run one scenario; return (exit code, ordered events)."""
        if scenario.get("setup"):
            self.fixture(scenario["setup"])
        for tick, script in scenario.get("ticks", {}).items():
            (self.ticks / f"{tick}.sh").write_text(script + "\n")
        for tick, signal in scenario.get("signals", {}).items():
            (self.ticks / f"{tick}.signal").write_text(signal + "\n")
        (self.ticks / "last").write_text(f"{scenario['last']}\n")
        if settle:
            (self.ticks / "settle").touch()
        stat_tail = " ".join(["0"] * 17) + f" {START_TICKS} 0 0"
        wrapper = (f'"$BUSYBOX" mkdir -p "$RC01_PROC/$$" && '
                   f'printf "%s (rc01-fan-guard) S 1 {stat_tail}\\n" "$$" > "$RC01_PROC/$$/stat"; . "$1"')
        process = subprocess.Popen([BUSYBOX, "sh", "-c", wrapper, "rc01-fan-guard", str(guard)],
                                   env=self.env | {"LD_PRELOAD": str(self.preload)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = process.communicate(timeout=30)
        self.guard_pid, self.stderr = process.pid, err
        events = []
        log = self.work / "trace.log"
        for line in (log.read_text().splitlines() if log.exists() else []):
            kind, _, rest = line.partition(" ")
            if kind == "read":
                events.append(("read", rest))
            elif kind == "pwm":
                pid, _, value = rest.partition(" ")
                events.append(("pwm" if int(pid) == process.pid else "pwm-by-other-process", value))
            elif kind == "pwm-open-failed":
                events.append(("pwm-write-failed" if int(rest) == process.pid else "pwm-by-other-process", None))
            elif kind == "sleep":
                events.append(("sleep", rest))
            elif kind == "logger":
                events.append(("logger", rest.rsplit("rc01-fan ", 1)[-1]))
        return process.returncode, events

    @staticmethod
    def actions(events):
        return [e for e in events if e[0] != "read"]

    def record(self, name=None, wait_exit=True):
        """The exported record (final, or a tick snapshot); waits for the last lossy publish."""
        path = self.ticks / name if name else self.state
        deadline = time.monotonic() + 5
        while wait_exit and not name and time.monotonic() < deadline:
            if path.exists() and '"phase":"exited"' in path.read_text():
                break
            time.sleep(0.02)
        return json.loads(path.read_text()) if path.exists() else None

    def collect_record(self, snapshot, uptime):
        target = self.work / "var/run/rc01-fan-guard.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(snapshot, target)
        self.write(self.work / "proc/uptime", f"{uptime:.2f} 0")
        script = (f"import {{ collect }} from {json.dumps(str(PLATFORM))};\n"
                  f"print(sprintf('%J', collect({json.dumps(str(self.work))}, null).fan.protection));\n")
        result = subprocess.run([UCODE, "-"], input=script, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # --- protection behaviour -------------------------------------------------

    def test_normal_startup_then_loop_with_sample_order(self):
        code, events = self.run_guard(SCENARIOS["normal"])
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(self.actions(events), [("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"),
                                                ("sleep", "2"), ("pwm", "255")])
        sample = [e[1] for e in events[events.index(("sleep", "3")) + 1:events.index(("pwm", "77"))]]
        self.assertEqual(sample, ["/class/thermal/thermal_zone7/type", "/class/thermal/thermal_zone7/temp",
                                  "/class/hwmon/hwmon8/fan1_input", "/class/hwmon/hwmon8/pwm1",
                                  "/class/hwmon/hwmon8/fan1_fault"])
        starting, startup, loop = (self.record(f"snap.{n}") for n in (1, 2, 3))
        self.assertEqual((starting["phase"], starting["event"], starting["sample_reason"], starting["sampled_uptime"]),
                         ("starting", "startup", "not-sampled", None))
        self.assertEqual((startup["phase"], startup["event"], startup["sampled_uptime"], startup["failures"],
                          startup["latched"]), ("monitoring", "startup", 103.0, 0, False))
        self.assertEqual((loop["event"], loop["sample_reason"], loop["sampled_uptime"]), ("sample", "ok", 105.0))
        final = self.record()
        self.assertEqual((final["phase"], final["event"], final["updated_uptime"], final["sampled_uptime"],
                          final["latch_reason"]), ("exited", "loop-ended", 107.0, 105.0, None))
        self.assertEqual((final["boot_id"], final["pid"], final["start_ticks"], final["schema"]),
                         (BOOT_ID, self.guard_pid, START_TICKS, 1))

    def test_startup_failure_latches_immediately_with_zero_failures(self):
        code, events = self.run_guard(SCENARIOS["startup-failure"])
        self.assertEqual(code, 0)
        self.assertEqual(self.actions(events), [
            ("pwm", "255"), ("sleep", "3"), ("logger", "Startup health check failed; full-speed floor latched"),
            ("sleep", "2"), ("sleep", "2"), ("pwm", "255")])
        startup = self.record("snap.2")
        self.assertEqual((startup["failures"], startup["latched"], startup["latch_reason"], startup["sample_reason"]),
                         (0, True, "startup:rpm-zero", "rpm-zero"))
        loop = self.record("snap.3")
        self.assertEqual((loop["failures"], loop["latch_reason"]), (1, "startup:rpm-zero"))

    def test_third_consecutive_failure_latches_and_recovery_keeps_latch(self):
        code, events = self.run_guard(SCENARIOS["third-failure-latches-and-recovery-keeps-it"])
        self.assertEqual(code, 0)
        self.assertEqual(self.actions(events), [
            ("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"), ("sleep", "2"), ("sleep", "2"),
            ("pwm", "255"), ("logger", "Fan or thermal readings failed; full-speed floor latched"),
            ("sleep", "2"), ("sleep", "2"), ("sleep", "2"), ("pwm", "255")])
        snaps = [self.record(f"snap.{n}") for n in (3, 4, 5, 6, 7)]
        self.assertEqual([(s["failures"], s["latched"], s["sample_reason"]) for s in snaps],
                         [(1, False, "rpm-zero"), (2, False, "rpm-zero"), (3, True, "rpm-zero"),
                          (0, True, "ok"), (0, True, "ok")])
        self.assertEqual({s["latch_reason"] for s in snaps[2:]}, {"consecutive-failures:rpm-zero"})

    def test_success_clears_count_before_third_failure(self):
        code, events = self.run_guard(SCENARIOS["one-two-failures-then-success-clears"])
        self.assertEqual(code, 0)
        self.assertEqual([e for e in self.actions(events) if e[0] != "sleep"], [("pwm", "255"), ("pwm", "77"), ("pwm", "255")])
        self.assertEqual([self.record(f"snap.{n}")["failures"] for n in range(3, 9)], [1, 2, 0, 1, 2, 0])
        self.assertFalse(self.record()["latched"])

    def test_over_temperature_boundary_latches_on_valid_sample_only(self):
        code, events = self.run_guard(SCENARIOS["over-temperature-boundary"])
        self.assertEqual(self.actions(events), [
            ("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"), ("sleep", "2"), ("pwm", "255"),
            ("logger", "Temperature exceeds 75 C; full-speed floor latched"), ("sleep", "2"), ("sleep", "2"),
            ("pwm", "255")])
        self.assertEqual(self.record("snap.4")["latch_reason"], "over-temperature")
        self.assertEqual(self.record("snap.5")["latched"], True)
        code, events = self.run_guard_fresh(SCENARIOS["startup-at-threshold-writes-floor-first"])
        self.assertEqual(self.actions(events)[:5], [("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"),
                                                    ("pwm", "255")])
        code, events = self.run_guard_fresh(SCENARIOS["hot-with-invalid-rpm-counts-failure"])
        self.assertEqual([e for e in self.actions(events) if e[0] == "pwm"], [("pwm", "255"), ("pwm", "77"), ("pwm", "255")])
        self.assertEqual((self.record("snap.3")["failures"], self.record("snap.3")["sample_reason"]), (1, "rpm-invalid"))

    def run_guard_fresh(self, scenario):
        self.temporary.cleanup()
        self.setUp()
        return self.run_guard(scenario)

    def test_first_failing_check_is_the_reported_reason(self):
        for script, reason in REASONS:
            with self.subTest(fixture=script):
                # Applied during the startup wait: the guard writes 255 to pwm1 first.
                self.run_guard_fresh(dict(ticks={1: script} if script else {}, last=1))
                startup = self.record("snap.2")
                self.assertEqual(startup["sample_reason"], reason)
                self.assertEqual(startup["latch_reason"], None if reason == "ok" else f"startup:{reason}")

    def test_discovery_failure_never_touches_pwm(self):
        for name in ("controller-missing", "controller-ambiguous"):
            with self.subTest(name=name):
                code, events = self.run_guard_fresh(SCENARIOS[name])
                self.assertEqual(code, 1)
                self.assertEqual(self.actions(events), [("logger", "EMC2301 hwmon device unavailable or ambiguous")])
                final = self.record()
                self.assertEqual((final["event"], final["sampled_uptime"], final["latched"]),
                                 ("controller-unavailable-or-ambiguous", None, False))

    def test_pwm_write_failures_return_and_keep_exit_attempt(self):
        code, events = self.run_guard_fresh(SCENARIOS["initial-pwm-write-fails"])
        self.assertEqual((code, self.actions(events)), (1, [("pwm-write-failed", None), ("pwm-write-failed", None)]))
        self.assertEqual(self.record()["event"], "pwm-write-failed")
        code, events = self.run_guard_fresh(SCENARIOS["startup-floor-write-fails"])
        self.assertEqual((code, self.actions(events)), (1, [("pwm", "255"), ("sleep", "3"), ("pwm-write-failed", None),
                                                             ("pwm-write-failed", None)]))
        code, events = self.run_guard_fresh(SCENARIOS["latch-write-fails"])
        self.assertEqual(code, 1)
        self.assertEqual(self.actions(events)[-3:], [("sleep", "2"), ("pwm-write-failed", None), ("pwm-write-failed", None)])
        final = self.record()
        # The software latch stays set although the full-speed write failed.
        self.assertEqual((final["event"], final["failures"], final["latched"], final["latch_reason"]),
                         ("pwm-write-failed", 3, True, "consecutive-failures:rpm-zero"))

    def test_signals_run_both_protection_writes(self):
        code, events = self.run_guard_fresh(SCENARIOS["term-in-loop"])
        self.assertEqual((code, self.actions(events)), (0, [("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"),
                                                             ("sleep", "2"), ("pwm", "255"), ("pwm", "255")]))
        final = self.record()
        self.assertEqual((final["event"], final["sample_reason"], final["sampled_uptime"]), ("signal-stop", "ok", 105.0))
        code, events = self.run_guard_fresh(SCENARIOS["int-during-startup"])
        self.assertEqual((code, self.actions(events)), (0, [("pwm", "255"), ("sleep", "3"), ("pwm", "255"), ("pwm", "255")]))
        self.assertEqual((self.record()["event"], self.record()["sampled_uptime"]), ("signal-stop", None))

    @unittest.skipUnless(UCODE, "ucode is required for the real status collector")
    def test_pwm_failure_sample_time_matches_the_new_result_and_consumer(self):
        cases = [
            ("initial-write", SCENARIOS["initial-pwm-write-fails"], None, "not-sampled", 0, False),
            ("startup-floor", SCENARIOS["startup-floor-write-fails"], 103.0, "ok", 0, False),
            ("three-failures", SCENARIOS["latch-write-fails"], 109.0, "rpm-zero", 3, True),
            ("hot-write", dict(ticks={2: temp(75000) + "; " + READ_ONLY_PWM}, last=3), 105.0, "ok", 0, True),
        ]
        for name, scenario, sampled, reason, failures, latched in cases:
            with self.subTest(case=name):
                code, events = self.run_guard_fresh(scenario)
                self.assertEqual(code, 1)
                self.assertEqual(self.actions(events)[-1], ("pwm-write-failed", None))
                final = self.record()
                self.assertEqual((final["phase"], final["event"], final["sampled_uptime"],
                                  final["sample_reason"], final["failures"], final["latched"]),
                                 ("exited", "pwm-write-failed", sampled, reason, failures, latched))
                now = final["updated_uptime"] + 5
                state = self.collect_record(self.state, now)
                self.assertEqual((state["status"], state["sample_age"], state["sample_reason"],
                                  state["failures"], state["latched"]),
                                 ("exited", None if sampled is None else now - sampled, reason, failures, latched))

    @unittest.skipUnless(UCODE, "ucode is required for the real status collector")
    def test_failed_sample_clock_does_not_reuse_or_invent_a_time(self):
        script = r'''
        . "$1"
        failures=3 latched=1
        RC01_FAN_BOOT="$2" RC01_FAN_TICKS=4242
        RC01_FAN_SAMPLED=107 RC01_FAN_SAMPLE_PENDING=1
        RC01_FAN_REASON=rpm-zero RC01_FAN_LATCH=consecutive-failures:rpm-zero
        echo broken > "$RC01_PROC/uptime"
        rc01_fan_report monitoring 1
        [ -z "$RC01_FAN_SAMPLED" ] || exit 42
        [ "$RC01_FAN_SAMPLE_PENDING" = 0 ] || exit 43
        echo '111.00 0' > "$RC01_PROC/uptime"
        RC01_FAN_EVENT=pwm-write-failed
        rc01_fan_report exited
        wait "$RC01_FAN_PUBLISHER"
        '''
        result = subprocess.run([BUSYBOX, "sh", "-c", script, "x", str(GUARD), BOOT_ID],
                                env=self.env | {"RC01_FAN_LIBRARY": "1"}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        final = self.record()
        self.assertIsNone(final["sampled_uptime"])
        self.assertEqual((final["updated_uptime"], final["sample_reason"], final["failures"]), (111, "rpm-zero", 3))
        state = self.collect_record(self.state, 115)
        self.assertEqual((state["status"], state["sample_age"], state["sample_reason"]), ("exited", None, "rpm-zero"))

    def test_protection_trace_matches_baseline_guard(self):
        baseline = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASELINE}:{GUARD_PATH}"], capture_output=True)
        if baseline.returncode:
            self.skipTest("baseline guard is not available from Git")
        with tempfile.TemporaryDirectory(prefix="rc01-fan-baseline-") as directory:
            old = Path(directory) / "rc01-fan-guard"
            old.write_bytes(baseline.stdout)
            cases = [(name, s) for name, s in SCENARIOS.items()] + [
                (f"reason:{script}", dict(ticks={1: script} if script else {}, last=4)) for script, _ in REASONS]
            for name, scenario in cases:
                with self.subTest(name=name):
                    self.temporary.cleanup(); self.setUp()
                    expected = self.run_guard(scenario, guard=old, settle=False)
                    self.temporary.cleanup(); self.setUp()
                    self.assertEqual(self.run_guard(scenario, settle=False), expected)

    @unittest.skipUnless(UCODE, "ucode is required for the real status collector")
    def test_guard_records_are_read_by_the_status_collector(self):
        self.run_guard(SCENARIOS["third-failure-latches-and-recovery-keeps-it"])
        pid = str(self.guard_pid)
        self.write(self.work / f"proc/{pid}/comm", "rc01-fan-guard")
        self.write(self.work / f"proc/{pid}/status", "Name:\trc01-fan-guard\nPPid:\t1\n")

        snapshot = self.ticks / "snap.5"
        updated = json.loads(snapshot.read_text())["updated_uptime"]
        current = self.collect_record(snapshot, updated + 2)
        self.assertEqual((current["status"], current["sample_age"], current["failures"], current["latched"],
                          current["latch_reason"]), ("current", 2, 3, True, "consecutive-failures:rpm-zero"))
        self.assertEqual(self.collect_record(snapshot, updated + 11)["detail"], "expired")
        final = self.collect_record(self.state, updated + 20)
        self.assertEqual((final["status"], final["event"], final["failures"]), ("exited", "loop-ended", 0))

    # --- export isolation -----------------------------------------------------

    def test_slow_export_is_neither_waited_for_nor_queued(self):
        self.mv("slow")
        code, events = self.run_guard(SCENARIOS["third-failure-latches-and-recovery-keeps-it"], settle=False)
        self.assertEqual(code, 0)
        self.assertEqual(self.actions(events), self.expected_actions("third-failure-latches-and-recovery-keeps-it"))
        # Only the first publish started; every later one was dropped while it hung.
        self.assertEqual((self.ticks / "mv.log").read_text().split(), ["start"])
        self.assertFalse(self.state.exists())
        with open(self.ticks / "gate", "w") as gate:
            gate.write("go\n")
        deadline = time.monotonic() + 5
        while not self.state.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.record(wait_exit=False)["phase"], "starting")
        self.assertEqual(sorted(p.name for p in self.state.parent.iterdir()), [self.state.name])

    def test_publisher_does_not_inherit_protection_traps(self):
        self.mv("slow")
        # While the first publisher hangs, signal every other child of the guard.
        kill = ('for s in /proc/[0-9]*/stat; do read -r l < "$s" || continue; set -- ${l##*) }; '
                'p=${s#/proc/}; p=${p%/stat}; [ "$2" = "$GUARD_PID" ] && [ "$p" != "$PPID" ] && '
                '{ kill -TERM "$p"; kill -INT "$p"; echo "$p" >> "$TICKS/killed"; }; done 2>/dev/null; :')
        scenario = dict(SCENARIOS["normal"], ticks={1: kill})
        code, events = self.run_guard(scenario, settle=False)
        self.assertTrue((self.ticks / "killed").exists())
        self.assertEqual((code, self.actions(events)), (0, self.expected_actions("normal")))
        with open(self.ticks / "gate", "w") as gate:
            gate.write("go\n")

    def test_failed_or_impossible_export_keeps_protection(self):
        variants = {
            "state-directory-missing": lambda: self.env.update(RC01_FAN_STATE=str(self.work / "missing/state.json")),
            "rename-fails": lambda: self.mv("fail"),
            "no-boot-id": lambda: (self.work / "proc/sys/kernel/random/boot_id").unlink(),
            "no-uptime": lambda: (self.work / "proc/uptime").write_text("garbage\n"),
        }
        for name, prepare in variants.items():
            for scenario in ("third-failure-latches-and-recovery-keeps-it", "term-after-latch", "latch-write-fails"):
                with self.subTest(variant=name, scenario=scenario):
                    self.temporary.cleanup(); self.setUp()
                    prepare()
                    result = self.run_guard(SCENARIOS[scenario], settle=False)
                    self.assertEqual((result[0], self.actions(result[1])),
                                     (self.expected_code(scenario), self.expected_actions(scenario)))
                    # The last publish may still be cleaning up after the guard exits.
                    deadline = time.monotonic() + 5
                    while any(self.work.glob("**/rc01-fan-guard.json.*")) and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertFalse(any(self.work.glob("**/rc01-fan-guard.json.*")))
                    self.assertFalse(self.state.exists())

    def expected_actions(self, name):
        return {
            "normal": [("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"), ("sleep", "2"), ("pwm", "255")],
            "third-failure-latches-and-recovery-keeps-it": [
                ("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"), ("sleep", "2"), ("sleep", "2"),
                ("pwm", "255"), ("logger", "Fan or thermal readings failed; full-speed floor latched"),
                ("sleep", "2"), ("sleep", "2"), ("sleep", "2"), ("pwm", "255")],
            "term-after-latch": [
                ("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"), ("sleep", "2"), ("sleep", "2"),
                ("pwm", "255"), ("logger", "Fan or thermal readings failed; full-speed floor latched"),
                ("sleep", "2"), ("sleep", "2"), ("pwm", "255"), ("pwm", "255")],
            "latch-write-fails": [
                ("pwm", "255"), ("sleep", "3"), ("pwm", "77"), ("sleep", "2"), ("sleep", "2"), ("sleep", "2"),
                ("pwm-write-failed", None), ("pwm-write-failed", None)],
        }[name]

    @staticmethod
    def expected_code(name):
        return 1 if name == "latch-write-fails" else 0

    def test_repeated_publishing_does_not_grow_the_shell(self):
        # A non-interactive ash keeps finished background jobs until they are
        # waited for; one publish every 2 s must not accumulate for days.
        script = r'''
        vm() { while read -r k v _; do [ "$k" != VmData: ] || echo "$v"; done < /proc/$$/status; }
        . "$1"; rc01_fan_identity; failures=0 latched=0
        published=0 i=0
        while [ $i -lt 5000 ]; do
            previous=$RC01_FAN_PUBLISHER; rc01_fan_report monitoring 1
            [ "$RC01_FAN_PUBLISHER" = "$previous" ] || published=$((published + 1))
            cat /dev/null; i=$((i + 1))
            [ $i -ne 1000 ] || early=$(vm)
        done
        echo "$published $early $(vm)"
        '''
        wrapper = (f'"$BUSYBOX" mkdir -p "$RC01_PROC/$$" && printf "%s (sh) S 1 {" ".join(["0"] * 17)} 1 0\\n" "$$" '
                   f'> "$RC01_PROC/$$/stat"; ' + script)
        result = subprocess.run([BUSYBOX, "sh", "-c", wrapper, "sh", str(GUARD)],
                                env=self.env | {"RC01_FAN_LIBRARY": "1"}, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        published, early, late = map(int, result.stdout.split())
        self.assertGreater(published, 2000)
        # About 44 bytes per unreleased job would add well over 100 kB here.
        self.assertLess(late - early, 48, (published, early, late))

    def test_stub_path_has_no_host_fallback(self):
        (self.bin / "logger").unlink()
        result = subprocess.run([BUSYBOX, "sh", "-c", "command -v logger || echo missing"], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "missing")
