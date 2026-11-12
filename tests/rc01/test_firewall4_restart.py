"""firewall4 restart continuity (005 + 006 + local result reporting).

Rendering uses real ucode with fw4's own mocklib (see test_firewall4_bridge).
The real /sbin/fw4 script runs under BusyBox ash with LOCK/STATE/MAIN
rewritten into a fixture directory and PATH holding only stubs for utpl, nft,
mktemp, cp, rm and flock. No real nft, ip or ubus is run and no real state,
lock or /tmp file is touched. nft acceptance and kernel behaviour are not
checked here.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import test_firewall4_bridge as bridge

BUSYBOX = os.environ.get("BUSYBOX") or shutil.which("busybox")

STUBS = {
    # Mimics fw4.uc: ACTION=start writes the new state while rendering.
    "utpl": r'''echo "utpl $ACTION restart=${FW4_RESTART:-}" >> "$FIX/trace"
case "$ACTION" in
start)
	echo "table inet fw4 { partial"
	[ -z "$FAIL_RENDER" ] || exit 1
	echo new > "$FIX/state"
	echo "rendered }" ;;
includes)
	[ -z "$FAIL_INCLUDES" ] || { echo "Include '/etc/x.sh' failed with exit code 1" >&2; exit 1; } ;;
esac''',
    "nft": r'''echo "nft $*" >> "$FIX/trace"
for f; do :; done
case " $* " in
*" -c "*) [ -z "$FAIL_CHECK" ] || exit 1; "$BUSYBOX" cat "$f" > "$FIX/checked" ;;
*) [ -z "$FAIL_APPLY" ] || exit 1; "$BUSYBOX" cat "$f" > "$FIX/applied" ;;
esac''',
    "mktemp": r'''n=$(( $("$BUSYBOX" ls "$FIX/tmp" | "$BUSYBOX" wc -l) + 1 ))
f="$FIX/tmp/${1##*/}.$n"; : > "$f"; echo "$f"''',
    "cp": r'''for f; do :; done
if [ -n "$FAIL_RESTORE" ] && [ "$f" = "$FIX/state" ]; then exit 1; fi
exec "$BUSYBOX" cp "$@"''',
    "rm": r'''for f; do
	if [ -n "$FAIL_REMOVE" ] && [ "$f" = "$FIX/state" ] && [ "$("$BUSYBOX" cat "$f" 2>/dev/null)" = new ]; then exit 1; fi
done
exec "$BUSYBOX" rm "$@"''',
    "flock": 'echo "flock $*" >> "$FIX/trace"',
}


class PackagePatches(unittest.TestCase):
    def test_upstream_patches_are_unchanged_and_release_bumped(self):
        # Git blob SHAs of 005/006 at VIKINGYFY 43d9a24a (read at dd8de871).
        for name, blob in (("005-restart-owned-tables-atomically.patch",
                            "12169701ebf220356d0d07b1e34f0e33457005b7"),
                           ("006-keep-established-state-matching-live-on-restart.patch",
                            "aaf2cc06fbfa9921b45ed3054a42a8d40d2caf2d")):
            data = (bridge.PACKAGE / "patches" / name).read_bytes()
            self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), blob, name)
        makefile = (bridge.PACKAGE / "Makefile").read_text()
        for line in ("PKG_RELEASE:=15", "PKG_SOURCE_DATE:=2026-10-29",
                     "PKG_SOURCE_VERSION:=e6f600c458bda7bc2d87b07e78fd06b50c9d7131",
                     "PKG_MIRROR_HASH:=d67c4a2fe4072f641ea532379f463ab3169aea8c2aa54966154a4670fda2e1ac"):
            self.assertIn(line + "\n", makefile)


@unittest.skipUnless(bridge.UCODE, "ucode is required to render firewall4")
@unittest.skipUnless(bridge.SOURCE, "set RC01_FIREWALL4_SOURCE to the firewall4 e6f600c tree")
class RestartRender(unittest.TestCase):
    setUpClass = classmethod(bridge.BridgeScope.setUpClass.__func__)
    render = bridge.BridgeScope.render

    def ruleset(self, defaults=None, env=None):
        flowtables = {f"fs/popen~_usr_sbin_nft_--terse_--json_list_flowtables_{family}.txt":
                      {"nftables": [{"flowtable": {"family": family, "table": "fw4", "name": name}}]}
                      for family, name in (("inet", "ft"), ("bridge", "fb"))}
        return self.render(defaults or {}, files=flowtables, env=env, whole=True)

    def test_restart_render_suppresses_flowtable_deletes(self):
        normal = self.ruleset()
        self.assertIn("delete flowtable inet fw4 ft\n", normal)
        self.assertIn("delete flowtable bridge fw4 fb\n", normal)
        restart = self.ruleset(env={"FW4_RESTART": "1"})
        self.assertNotIn("delete flowtable", restart)
        # Everything else is the same rendered ruleset.
        self.assertEqual(re.sub(r"delete flowtable \w+ fw4 \w+\n", "", normal), restart)

    def test_state_matching_is_direct_and_invalid_drop_optional(self):
        for drop in ("0", "1"):
            with self.subTest(drop_invalid=drop):
                output = self.ruleset({"drop_invalid": drop})
                self.assertNotIn("ct state vmap", output)
                for chain, word in (("input", "inbound"), ("forward", "forwarded"),
                                    ("output", "outbound")):
                    body = re.search(r"\tchain %s \{\n(.*?)\n\t\}" % chain, output, re.S)[1]
                    self.assertIn('ct state established,related accept comment '
                                  f'"!fw4: Handle {word} flows"', body)
                    invalid = f'ct state invalid drop comment "!fw4: Drop invalid {word} packets"'
                    self.assertEqual(invalid in body, drop == "1", chain)
                    if drop == "1":
                        # Invalid drop follows the established/related accept.
                        self.assertLess(body.index("established,related accept"), body.index(invalid))

    def test_script_include_failure_is_returned(self):
        # main.uc runs directly as utpl -S would; mocklib's system() always
        # returns 0, so a fixture module replaces it with listed exit codes.
        fixture = Path(tempfile.mkdtemp(dir=self.temporary.name))
        (fixture / "fs").mkdir()
        (fixture / "fs/open~_var_run_fw4_state.txt").write_text(json.dumps({"includes": [
            {"enabled": True, "path": "/etc/a.sh", "type": "script"},
            {"enabled": True, "path": "/etc/b.sh", "type": "script"}]}))
        (fixture / "includecodes.uc").write_text(
            'let codes = map(split(getenv("INCLUDE_CODES"), ","), (c) => +c);\n'
            'global.system = function(argv, timeout) { warn("include run\\n"); return shift(codes); };\n')
        for codes, status in (("0,0", 0), ("0,3", 1), ("5,0", 1)):
            with self.subTest(codes=codes):
                result = subprocess.run(
                    [bridge.UCODE, "-S", "-T,", "-L", "./tests/lib", "-L", "./root/usr/share/ucode",
                     "-L", str(fixture),
                     "-D", "MOCK_SEARCH_PATH=" + json.dumps([str(fixture), "./tests/mocks"]),
                     "-l", "mocklib", "-l", "includecodes", "./root/usr/share/firewall4/main.uc"],
                    cwd=self.source, capture_output=True, text=True, timeout=30,
                    env={**os.environ, "ACTION": "includes", "INCLUDE_CODES": codes})
                self.assertEqual(result.returncode, status, result.stderr)
                # A failed include does not stop the remaining ones.
                self.assertEqual(result.stderr.count("include run\n"), 2)
                failed = [f"Include '/etc/{name}.sh' failed with exit code {code}\n"
                          for name, code in zip("ab", codes.split(",")) if code != "0"]
                self.assertEqual([line + "\n" for line in result.stderr.splitlines()
                                  if line.startswith("Include")], failed)

@unittest.skipUnless(bridge.SOURCE, "set RC01_FIREWALL4_SOURCE to the firewall4 e6f600c tree")
@unittest.skipUnless(BUSYBOX, "BusyBox ash is required to run fw4")
class RestartScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="rc01-fw4-restart-"))
        cls.addClassCleanup(shutil.rmtree, cls.root)
        source = Path(bridge.SOURCE) / "root/sbin/fw4"
        text = source.read_text()
        if "FW4_RESTART=1" not in text:
            # Pristine tree: take the patched script from the render fixture.
            bridge.BridgeScope.setUpClass.__func__(cls)
            text = (cls.source / "root/sbin/fw4").read_text()
        cls.script = text

    def setUp(self):
        self.fix = Path(tempfile.mkdtemp(dir=self.root))
        (self.fix / "bin").mkdir()
        (self.fix / "tmp").mkdir()
        for name, body in STUBS.items():
            stub = self.fix / "bin" / name
            stub.write_text(f"#!{BUSYBOX} sh\n{body}\n")
            stub.chmod(0o755)
        text = self.script
        for name, path in (("MAIN", "/usr/share/firewall4/main.uc"), ("LOCK", "/var/run/fw4.lock"),
                           ("STATE", "/var/run/fw4.state")):
            line = f"{name}={path}\n"
            self.assertEqual(text.count(line), 1, name)
            text = text.replace(line, f"{name}={self.fix}/{name.lower()}\n")
        self.assertNotIn("/var/run", text)
        (self.fix / "fw4").write_text(text)
        self.env = {"PATH": str(self.fix / "bin"), "FIX": str(self.fix), "BUSYBOX": BUSYBOX}
        # Preflight: applets must not bypass the PATH stubs (no SH_STANDALONE
        # or PREFER_APPLETS), otherwise mktemp/cp/rm would act on the host.
        probe = subprocess.run([BUSYBOX, "sh", "-c", "for c in mktemp cp rm flock; do command -v $c; done"],
                               env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(probe.stdout.split(), [str(self.fix / "bin" / n)
                                                for n in ("mktemp", "cp", "rm", "flock")])

    def fw4(self, *args, state=None, **fail):
        if state is not None:
            (self.fix / "state").write_text(state + "\n")
        env = {**self.env, **{k: "1" for k in fail}}
        result = subprocess.run([BUSYBOX, "sh", str(self.fix / "fw4"), *args], env=env,
                                capture_output=True, text=True, timeout=30)
        trace = (self.fix / "trace").read_text().splitlines() if (self.fix / "trace").exists() else []
        current = (self.fix / "state").read_text().strip() if (self.fix / "state").exists() else None
        return result, trace, current

    def assertNoTemporaries(self):
        self.assertEqual(sorted(p.name for p in (self.fix / "tmp").iterdir()), [])

    RENDER = "utpl start restart=1"
    CHECK = "nft -c -f {fix}/tmp/fw4-restart.XXXXXX.1"
    APPLY = "nft -f {fix}/tmp/fw4-restart.XXXXXX.1"

    def calls(self, *names):
        return ["flock -x 1000"] + [getattr(self, n).format(fix=self.fix) for n in names]

    def test_success_renders_once_and_applies_one_batch(self):
        for state in ("old", None):
            with self.subTest(previous_state=state):
                self.setUp()
                result, trace, current = self.fw4("restart", state=state)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(trace, self.calls("RENDER", "CHECK", "APPLY") + ["utpl includes restart="])
                batch = (self.fix / "applied").read_text()
                self.assertEqual(batch, (self.fix / "checked").read_text())
                self.assertTrue(batch.startswith(
                    "table inet fw4\ndelete table inet fw4\ntable bridge fw4\ndelete table bridge fw4\n"
                    "table inet fw4 { partial\nrendered }\n"), batch)
                self.assertEqual(current, "new")
                self.assertNoTemporaries()

    def test_failures_keep_old_tables_and_restore_state(self):
        cases = (("FAIL_RENDER", "render", ["RENDER"]),
                 ("FAIL_CHECK", "check", ["RENDER", "CHECK"]),
                 ("FAIL_APPLY", "apply", ["RENDER", "CHECK", "APPLY"]))
        for flag, stage, calls in cases:
            for state in ("old", None):
                with self.subTest(stage=stage, previous_state=state):
                    self.setUp()
                    result, trace, current = self.fw4("restart", state=state, **{flag: 1})
                    self.assertEqual(result.returncode, 1)
                    # No stop/delete was issued outside the failed batch, and
                    # includes were not run.
                    self.assertEqual(trace, self.calls(*calls))
                    self.assertFalse((self.fix / "applied").exists())
                    self.assertEqual(current, state)
                    self.assertEqual(result.stderr,
                                     f"Firewall restart: ruleset {stage} failed; owned nft tables "
                                     "were not replaced.\nFirewall restart failed.\n")
                    self.assertNoTemporaries()

    def test_state_restore_failure_is_reported(self):
        result, _, current = self.fw4("restart", state="old", FAIL_CHECK=1, FAIL_RESTORE=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"unable to restore previous state file {self.fix}/state.\n", result.stderr)
        self.assertNotEqual(current, "old")
        self.setUp()
        result, _, current = self.fw4("restart", FAIL_APPLY=1, FAIL_REMOVE=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"unable to remove new state file {self.fix}/state.\n", result.stderr)
        self.assertEqual(current, "new")
        self.assertNotIn("restored", result.stderr)

    def test_include_failure_after_apply_is_not_success(self):
        result, trace, current = self.fw4("restart", state="old", FAIL_INCLUDES=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(trace, self.calls("RENDER", "CHECK", "APPLY") + ["utpl includes restart="])
        # The new ruleset and state are live; nothing claims the old one was kept.
        self.assertEqual(current, "new")
        self.assertEqual(result.stderr,
                         "Include '/etc/x.sh' failed with exit code 1\n"
                         "Firewall restart: new ruleset is active, but firewall includes failed.\n"
                         "Firewall restart failed.\n")
        self.assertNotIn("kept", result.stderr)

    def test_stop_deletes_both_owned_tables_and_restart_from_stopped(self):
        result, trace, current = self.fw4("stop", state="old")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, ["flock -x 1000", "nft -f /dev/stdin"])
        self.assertEqual((self.fix / "applied").read_text(),
                         "table inet fw4\ndelete table inet fw4\ntable bridge fw4\ndelete table bridge fw4\n")
        self.assertIsNone(current)
        (self.fix / "trace").unlink()
        result, trace, current = self.fw4("restart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, self.calls("RENDER", "CHECK", "APPLY") + ["utpl includes restart="])
        self.assertEqual(current, "new")


if __name__ == "__main__":
    unittest.main()
