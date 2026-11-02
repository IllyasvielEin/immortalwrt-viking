"""Verify manual network ownership and disabled APs using the actual scripts."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "package/emortal/luci-app-router-tool"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")
NODE = shutil.which("node")


class Router(unittest.TestCase):
    @unittest.skipUnless(NODE, "Node.js is required for LuCI save regression")
    def test_luci_save_preserves_manual_network_without_snapshot_or_rpc(self):
        source = TOOL / "htdocs/luci-static/resources/tools/router-tool.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
const config = {
  'router-tool': { main: { '.type': 'settings', auto_network: '0',
    auto_role: 'sub', state: 'obsolete-routed-snapshot' } },
  network: { globals: { '.name': 'globals', '.type': 'globals' } }
};
const uci = {
  state: { values: config },
  get(c, s, k) { return k === undefined ? config[c]?.[s] : config[c]?.[s]?.[k]; },
  sections(c, t) { return Object.values(config[c] || {}).filter(v => v['.type'] === t); },
  set() { throw Error('manual save unexpectedly wrote a preset'); },
  unset() { throw Error('manual save unexpectedly deleted a setting'); }
};
const rpc = { declare() { return () => { throw Error('manual save unexpectedly queried a role snapshot'); }; } };
const factory = new Function('baseclass', 'form', 'fs', 'rpc', 'ui', 'uci', 'L', '_',
  fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, {}, {}, rpc, {}, uci, { toArray: v => v || [] }, v => v);
const map = { chain() {}, load() { return Promise.resolve(); }, save(cb) { return Promise.resolve().then(cb); } };
api.attach(map);
map.save(() => 'saved').then(result => {
  assert.equal(result, 'saved');
  assert.equal(config['router-tool'].main.auto_network, '0');
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([NODE, "-e", program, str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(UCODE, "ucode is required for runtime preset regression")
    def test_manual_mode_keeps_lan_dhcp_and_wan_but_initializes_ppe_once(self):
        module = TOOL / "root/usr/share/router-tool/auto.uc"
        presets = TOOL / "root/usr/share/router-tool/auto.json"
        script = f"import {{ synchronize }} from {json.dumps(str(module))};\n"
        script += "let presets = " + presets.read_text() + ";\n"
        script += r'''
let values = {
  'router-tool': { main: { '.type': 'settings', '.name': 'main', auto_network: '0',
    auto_role: 'sub', state: 'obsolete-routed-snapshot' } },
  network: {
    globals: { '.type': 'globals', '.name': 'globals' },
    lan: { '.type': 'interface', ipaddr: '192.168.50.3/24' },
    wan: { '.type': 'interface', auto: '0', proto: 'dhcp' }
  },
  firewall: { defaults: { '.type': 'defaults', '.name': 'defaults', flow_offloading: '0' } },
  dhcp: { lan: { '.type': 'dhcp', ignore: '1', ra: 'disabled', dhcpv6: 'disabled' } }
};
let cursor = {
  get: (c, s, k) => values[c]?.[s]?.[k],
  get_all: (c, s) => values[c]?.[s],
  foreach: (c, t, fn) => { for (let s, v in values[c] || {}) if (v['.type'] == t) fn(v); },
  set: (c, s, k, v) => { values[c][s][k] = v; return true; },
  delete: (c, s, k) => { delete values[c][s][k]; return true; }
};
let before = sprintf('%J', [values.network.lan, values.network.wan, values.dhcp]);
synchronize(cursor, presets, [], 'qualcommbe/ipq95xx');
if (sprintf('%J', [values.network.lan, values.network.wan, values.dhcp]) != before)
  die('manual network/DHCP settings changed');
if (values.firewall.defaults.flow_offloading != '1' || values.firewall.defaults.flow_offloading_hw != '1')
  die('PPE platform defaults were lost');
values.firewall.defaults.flow_offloading = '0';
synchronize(cursor, presets, [], 'qualcommbe/ipq95xx');
if (values.firewall.defaults.flow_offloading != '0') die('explicit offload choice overwritten');
print('manual-preset-check:ok\n');
'''
        result = subprocess.run([UCODE, "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "manual-preset-check:ok\n")

    @unittest.skipUnless(UCODE, "ucode is required for first-boot wireless regression")
    def test_first_generated_rc01_ap_is_disabled_before_any_service_reload(self):
        source = ROOT / "package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc"
        with tempfile.TemporaryDirectory(prefix="rc01-wireless-test-") as temporary:
            path = Path(temporary)
            (path / "fs.uc").write_text('export function readfile(path) { return path == "/tmp/sysinfo/board_name" ? "xiaomi,be10000\\n" : "02:00:00:00:00:01\\n"; }\n')
            (path / "uci.uc").write_text('export function cursor() { return { get_all: () => ({}) }; }\n')
            board = '''export function board_wlan() {
              return { phy0: { path: 'platform/test', info: { radios: [],
                bands: { '2G': { max_width: 20, he: true } } } }, defaults: { country: 'US' } };
            }'''
            (path / "wifi").mkdir()
            (path / "wifi/utils.uc").write_text(board)
            result = subprocess.run([UCODE, "-L", str(path / "*.uc"), str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("set wireless.radio0.disabled='1'", result.stdout)
            self.assertIn("set wireless.default_radio0.disabled='1'", result.stdout)


if __name__ == "__main__":
    unittest.main()
