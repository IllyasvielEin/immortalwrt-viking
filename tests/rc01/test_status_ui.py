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
            (path / "rc01/platform.uc").write_text('''export function connectivity() { return { status: 'disabled' }; }
            export function snapshot() {
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
            source += "print(sprintf('%J', { objects: keys(api), methods: keys(api.rc01), status: api.rc01.status.call(),\n"
            source += "  connectivity: api.rc01.connectivity.call() }));\n"
            result = subprocess.run(command + ["-"], input=source, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            api = json.loads(result.stdout)
            self.assertEqual(api["objects"], ["rc01"])
            # Both methods only read; neither takes arguments.
            self.assertEqual(api["methods"], ["status", "connectivity"])
            self.assertEqual(api["status"]["schema"], 1)
            self.assertEqual(api["connectivity"], {"status": "disabled"})

    def test_rpc_acl_grants_only_status_read(self):
        acl = json.loads((APP / "root/usr/share/rpcd/acl.d/luci-app-rc01.json").read_text())
        entry = acl["luci-app-rc01"]
        self.assertNotIn("write", entry)
        self.assertEqual(entry["read"], {"ubus": {"rc01": ["status"]}})
        # The connectivity LED page may write only its own UCI package.
        netled = acl["luci-app-rc01-netled"]
        self.assertEqual(netled["read"], {"ubus": {"rc01": ["connectivity"]}, "uci": ["rc01_netled"]})
        self.assertEqual(netled["write"], {"uci": ["rc01_netled"]})
        self.assertEqual(set(acl), {"luci-app-rc01", "luci-app-rc01-netled"})

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
  preflight: [{ target: 'hardware', passed: true, checks: [{ level: 'ok', message: 'Requires Xiaomi RC01 / BE10000' }] }]
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
    def test_initial_failure_malformed_responses_and_recovery_keep_polling(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
let refresh, responses = [ Error('offline') ];
const rpc = { declare: () => () => {
  const value = responses.shift();
  return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
} };
const poll = { add: fn => { refresh = fn; } };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const dom = { content: (node, children) => { node.children = children; } };
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, poll, dom, E, v => v);
const good = uptime => ({ schema: 1, sampled_at: { uptime }, temperatures: [],
  fans: [{ hwmon: 'hwmon8', rpm: 960, pwm: 77 }],
  preflight: [{ target: 'hardware', passed: true, checks: [{ level: 'ok', message: 'Board ok' }] }] });
const text = node => JSON.stringify(node);
const unavailable = node => {
  assert(text(node).includes('RC01 status is unavailable'));
  assert(!text(node).includes('hwmon8'), 'failed refresh must clear previous readings');
  assert(!text(node).includes('checks OK'), 'invalid response must not look passed');
};
(async () => {
  const node = api.render(await api.load());
  unavailable(node);
  assert.strictEqual(typeof refresh, 'function', 'initial RPC failure must still register a poll');
  responses.push(good(100)); await refresh();
  assert(text(node).includes('hwmon8'));
  assert(!text(node).includes('did not advance'));
  for (const bad of [ Error('offline'), null, {}, { schema: 1, preflight: [] },
      { ...good(101), preflight: [{ target: 'hardware', passed: true }] },
      { ...good(101), preflight: [{ target: 'hardware', passed: 'false', checks: [] }] },
      { ...good(101), preflight: [{ target: 'hardware', passed: true, checks: [{ level: 'bogus', message: 'Unknown check' }] }] } ]) {
    responses.push(bad); await refresh(); unavailable(node);
    responses.push(good(100)); await refresh();
    assert(text(node).includes('did not advance'), 'recovery must compare with the last successful sample');
  }
  responses.push(good(110)); await refresh();
  assert(!text(node).includes('did not advance'), 'new sample must clear the stale warning');
  for (const bad of [ null, {}, { schema: 1, preflight: [] } ]) {
    const initial = api.render(bad); unavailable(initial);
    responses.push(good(120)); await refresh();
    assert(text(initial).includes('hwmon8'), 'malformed initial response must recover');
  }
  for (const time of [ null, '130', -1, NaN, Infinity ])
    assert(text(api.render(good(time))).includes('did not advance'), 'invalid timestamp must not look fresh');
})().catch(e => { console.error(e); process.exitCode = 1; });
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
    direct_switch: { source: 'debugfs:ppe/direct_switch', status: 'malformed' } },
  preflight: [{ target: 'hardware', passed: true, checks: [] }]
};
const node = api.render(base);
const text = JSON.stringify(node);
assert(text.includes('18,446,744,073,709,551,615'), 'u64 counter rounded');
assert(text.includes('9,007,199,254,740,993'), '2^53+1 counter rounded');
assert(text.includes('Not exported by this kernel (debugfs:ppe/active_flows)'));
assert(text.includes('Unrecognized format; values withheld (debugfs:ppe/direct_switch)'));
assert(text.includes('not throughput'));
assert(text.includes('failure counters accumulate until reset'), 'historical failures must not read as current faults');
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
  storage: {}, proxy: {}, preflight: [{ target: 'hardware', passed: true, checks: [] }]
}));
assert(text.includes('Stopped (0 RPM)'), 'stopped fan must not look healthy');
assert(text.includes('NSS sensor missing'));
assert(text.includes('Unknown · Process present'), 'process presence shown while the record is unknown');
assert(text.includes('▁█ ·'), 'gap and missing temperature must stay visible: ' + text.match(/[▁-█· ]{3,}/));
assert(text.includes('sampled only while this page is open'), 'page samples must not read as guard heartbeats');
'''
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_guard_record_renders_current_last_and_unknown_honestly(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
const rpc = { declare: () => () => Promise.resolve({}) };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, rpc, { add: () => {} }, {}, E, v => v);
function rows(protection) {
  const tree = api.render({
    schema: 1, sampled_at: { uptime: 1000 }, temperatures: [], ports: [], processes: [], radios: [],
    fans: [{ hwmon: 'hwmon8', rpm: 960, pwm: 255, fault: 0, fault_available: true, health: 'ok' }],
    fan: { controller: 'found', temperature: { status: 'ok', celsius: 45 }, guard_process: 'running',
      trend: { interval: 10, capacity: 90, window: 900, samples: [] }, protection },
    storage: {}, proxy: {}, preflight: [{ target: 'hardware', passed: true, checks: [] }]
  });
  const found = {};
  (function walk(n) {
    if (Array.isArray(n)) return n.forEach(walk);
    if (!n || typeof n !== 'object') return;
    if (n.tag === 'tr' && Array.isArray(n.children) && n.children.length === 2)
      found[n.children[0].children] = n.children[1].children;
    walk(n.children);
  })(tree);
  return { found, text: JSON.stringify(tree) };
}
const record = { status: 'current', detail: null, age: 1, sample_age: 3, phase: 'monitoring', event: 'sample',
  sample_reason: 'ok', failures: 0, latched: true, latch_reason: 'consecutive-failures:rpm-zero' };
let r = rows(record);
assert.strictEqual(r.found['Fan guard record'], 'Current record from the running guard');
assert.strictEqual(r.found['Guard sample result'], 'Guard checks passed · 3 s', 'guard sample age, not the page poll time');
assert(!('Guard event' in r.found), 'a periodic check is the normal case');
assert.strictEqual(r.found['Consecutive failed checks'], '0');
assert.strictEqual(r.found['Software full-speed latch'], 'Set; first reason: Three consecutive failed checks: Stopped (0 RPM)',
  'a passing current sample must not hide the latch');
assert(r.text.includes('does not prove the fan reached full speed'));

r = rows({ ...record, status: 'stale', detail: 'expired', sample_reason: 'fault', failures: 2, latched: false, latch_reason: null });
assert.strictEqual(r.found['Fan guard record'], 'Last record only; current guard state unknown (not updated within 10 s) · Process present');
assert.strictEqual(r.found['Guard sample result'], 'Last record: Fault flag not 0 or unreadable · 3 s');
assert.strictEqual(r.found['Consecutive failed checks'], 'Last record: 2');
assert.strictEqual(r.found['Software full-speed latch'], 'Last record: Not set');

r = rows({ ...record, status: 'exited', event: 'pwm-write-failed', sample_age: null, latch_reason: 'startup:sensor-missing' });
assert.strictEqual(r.found['Guard event'], 'Last record: Stopped: PWM write failed');
assert.strictEqual(r.found['Guard sample result'], 'Last record: Guard checks passed', 'no age without a guard sample');
assert.strictEqual(r.found['Software full-speed latch'], 'Last record: Set; first reason: Startup check failed: NSS sensor missing');

const unknown = { status: 'unknown', detail: 'missing', age: null, sample_age: null, phase: null, event: null,
  sample_reason: null, failures: null, latched: null, latch_reason: null };
for (const p of [ unknown, undefined ]) {
  r = rows(p);
  assert(!r.text.includes('Current record'), 'a running process without a record is not current');
  for (const k of [ 'Guard sample result', 'Consecutive failed checks', 'Software full-speed latch' ])
    assert.strictEqual(r.found[k], 'Unknown', k + ' must be unknown, not zero or false');
}
assert.strictEqual(rows(unknown).found['Fan guard record'], 'Unknown (no record) · Process present');
'''
        result = subprocess.run([NODE, "-e", program, str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
    def test_preflight_lists_problems_and_hides_uninstalled_proxies(self):
        source = APP / "htdocs/luci-static/resources/view/status/rc01.js"
        program = r'''
const fs = require('fs');
const assert = require('assert');
const E = (tag, attrs, children) => ({ tag, attrs, children });
const factory = new Function('view', 'rpc', 'poll', 'dom', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, { declare: () => () => Promise.resolve({}) }, { add: () => {} }, {}, E, v => v);
const check = (level, message) => ({ code: message, level, message });
const text = JSON.stringify(api.render({
  schema: 1, sampled_at: { uptime: 1 }, temperatures: [], fans: [], fan: {},
  proxy: { nikki_installed: true, dae_installed: false, daed_installed: false },
  preflight: [
    { target: 'hardware', passed: false, checks: [ check('ok', 'Board ok'), check('error', 'Layout broken'), check('warn', 'PPE missing') ] },
    { target: 'nikki', passed: true, checks: [ check('ok', 'Installed'), check('info', 'Caveat only') ] },
    { target: 'dae', passed: false, checks: [ check('error', 'Dae core missing') ] },
    { target: 'daed', passed: false, checks: [ check('error', 'Daed core missing') ] }
  ]
}));
assert(text.includes('hardware: Failed · 1/3 checks OK'));
assert(text.includes('Layout broken') && text.includes('PPE missing') && !text.includes('Board ok'), 'only problems listed');
assert(text.includes('nikki: Passed · 2/2 checks OK'), 'info is a caveat, not a failed check');
assert(text.includes('Caveat only'), 'info caveat stays visible');
assert(!text.includes('dae:') && !text.includes('Dae core missing') && !text.includes('Daed core missing'), 'uninstalled proxies hidden');
'''
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
  radios: [], storage: {}, proxy: {}, preflight: [{ target: 'hardware', passed: true, checks: [] }], sfp });
const sfp = (status, detected, carrier, extra) => ({ interface: 'sfp', interface_present: status !== 'interface-missing',
  sampled_at: 100, status, carrier, carrier_status: carrier == null ? 'unreadable' : 'ok',
  presence: { source: 'debugfs:sfp/state', method: 'i2c-inferred', status: 'ok', detected, module_state: detected ? 'present' : 'empty' },
  led: { presence_mode: true, controller: 'running' }, ...extra });
const node = api.render(base(100, sfp('link', true, 1)));
let text = JSON.stringify(node);
assert(text.includes('Module detected, link up'));
assert(text.includes('"present"'), 'kernel module state shown');
assert(!text.includes('debugfs:sfp/state'), 'source only shown when detection fails');
assert(text.includes('cannot prove the cage is empty'));
assert.strictEqual(api.handleSave, null);
const steps = [
  [ sfp('detected-no-link', true, 0), [ 'Module detected, no link' ], [ 'link up' ] ],
  [ sfp('not-detected', false, 0, { led: { presence_mode: false, controller: 'absent' } }),
    [ 'No module detected by the kernel', 'Disabled (default link/activity LED)' ], [ 'Module detected' ] ],
  [ sfp('unknown', null, 1, { presence: { source: 'debugfs:sfp/state', status: 'missing', detected: null, module_state: null } }),
    [ 'Unknown: module detection unavailable', 'Kernel debug state not available (debugfs:sfp/state)' ], [ 'No module detected', 'Not detected', 'Module detected' ] ],
  [ sfp('detected-link-unknown', true, null), [ 'Module detected, link state unreadable' ], [ 'link up', 'detected, no link' ] ],
  [ sfp('interface-missing', true, null), [ 'SFP network device missing' ], [ 'Module detected,' ] ],
  [ sfp('link', true, 1, { led: { presence_mode: true, controller: 'absent' } }), [ 'Enabled; Not running' ], [] ],
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
