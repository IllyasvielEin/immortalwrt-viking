"""Exercise the shipped dae guard with real child processes, without networking."""
from pathlib import Path
import os
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
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


if __name__ == "__main__":
    unittest.main()
