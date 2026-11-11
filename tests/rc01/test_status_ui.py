"""Exercise CLI/RPC contracts and stale-reading behavior without a browser."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "package/emortal/luci-app-rc01"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")
NODE = shutil.which("node")


class StatusInterfaces(unittest.TestCase):
    @unittest.skipUnless(UCODE, "ucode is required for CLI/RPC contracts")
    def test_cli_exit_codes_and_rpc_read_only_method(self):
        # The collector itself is exercised against real files in test_status.
        # Here the controlled result isolates dispatch and exit-code behavior.
        with tempfile.TemporaryDirectory(prefix="rc01-status-api-") as temporary:
            path = Path(temporary)
            (path / "rc01").mkdir()
            (path / "uci.uc").write_text("export function cursor() { return {}; }\n")
            (path / "rc01/platform.uc").write_text('''export function snapshot() {
                return { schema: 1, preflight: [
                  { target: 'hardware', passed: true, checks: [] },
                  { target: 'nikki', passed: true, checks: [] },
                  { target: 'dae', passed: false, checks: [] },
                  { target: 'daed', passed: false, checks: [] }
                ] };
            }\n''')
            command = [UCODE, "-L", str(path / "*.uc")]
            cli = ROOT / "package/emortal/rc01-platform/files/rc01-check"
            for args, code in [(["status"], 0), (["check", "hardware"], 0),
                               (["check", "dae"], 1), (["check", "daed"], 1), (["check", "invalid"], 2),
                               (["status", "nikki"], 2)]:
                result = subprocess.run(command + [str(cli)] + args, text=True, capture_output=True)
                self.assertEqual(result.returncode, code, result.stderr)
                if code == 2:
                    self.assertIn("Usage:", result.stderr)
                else:
                    self.assertIsInstance(json.loads(result.stdout), dict)
            rpc = APP / "root/usr/share/rpcd/ucode/rc01.uc"
            source = f"let api = loadfile({json.dumps(str(rpc))})();\n"
            source += "print(sprintf('%J', { objects: keys(api), methods: keys(api.rc01), status: api.rc01.status.call() }));\n"
            result = subprocess.run(command + ["-"], input=source, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            api = json.loads(result.stdout)
            self.assertEqual(api["objects"], ["rc01"])
            self.assertEqual(api["methods"], ["status"])
            self.assertEqual(api["status"]["schema"], 1)

    def test_rpc_acl_grants_only_status_read(self):
        acl = json.loads((APP / "root/usr/share/rpcd/acl.d/luci-app-rc01.json").read_text())
        entry = acl["luci-app-rc01"]
        self.assertNotIn("write", entry)
        self.assertEqual(entry["read"], {"ubus": {"rc01": ["status"]}})

    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_status_view_clears_readings_on_refresh_failure_and_has_no_save(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
let refresh;
const rpc = { declare: () => () => Promise.reject(Error('offline')) };
const poll = { add: fn => { refresh = fn; } };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const dom = { content: (node, children) => { node.children = children; } };
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, poll, dom, E, v => v);
const state = {
  schema: 1, board: 'xiaomi,be10000', kernel: '6.18.55',
  temperatures: [{ name: 'nss-top-thermal', celsius: 45 }],
  fans: [{ hwmon: 'hwmon8', rpm: 960, pwm: 77 }], ports: [], processes: [],
  storage: { rootfs: { size: 241434624 }, environment_map_valid: true },
  ppe: { active_flows: { total: 0 } }, proxy: { btf: false },
  preflight: [{ target: 'hardware', checks: [{ level: 'ok', message: 'Requires Xiaomi RC01 / BE10000' }] }]
};
const node = api.render(state);
assert(JSON.stringify(node).includes('hwmon8'));
assert.strictEqual(api.handleSave, null);
assert.strictEqual(api.handleSaveApply, null);
refresh().then(() => {
  assert(!JSON.stringify(node).includes('hwmon8'));
  assert(JSON.stringify(node).includes('Previous values are no longer current'));
}).catch(e => { console.error(e); process.exitCode = 1; });
'''
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_large_counters_render_exactly_and_repeated_samples_are_stale(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
let refresh, responses = [];
const rpc = { declare: () => () => Promise.resolve(responses.shift()) };
const poll = { add: fn => { refresh = fn; } };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const dom = { content: (node, children) => { node.children = children; } };
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, poll, dom, E, v => v);
const base = {
  schema: 1, board: 'xiaomi,be10000', kernel: '6.18.60', sampled_at: { uptime: 100 },
  temperatures: [], fans: [], ports: [], processes: [], radios: [],
  storage: { rootfs: { size: 241434624 }, environment_map_valid: true }, proxy: { btf: false },
  ppe: { present: true, debugfs_accessible: true, active_flows: null,
    active_flows_source: { source: 'debugfs:ppe/active_flows', status: 'missing' },
    resources: { source: 'debugfs:ppe/resource_stats', status: 'ok', failures: {
      core_role: { enospc: '18446744073709551615', enomem: '9007199254740993', enoent: '0', last_errno: '-28' } } },
    direct_switch: { source: 'debugfs:ppe/direct_switch', status: 'malformed' },
    detail: { flows: { source: 'debugfs:ppe/flows', status: 'not-collected' },
      reasons: { source: 'debugfs:ppe/reason_counters', status: 'not-collected' } } },
  preflight: [{ target: 'hardware', checks: [] }]
};
const node = api.render(base);
const text = JSON.stringify(node);
assert(text.includes('18,446,744,073,709,551,615'), 'u64 counter rounded');
assert(text.includes('9,007,199,254,740,993'), '2^53+1 counter rounded');
assert(text.includes('Not exported by this kernel (debugfs:ppe/active_flows)'));
assert(text.includes('Unrecognized format; values withheld (debugfs:ppe/direct_switch)'));
assert(text.includes('Not collected; avoids a full hardware table scan (debugfs:ppe/flows)'));
assert(text.includes('Not collected; avoids a full hardware table scan (debugfs:ppe/reason_counters)'));
assert(!text.includes('cumulative since the driver loaded'));
assert(text.includes('Active counts and resource use are current state'));
assert(!text.includes('did not advance'));
responses.push(JSON.parse(JSON.stringify(base)));
const old = JSON.parse(JSON.stringify(base)); old.sampled_at.uptime = 250;
old.ppe.resources = { source: 'debugfs:ppe/resource_stats', status: 'truncated' };
responses.push(old);
refresh().then(() => {
  assert(JSON.stringify(node).includes('sample time did not advance'));
  return refresh();
}).then(() => {
  const now = JSON.stringify(node);
  assert(!now.includes('did not advance'));
  assert(now.includes('Output exceeded the read bound; values withheld'));
  assert(!now.includes('18,446,744,073,709,551,615'), 'failed source must not retain old counts');
}).catch(e => { console.error(e); process.exitCode = 1; });
'''
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_fan_health_and_trend_gaps_are_rendered_honestly(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
const rpc = { declare: () => () => Promise.resolve({}) };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, { add: () => {} }, {}, E, v => v);
const samples = [
  { t: 100, celsius: 40, rpm: 900, pwm: 77, gap_before: false },
  { t: 110, celsius: 50, rpm: 1800, pwm: 120, gap_before: false },
  { t: 300, celsius: null, rpm: 0, pwm: 255, gap_before: true }
];
const text = JSON.stringify(api.render({
  schema: 1, sampled_at: { uptime: 300 }, temperatures: [], ports: [], processes: [], radios: [],
  fans: [{ hwmon: 'hwmon8', rpm: 0, pwm: 255, fault: 0, fault_available: true, health: 'stopped' }],
  fan: { controller: 'found', temperature: { status: 'missing', celsius: null }, guard_process: 'running',
    trend: { interval: 10, capacity: 90, window: 900, samples } },
  storage: {}, proxy: {}, preflight: [{ target: 'hardware', checks: [] }]
}));
assert(text.includes('Stopped (0 RPM)'), 'stopped fan must not look healthy');
assert(text.includes('NSS sensor missing'));
assert(text.includes('Process present'));
assert(text.includes('▁█ ·'), 'gap and missing temperature must stay visible: ' + text.match(/[▁-█· ]{3,}/));
assert(text.includes('does not prove that fan protection is working'));
assert(text.includes('not a heartbeat from the fan guard'), 'page sample time must not read as a guard heartbeat');
'''
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_channel_hints_render_and_failed_capabilities_drop_old_results(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r"""
const fs = require('fs');
const assert = require('assert');
let refresh, responses = [];
const rpc = { declare: () => () => Promise.resolve(responses.shift()) };
const poll = { add: fn => { refresh = fn; } };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const dom = { content: (node, children) => { node.children = children; } };
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, poll, dom, E, v => v);
const radios = [ { name: 'radio0', disabled: true }, { name: 'radio1', disabled: false }, { name: 'radio2', disabled: true } ];
const base = (uptime, wireless) => ({ schema: 1, sampled_at: { uptime }, temperatures: [], fans: [], ports: [], processes: [],
  radios, storage: {}, proxy: {}, preflight: [{ target: 'hardware', checks: [] }], wireless });
const node = api.render(base(100, { source: 'nl80211 GET_WIPHY', status: 'ok',
  phys: [ { name: 'phy0', bands: [ '2g' ] }, { name: 'phy1', bands: [ '5g' ] } ],
  hints: [
    { radio: 'radio0', band: '2g', channel: '6', frequency: 2437, phy: 'phy0', status: 'ok', flags: [] },
    { radio: 'radio1', band: '6g', channel: '1', frequency: 5955, phy: 'phy1', status: 'band-unsupported', supported_bands: [ '5g' ], flags: [] },
    { radio: 'radio2', band: '5g', channel: 'auto', frequency: null, phy: null, status: 'not-ready', flags: [] } ] }));
let text = JSON.stringify(node);
assert(text.includes('6 (2437 MHz)'));
assert(text.includes('Matches: primary channel is in the radio frequency list'));
assert(text.includes('Mismatch: radio does not support this band (supported: 5g)'));
assert(text.includes('Cannot determine: radio not detected yet'));
assert(text.includes('phy1 (5g)'));
assert(text.includes('DFS/CAC'), 'scope of the check must be stated');
assert.strictEqual(api.handleSave, null);
responses.push(base(105, { source: 'nl80211 GET_WIPHY', status: 'unavailable', phys: null, hints: [
  { radio: 'radio0', band: '2g', channel: '6', frequency: 2437, phy: null, status: 'capability-unavailable', flags: [] } ] }));
responses.push(base(110, undefined));
refresh().then(() => {
  text = JSON.stringify(node);
  assert(!text.includes('Matches'), 'old match must not survive a failed query');
  assert(!text.includes('phy1 (5g)'));
  assert(text.includes('Cannot determine: radio capabilities unavailable'));
  assert(text.includes('Query failed or wireless driver not loaded'));
  return refresh();
}).then(() => {
  text = JSON.stringify(node);
  assert(!text.includes('Mismatch:') && !text.includes('Matches:') && !text.includes('Cannot determine'));
  assert(text.includes('radio1'), 'radios still listed without hint data');
}).catch(e => { console.error(e); process.exitCode = 1; });
"""
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_sfp_states_render_and_unknown_never_shows_an_empty_cage(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r"""
const fs = require('fs');
const assert = require('assert');
let refresh, responses = [];
const rpc = { declare: () => () => Promise.resolve(responses.shift()) };
const poll = { add: fn => { refresh = fn; } };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const dom = { content: (node, children) => { node.children = children; } };
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, poll, dom, E, v => v);
const base = (uptime, sfp) => ({ schema: 1, sampled_at: { uptime }, temperatures: [], fans: [], ports: [], processes: [],
  radios: [], storage: {}, proxy: {}, preflight: [{ target: 'hardware', checks: [] }], sfp });
const sfp = (status, detected, carrier, extra) => ({ interface: 'sfp', interface_present: status !== 'interface-missing',
  sampled_at: 100, status, carrier, carrier_status: carrier == null ? 'unreadable' : 'ok',
  presence: { source: 'debugfs:sfp/state', method: 'i2c-inferred', status: 'ok', detected, module_state: detected ? 'present' : 'empty' },
  led: { presence_mode: true, controller: 'running' }, ...extra });
const node = api.render(base(100, sfp('link', true, 1)));
let text = JSON.stringify(node);
assert(text.includes('Module detected, link up'));
assert(text.includes('Detected'));
assert(text.includes('debugfs:sfp/state'));
assert(text.includes('100 s'));
assert(text.includes('does not mean a module is inserted'));
assert(text.includes('cannot prove the cage is empty'));
assert.strictEqual(api.handleSave, null);
const steps = [
  [ sfp('detected-no-link', true, 0), [ 'Module detected, no link' ], [ 'link up' ] ],
  [ sfp('not-detected', false, 0, { led: { presence_mode: false, controller: 'absent' } }),
    [ 'No module detected by the kernel', 'Not detected', 'Disabled (default link/activity LED)' ], [ 'Module detected' ] ],
  [ sfp('unknown', null, 1, { presence: { source: 'debugfs:sfp/state', status: 'missing', detected: null, module_state: null } }),
    [ 'Unknown: module detection unavailable', 'Kernel debug state not available' ], [ 'No module detected', 'Not detected', 'Module detected' ] ],
  [ sfp('detected-link-unknown', true, null), [ 'Module detected, link state unreadable', 'Unreadable' ], [ 'link up', 'detected, no link' ] ],
  [ sfp('interface-missing', true, null), [ 'SFP network device missing', 'sfp: Missing' ], [ 'Module detected,' ] ],
  [ undefined, [], [ 'SFP port', 'Module detected', 'No module detected' ] ]
];
let chain = Promise.resolve();
steps.forEach(([ value, present, absent ], i) => {
  chain = chain.then(() => { responses.push(base(105 + i, value)); return refresh(); }).then(() => {
    text = JSON.stringify(node);
    for (const s of present) assert(text.includes(s), `${i}: missing ${s}`);
    for (const s of absent) assert(!text.includes(s), `${i}: stale ${s}`);
  });
});
chain.catch(e => { console.error(e); process.exitCode = 1; });
"""
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
