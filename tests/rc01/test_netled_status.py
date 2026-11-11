"""Connectivity LED status reader (ucode), LuCI view and page/service validation parity.

LUCI_VALIDATION_JS may point to luci-base's validation.js from the pinned
LuCI feed commit; the parity test then checks that every value the page
accepts is accepted by `rc01-netled check` and vice versa.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "package/emortal/rc01-platform/files/platform.uc"
DAEMON = ROOT / "package/emortal/rc01-platform/files/rc01-netled"
VIEW = ROOT / "package/emortal/luci-app-rc01/htdocs/luci-static/resources/view/rc01/netled.js"
APP = ROOT / "package/emortal/luci-app-rc01"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")
NODE = shutil.which("node")
VALIDATION = os.environ.get("LUCI_VALIDATION_JS")
FORM = os.environ.get("LUCI_FORM_JS") or (str(Path(VALIDATION).with_name("form.js")) if VALIDATION else None)


def report(pid=4242, updated=990, valid_until=1050, overall="up"):
    family = {"enabled": True, "interface": "auto", "targets": ["203.0.113.1"], "state": "up", "last": "ok",
              "reason": "reply", "device": "br-lan", "target": "203.0.113.1", "ok_streak": 4, "bad_streak": 0,
              "error_streak": 0, "last_result_at": updated}
    return {"schema": 1, "pid": pid, "started_at": 900, "updated_at": updated, "valid_until": valid_until,
            "rounds": 4, "interval": 30, "timeout": 2, "fail_count": 3, "recover_count": 2, "overall": overall,
            "families": {"ipv4": family, "ipv6": {**family, "enabled": False, "state": "disabled", "targets": []}},
            "leds": {"white": {"name": "white:network", "control": "owned", "shown": "on"},
                     "amber": {"name": "amber:network", "control": "owned", "shown": "off"}}}


@unittest.skipUnless(UCODE, "ucode is required for the real status collector")
class StatusReader(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-netled-status-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("proc/uptime", "1000.00 1.00")
        self.enabled = "1"

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    def process(self, pid=4242, parent=1):
        self.write(f"proc/{pid}/comm", "rc01-netled\n")
        self.write(f"proc/{pid}/status", f"Name:\trc01-netled\nPPid:\t{parent}\nVmRSS:\t   900 kB\n")

    def collect(self):
        config = {"rc01_netled": {"main": {"enabled": self.enabled}}} if self.enabled is not None else {}
        source = f'import {{ collect }} from {json.dumps(str(MODULE))};\n'
        source += f"let values = {json.dumps(config)};\n"
        source += "let uci = { get: (c, s, k) => values[c]?.[s]?.[k], foreach: () => null };\n"
        source += f"print(sprintf('%J', collect({json.dumps(str(self.root))}, uci).connectivity));\n"
        result = subprocess.run([UCODE, "-"], input=source, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_current_report_is_passed_on(self):
        self.process()
        self.process(4300, parent=4242)  # forked probe subshell, same name
        self.write("var/run/rc01-netled/state.json", report())
        state = self.collect()
        self.assertEqual((state["status"], state["controller"], state["age"]), ("ok", "running", 10))
        self.assertEqual(state["report"]["overall"], "up")
        self.assertEqual(state["source"], "/var/run/rc01-netled/state.json")

    def test_results_are_withheld_unless_current(self):
        cases = {
            "disabled": dict(enabled="0"),
            "disabled ": dict(enabled=None),
            "not-running": dict(process=False),
            "starting": dict(file=None),
            "malformed": dict(file="{\"schema\":1,"),
            "malformed ": dict(file={**report(), "overall": "great"}),
            "malformed  ": dict(file={**report(), "pid": "4242"}),
            "stale": dict(file=report(valid_until=999)),
            "not-running ": dict(file=report(pid=77)),
        }
        for expected, case in cases.items():
            with self.subTest(case=expected):
                self.setUp()
                if "enabled" in case:
                    self.enabled = case["enabled"]
                if case.get("process", True):
                    self.process()
                file = case.get("file", report())
                if file is not None:
                    self.write("var/run/rc01-netled/state.json", file if isinstance(file, str) else json.dumps(file))
                state = self.collect()
                self.assertEqual(state["status"], expected.strip())
                self.assertIsNone(state["report"], "no earlier result may be shown")

    def test_oversized_report_is_not_parsed(self):
        self.process()
        self.write("var/run/rc01-netled/state.json", json.dumps(report()) + " " * 9000)
        state = self.collect()
        self.assertEqual((state["status"], state["report"]), ("truncated", None))


VIEW_HARNESS = r'''
const fs = require('fs');
const assert = require('assert');
String.prototype.format = function(...args) { let i = 0; return this.replace(/%[sd]/g, () => String(args[i++])); };
let refresh, responses = [];
const rpc = { declare: () => () => responses.length ? Promise.resolve(responses.shift()) : Promise.reject(Error('offline')) };
const poll = { add: fn => { refresh = fn; } };
const E = (tag, attrs, children) => ({ tag, attrs, children });
const dom = { content: (node, children) => { node.children = children; } };
const network = { getNetworks: () => Promise.resolve([]) };
class Option {
  constructor(type, name, title, description) { Object.assign(this, { type, name, title, description, values: [], deps: [] }); }
  value(key) { this.values.push(key); }
  depends(key, value) { this.deps.push([ key, value ]); }
}
const options = {};
const form = {
  Map: class { constructor(config, title, description) { Object.assign(this, { config, title, description }); }
    section(cls, name) { this.sectionName = name; return { option: (t, n, ti, d) => (options[n] = new Option(t, n, ti, d)) }; }
    render() { return Promise.resolve({ tag: 'map', config: this.config, description: this.description }); } },
  NamedSection: 'NamedSection', Flag: 'Flag', DynamicList: 'DynamicList', Value: 'Value'
};
const factory = new Function('view', 'form', 'rpc', 'poll', 'dom', 'network', 'E', '_', fs.readFileSync(process.argv[1], 'utf8'));
const api = factory({ extend: v => v }, form, rpc, poll, dom, network, E, v => v);
const input = JSON.parse(process.argv[2]);
'''


@unittest.skipUnless(NODE, "Node.js is required for the LuCI view regression")
class View(unittest.TestCase):
    def node(self, body, data=None):
        result = subprocess.run([NODE, "-e", VIEW_HARNESS + body, str(VIEW), json.dumps(data)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_form_options_match_the_service(self):
        out = self.node(r'''
api.render([ null, [ { getName: () => 'lan' }, { getName: () => 'wan' }, { getName: () => 'loopback' } ] ]).then(node => {
  const text = JSON.stringify(node);
  const result = {};
  for (const [ name, o ] of Object.entries(options))
    result[name] = { type: o.type, datatype: o.datatype || null, default: o.default ?? null, values: o.values, deps: o.deps,
      custom: typeof o.validate === 'function' };
  console.log(JSON.stringify({ result, text, description: node.children[0].description }));
});
''')
        data = json.loads(out)
        options = data["result"]
        self.assertEqual(set(options), {"enabled", "ipv4", "ipv6", "ipv4_target", "ipv6_target", "ipv4_interface",
                                        "ipv6_interface", "interval", "timeout", "fail_count", "recover_count"})
        config = (ROOT / "package/emortal/rc01-platform/files/rc01_netled.config").read_text()
        for name in ("interval", "timeout", "fail_count", "recover_count", "ipv4_interface", "ipv6_interface"):
            self.assertIn(f"option {name} '{options[name]['default']}'", config, name)
        for name, (low, high) in {"interval": (10, 3600), "timeout": (1, 5), "fail_count": (1, 10),
                                  "recover_count": (1, 10)}.items():
            # Integers only: LuCI's range() alone accepts decimals.
            self.assertEqual(options[name]["datatype"], f"and(uinteger,range({low},{high}))")
            self.assertTrue(options[name]["custom"])
        self.assertEqual(options["ipv4_interface"]["values"], ["auto", "lan", "wan"])
        self.assertEqual(options["ipv6_target"]["deps"], [[{"enabled": "1", "ipv6": "1"}, None]])
        self.assertIsNone(options["enabled"]["default"], "enabled stays as configured (off)")
        description = data["description"]
        self.assertIn("does not test DNS, web pages or proxies", description)
        self.assertIn("filter ICMP", description)
        for line in ("White steady", "White blinking", "Amber steady", "Amber blinking", "Both off"):
            self.assertIn(line, data["text"])

    def test_status_rendering_and_failure_clears_old_values(self):
        self.node(r'''
const s = JSON.stringify(api.statusNodes(input.ok));
assert(s.includes('All enabled families reach a target'), s);
assert(s.includes('Reachable: a target answered ICMP echo'));
assert(s.includes('automatic (kernel route) → br-lan'));
assert(s.includes('Driven by this service'));
const partial = JSON.parse(JSON.stringify(input.ok));
partial.report.overall = 'degraded';
partial.report.families.ipv6 = { ...partial.report.families.ipv4, state: 'no-path', last: 'nopath', reason: 'no-route', device: null };
const p = JSON.stringify(api.statusNodes(partial));
assert(p.includes('Only some enabled families reach a target'));
assert(p.includes('No applicable route or source address for this family'));
assert(p.includes('no route/address (no-route)'));
for (const [ status, text ] of [ [ 'stale', 'older than its validity bound' ], [ 'not-running', 'not running' ],
    [ 'disabled', 'Disabled in the configuration' ], [ 'malformed', 'Report unreadable' ] ]) {
  const t = JSON.stringify(api.statusNodes({ status, report: input.ok.report }));
  assert(t.includes(text), status);
  assert(!t.includes('All enabled families'), `${status} must not show the old result`);
  assert(!t.includes('Reachable'), status);
}
const failed = JSON.stringify(api.statusNodes(null));
assert(failed.includes('Previous values are no longer current'));
''', {"ok": {"status": "ok", "age": 10, "report": report()}})

    def test_poll_failure_replaces_the_shown_result(self):
        self.node(r'''
api.render([ input.ok, [] ]).then(node => {
  const status = node.children[2];
  assert(JSON.stringify(status).includes('All enabled families reach a target'));
  return refresh().then(() => {
    const text = JSON.stringify(status);
    assert(!text.includes('All enabled families'), 'old success must not survive a failed refresh');
    assert(text.includes('Previous values are no longer current'));
  });
}).catch(e => { console.error(e); process.exitCode = 1; });
''', {"ok": {"status": "ok", "age": 10, "report": report()}})


PARITY_HARNESS = r'''
const fs = require('fs');
String.prototype.format = function(...args) { let i = 0; return this.replace(/%[sd]/g, () => String(args[i++])); };
global._ = s => s;
global.L = { raise: (t, m) => { throw new Error(m); } };
global.findParent = () => true;
global.CustomEvent = class { constructor(n, o) { this.name = n; this.detail = o?.detail; } };
const baseclass = { extend(proto) {
  const C = function(...a) { if (this.__init__) this.__init__(...a); };
  C.prototype = proto;
  return C;
} };
const Factory = new Function('baseclass', fs.readFileSync(process.argv[1], 'utf8'))(baseclass);
const factory = new Factory();
const view = new Function('view', 'form', 'rpc', 'poll', 'dom', 'network', 'E', '_',
  fs.readFileSync(process.argv[2], 'utf8'))({ extend: v => v }, {}, { declare: () => () => null }, {}, {}, {}, () => null, s => s);
const cases = JSON.parse(process.argv[3]);
const out = {};
for (const [ key, datatype, values ] of cases) {
  out[key] = {};
  for (const value of values) {
    const field = { value, classList: { add() {}, remove() {} }, setAttribute() {}, removeAttribute() {}, dispatchEvent() {} };
    let custom;
    if (key === 'ipv4_target') custom = v => view.unicast4(v) || 'invalid';
    else if (key === 'ipv6_target') custom = v => view.unicast6(v) || 'invalid';
    else if (key.endsWith('_interface')) custom = null;
    else custom = v => view.plainInteger('main', v);
    out[key][value] = factory.create(field, datatype, false, custom).validate();
  }
}
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE and VALIDATION and Path(VALIDATION or "/").is_file(),
                     "set LUCI_VALIDATION_JS to the pinned luci-base validation.js")
class Parity(unittest.TestCase):
    CASES = [
        ("ipv4_target", 'ip4addr("nomask")', ["203.0.113.1", "1.1.1.1", "223.255.255.254", "01.2.3.4", "1.2.3.004",
                                              "0.1.2.3", "127.0.0.1", "224.0.0.1", "255.255.255.255", "1.2.3",
                                              "256.1.1.1", "192.0.2.1/24", "1.2.3.4 ", "-1.2.3.4"]),
        ("ipv6_target", 'ip6addr("nomask")', ["2001:db8::1", "2001:DB8:0:0:0:0:0:1", "2001:db8::", "1::", "::",
                                              "::1", "::0001", "fe80::1", "FEBF::1", "ff02::1", "::ffff:192.0.2.1",
                                              "2001:db8::1%eth0", "1:2:3:4:5:6:7:8:9", "1::2::3", "12345::1",
                                              "2001:db8::/64", "fe8::1", "ff::1"]),
        ("ipv4_interface", 'or("auto", uciname)', ["auto", "lan", "wan_6", "wan-6", "wan 6", "Auto", "x" * 40]),
        ("interval", "and(uinteger,range(10,3600))", ["10", "3600", "30", "9", "3601", "30.5", "030", "+30", "1e3",
                                                       "-10", " 30"]),
        ("timeout", "and(uinteger,range(1,5))", ["1", "5", "0", "6", "2.0", "02"]),
        ("fail_count", "and(uinteger,range(1,10))", ["1", "10", "11", "1.5"]),
        ("recover_count", "and(uinteger,range(1,10))", ["2", "0", "010"]),
    ]

    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_netled import isolated_bin
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-netled-parity-")
        self.addCleanup(self.temporary.cleanup)
        self.bin = isolated_bin(Path(self.temporary.name) / "bin", ("sh",))
        self.addCleanup(lambda: self.assertFalse((self.bin / "forbidden-calls").exists()))

    def backend(self, key, value):
        base = {"ipv4": "1", "ipv6": "1", "ipv4_target": "203.0.113.1", "ipv6_target": "2001:db8::1"}
        base[key] = value
        args = [f"{k}={v}" for k, v in base.items()]
        result = subprocess.run([str(self.bin / "sh"), "-c", f'. "{DAEMON}"\nrc01_netled_main check "$@"', "x", *args],
                                env={"PATH": str(self.bin), "RC01_NETLED_LIBRARY": "1"},
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0

    def test_page_and_service_accept_the_same_values(self):
        result = subprocess.run([NODE, "-e", PARITY_HARNESS, VALIDATION, str(VIEW), json.dumps(self.CASES)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = json.loads(result.stdout)
        for key, _, values in self.CASES:
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(page[key][value], self.backend(key, value))

    @unittest.skipUnless(FORM and Path(FORM or "/").is_file(), "set LUCI_FORM_JS to the pinned LuCI form.js")
    def test_real_form_save_enforces_family_and_target_requirements(self):
        cases = [
            {"name": "empty-ipv4", "values": {"ipv4_target": []}, "accepted": False},
            {"name": "empty-ipv6", "values": {"ipv6_target": []}, "accepted": False},
            {"name": "empty-both", "values": {"ipv4_target": [], "ipv6_target": []}, "accepted": False},
            {"name": "blank-entry", "values": {"ipv4_target": [""]}, "accepted": False},
            {"name": "neither-family", "values": {"ipv4": "0", "ipv6": "0"}, "accepted": False},
            {"name": "too-many-targets", "values": {"ipv4_target": [f"203.0.113.{i}" for i in range(1, 5)]},
             "accepted": False},
            {"name": "both-families", "values": {}, "accepted": True},
            {"name": "ipv4-only", "values": {"ipv6": "0", "ipv6_target": []}, "accepted": True},
            {"name": "ipv6-only", "values": {"ipv4": "0", "ipv4_target": []}, "accepted": True},
            {"name": "three-targets", "values": {"ipv4_target": [f"203.0.113.{i}" for i in range(1, 4)]},
             "accepted": True},
            {"name": "disable-without-families", "values": {"enabled": "0", "ipv4": "0", "ipv6": "0",
                                                             "ipv4_target": [], "ipv6_target": []}, "accepted": True},
            {"name": "disable-empty-targets", "values": {"enabled": "0", "ipv4_target": [], "ipv6_target": []},
             "accepted": True},
        ]
        harness = Path(__file__).with_name("netled-form-check.js")
        result = subprocess.run([NODE, str(harness), str(VIEW), FORM, VALIDATION], input=json.dumps(cases),
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        results = json.loads(result.stdout)
        self.assertEqual(len(results), len(cases))
        for case, parsed in zip(cases, results):
            with self.subTest(case=case["name"]):
                self.assertEqual(parsed["accepted"], case["accepted"], parsed["errors"])
                if not parsed["accepted"]:
                    continue
                saved = parsed["saved"]
                # Hiding disabled targets must preserve the prior list.
                self.assertNotIn("ipv4_target", parsed["removed"])
                self.assertNotIn("ipv6_target", parsed["removed"])
                if saved["enabled"] == "0":
                    continue  # The real init returns before backend check.
                arguments = []
                for key, value in saved.items():
                    if key != "enabled":
                        arguments.extend(f"{key}={v}" for v in (value if isinstance(value, list) else [value]))
                checked = subprocess.run([str(self.bin / "sh"), "-c",
                                          f'. "{DAEMON}"\nrc01_netled_main check "$@"', "x", *arguments],
                                         env={"PATH": str(self.bin), "RC01_NETLED_LIBRARY": "1"},
                                         text=True, capture_output=True, timeout=10)
                self.assertEqual(checked.returncode, 0, checked.stderr)


class Translations(unittest.TestCase):
    def test_every_page_string_has_one_translation(self):
        import re
        po = (APP / "po/zh_Hans/rc01.po").read_text()
        entries = re.findall(r'^msgid "((?:[^"\\]|\\.)*)"\nmsgstr "((?:[^"\\]|\\.)*)"$', po, re.M)
        ids = [i for i, _ in entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate msgid")
        self.assertEqual(len(ids), len(re.findall(r"^msgid ", po, re.M)), "unparsed entry")
        translated = {i.replace('\\"', '"') for i, value in entries if value}
        strings = set(re.findall(r"_\('((?:[^'\\]|\\.)*)'\)", VIEW.read_text()))
        menu = json.loads((APP / "root/usr/share/luci/menu.d/luci-app-rc01.json").read_text())
        strings.add(menu["admin/services/rc01-netled"]["title"])
        self.assertEqual(sorted(strings - translated), [])


if __name__ == "__main__":
    unittest.main()
