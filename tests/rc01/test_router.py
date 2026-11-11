"""Verify manual network ownership and disabled APs using the actual scripts."""
from pathlib import Path
import copy
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
    def wireless_fixture(self, band="5g", first_channel=36, width=160):
        cap_band = {"2g": "2G", "5g": "5G", "6g": "6G"}[band]
        values = {
            "router-tool": {},
            "wireless": {
                "radio0": {".name": "radio0", ".type": "wifi-device", "path": "pci-test",
                           "band": band, "disabled": "1"},
                "ap0": {".name": "ap0", ".type": "wifi-iface", "device": "radio0",
                        "mode": "ap", "disabled": "1", "ssid": "OWRT",
                        "key": "12345678", "encryption": "psk2+ccmp"},
            },
        }
        board = {"model": {"id": "xiaomi,be10000"}, "wlan": {
            "phy0": {"path": "pci-test", "info": {"bands": {
                cap_band: {"default_channel": first_channel, "max_width": width,
                           "modes": ["EHT20", "EHT40", "EHT80", "EHT160"]},
            }}},
        }}
        return values, board

    def run_wireless(self, values, board, operations=None):
        module = TOOL / "root/usr/share/router-tool/wireless.uc"
        script = f"import {{ synchronize_wireless }} from {json.dumps(str(module))};\n"
        script += "let values = " + json.dumps(values) + ";\n"
        script += "let board = " + json.dumps(board) + ";\n"
        script += r'''
let writes = 0;
let cursor = {
  get: (c, s, k) => values[c]?.[s]?.[k],
  get_all: (c, s) => values[c]?.[s],
  get_first: () => 'RC01',
  foreach: (c, t, fn) => { for (let s, v in values[c] || {}) if (v['.type'] == t) fn(v); },
  set: (c, s, k, v) => {
    writes++;
    if (v == null) { values[c][s] = { '.type': k, '.name': s }; return true; }
    values[c][s][k] = v; return true;
  },
  delete: (c, s, k) => { writes++; delete values[c][s][k]; return true; }
};
'''
        script += operations or r'''
let changed = synchronize_wireless(cursor, board);
print(sprintf('%J', { values, changed, writes }));
'''
        result = subprocess.run([UCODE, "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

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
    lan: { '.type': 'interface', ipaddr: '192.0.2.3/24' },
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
            (path / "fs.uc").write_text('export const rc01_fixture = true;\n'
                                        'export function readfile(path) { return path == "/tmp/sysinfo/board_name" ? "xiaomi,be10000\\n" : "02:00:00:00:00:01\\n"; }\n')
            (path / "uci.uc").write_text('export function cursor() { return { get_all: () => ({}) }; }\n')
            board = '''export function board_wlan() {
              return { phy0: { path: 'platform/test', info: { radios: [],
                bands: { '2G': { max_width: 20, he: true } } } }, defaults: { country: 'US' } };
            }'''
            (path / "wifi").mkdir()
            (path / "wifi/utils.uc").write_text(board)
            # The board-name stub must win over the native fs module; a UCODE
            # wrapper that prepends its own -L would otherwise read the host.
            probe = subprocess.run([UCODE, "-L", str(path / "*.uc"), "-"], capture_output=True, text=True,
                                   input='import { rc01_fixture } from "fs"; print(rc01_fixture ? "fixture" : "native");')
            self.assertEqual(probe.stdout, "fixture",
                             "UCODE resolves the native fs module before test stubs; use an installed "
                             "host ucode such as staging_dir/hostpkg/bin/ucode\n" + probe.stderr)
            result = subprocess.run([UCODE, "-L", str(path / "*.uc"), str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("set wireless.radio0.disabled='1'", result.stdout)
            self.assertIn("set wireless.default_radio0.disabled='1'", result.stdout)

    @unittest.skipUnless(UCODE, "ucode is required for restricted-band defaults regression")
    def test_restricted_high_5g_defaults_remain_disabled_and_legal(self):
        module = TOOL / "root/usr/share/router-tool/wireless.uc"
        script = f"import {{ synchronize_wireless }} from {json.dumps(str(module))};\n"
        script += r'''
let values = {
  'router-tool': {},
  wireless: {
    radio0: { '.name': 'radio0', '.type': 'wifi-device', path: 'pci-high', band: '5g', disabled: '1' },
    ap0: { '.name': 'ap0', '.type': 'wifi-iface', device: 'radio0', mode: 'ap', disabled: '1',
           ssid: 'OWRT', key: '12345678', encryption: 'psk2+ccmp' }
  }
};
let cursor = {
  get: (c, s, k) => values[c]?.[s]?.[k],
  get_all: (c, s) => values[c]?.[s],
  get_first: () => 'RC01',
  foreach: (c, t, fn) => { for (let s, v in values[c] || {}) if (v['.type'] == t) fn(v); },
  set: (c, s, k, v) => {
    if (v == null) { values[c][s] = { '.type': k }; return true; }
    values[c][s][k] = v; return true;
  },
  delete: (c, s, k) => { delete values[c][s][k]; return true; }
};
let board = { model: { id: 'xiaomi,be10000' }, wlan: {
  phy2: { path: 'pci-high', info: { bands: { '5G': {
    default_channel: 100, max_width: 160, modes: ['EHT20', 'EHT40', 'EHT80', 'EHT160']
  } } } }
} };
synchronize_wireless(cursor, board);
if (values.wireless.radio0.channel != 'auto' || values.wireless.radio0.htmode != 'EHT80')
  die('high-band card received unsupported low-band defaults');
if (values.wireless.radio0.disabled != '1' || values.wireless.ap0.disabled != '1')
  die('defaults enabled an AP');
if (values['router-tool'].radio0.defaults_done != '1') die('defaults marker missing');
values.wireless.radio0.channel = '149';
synchronize_wireless(cursor, board);
if (values.wireless.radio0.channel != '149') die('saved user channel was overwritten');
print('restricted-band-check:ok\n');
'''
        result = subprocess.run([UCODE, "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "restricted-band-check:ok\n")

    @unittest.skipUnless(UCODE, "ucode is required for per-band defaults regression")
    def test_band_defaults_and_repeat_run_preserve_disabled_state(self):
        for band, first, width, channel, mode, suffix, encryption in (
            ("2g", 6, 40, "1", "EHT20", "2.4G", "psk2+ccmp"),
            ("5g", 36, 160, "auto", "EHT160", "5.2G", "psk2+ccmp"),
            ("5g", 100, 160, "auto", "EHT80", "5.8G", "psk2+ccmp"),
            ("5g", 36, 80, "auto", "EHT80", "5.8G", "psk2+ccmp"),
            ("6g", 5, 160, "auto", "EHT160", "6G", "sae+ccmp"),
        ):
            with self.subTest(band=band, first=first, width=width):
                values, board = self.wireless_fixture(band, first, width)
                result = self.run_wireless(values, board, r'''
let first = synchronize_wireless(cursor, board);
let initialized = sprintf('%J', values);
let first_writes = writes;
let second = synchronize_wireless(cursor, board);
print(sprintf('%J', { values, first, second, unchanged: initialized == sprintf('%J', values),
                     repeat_writes: writes - first_writes }));
''')
                radio, ap = result["values"]["wireless"]["radio0"], result["values"]["wireless"]["ap0"]
                self.assertEqual((radio["channel"], radio["htmode"]), (channel, mode))
                self.assertEqual(ap["ssid"], "RC01-Xiaomi-" + suffix)
                self.assertEqual(ap["encryption"], encryption)
                self.assertEqual(ap.get("ieee80211w"), "2" if band == "6g" else None)
                self.assertEqual((radio["disabled"], ap["disabled"]), ("1", "1"))
                self.assertEqual(result["values"]["router-tool"]["radio0"]["defaults_done"], "1")
                self.assertCountEqual(result["first"], ["wireless", "router-tool"])
                self.assertFalse(result["second"])
                self.assertTrue(result["unchanged"])
                self.assertEqual(result["repeat_writes"], 0)

    @unittest.skipUnless(UCODE, "ucode is required for child-radio selection regression")
    def test_selected_high_5g_child_overrides_aggregate_low_channel(self):
        for index in (1, "1"):
            with self.subTest(index=index):
                values, board = self.wireless_fixture()
                values["wireless"]["radio0"]["radio"] = index
                board["wlan"]["phy0"]["info"]["radios"] = [
                    {"index": 0, "bands": {"5G": {"default_channel": 36}}},
                    {"index": 1, "bands": {"5G": {"default_channel": 100}}},
                ]
                result = self.run_wireless(values, board)
                radio, ap = result["values"]["wireless"]["radio0"], result["values"]["wireless"]["ap0"]
                self.assertEqual((radio["channel"], radio["htmode"]), ("auto", "EHT80"))
                self.assertEqual(ap["ssid"], "RC01-Xiaomi-5.8G")
                self.assertEqual((radio["disabled"], ap["disabled"]), ("1", "1"))

    @unittest.skipUnless(UCODE, "ucode is required for missing child-radio regression")
    def test_missing_child_or_band_waits_without_initializing_or_marking(self):
        for children in (
            [{"index": 0, "bands": {"5G": {"default_channel": 36}}}],
            [{"index": 1, "bands": {"2G": {"default_channel": 1}}}],
            [],
            None,
        ):
            with self.subTest(children=children):
                values, board = self.wireless_fixture()
                values["wireless"]["radio0"]["radio"] = "1"
                if children is not None:
                    board["wlan"]["phy0"]["info"]["radios"] = children
                result = self.run_wireless(values, board)
                self.assertEqual(result["values"], values)
                self.assertFalse(result["changed"])
                self.assertEqual(result["writes"], 0)

    @unittest.skipUnless(UCODE, "ucode is required for delayed child-radio capabilities regression")
    def test_pending_child_initializes_after_its_capabilities_arrive(self):
        values, board = self.wireless_fixture()
        values["wireless"]["radio0"]["radio"] = "1"
        board["wlan"]["phy0"]["info"]["radios"] = []
        result = self.run_wireless(values, board, r'''
let first = synchronize_wireless(cursor, board);
let pending = sprintf('%J', values);
let pending_writes = writes;
board.wlan.phy0.info.radios = [ { index: 1, bands: { '5G': { default_channel: 100 } } } ];
let second = synchronize_wireless(cursor, board);
print(sprintf('%J', { values, first, second, pending, pending_writes }));
''')
        self.assertFalse(result["first"])
        self.assertEqual(json.loads(result["pending"]), values)
        self.assertEqual(result["pending_writes"], 0)
        radio = result["values"]["wireless"]["radio0"]
        self.assertEqual((radio["channel"], radio["htmode"], radio["disabled"]), ("auto", "EHT80", "1"))
        self.assertEqual(result["values"]["wireless"]["ap0"]["disabled"], "1")
        self.assertEqual(result["values"]["router-tool"]["radio0"]["defaults_done"], "1")
        self.assertCountEqual(result["second"], ["wireless", "router-tool"])

    @unittest.skipUnless(UCODE, "ucode is required for customized AP protection regression")
    def test_custom_credentials_and_non_ap_profiles_preserve_wireless_options(self):
        variants = [
            {"ssid": "My-custom-SSID"}, {"key": "already-private"}, {"encryption": "sae+ccmp"},
            {"mode": "sta"}, {"mode": "mesh"}, {"wds": "1"}, {"mlo": "1"},
            {"device": ["radio0", "radio1"]},
        ]
        for options in variants:
            with self.subTest(options=options):
                values, board = self.wireless_fixture()
                values["wireless"]["ap0"].update(options)
                values["wireless"]["radio0"].update({"channel": "149", "htmode": "EHT40", "country": "CN"})
                original_wireless = copy.deepcopy(values["wireless"])
                result = self.run_wireless(values, board)
                self.assertEqual(result["values"]["wireless"], original_wireless)
                self.assertEqual(result["values"]["router-tool"]["radio0"]["defaults_done"], "1")
                self.assertEqual(result["changed"], ["router-tool"])

    @unittest.skipUnless(UCODE, "ucode is required for saved defaults marker regression")
    def test_marked_radio_keeps_even_untouched_ap_and_manual_channel(self):
        values, board = self.wireless_fixture()
        values["router-tool"]["radio0"] = {".name": "radio0", ".type": "radio", "defaults_done": "1"}
        values["wireless"]["radio0"].update({"channel": "157", "htmode": "EHT40", "country": "CN"})
        result = self.run_wireless(values, board)
        self.assertEqual(result["values"], values)
        self.assertFalse(result["changed"])
        self.assertEqual(result["writes"], 0)

    @unittest.skipUnless(UCODE, "ucode is required for missing AP retry regression")
    def test_radio_without_interfaces_remains_unmarked_for_later_initialization(self):
        values, board = self.wireless_fixture()
        del values["wireless"]["ap0"]
        result = self.run_wireless(values, board)
        self.assertEqual(result["values"], values)
        self.assertFalse(result["changed"])
        self.assertEqual(result["writes"], 0)


if __name__ == "__main__":
    unittest.main()
