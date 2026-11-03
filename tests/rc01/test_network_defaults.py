"""Run the shipped first-boot shell with recorded UCI writes; no live networking."""
from pathlib import Path
import os
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/rc01/files/etc/uci-defaults/89-rc01-bypass"
STUBS = r'''board_name() { printf '%s\n' "$RC01_TEST_BOARD"; }
config_load() { :; }
config_foreach() { :; }
uci() {
    case "$1:$2" in
        -q:batch) cat ;;
        *) : ;;
    esac
}
'''


class DHCPDiagnosticDefaultsTests(unittest.TestCase):
    def run_defaults(self, board="xiaomi,be10000"):
        source = SOURCE.read_text()
        self.assertEqual(source.count(". /lib/functions.sh"), 1)
        source = source.replace(". /lib/functions.sh", STUBS)
        source = source.replace("/etc/init.d/", "/nonexistent-rc01-test-init.d/")
        env = dict(os.environ, RC01_TEST_BOARD=board)
        result = subprocess.run(["sh"], input=source, text=True,
                                capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def apply_writes(self, output, state):
        for line in output.splitlines():
            fields = shlex.split(line)
            if not fields or fields[0] == "commit":
                continue
            if fields[0] == "set":
                key, value = fields[1].split("=", 1)
                state[key] = value
            elif fields[0] == "delete":
                state.pop(fields[1], None)
            elif fields[0] == "add_list":
                key, value = fields[1].split("=", 1)
                state.setdefault(key, []).append(value)
            else:
                self.fail(f"Unexpected UCI operation: {line}")
        return state

    def test_dhcp_client_replaces_old_static_address_route_and_dns(self):
        state = self.apply_writes(self.run_defaults(), {
            "network.lan.proto": "static",
            "network.lan.ipaddr": "192.168.50.3/24",
            "network.lan.netmask": "255.255.255.0",
            "network.lan.gateway": "192.168.50.1",
            "network.lan.dns": ["192.168.50.1"],
        })
        self.assertEqual(state["network.lan.proto"], "dhcp")
        self.assertEqual(state["network.lan.device"], "br-lan")
        self.assertEqual(state["network.lan.hostname"], "RC01-DHCP-test")
        for option in ("ipaddr", "netmask", "gateway", "dns"):
            self.assertNotIn(f"network.lan.{option}", state)

    def test_dhcp_client_does_not_enable_lan_server_wan_or_auto_presets(self):
        state = self.apply_writes(self.run_defaults(), {})
        for option in ("dhcpv4", "dhcpv6", "ra", "ndp"):
            self.assertEqual(state[f"dhcp.lan.{option}"], "disabled")
        self.assertEqual(state["dhcp.lan.ignore"], "1")
        for interface in ("wan", "wan6"):
            self.assertEqual(state[f"network.{interface}.auto"], "0")
        self.assertEqual(state["router-tool.main.auto_network"], "0")

    def test_other_board_receives_no_uci_writes(self):
        self.assertEqual(self.run_defaults("other,board"), "")
