"""Verify manual network ownership and RC01 factory Wi-Fi using the actual scripts."""
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
WIFI = ROOT / "package/network/config/wifi-scripts/files"
GENERATOR = WIFI / "lib/wifi/mac80211.uc"
CHANNELS = WIFI / "usr/share/ucode/wifi/channels.uc"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")
NODE = shutil.which("node")

# Test-only replacements for the native modules imported by mac80211.uc. The
# generator, its retry loop and wifi.channels run unchanged.
FS_STUB = r'''
const S = %s;
let files = {};
export const rc01_fixture = true;
export function readfile(p) {
	if (p == '/tmp/sysinfo/board_name') return S.board_name + '\n';
	let m = match(p, /^\/sys\/class\/ieee80211\/([^\/]+)\/macaddress$/);
	return m ? (S.macs[m[1]] ?? '02:00:00:00:00:ff') + '\n' : null;
}
export function stat(p) {
	if (p == '/etc/config/wireless' || files[p]) return { type: 'file', size: 0 };
	return p == '/usr/share/schema/wireless.wifi-device.json' && S.live_plan ? { type: 'file' } : null;
}
export function open(p) {
	if (p == '/var/run/wifi-config.notify') files[p] = true;
	return { close: () => true, lock: () => true };
}
export function unlink(p) { delete files[p]; return true; }
export function glob() { return []; }
export function realpath(p) { return S.realpaths[p]; }
'''
UCI_STUB = r'''
let state = %s;
const copy = (v) => json(sprintf('%%J', v));
export function cursor() {
	return {
		get_all: (c) => { let n = replace(c, /^\/etc\/config\//, ''); return state[n] ? copy(state[n]) : null; },
		get_first: (c, t, o) => c == 'system' && t == 'system' && o == 'hostname' ? 'RC01' : null,
		set: function(c, s, k, v) {
			if (v == null) { state[c][s] = { '.type': k, '.name': s }; return true; }
			if (type(v) != 'string' || !state[c][s]) die(`invalid uci set ${s}.${k}`);
			state[c][s][k] = v;
			return true;
		},
		commit: (c) => { print('COMMIT ', sprintf('%%J', state[c]), '\n'); return true; }
	};
}
'''
UBUS_STUB = r'''
const S = %s;
let calls = [];
export function error() { return null; }
export function connect() {
	return {
		call: function(obj, method, args) {
			push(calls, [ obj, method, args ]);
			return obj == 'network.wireless' && method == 'status' ? S.status : {};
		},
		disconnect: () => print('CALLS ', sprintf('%%J', calls), '\n')
	};
}
'''
# board.json as written by wifi-detect.uc from the same nl80211 band data.
UTILS_STUB = r'''
import { freq_to_channel, freq_range_match, usable_channel_blocks, band_capabilities } from 'wifi.channels';
const S = %s;
export function find_phy(config, rename) {
	if (rename !== true) die('hotplug retry must allow a renamed PHY');
	return config?.phy;
}
export function json_file_read(path) {
	if (path != '/etc/board.json') return null;
	let wlan = {};
	for (let name, phy in S.phys) {
		let info = { bands: {}, radios: map(phy.radios ?? [], (r) => ({ index: r.index, freq_ranges: r.freq_ranges, bands: {} })) };
		for (let band_name, band in phy.bands) {
			let band_info = info.bands[band_name] = band_capabilities(band, band_name);
			let ap = band_info.ap = band_capabilities(band, band_name, true);
			band_info.channel_blocks = usable_channel_blocks(band_name, band.freqs, band_info.max_width);
			ap.channel_blocks = usable_channel_blocks(band_name, band.freqs, ap.max_width);
			for (let radio in info.radios) {
				let freqs = filter(band.freqs, (f) => freq_range_match(radio.freq_ranges, f.freq));
				if (!length(freqs)) continue;
				radio.bands[band_name] = {
					channel_blocks: usable_channel_blocks(band_name, freqs, band_info.max_width, radio.freq_ranges),
					ap_channel_blocks: usable_channel_blocks(band_name, freqs, ap.max_width, radio.freq_ranges),
					default_channel: freq_to_channel(freqs[0].freq)
				};
			}
			band_info.default_channel = freq_to_channel(band.freqs[0].freq);
		}
		wlan[name] = { path: phy.path, info };
	}
	return { model: { id: 'xiaomi,be10000' }, wlan };
}
'''


def nl_band(band, he=0x0E, eht=0x03, vht=0x0C, ht=0x02, start=None, end=None):
    """An nl80211 wiphy band: all channels usable, AP iftype capabilities."""
    first, last, step = {"2G": (2412, 2472, 5), "5G": (5180, 5885, 20), "6G": (5955, 7115, 20)}[band]
    freqs = [{"freq": f} for f in range(first, last + 1, step)
             if (band != "5G" or not 5340 <= f <= 5480) and (start is None or start <= f <= end)]
    iftype = {"iftypes": {"ap": True}, "he_cap_phy": [he]}
    if eht is not None:
        iftype["eht_cap_phy"] = [eht]
    return {"ht_capa": 0 if band == "6G" else ht, "vht_capa": vht if band == "5G" else 0,
            "iftype_data": [iftype], "freqs": freqs}


# A single EHT wiphy with 2.4 GHz, low 5 GHz, high 5 GHz and 6 GHz child radios.
RANGES = {"2G": [2401000, 2483000], "5GL": [5170000, 5330000],
          "5GH": [5490000, 5895000], "6G": [5945000, 7125000]}


def multi_radio_phy(children=("2G", "5GL", "5GH", "6G")):
    return {"path": "platform/soc/wifi", "bands": {b: nl_band(b) for b in ("2G", "5G", "6G")},
            "radios": [{"index": i, "freq_ranges": [RANGES[c]]} for i, c in enumerate(children)]}


def run_wifi_config(phys, wireless=None, board_name="xiaomi,be10000", live_plan=True,
                    args=(), devpath=None, status=None, realpaths=None):
    """Run the production mac80211.uc; return its exit code, commit and ubus calls."""
    with tempfile.TemporaryDirectory(prefix="rc01-wifi-config-") as temporary:
        path = Path(temporary)
        (path / "wifi").mkdir()
        (path / "bin").mkdir()
        dump = json.dumps
        (path / "fs.uc").write_text(FS_STUB % dump({
            "board_name": board_name, "live_plan": live_plan, "realpaths": realpaths or {},
            "macs": {name: f"02:00:00:00:00:{i:02x}" for i, name in enumerate(phys)}}))
        (path / "uci.uc").write_text(UCI_STUB % dump({"wireless": wireless or {}}))
        (path / "ubus.uc").write_text(UBUS_STUB % dump({"status": status or {}}))
        (path / "wifi/utils.uc").write_text(UTILS_STUB % dump({"phys": phys}))
        (path / "wifi/channels.uc").symlink_to(CHANNELS)
        # generate() first refreshes board.json through wifi-detect.uc.
        (path / "bin/ucode").write_text("#!/bin/sh\nexit 0\n")
        (path / "bin/ucode").chmod(0o755)
        probe = subprocess.run([UCODE, "-L", str(path / "*.uc"), "-"], capture_output=True, text=True,
                               input='import { rc01_fixture } from "fs"; print(rc01_fixture ? "fixture" : "native");')
        if probe.stdout != "fixture":
            raise AssertionError("UCODE resolves the native fs module before test stubs; use an installed "
                                 "host ucode such as staging_dir/hostpkg/bin/ucode\n" + probe.stderr)
        env = {**os.environ, "PATH": f"{path / 'bin'}:{os.environ.get('PATH', '')}"}
        env.pop("DEVPATH", None)
        if devpath:
            env["DEVPATH"] = devpath
        result = subprocess.run([UCODE, "-L", str(path / "*.uc"), str(GENERATOR), *args],
                                capture_output=True, text=True, env=env, timeout=20)
    out = {"code": result.returncode, "stderr": result.stderr, "commit": None, "calls": []}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "COMMIT":
            out["commit"] = json.loads(value)
        elif key == "CALLS":
            out["calls"] += json.loads(value)
    return out


def runtime_plan(band, radio_range, device):
    """Plan an AP as the ucode netifd backend does when the radio starts."""
    script = f"import {{ ap_channel_plan }} from {json.dumps(str(CHANNELS))};\n"
    script += "let info = { radios: [ { index: 0, freq_ranges: [ { start: %d, end: %d } ] } ] };\n" % tuple(radio_range)
    script += f"print(sprintf('%J', ap_channel_plan(info, {json.dumps(band)}, {json.dumps(device)})));\n"
    result = subprocess.run([UCODE, "-"], input=script, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


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
    lan: { '.type': 'interface', ipaddr: '192.0.2.3/24' },
    wan: { '.type': 'interface', auto: '0', proto: 'dhcp' }
  },
  // Upstream keeps an explicit offload choice; with none present the platform
  // preset initializes once (firewall4 itself ships flow_offloading 1/1).
  firewall: { defaults: { '.type': 'defaults', '.name': 'defaults' } },
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



@unittest.skipUnless(UCODE, "ucode is required for factory Wi-Fi regression")
class FactoryWifi(unittest.TestCase):
    def generate(self, phys, **kwargs):
        result = run_wifi_config(phys, **kwargs)
        self.assertEqual(result["code"], 0, result["stderr"])
        self.assertIsNotNone(result["commit"], result["stderr"])
        return result["commit"]

    def test_rc01_radios_and_aps_start_disabled_with_fixed_band_widths(self):
        config = self.generate({"phy0": multi_radio_phy()})
        expected = [("2g", "0", "EHT20", "2.4G"), ("5g", "1", "EHT160", "5.2G"),
                    ("5g", "2", "EHT80", "5.8G"), ("6g", "3", "EHT160", "6G")]
        self.assertCountEqual(config, [f"{p}radio{i}" for i in range(4) for p in ("", "default_")])
        for i, (band, child, htmode, suffix) in enumerate(expected):
            with self.subTest(band=band, child=child):
                radio, ap = config[f"radio{i}"], config[f"default_radio{i}"]
                self.assertEqual({k: radio.get(k) for k in ("band", "radio", "channel", "htmode", "country", "disabled")},
                                 {"band": band, "radio": child, "channel": "auto", "htmode": htmode,
                                  "country": "US", "disabled": "1"})
                # Neither runtime re-planning nor a frozen channel list.
                self.assertNotIn("auto_htmode", radio)
                self.assertNotIn("channels", radio)
                self.assertEqual(ap["disabled"], "1")
                self.assertEqual(ap["ssid"], "RC01-Xiaomi-" + suffix)
                self.assertEqual(ap["key"], "12345678")
                self.assertEqual(ap["encryption"], "sae+ccmp" if band == "6g" else "psk2+ccmp")
                self.assertEqual(ap.get("ieee80211w"), "2" if band == "6g" else None)

    def test_runtime_keeps_rc01_width_although_eht320_is_usable(self):
        config = self.generate({"phy0": multi_radio_phy(("6G",))})
        radio = config["radio0"]
        device = {k: radio[k] for k in ("band", "channel", "htmode")}
        device["radio"] = "0"
        plan = runtime_plan(nl_band("6G"), RANGES["6G"], device)
        self.assertEqual(plan["htmode"], "EHT160")
        self.assertTrue(plan["channels"])
        # Upstream's auto_htmode would replan to the widest usable mode.
        self.assertEqual(runtime_plan(nl_band("6G"), RANGES["6G"], {**device, "auto_htmode": "EHT160"})["htmode"],
                         "EHT320")

    def test_child_modes_and_frequency_ranges_bound_rc01_widths(self):
        for name, phy, htmode in (
            ("80 MHz child span", {"bands": {"5G": nl_band("5G")}, "radios": [{"index": 0, "freq_ranges": [[5170000, 5250000]]}]}, "EHT80"),
            ("40 MHz child span", {"bands": {"5G": nl_band("5G")}, "radios": [{"index": 0, "freq_ranges": [[5170000, 5230000]]}]}, "EHT40"),
            ("HE80 radio", {"bands": {"5G": nl_band("5G", he=0x04, eht=None, vht=0)}}, "HE80"),
            ("high 5 GHz only", {"bands": {"5G": nl_band("5G", start=5500, end=5885)}}, "EHT80"),
            ("2.4 GHz HT40-capable", {"bands": {"2G": nl_band("2G")}}, "EHT20"),
        ):
            with self.subTest(name):
                config = self.generate({"phy0": {"path": "platform/soc/wifi", **phy}})
                self.assertEqual(config["radio0"]["htmode"], htmode)
                self.assertEqual(config["radio0"]["disabled"], "1")

    def test_shell_backend_gets_capped_channel_list_without_replanning(self):
        config = self.generate({"phy0": multi_radio_phy(("6G",))}, live_plan=False)
        radio = config["radio0"]
        self.assertEqual(radio["htmode"], "EHT160")
        self.assertNotIn("auto_htmode", radio)
        self.assertEqual(radio["channels"].split()[:8], [str(c) for c in range(1, 30, 4)])

    def test_existing_or_edited_sections_are_never_rewritten(self):
        config = self.generate({"phy0": multi_radio_phy()})
        rerun = run_wifi_config({"phy0": multi_radio_phy()}, wireless=config)
        self.assertEqual((rerun["code"], rerun["commit"]), (0, None), rerun["stderr"])
        edited = copy.deepcopy(config)
        edited["radio1"].update({"disabled": "0", "htmode": "EHT40", "channel": "36"})
        edited["default_radio1"].update({"disabled": "0", "ssid": "Home"})
        del edited["radio3"]["disabled"]
        # A later PHY adds only its own disabled defaults.
        later = {"phy0": multi_radio_phy(), "phy1": {"path": "pci0000:00/0000:00:00.0", "bands": {"5G": nl_band("5G")}}}
        updated = self.generate(later, wireless=edited)
        self.assertEqual({k: v for k, v in updated.items() if k in edited}, edited)
        self.assertEqual(set(updated) - set(edited), {"radio4", "default_radio4"})
        self.assertEqual((updated["radio4"]["disabled"], updated["default_radio4"]["disabled"]), ("1", "1"))

    def test_child_without_band_frequencies_creates_no_section(self):
        phy = multi_radio_phy(("5GL",))
        phy["radios"].append({"index": 1, "freq_ranges": [[3000000, 3100000]]})
        config = self.generate({"phy0": phy})
        self.assertEqual(set(config), {"radio0", "default_radio0"})
        self.assertEqual(config["radio0"]["radio"], "0")

    def test_rc01_width_does_not_freeze_a_transient_first_regdom(self):
        # The first PHY event may precede the final regulatory domain. Without
        # auto_htmode a width planned from those channels would never recover.
        def restricted(child, enabled):
            phy = multi_radio_phy((child,))
            band = "6G" if child == "6G" else "5G"
            for freq in phy["bands"][band]["freqs"]:
                if not enabled(freq["freq"]):
                    freq["disabled"] = True
            return phy
        for name, phy, live_plan in (
            ("6 GHz disabled", restricted("6G", lambda f: False), True),
            ("low 5 GHz 36-48 only", restricted("5GL", lambda f: f <= 5240), True),
            ("low 5 GHz 36-48 only, shell backend", restricted("5GL", lambda f: f <= 5240), False),
        ):
            with self.subTest(name):
                radio = self.generate({"phy0": phy}, live_plan=live_plan)["radio0"]
                self.assertEqual((radio["channel"], radio["htmode"]), ("auto", "EHT160"))
                self.assertNotIn("auto_htmode", radio)
                self.assertNotIn("channels", radio)
                self.assertEqual(radio["disabled"], "1")

    def test_es_variant_and_unreadable_board_name_keep_upstream_defaults(self):
        for board_name in ("xiaomi,be10000-es", ""):
            with self.subTest(board_name=board_name):
                config = self.generate({"phy0": multi_radio_phy(("6G",))}, board_name=board_name)
                radio, ap = config["radio0"], config["default_radio0"]
                self.assertNotIn("disabled", radio)
                self.assertEqual(ap["disabled"], "0")
                self.assertEqual((radio["htmode"], radio["auto_htmode"]), ("EHT320", "EHT320"))

    def test_other_boards_keep_upstream_automatic_widths(self):
        config = self.generate({"phy0": multi_radio_phy()}, board_name="example,board")
        for i, htmode in enumerate(("EHT40", "EHT160", "EHT160", "EHT320")):
            with self.subTest(radio=i):
                radio, ap = config[f"radio{i}"], config[f"default_radio{i}"]
                self.assertNotIn("disabled", radio)
                self.assertEqual(ap["disabled"], "0")
                self.assertEqual((radio["channel"], radio["htmode"], radio["auto_htmode"]), ("auto", htmode, htmode))


if __name__ == "__main__":
    unittest.main()
