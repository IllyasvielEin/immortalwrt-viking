"""Exercise the shipped dae guard with real child processes, without networking."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dae_sources", ROOT / "scripts/rc01-software-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)
PATCH = ROOT / "config/rc01/patches/daede/100-rc01-dae-lifecycle-and-source-policy.patch"


class DaeGuard(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-dae-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "apply", "--include=dae/files/dae-guard", str(PATCH)], check=True)
        self.guard = self.root / "dae/files/dae-guard"
        self.events = self.root / "events"
        self.env = os.environ | {"DAE_GUARD_LIBRARY": "1", "EVENTS": str(self.events)}

    def test_cleanup_refuses_active_or_unknown_proxy_and_never_calls_ip(self):
        for code in [0, 2]:
            result = subprocess.run(["sh", "-c", '''. "$1"
                probe_code="$2"
                pidof() { return "$probe_code"; }
                logger() { :; }
                ip() { echo unexpected-network-command; return 0; }
                dae_guard_cleanup
                ''', "test", str(self.guard), str(code)], env=self.env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("unexpected-network-command", result.stdout)

    def test_cleanup_leaves_unrelated_namespace_link_and_bpf_untouched(self):
        result = subprocess.run(["sh", "-c", '''. "$1"
            pidof() { return 1; }
            ip() {
                case "$*" in
                    'netns list') echo unrelated ;;
                    '-o link show') echo '1: lo: <LOOPBACK>' ;;
                    *) echo unexpected-network-command; return 1 ;;
                esac
            }
            dae_guard_cleanup
            ''', "test", str(self.guard)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unexpected-network-command", result.stdout)

    def start(self, ignore_term=False):
        program = self.root / "core"
        handler = ":" if ignore_term else 'echo term >> "$EVENTS"; sleep 0.15; echo exited >> "$EVENTS"; exit 0'
        program.write_text("#!/bin/sh\ntrap '" + handler + "' TERM\ntrap 'echo hup >> \"$EVENTS\"' HUP\n"
                           'echo started >> "$EVENTS"\nwhile :; do sleep 0.05; done\n')
        program.chmod(0o755)
        command = '''. "$1"
            logger() { :; }
            dae_guard_cleanup() { echo cleanup >> "$EVENTS"; }
            # Speed only the guard's timeout polling, never the child.
            sleep() { command sleep 0.02; }
            dae_guard_main run
        '''
        process = subprocess.Popen(["sh", "-c", command, "test", str(self.guard)],
                                   env=self.env | {"DAE_GUARD_PROGRAM": str(program)},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        def stop():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        self.addCleanup(stop)
        self.wait_event("started")
        return process

    def wait_event(self, wanted):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.events.exists() and wanted in self.events.read_text().splitlines():
                return
            time.sleep(0.01)
        self.fail(f"missing event {wanted}: {self.events.read_text() if self.events.exists() else ''}")

    def test_term_reaps_core_before_post_exit_cleanup(self):
        process = self.start()
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual(self.events.read_text().splitlines(), ["cleanup", "started", "term", "exited", "cleanup"])

    def test_hup_is_forwarded_without_cleaning_a_live_core(self):
        process = self.start()
        process.send_signal(signal.SIGHUP)
        self.wait_event("hup")
        self.assertEqual(self.events.read_text().splitlines().count("cleanup"), 1)
        self.assertIsNone(process.poll())
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=5), 0)

    def test_unresponsive_core_is_killed_and_failure_is_preserved(self):
        process = self.start(ignore_term=True)
        process.send_signal(signal.SIGTERM)
        self.assertNotEqual(process.wait(timeout=5), 0)
        self.assertEqual(self.events.read_text().splitlines(), ["cleanup", "started", "cleanup"])



class DaeSourceGuard(unittest.TestCase):
    """Exercise production locked preparation against tiny immutable feed fixtures."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-dae-source-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.feed = self.root / "feeds/daede"
        self.feed.mkdir(parents=True)
        self.patches = self.root / "config/rc01/patches/daede"
        self.patches.mkdir(parents=True)
        self.recipe = self.feed / "dae/Makefile"
        self.recipe.parent.mkdir()
        self.before = b"PKG_SOURCE_URL:=https://example.invalid/reviewed\nPKG_RELEASE:=2\n"
        self.after = self.before.replace(b"PKG_RELEASE:=2", b"PKG_RELEASE:=3")
        self.recipe.write_bytes(self.before)
        self.pins = self.feed / "ci/pins.env"
        self.pins.parent.mkdir()
        self.pins.write_text("CORE_UPSTREAM_COMMIT=reviewed\n")
        subprocess.run(["git", "init", "-q", str(self.feed)], check=True)
        self.git("add", ".")
        self.git("-c", "user.name=RC01 fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture baseline")
        revision = self.git("rev-parse", "HEAD").stdout.strip()
        self.recipe.write_bytes(self.after)
        self.patch = self.patches / "100-local.patch"
        self.patch.write_text(self.git("diff", "--no-ext-diff", "--binary", "HEAD").stdout)
        self.recipe.write_bytes(self.before)
        digest = lambda payload: hashlib.sha256(payload).hexdigest()
        self.lock = {"feed_commit": revision,
                     "patches": [{"name": self.patch.name, "sha256": digest(self.patch.read_bytes())}],
                     "files": {"dae/Makefile": {"before": digest(self.before), "after": digest(self.after)},
                               "ci/pins.env": {"before": digest(self.pins.read_bytes()),
                                               "after": digest(self.pins.read_bytes())}}}
        (self.patches / "sources.json").write_text(json.dumps(self.lock))
        packages = self.patches.parent / "packages"
        packages.mkdir()
        (packages / "manifest.json").write_text('{"packages": []}')
        self.baselines = self.root / "baselines"
        self.baselines.mkdir()
        closure = {"daede": {"roots": ["dae", "ci/pins.env"], "files": {
            name: {"sha256": item["before"], "mode": 0o644} for name, item in self.lock["files"].items()}}}
        (self.baselines / "recipe-closure.json").write_text(json.dumps(closure))
        self.plan = {"mode": "locked", "with_dae": True, "prepared": False,
                     "feeds": {"daede": {"commit": revision}}}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(sources, "ROOT", self.root).start()
        mock.patch.object(sources, "STATE", self.root / ".rc01-sources").start()
        mock.patch.object(sources, "BASELINES", self.baselines).start()
        mock.patch.object(sources, "validate_lock", side_effect=lambda lock: lock).start()
        mock.patch.object(sources, "ensure_baseline_objects").start()
        mock.patch.object(sources, "go_adapter").start()
        sources.write_json(sources.STATE / "lock.json", self.plan)
        sources.write_preparation_phase(self.plan, "planned")

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.feed), *arguments],
                              capture_output=True, text=True, check=True)

    def replay(self):
        sources.prepare()

    def test_exact_preimage_applies_and_replay_is_idempotent(self):
        self.replay()
        self.assertEqual(self.recipe.read_bytes(), self.after)
        first_diff = self.git("diff", "--no-ext-diff", "--binary", "HEAD").stdout
        self.replay()
        self.assertEqual(self.git("diff", "--no-ext-diff", "--binary", "HEAD").stdout, first_diff)

    def test_other_revision_is_rejected_before_mutating_feed(self):
        self.git("-c", "user.name=RC01 fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "other revision")
        with self.assertRaisesRegex(sources.SourceError, "locked commit: daede"):
            self.replay()
        self.assertEqual(self.recipe.read_bytes(), self.before)

    def test_same_head_recipe_source_drift_is_rejected(self):
        self.recipe.write_bytes(self.before.replace(b"https://example.invalid/reviewed", b"https://example.invalid/unreviewed"))
        changed = self.recipe.read_bytes()
        with self.assertRaisesRegex(sources.SourceError, "pre/postimage drift|differs from build lock"):
            self.replay()
        self.assertEqual(self.recipe.read_bytes(), changed)

    def test_pins_or_patch_bytes_cannot_drift_at_matching_head(self):
        original_pins = self.pins.read_bytes()
        self.pins.write_text("CORE_UPSTREAM_COMMIT=unreviewed\n")
        with self.assertRaisesRegex(sources.SourceError, "unrelated local feed changes"):
            self.replay()
        self.assertEqual(self.recipe.read_bytes(), self.before)
        self.pins.write_bytes(original_pins)
        self.patch.write_text(self.patch.read_text() + "\n# unreviewed patch bytes\n")
        with self.assertRaisesRegex(sources.SourceError, "daemon patch differs"):
            self.replay()
        self.assertEqual(self.recipe.read_bytes(), self.before)

    def test_partially_altered_postimage_is_rejected(self):
        self.replay()
        self.recipe.write_bytes(self.after + b"GO_PKG_TAGS:=unreviewed\n")
        with self.assertRaisesRegex(sources.SourceError, "pre/postimage drift|differs from build lock"):
            self.replay()


if __name__ == "__main__":
    unittest.main()
