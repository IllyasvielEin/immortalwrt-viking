"""Run the shipped first-boot shell with recorded UCI writes; no live networking."""
from pathlib import Path
import os
import shlex
import subprocess
import unittest
import tempfile

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


class DHCPDefaultsTests(unittest.TestCase):
    def run_defaults(self, board="xiaomi,be10000"):
        source = SOURCE.read_text()
        self.assertEqual(source.count(". /lib/functions.sh"), 1)
        source = source.replace(". /lib/functions.sh", STUBS)
        with tempfile.TemporaryDirectory(prefix="rc01-first-boot-") as temporary:
            work = Path(temporary)
            log = work / "service-calls"
            for name in ("tailscale", "tailscale-settings"):
                service = work / name
                service.write_text("#!/bin/sh\nprintf '%s %s\\n' '" + name +
                                   "' \"$*\" >>\"$RC01_TEST_SERVICE_LOG\"\n")
                service.chmod(0o755)
            source = source.replace("/etc/init.d/", str(work) + "/")
            env = dict(os.environ, RC01_TEST_BOARD=board, RC01_TEST_SERVICE_LOG=str(log))
            result = subprocess.run(["sh"], input=source, text=True,
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.service_calls = log.read_text().splitlines() if log.exists() else []
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
        self.assertEqual(state["network.lan.hostname"], "RC01")
        self.assertEqual(state["system.@system[0].hostname"], "RC01")
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

    def test_inherited_dns_ttl_and_hijack_presets_are_removed(self):
        state = self.apply_writes(self.run_defaults(), {
            "dhcp.@dnsmasq[0].min_cache_ttl": "3600",
            "dhcp.@dnsmasq[0].use_stale_cache": "3600",
            "dhcp.@dnsmasq[0].nonegcache": "1",
            "dhcp.@dnsmasq[0].dns_redirect": "1",
        })
        for option in ("min_cache_ttl", "use_stale_cache"):
            self.assertNotIn(f"dhcp.@dnsmasq[0].{option}", state)
        for option in ("nonegcache", "dns_redirect"):
            self.assertEqual(state[f"dhcp.@dnsmasq[0].{option}"], "0")
        self.assertEqual(state["dhcp.@dnsmasq[0].cachesize"], "8192")
        self.assertEqual(state["dhcp.@dnsmasq[0].ednspacket_max"], "1232")

    def test_tailscale_main_and_helper_remain_disabled(self):
        state = self.apply_writes(self.run_defaults(), {})
        self.assertEqual(state["tailscale.settings.service_enabled"], "0")
        self.assertEqual(self.service_calls, ["tailscale disable", "tailscale-settings disable"])

    def test_https_listener_is_configured_without_shipping_device_keys(self):
        state = self.apply_writes(self.run_defaults(), {
            "uhttpd.main.listen_https": ["old-listener:443"],
        })
        self.assertEqual(state["uhttpd.main.listen_https"], ["0.0.0.0:443", "[::]:443"])
        self.assertEqual(state["uhttpd.main.redirect_https"], "0")
        self.assertEqual(state["uhttpd.defaults.commonname"], "RC01")
        self.assertFalse(any((ROOT / "config/rc01/files/etc" / name).exists()
                             for name in ("uhttpd.key", "uhttpd.crt")))

    def test_other_board_receives_no_uci_writes(self):
        self.assertEqual(self.run_defaults("other,board"), "")
