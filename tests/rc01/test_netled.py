"""Egress connectivity LED service, init script and package, without a router.

The daemon runs from its real file under dash and, when available, BusyBox
ash (set BUSYBOX=/path/to/busybox; its timeout/sleep/mv/... applets are then
used too). ip, ping, ubus, jsonfilter and logger are stubs in a private
PATH: nothing sends ICMP, reads the host routing table or writes syslog.
LEDs are a fake class directory whose trigger file behaves like the kernel's.
"""
from pathlib import Path
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "package/emortal/rc01-platform/files"
DAEMON = FILES / "rc01-netled"
INIT = FILES / "rc01-netled.init"
CONFIG = FILES / "rc01_netled.config"
BUSYBOX = os.environ.get("BUSYBOX") or shutil.which("busybox")
SHELLS = [s for s in ("dash", "busybox") if (BUSYBOX if s == "busybox" else shutil.which(s))]
APPLETS = ("timeout", "sleep", "mv", "rm", "mkdir", "cat")

STUBS = {
    "ip": r'''#!/bin/sh
echo "ip $*" >> "$WORK/calls"
# ip -4|-6 route get TARGET [oif DEV]
key="$4"
[ "$5" = oif ] && key="$4@$6"
f="$WORK/routes/$key"
if [ -f "$f" ]; then
	cat "$f"
	[ -f "$f.rc" ] && exit "$(cat "$f.rc")"
	[ -f "$f.hang" ] && exec sleep 60
	exit 0
fi
echo "RTNETLINK answers: Network is unreachable" >&2
exit 2
''',
    "ping": r'''#!/bin/sh
echo "ping $*" >> "$WORK/calls"
for target; do :; done
mode=fail
[ -f "$WORK/ping/$target" ] && read -r mode < "$WORK/ping/$target"
case "$mode" in
ok) exit 0 ;;
hang) exec sleep 60 ;;
crash) exit 2 ;;
*) exit 1 ;;
esac
''',
    "ubus": r'''#!/bin/sh
echo "ubus $*" >> "$WORK/calls"
f="$WORK/ifaces/${4#network.interface.}.json"
[ "$1 $2 $3 $5" = "-t 3 call status" ] && [ -f "$f" ] && { cat "$f"; exit 0; }
echo "Command failed: Not found" >&2
exit 4
''',
    "jsonfilter": r'''#!/usr/bin/env -S python3 -I
import json, sys
args = sys.argv[1:]
data = json.load(open(args[args.index("-i") + 1]))
value = data.get(args[args.index("-e") + 1][2:])
if value is None:
    sys.exit(1)
print(("true" if value else "false") if isinstance(value, bool) else value)
''',
    "logger": '#!/bin/sh\necho "$*" >> "$WORK/syslog"\n',
}

FORBIDDEN = ("ping", "ping6", "ip", "ubus", "jsonfilter", "logger")


def isolated_bin(directory, tools):
    """A PATH directory with only `tools` linked from the host plus guard
    stubs for the probe/syslog commands, which record any call and fail."""
    directory.mkdir(exist_ok=True)
    for tool in tools:
        (directory / tool).symlink_to(shutil.which(tool))
    for name in FORBIDDEN:
        guard = directory / name
        if not guard.exists():
            guard.write_text(f'#!/bin/sh\necho "{name} $*" >> "{directory}/forbidden-calls"\nexit 127\n')
            guard.chmod(0o755)
    return directory


# Emulates the kernel LED trigger file: writing a name selects it (shown in
# brackets), "none" switches off, default-on/timer switch on. A file
# fail-<attr> in the LED directory makes writes to that attribute fail.
FAKE_KERNEL = r'''
rc01_netled_sysfs_write() {
	local dir="${1%/*}" name="${1##*/}" t out=
	[ -e "$dir/fail-$name" ] && return 1
	if [ "$name" != trigger ]; then
		printf '%s\n' "$2" > "$1"
		return
	fi
	case " $(cat "$WORK/available") " in *" $2 "*) ;; *) return 1 ;; esac
	echo "${dir##*/} $2" >> "$WORK/writes"
	for t in $(cat "$WORK/available"); do
		[ "$t" = "$2" ] && t="[$t]"
		out="$out $t"
	done
	printf '%s\n' "${out# }" > "$dir/trigger"
	case "$2" in none) echo 0 ;; *) echo 1 ;; esac > "$dir/brightness"
	[ "$2" = timer ] && echo 500 > "$dir/delay_on" && echo 500 > "$dir/delay_off"
	return 0
}
'''

V4_ROUTE = "203.0.113.1 via 192.0.2.1 dev br-lan src 192.0.2.10 uid 0\n    cache\n"
V6_ROUTE = "2001:db8:1::1 from :: via fe80::1 dev br-lan proto ra src 2001:db8::10 metric 1024 pref medium\n"


class Fixture(unittest.TestCase):
    shell = SHELLS[0] if SHELLS else "sh"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-netled-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        # PATH holds only the stubs and these utilities, so a missing stub is
        # really missing and no host ping/ip/logger can ever run.
        for tool in APPLETS + ("env", "python3"):
            if self.shell == "busybox" and tool in APPLETS:
                (self.bin / tool).symlink_to(BUSYBOX)
            else:
                (self.bin / tool).symlink_to(shutil.which(tool))
        for d in ("routes", "ping", "ifaces", "run"):
            (self.work / d).mkdir()
        (self.work / "proc").mkdir()
        self.uptime(1000)
        (self.work / "available").write_text("none timer default-on heartbeat netdev\n")
        self.leds = {c: self.work / f"sys/class/leds/{c}:network" for c in ("white", "amber")}
        for led in self.leds.values():
            led.mkdir(parents=True)
            (led / "trigger").write_text("[none] timer default-on heartbeat netdev\n")
            (led / "brightness").write_text("0\n")
        self.env = {"PATH": str(self.bin), "WORK": str(self.work),
                    "RC01_NETLED_SYSFS": str(self.work / "sys"), "RC01_NETLED_PROC": str(self.work / "proc"),
                    "RC01_NETLED_RUN": str(self.work / "run"), "RC01_NETLED_LIBRARY": "1"}

    # -- fixture controls ---------------------------------------------------
    def uptime(self, seconds):
        (self.work / "proc/uptime").write_text(f"{seconds}.42 10.00\n")

    def route(self, target, text=V4_ROUTE, rc=None, dev=None, hang=False):
        path = self.work / "routes" / (f"{target}@{dev}" if dev else target)
        path.write_text(text)
        if rc is not None:
            Path(f"{path}.rc").write_text(f"{rc}\n")
        if hang:
            Path(f"{path}.hang").write_text("")

    def ping(self, target, mode):
        (self.work / "ping" / target).write_text(mode + "\n")

    def iface(self, name, up=True, device="br-lan"):
        value = {"up": up, "l3_device": device} if device else {"up": up}
        (self.work / "ifaces" / f"{name}.json").write_text(json.dumps(value))

    def select(self, color, trigger, brightness=None):
        words = (self.work / "available").read_text().split()
        (self.leds[color] / "trigger").write_text(" ".join(f"[{w}]" if w == trigger else w for w in words) + "\n")
        if brightness is not None:
            (self.leds[color] / "brightness").write_text(f"{brightness}\n")

    def selected(self, color):
        return re.search(r"\[([^]]+)\]", (self.leds[color] / "trigger").read_text()).group(1)

    def shown(self):
        return {c: self.selected(c) for c in self.leds}

    def lines(self, name):
        path = self.work / name
        return path.read_text().splitlines() if path.exists() else []

    def state(self):
        return json.loads((self.work / "run/state.json").read_text())

    @staticmethod
    def args(**overrides):
        values = {"ipv4": "1", "ipv6": "0", "ipv4_target": ["203.0.113.1", "198.51.100.1"],
                  "ipv6_target": ["2001:db8:1::1", "2001:db8:2::1"], "ipv4_interface": "auto",
                  "ipv6_interface": "auto", "interval": "10", "timeout": "1", "fail_count": "2",
                  "recover_count": "2", "led_white": "white:network", "led_amber": "amber:network", **overrides}
        out = []
        for key, value in values.items():
            for item in value if isinstance(value, list) else [value]:
                out.append(f"{key}={item}")
        return out

    def command(self):
        shell = BUSYBOX if self.shell == "busybox" else shutil.which(self.shell)
        return [shell] + (["sh"] if self.shell == "busybox" else [])

    def run_shell(self, body, args=None, start=True, check=True):
        script = f'. "{DAEMON}"\n{FAKE_KERNEL}\nset -f\n'
        if args is not None or start:
            script += "set -- " + " ".join(shlex.quote(a) for a in (args or self.args())) + "\n"
            script += 'rc01_netled_parse "$@" || exit 9\n'
        if start:
            script += "rc01_netled_start\n"
        result = subprocess.run(self.command() + ["-c", script + body], env=self.env,
                                capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def rounds(self, count=1, args=None, between=""):
        return self.run_shell("\n".join(["rc01_netled_round", between] * count), args)


class Validation(Fixture):
    def check(self, *args):
        return subprocess.run(self.command() + ["-c", f'. "{DAEMON}"\nrc01_netled_main check "$@"', "x", *args],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_default_configuration_is_valid(self):
        result = self.check(*self.args(ipv6="1"))
        self.assertEqual(result.returncode, 0, result.stderr)
        # The shipped defaults, as the init script passes them.
        targets4 = re.findall(r"list ipv4_target '([^']+)'", CONFIG.read_text())
        targets6 = re.findall(r"list ipv6_target '([^']+)'", CONFIG.read_text())
        self.assertEqual(len(targets4), 2)
        self.assertEqual(len(targets6), 2)
        result = self.check(*self.args(ipv6="1", ipv4_target=targets4, ipv6_target=targets6))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepted_addresses(self):
        for v4 in ("1.1.1.1", "203.0.113.255", "223.255.255.254", "10.0.0.1"):
            with self.subTest(v4=v4):
                self.assertEqual(self.check(*self.args(ipv4_target=[v4])).returncode, 0)
        for v6 in ("2001:db8::1", "2001:DB8:0:0:0:0:0:1", "2001:db8::", "2400:3200::1", "64:ff9b::1", "fc00::1",
                   "2001:db8:1:2:3:4:5:6", "1::"):
            with self.subTest(v6=v6):
                result = self.check(*self.args(ipv4="0", ipv6="1", ipv6_target=[v6]))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejected_values_are_never_executed(self):
        marker = self.work / "executed"
        injected = f"$(touch {marker})"
        bad = [
            dict(ipv4_target=["256.1.1.1"]), dict(ipv4_target=["01.2.3.4"]), dict(ipv4_target=["1.2.3"]),
            dict(ipv4_target=["1.2.3.4.5"]), dict(ipv4_target=["0.1.2.3"]), dict(ipv4_target=["127.0.0.1"]),
            dict(ipv4_target=["224.0.0.1"]), dict(ipv4_target=["255.255.255.255"]), dict(ipv4_target=["1.2.3.4 "]),
            dict(ipv4_target=["1.2.3.4;reboot"]), dict(ipv4_target=[injected]), dict(ipv4_target=["-c1"]),
            dict(ipv4_target=["example.com"]), dict(ipv4_target=[]),
            dict(ipv4_target=["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"]),
            dict(ipv4_interface="wan;x"), dict(ipv4_interface=injected), dict(ipv4_interface="../x"),
            dict(ipv4_interface=""), dict(interval="9"), dict(interval="3601"), dict(interval="030"),
            dict(interval="1e3"), dict(timeout="0"), dict(timeout="6"), dict(fail_count="11"),
            dict(recover_count="0"), dict(ipv4="0"), dict(ipv4="yes"), dict(led_white="../../x"),
            dict(ipv6="1", ipv6_target=["2001:db8::1", "fe80::1"]),
        ]
        for case in bad:
            with self.subTest(case=case):
                self.assertNotEqual(self.check(*self.args(**case)).returncode, 0)
        for v6 in (":::", "1::2::3", "::", "::1", "fe80::1", "FE80::1", "febf::1", "ff02::1", "2001:db8::1%eth0",
                   "::ffff:192.0.2.1", "12345::1", "1:2:3:4:5:6:7:8:9", "1:2:3:4:5:6:7", ":1::2", "1::2:",
                   "1:2:3:4:5:6:7::8", "g::1", injected):
            with self.subTest(v6=v6):
                self.assertNotEqual(self.check(*self.args(ipv4="0", ipv6="1", ipv6_target=[v6])).returncode, 0)
        self.assertNotEqual(self.check(*self.args(), "colour=red").returncode, 0)
        self.assertNotEqual(self.check(*self.args(), "noequals").returncode, 0)
        self.assertFalse(marker.exists())

    def test_run_refuses_invalid_arguments_without_touching_leds(self):
        result = subprocess.run(self.command() + ["-c", f'. "{DAEMON}"\n{FAKE_KERNEL}\nrc01_netled_main run "$@"', "x",
                                *self.args(interval="5")], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("interval must be 10..3600", result.stderr)
        self.assertEqual(self.lines("writes"), [])
        self.assertFalse((self.work / "run/state.json").exists())


class Probing(Fixture):
    def test_ipv4_auto_route_on_lan_dhcp_bypass(self):
        # Bypass: the default route is learnt on lan (br-lan); wan has none.
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "ok")
        self.rounds()
        state = self.state()
        self.assertEqual(state["overall"], "up")
        v4 = state["families"]["ipv4"]
        self.assertEqual((v4["state"], v4["last"], v4["device"], v4["target"]), ("up", "ok", "br-lan", "203.0.113.1"))
        self.assertEqual(state["families"]["ipv6"]["state"], "disabled")
        self.assertEqual(self.lines("calls"), ["ip -4 route get 203.0.113.1", "ping -4 -q -c 1 -W 1 203.0.113.1"])
        self.assertEqual(self.shown(), {"white": "default-on", "amber": "none"})
        self.assertEqual(state["leds"]["white"], {"name": "white:network", "control": "owned", "shown": "on"})

    def test_busybox_route_format(self):
        self.route("203.0.113.1", "203.0.113.1 via 192.0.2.1 dev br-lan  src 192.0.2.10 \n    cache \n")
        self.ping("203.0.113.1", "ok")
        self.rounds()
        self.assertEqual(self.state()["families"]["ipv4"]["state"], "up")

    def test_ipv6_only(self):
        self.route("2001:db8:1::1", V6_ROUTE)
        self.ping("2001:db8:1::1", "ok")
        self.rounds(args=self.args(ipv4="0", ipv6="1"))
        state = self.state()
        self.assertEqual((state["overall"], state["families"]["ipv6"]["state"]), ("up", "up"))
        self.assertEqual(state["families"]["ipv4"]["state"], "disabled")
        self.assertIn("ping -6 -q -c 1 -W 1 2001:db8:1::1", self.lines("calls"))
        self.assertFalse([c for c in self.lines("calls") if " -4 " in c])

    def test_one_family_without_route_is_partial(self):
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "ok")
        self.rounds(2, args=self.args(ipv6="1"))
        state = self.state()
        self.assertEqual(state["families"]["ipv6"]["state"], "no-path")
        self.assertEqual(state["families"]["ipv6"]["reason"], "no-route")
        self.assertEqual(state["overall"], "degraded")
        self.assertEqual(self.shown(), {"white": "timer", "amber": "none"})
        # No IPv6 echo is sent without a route.
        self.assertFalse([c for c in self.lines("calls") if c.startswith("ping -6")])

    def test_no_route_at_all_needs_confirmation(self):
        self.run_shell("rc01_netled_round")
        state = self.state()
        self.assertEqual((state["overall"], state["families"]["ipv4"]["state"]), ("unknown", "unknown"))
        self.assertEqual(state["families"]["ipv4"]["last"], "nopath")
        self.assertEqual(self.shown(), {"white": "none", "amber": "timer"})
        self.rounds(2)
        state = self.state()
        self.assertEqual((state["overall"], state["families"]["ipv4"]["state"]), ("down", "no-path"))
        self.assertEqual(self.shown(), {"white": "none", "amber": "default-on"})
        self.assertFalse([c for c in self.lines("calls") if c.startswith("ping")])

    def test_unusable_routes_count_as_no_path(self):
        cases = {
            "unreachable 203.0.113.1 dev lo table unspec proto kernel metric 4294967295 error -101\n": "route-unreachable",
            "local 203.0.113.1 dev lo table local src 203.0.113.1 uid 0\n": "route-local",
            "203.0.113.1 dev lo src 127.0.0.1\n": "route-local",
            "203.0.113.1 via 192.0.2.1 dev br-lan uid 0\n": "no-source-address",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                self.setUp()
                self.route("203.0.113.1", text)
                self.rounds(2, args=self.args(ipv4_target=["203.0.113.1"]))
                v4 = self.state()["families"]["ipv4"]
                self.assertEqual((v4["state"], v4["reason"]), ("no-path", reason))
        self.setUp()
        self.route("2001:db8:1::1", "2001:db8:1::1 from :: via fe80::1 dev br-lan src fe80::2 metric 1024\n")
        self.rounds(2, args=self.args(ipv4="0", ipv6="1", ipv6_target=["2001:db8:1::1"]))
        self.assertEqual(self.state()["families"]["ipv6"]["reason"], "no-source-address")

    def test_named_egress_pins_route_and_echo_to_its_device(self):
        self.iface("wan", device="eth0")
        self.route("203.0.113.1", "203.0.113.1 via 198.51.100.254 dev eth0 src 198.51.100.7 uid 0\n", dev="eth0")
        self.ping("203.0.113.1", "ok")
        self.rounds(args=self.args(ipv4_interface="wan"))
        v4 = self.state()["families"]["ipv4"]
        self.assertEqual((v4["state"], v4["interface"], v4["device"]), ("up", "wan", "eth0"))
        self.assertEqual(self.lines("calls"), ["ubus -t 3 call network.interface.wan status",
                                               "ip -4 route get 203.0.113.1 oif eth0",
                                               "ping -4 -q -c 1 -W 1 -I eth0 203.0.113.1"])

    def test_named_egress_unavailable(self):
        cases = [("missing", None, "interface-missing"), ("down", dict(up=False), "interface-down"),
                 ("nodev", dict(device=None), "interface-no-device")]
        for name, iface, reason in cases:
            with self.subTest(name=name):
                self.setUp()
                if iface is not None:
                    self.iface("wan", **iface)
                self.route("203.0.113.1")
                self.ping("203.0.113.1", "ok")
                self.rounds(2, args=self.args(ipv4_interface="wan"))
                state = self.state()
                v4 = state["families"]["ipv4"]
                self.assertEqual((v4["state"], v4["reason"], state["overall"]), ("egress-unavailable", reason, "down"))
                self.assertFalse([c for c in self.lines("calls") if not c.startswith("ubus")])

    def test_named_egress_without_route_through_it(self):
        # The main table has a default route on br-lan, but not via wan's device.
        self.iface("wan", device="eth0")
        self.route("203.0.113.1")
        self.route("203.0.113.1", "", rc=2, dev="eth0")
        self.ping("203.0.113.1", "ok")
        self.rounds(2, args=self.args(ipv4_interface="wan", ipv4_target=["203.0.113.1"]))
        self.assertEqual(self.state()["families"]["ipv4"]["state"], "no-path")
        self.assertFalse([c for c in self.lines("calls") if c.startswith("ping")])

    def test_one_failing_target_does_not_mark_the_egress_down(self):
        self.route("203.0.113.1")
        self.route("198.51.100.1", V4_ROUTE.replace("203.0.113.1", "198.51.100.1"))
        self.ping("203.0.113.1", "fail")
        self.ping("198.51.100.1", "ok")
        self.rounds(3)
        v4 = self.state()["families"]["ipv4"]
        self.assertEqual((v4["state"], v4["target"], v4["ok_streak"]), ("up", "198.51.100.1", 3))
        # The first reply ends a round: with the first target answering the
        # second is never probed.
        (self.work / "calls").unlink()
        self.ping("203.0.113.1", "ok")
        self.rounds()
        self.assertEqual([c for c in self.lines("calls") if c.startswith("ping")], ["ping -4 -q -c 1 -W 1 203.0.113.1"])

    def test_failure_confirmation_and_recovery(self):
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "ok")
        args = self.args(ipv4_target=["203.0.113.1"], fail_count="3", recover_count="2")
        script = []
        for mode in ["ok", "fail", "fail", "fail", "ok", "ok", "fail", "ok"]:
            script.append(f'echo {mode} > "$WORK/ping/203.0.113.1"; rc01_netled_round; '
                          'printf "%s %s\\n" "$F4_ST" "$RC01_NETLED_OVERALL" >> "$WORK/trace"')
        self.run_shell("\n".join(script), args)
        self.assertEqual(self.lines("trace"), ["up up", "up up", "up up", "down down", "down down",
                                               "up up", "up up", "up up"])
        self.assertEqual(self.lines("writes"), [
            "white:network none", "amber:network timer",          # start: unknown
            "white:network default-on", "amber:network none",     # up
            "white:network none", "amber:network default-on",     # down after 3
            "white:network default-on", "amber:network none"])    # up after 2
        transitions = [l for l in self.lines("syslog") if "reachability" in l]
        self.assertEqual(len(transitions), 3, transitions)

    def test_probe_errors_are_neutral_then_stale(self):
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "ok")
        args = self.args(ipv4_target=["203.0.113.1"], fail_count="2")
        self.run_shell('''
rc01_netled_round
echo crash > "$WORK/ping/203.0.113.1"
rc01_netled_round
echo "$F4_ST $F4_LAST $F4_REASON" >> "$WORK/trace"
rc01_netled_round
echo "$F4_ST $RC01_NETLED_OVERALL" >> "$WORK/trace"
echo ok > "$WORK/ping/203.0.113.1"
rc01_netled_round
echo "$F4_ST" >> "$WORK/trace"
''', args)
        self.assertEqual(self.lines("trace"), ["up error probe-failed", "stale unknown", "up"])
        # Stale shows amber blinking, never the old success.
        self.assertIn("amber:network timer", self.lines("writes")[4:])

    def test_errors_from_unknown_stay_unknown_and_errors_do_not_reset_failures(self):
        self.route("203.0.113.1")
        args = self.args(ipv4_target=["203.0.113.1"], fail_count="2")
        self.run_shell('''
echo crash > "$WORK/ping/203.0.113.1"
rc01_netled_round; rc01_netled_round; rc01_netled_round
echo "$F4_ST $F4_ERRN" >> "$WORK/trace"
echo fail > "$WORK/ping/203.0.113.1"; rc01_netled_round
echo crash > "$WORK/ping/203.0.113.1"; rc01_netled_round
echo fail > "$WORK/ping/203.0.113.1"; rc01_netled_round
echo "$F4_ST $F4_BADN" >> "$WORK/trace"
''', args)
        self.assertEqual(self.lines("trace"), ["unknown 3", "down 2"])

    def test_missing_tools_and_route_errors_are_errors_not_failures(self):
        for setup, reason in [(lambda: (self.bin / "ping").unlink(), "probe-failed"),
                              (lambda: self.route("203.0.113.1", "", rc=137), "route-query-failed"),
                              (lambda: self.route("203.0.113.1", "\n"), "route-unparsable"),
                              (lambda: (self.bin / "ip").unlink(), "route-query-failed")]:
            with self.subTest(reason=reason):
                self.setUp()
                self.route("203.0.113.1")
                self.ping("203.0.113.1", "ok")
                setup()
                self.rounds(2, args=self.args(ipv4_target=["203.0.113.1"]))
                v4 = self.state()["families"]["ipv4"]
                self.assertEqual((v4["state"], v4["last"], v4["reason"], v4["error_streak"]),
                                 ("unknown", "error", reason, 2))

    def test_hanging_commands_are_killed_within_the_bound(self):
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "hang")
        start = time.monotonic()
        self.rounds(args=self.args(ipv4_target=["203.0.113.1"], timeout="1"))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3 + 2, "echo bound is timeout+2 s")
        self.assertGreaterEqual(elapsed, 2.5)
        self.assertEqual(self.state()["families"]["ipv4"]["last"], "error")
        self.setUp()
        self.route("203.0.113.1", hang=True)
        start = time.monotonic()
        self.rounds(args=self.args(ipv4_target=["203.0.113.1"]))
        self.assertLess(time.monotonic() - start, 3 + 2, "route lookup bound is 3 s")
        self.assertEqual(self.state()["families"]["ipv4"]["reason"], "route-query-failed")

    def test_number_of_commands_per_round_is_bounded(self):
        targets = ["203.0.113.1", "203.0.113.2", "203.0.113.3"]
        for t in targets:
            self.route(t, V4_ROUTE.replace("203.0.113.1", t))
        self.iface("lan")
        for t in targets:
            self.route(t, V4_ROUTE.replace("203.0.113.1", t), dev="br-lan")
        self.rounds(args=self.args(ipv4_target=targets, ipv4_interface="lan", ipv6="1",
                                   ipv6_target=["2001:db8::1", "2001:db8::2", "2001:db8::3"]))
        calls = self.lines("calls")
        # Per family: one interface query, then per target one route lookup
        # and at most one echo.
        self.assertEqual(len([c for c in calls if c.startswith("ubus")]), 1)
        self.assertEqual(len([c for c in calls if c.startswith("ip -4")]), 3)
        self.assertEqual(len([c for c in calls if c.startswith("ping -4")]), 3)
        self.assertEqual(len([c for c in calls if c.startswith("ip -6")]), 3)
        state = self.state()
        bound = state["valid_until"] - state["updated_at"] - state["interval"]
        self.assertEqual(bound, 6 * (3 + 1 + 2) + 2 * 5 + 5)


class Leds(Fixture):
    def up(self):
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "ok")

    def test_user_trigger_is_left_alone_and_not_taken_back(self):
        self.up()
        self.run_shell('''
rc01_netled_round
echo "white:network heartbeat" >> "$WORK/writes"
echo 'none timer default-on [heartbeat] netdev' > "$RC01_NETLED_SYSFS/class/leds/white:network/trigger"
echo fail > "$WORK/ping/203.0.113.1"; echo fail > "$WORK/ping/198.51.100.1"
rc01_netled_round; rc01_netled_round; rc01_netled_round
echo ok > "$WORK/ping/203.0.113.1"
rc01_netled_round; rc01_netled_round
rc01_netled_leds_restore
''')
        self.assertEqual(self.selected("white"), "heartbeat")
        white = [w for w in self.lines("writes") if w.startswith("white")]
        self.assertEqual(white, ["white:network none", "white:network default-on", "white:network heartbeat"])
        self.assertEqual(self.selected("amber"), "none", "amber restored on stop")
        self.assertEqual(len([l for l in self.lines("syslog") if "leaving it alone" in l]), 1)
        self.assertEqual(self.state()["leds"]["white"]["control"], "yielded")

    def test_user_brightness_under_none_counts_as_a_takeover(self):
        self.up()
        self.run_shell('''
rc01_netled_round
echo 1 > "$RC01_NETLED_SYSFS/class/leds/amber:network/brightness"
echo fail > "$WORK/ping/203.0.113.1"; echo fail > "$WORK/ping/198.51.100.1"
rc01_netled_round; rc01_netled_round
''')
        self.assertEqual([w for w in self.lines("writes") if w.startswith("amber")], ["amber:network timer", "amber:network none"])
        self.assertEqual(self.state()["leds"]["amber"]["control"], "yielded")

    def test_led_already_in_use_at_start_is_not_taken(self):
        self.up()
        self.select("white", "netdev")
        self.select("amber", "none", brightness=1)
        self.rounds()
        self.assertEqual(self.lines("writes"), [])
        state = self.state()
        self.assertEqual(state["leds"]["white"]["control"], "yielded")
        self.assertEqual(state["leds"]["amber"]["control"], "yielded")
        self.assertEqual(state["overall"], "up", "probing continues without LEDs")

    def test_leftover_trigger_from_a_killed_instance_is_reclaimed(self):
        self.up()
        self.select("white", "default-on")
        self.select("amber", "timer")
        self.rounds()
        self.assertEqual(self.shown(), {"white": "default-on", "amber": "none"})

    def test_unmanaged_and_missing_leds(self):
        self.up()
        shutil.rmtree(self.leds["amber"])
        self.rounds(args=self.args(led_white=""))
        state = self.state()
        self.assertEqual(state["leds"]["white"], {"name": None, "control": "unmanaged", "shown": None})
        self.assertEqual(state["leds"]["amber"]["control"], "missing")
        self.assertEqual(self.lines("writes"), [])
        self.assertIn("-t rc01-netled LED amber:network not found", self.lines("syslog"))

    def test_write_failure_is_reported_logged_once_and_retried(self):
        self.up()
        (self.leds["white"] / "fail-trigger").write_text("")
        self.rounds(3)
        state = self.state()
        self.assertEqual(state["leds"]["white"], {"name": "white:network", "control": "write-failed", "shown": None})
        self.assertEqual(len([l for l in self.lines("syslog") if "cannot set LED white:network" in l]), 1)
        (self.leds["white"] / "fail-trigger").unlink()
        self.run_shell('''
rc01_netled_round
LW_LAST=retry LW_WFAIL=1
rc01_netled_round
rc01_netled_state_write
''')
        self.assertEqual(self.state()["leds"]["white"]["control"], "owned")
        self.assertEqual(self.selected("white"), "default-on")

    def test_unavailable_trigger_is_a_write_failure(self):
        self.up()
        (self.work / "available").write_text("none timer netdev\n")
        self.rounds()
        self.assertEqual(self.state()["leds"]["white"]["control"], "write-failed")
        self.assertEqual(self.selected("white"), "none")

    def test_real_write_to_a_read_only_attribute_fails_without_aborting(self):
        # The production writer (no fake kernel): a failed redirection must
        # return an error and leave the shell running.
        target = self.leds["white"] / "trigger"
        target.chmod(0o444)
        self.addCleanup(target.chmod, 0o644)
        script = (f'. "{DAEMON}"\nrc01_netled_sysfs_write "{target}" default-on 2>/dev/null && echo wrote || echo failed\n'
                  f'rc01_netled_sysfs_write "{self.leds["amber"]}/trigger" timer && echo wrote')
        result = subprocess.run(self.command() + ["-c", script], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.stdout.split(), ["failed", "wrote"], result.stderr)

    def test_odd_route_output_keeps_the_report_valid(self):
        self.route("203.0.113.1", '203.0.113.1 via 192.0.2.1 dev we"ird src 192.0.2.10\n')
        self.ping("203.0.113.1", "ok")
        self.rounds()
        self.assertEqual(self.state()["families"]["ipv4"]["device"], None)
        self.setUp()
        self.route("203.0.113.1", 'th"row 203.0.113.1 dev lo\n')
        self.rounds()
        self.assertEqual(self.state()["families"]["ipv4"]["reason"], "route-unusable")

    def test_timer_blink_period_is_set(self):
        self.rounds()
        self.assertEqual(self.selected("amber"), "timer")
        self.assertEqual((self.leds["amber"] / "delay_on").read_text().strip(), "1000")
        self.assertEqual((self.leds["amber"] / "delay_off").read_text().strip(), "1000")


class Lifecycle(Fixture):
    def service(self, args, sleep="0.2"):
        env = {**self.env, "RC01_NETLED_SLEEP": sleep}
        script = f'. "{DAEMON}"\n{FAKE_KERNEL}\nrc01_netled_main run "$@"'
        return subprocess.Popen(self.command() + ["-c", script, "rc01-netled", *args], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def wait_for(self, condition, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if condition():
                    return True
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.05)
        return False

    def stop(self, process):
        process.send_signal(signal.SIGTERM)
        try:
            return process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_run_stop_restart_with_new_configuration(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.shell = shell
                self.setUp()
                self.route("203.0.113.1")
                self.ping("203.0.113.1", "ok")
                process = self.service(self.args())
                try:
                    self.assertTrue(self.wait_for(lambda: self.state()["overall"] == "up"))
                    self.assertEqual(self.shown(), {"white": "default-on", "amber": "none"})
                    self.assertEqual(self.state()["pid"], process.pid)
                    self.assertEqual(self.stop(process), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                self.assertEqual(self.shown(), {"white": "none", "amber": "none"})
                self.assertFalse((self.work / "run/state.json").exists())
                # New configuration (a target without route): the new process
                # starts from unknown and does not show the earlier success.
                self.uptime(2000)
                (self.work / "proc/uptime").write_text("2000.00 1.00\n")
                process = self.service(self.args(ipv4_target=["192.0.2.99"]), sleep="30")
                try:
                    self.assertTrue(self.wait_for(lambda: self.state()["rounds"] == 1))
                    state = self.state()
                    self.assertEqual((state["overall"], state["families"]["ipv4"]["state"]), ("unknown", "unknown"))
                    self.assertEqual(state["started_at"], 2000)
                    self.assertEqual(state["families"]["ipv4"]["targets"], ["192.0.2.99"])
                    self.assertEqual(self.shown(), {"white": "none", "amber": "timer"})
                    self.assertEqual(self.stop(process), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                self.assertEqual(self.shown(), {"white": "none", "amber": "none"})

    def test_stop_during_a_hanging_probe_is_prompt(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.shell = shell
                self.setUp()
                self.route("203.0.113.1")
                self.ping("203.0.113.1", "hang")
                process = self.service(self.args(ipv4_target=["203.0.113.1"], timeout="5"))
                try:
                    self.assertTrue(self.wait_for(lambda: "ping" in (self.work / "calls").read_text()))
                    start = time.monotonic()
                    self.assertEqual(self.stop(process), 0)
                    # procd sends SIGKILL 5 s after SIGTERM; restore must win.
                    self.assertLess(time.monotonic() - start, 2)
                finally:
                    if process.poll() is None:
                        process.kill()
                self.assertEqual(self.shown(), {"white": "none", "amber": "none"})

    def test_stop_leaves_a_user_trigger(self):
        self.route("203.0.113.1")
        self.ping("203.0.113.1", "ok")
        process = self.service(self.args())
        try:
            self.assertTrue(self.wait_for(lambda: self.selected("white") == "default-on"))
            self.select("white", "heartbeat")
            self.assertTrue(self.wait_for(lambda: self.state()["leds"]["white"]["control"] == "yielded"))
            self.assertEqual(self.stop(process), 0)
        finally:
            if process.poll() is None:
                process.kill()
        self.assertEqual(self.shown(), {"white": "heartbeat", "amber": "none"})


# rc.common/procd stubs; config parsing uses the real functions.sh and uci.sh
# with /sbin/uci replaced by a stub that exports fixture files.
INIT_STUBS = r'''
board_name() { echo "$BOARD"; }
logger() { echo "logger $*"; }
procd_open_instance() { echo open; }
procd_set_param() { echo "param $*"; }
procd_close_instance() { echo close; }
procd_add_reload_trigger() { echo "reload $*"; }
'''

UCI_STUB = r'''#!/bin/sh
case "$*" in
"-q get system.@system[-1].leds_off") [ -n "$LEDS_OFF" ] && echo "$LEDS_OFF" ;;
*" export "*) for p; do :; done; [ -f "$UCI_FIXTURES/$p" ] && grep -v '^#' "$UCI_FIXTURES/$p" ;;
*) exit 1 ;;
esac
'''


class Init(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-netled-init-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        isolated_bin(self.work / "bin", ("sh", "cat", "grep"))
        self.addCleanup(lambda: self.assertFalse((self.work / "bin/forbidden-calls").exists()))
        (self.work / "bin/uci").write_text(UCI_STUB)
        (self.work / "bin/uci").chmod(0o755)
        (self.work / "bin/rc01-netled").write_text(f'#!/bin/sh\nexec sh "{DAEMON}" "$@"\n')
        (self.work / "bin/rc01-netled").chmod(0o755)
        uci_sh = (ROOT / "package/system/uci/files/lib/config/uci.sh").read_text()
        (self.work / "uci.sh").write_text(uci_sh.replace("/sbin/uci", str(self.work / "bin/uci")))
        (self.work / "config").mkdir()
        self.config(CONFIG.read_text())
        self.system("config system\n\toption hostname 'RC01'\n")

    def config(self, text):
        (self.work / "config/rc01_netled").write_text("package rc01_netled\n" + text)

    def system(self, text):
        (self.work / "config/system").write_text("package system\n" + text)

    def enable(self, text=None):
        text = text if text is not None else CONFIG.read_text()
        self.config(text.replace("option enabled '0'", "option enabled '1'"))

    def start(self, board="xiaomi,be10000", leds_off=None):
        env = {"PATH": str(self.work / "bin"), "BOARD": board,
               "UCI_FIXTURES": str(self.work / "config")}
        if leds_off:
            env["LEDS_OFF"] = leds_off
        script = (f'. "{ROOT}/package/base-files/files/lib/functions.sh"\n. "{self.work}/uci.sh"\n{INIT_STUBS}\n'
                  f'. "{INIT}"\nNETLED_BIN="{self.work}/bin/rc01-netled"\nstart_service; echo "rc=$?"\nservice_triggers')
        # functions.sh uses "export -n" (BusyBox ash on the target); dash lacks it.
        shell = [BUSYBOX, "sh"] if BUSYBOX else [shutil.which("bash")]
        result = subprocess.run(shell + ["-c", script], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def command(self, out):
        lines = [l for l in out if l.startswith("param command ")]
        self.assertEqual(len(lines), 1, out)
        return lines[0].split()[2:]

    def test_disabled_by_default(self):
        out = self.start()
        self.assertNotIn("open", out)
        self.assertIn("rc=0", out)
        self.assertIn("reload rc01_netled system", out)

    def test_enabled_passes_every_setting(self):
        self.enable()
        out = self.start()
        self.assertEqual(out.count("open"), 1)
        self.assertEqual(self.command(out), [
            str(self.work / "bin/rc01-netled"), "run", "ipv4=1", "ipv6=1",
            "ipv4_target=223.5.5.5", "ipv4_target=119.29.29.29", "ipv6_target=2400:3200::1", "ipv6_target=2402:4e00::",
            "ipv4_interface=auto", "ipv6_interface=auto", "interval=30", "timeout=2", "fail_count=3",
            "recover_count=2", "led_white=white:network", "led_amber=amber:network"])
        self.assertIn("param respawn 3600 5 5", out)
        self.assertEqual(self.command(self.start(board="xiaomi,be10000-es"))[2], "ipv4=1")

    def test_changed_configuration_changes_the_command(self):
        # procd restarts an instance on reload only if its command changed.
        self.enable()
        before = self.command(self.start())
        self.enable(CONFIG.read_text().replace("119.29.29.29", "192.0.2.7").replace("option interval '30'",
                                                                                     "option interval '60'"))
        after = self.command(self.start())
        self.assertNotEqual(before, after)
        self.assertIn("ipv4_target=192.0.2.7", after)
        self.assertIn("interval=60", after)

    def test_system_led_sections_and_leds_off_keep_their_leds(self):
        self.enable()
        self.system("config system\nconfig led 'net'\n\toption sysfs 'white:network'\n\toption trigger 'heartbeat'\n")
        command = self.command(self.start())
        self.assertIn("led_white=", command)
        self.assertIn("led_amber=amber:network", command)
        self.system("config system\n\toption leds_off '1'\n")
        command = self.command(self.start(leds_off="1"))
        self.assertEqual(command[-2:], ["led_white=", "led_amber="])

    def test_invalid_configuration_is_not_started(self):
        cases = [
            CONFIG.read_text().replace("'223.5.5.5'", "'999.1.1.1'"),
            CONFIG.read_text().replace("'223.5.5.5'", "'223.5.5.5 $(reboot)'"),
            CONFIG.read_text().replace("'223.5.5.5'", "'223.5.5.5\n203.0.113.9'"),
            CONFIG.read_text().replace("option interval '30'", "option interval '1'"),
            CONFIG.read_text().replace("option ipv4_interface 'auto'", "option ipv4_interface 'wan; reboot'"),
            CONFIG.read_text().replace("option ipv4 '1'", "option ipv4 '0'").replace("option ipv6 '1'", "option ipv6 '0'"),
            CONFIG.read_text() + "\tlist ipv4_target '192.0.2.1'\n\tlist ipv4_target '192.0.2.2'\n",
        ]
        for text in cases:
            with self.subTest(text=text[-80:]):
                self.enable(text)
                out = self.start()
                self.assertNotIn("open", out)
                self.assertIn("rc=1", out)
                self.assertIn("logger -t rc01-netled invalid configuration; not started", out)

    def test_other_boards_do_nothing(self):
        self.enable()
        self.assertNotIn("open", self.start(board="xiaomi,be7000"))


class Package(unittest.TestCase):
    def test_package_installs_service_and_keeps_user_config(self):
        makefile = (ROOT / "package/emortal/rc01-platform/Makefile").read_text()
        self.assertIn("$(INSTALL_BIN) ./files/rc01-netled.init $(1)/etc/init.d/rc01-netled", makefile)
        self.assertIn("$(INSTALL_BIN) ./files/rc01-netled $(1)/usr/libexec/rc01-netled", makefile)
        self.assertIn("$(INSTALL_CONF) ./files/rc01_netled.config $(1)/etc/config/rc01_netled", makefile)
        self.assertRegex(makefile, r"define Package/rc01-platform/conffiles\n/etc/config/rc01_netled\nendef")
        self.assertIn("$(1)/etc/config", re.search(r"\$\(INSTALL_DIR\)[^\n]*", makefile).group(0))
        for source in re.findall(r"\./files/([\w.-]+)", makefile):
            self.assertTrue((FILES / source).is_file(), source)
        init = INIT.read_text()
        self.assertTrue(init.startswith("#!/bin/sh /etc/rc.common\n"))
        self.assertEqual(re.search(r"^START=(\d+)$", init, re.M).group(1), "97")
        self.assertTrue(DAEMON.read_text().startswith("#!/bin/sh\n"))
        self.assertIn("option enabled '0'", CONFIG.read_text())

    def test_only_the_network_leds_are_named(self):
        self.assertEqual(sorted(set(re.findall(r"\b\w+:network\b", INIT.read_text()))), ["amber:network", "white:network"])
        dts = (ROOT / "target/linux/qualcommbe/dts/ipq9570-be10000.dts").read_text()
        for color, gpio in (("WHITE", 42), ("AMBER", 43)):
            self.assertRegex(dts, rf'function = "network";\s*color = <LED_COLOR_ID_{color}>;\s*gpios = <&tlmm {gpio} ')
        for other in ("sfp", "status", "lan", "rgb", "red", "green", "blue"):
            self.assertNotIn(other, re.sub(r"#.*", "", DAEMON.read_text()).lower().replace("status", "")
                             if other == "status" else re.sub(r"#.*", "", DAEMON.read_text()).lower())
        leds = (ROOT / "target/linux/qualcommbe/ipq95xx/base-files/etc/board.d/01_leds").read_text()
        self.assertNotIn("network", leds, "no board LED policy competes for the network LEDs")

    def test_shell_syntax(self):
        for shell in SHELLS or ["sh"]:
            command = [BUSYBOX, "sh"] if shell == "busybox" else [shutil.which(shell)]
            for path in (DAEMON, INIT):
                result = subprocess.run(command + ["-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{shell} {path}: {result.stderr}")

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_shellcheck(self):
        result = subprocess.run(["shellcheck", "-s", "sh", "-e", "SC1007,SC3043,SC2086,SC1091,SC2034,SC2154,SC2155",
                                 str(DAEMON), str(INIT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


# The same probe, LED and validation cases under BusyBox ash and its applets.
if BUSYBOX:
    for _base in (Validation, Probing, Leds):
        globals()[_base.__name__ + "BusyBox"] = type(_base.__name__ + "BusyBox", (_base,), {"shell": "busybox"})


if __name__ == "__main__":
    unittest.main()
