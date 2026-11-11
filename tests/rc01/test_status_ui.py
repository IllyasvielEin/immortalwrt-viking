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


if __name__ == "__main__":
    unittest.main()
