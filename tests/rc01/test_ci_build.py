"""Run the CI build driver with a tiny make executable; never compile firmware."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import hashlib
import importlib.util
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BuildAttempts(unittest.TestCase):
    def exercise(self, parallel_code, serial_code):
        with tempfile.TemporaryDirectory(prefix="rc01-ci-build-") as directory:
            work = Path(directory)
            fake = work / "make"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n"
                            'if [ "$1" = -j4 ]; then exit ' + str(parallel_code)
                            + '; else exit ' + str(serial_code) + '; fi\n')
            fake.chmod(0o755)
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rc01-ci-build.py")],
                                    cwd=work, env=os.environ | {"PATH": str(work) + os.pathsep + os.environ["PATH"]},
                                    capture_output=True, text=True)
            return result, json.loads((work / "rc01-build-timings.json").read_text()), (work / "build.log").read_text()

    def test_parallel_success_does_not_run_serial_retry(self):
        result, metrics, log = self.exercise(0, 9)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["exit_code"] for item in metrics["attempts"]], [0])
        self.assertEqual(log.strip(), "-j4")
        self.assertGreaterEqual(metrics["attempts"][0]["elapsed_seconds"], 0)

    def test_retry_is_timed_separately_and_success_is_preserved(self):
        result, metrics, log = self.exercise(2, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["exit_code"] for item in metrics["attempts"]], [2, 0])
        self.assertEqual(log.splitlines(), ["-j4", "-j1 V=s"])

    def test_serial_failure_remains_a_failed_job(self):
        result, metrics, _ = self.exercise(2, 7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(metrics["exit_code"], 7)
        self.assertEqual(len(metrics["attempts"]), 2)

    def test_timing_write_failure_preserves_make_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="rc01-ci-enospc-") as directory:
            work = Path(directory)
            spec = importlib.util.spec_from_file_location("ci_build", ROOT / "scripts/rc01-ci-build.py")
            driver = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(driver)
            fake = work / "make"
            fake.write_text("#!/bin/sh\nexit 7\n")
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(work) + os.pathsep + os.environ["PATH"]}), \
                    mock.patch.object(Path, "write_text", side_effect=OSError(28, "No space left on device")):
                self.assertEqual(driver.build(work), 7)


class CacheRecovery(unittest.TestCase):
    def test_lookup_miss_seeds_cache_but_hidden_restore_failure_cannot(self):
        spec = importlib.util.spec_from_file_location("ci_cache", ROOT / "scripts/rc01-ci-cache.py")
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        cases = [
            (False, "success", "", "skipped", "", True, False),
            (False, "success", "known-key", "success", "", False, False),
            (False, "success", "known-key", "success", "wrong-key", False, False),
            (False, "success", "known-key", "failure", "known-key", False, False),
            (False, "failure", "", "skipped", "", False, False),
            (False, "success", "known-key", "success", "known-key", True, True),
            (True, "success", "known-key", "success", "known-key", False, False),
        ]
        for cold, lookup, expected, restored, actual, allowed, retained in cases:
            with self.subTest(cold=cold, expected=expected, actual=actual), \
                    tempfile.TemporaryDirectory(prefix="rc01-cache-state-") as directory:
                cache = Path(directory) / "cache"
                cache.mkdir()
                (cache / "partial").write_text("partial archive contents")
                save, _ = policy.prepare_cache(cache, cold, lookup, expected, restored, actual)
                self.assertEqual(save, allowed)
                self.assertEqual((cache / "partial").exists(), retained)


class BuildObservations(unittest.TestCase):
    def test_real_make_resolves_cross_compiler_with_build_path(self):
        with tempfile.TemporaryDirectory(prefix="rc01-toolchain-resolution-") as directory:
            work = Path(directory)
            cross = work / "rc01-test-cross-gcc"
            cross.write_text("#!/bin/sh\nexit 0\n")
            cross.chmod(0o755)
            result = subprocess.run(["make", "-s", "--no-print-directory", "-f",
                                     "scripts/rc01-ci-toolchain.mk", "rc01-ci-toolchain",
                                     "CONFIG_USE_LLVM_HOST=", "CONFIG_USE_LLVM_PREBUILT=",
                                     "CONFIG_USE_LLVM_BUILD=", "TARGET_CROSS=rc01-test-cross-",
                                     "TOOLCHAIN_BIN_DIRS=" + str(work)], cwd=ROOT,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("target_cc=" + str(cross), result.stdout.splitlines())

    def test_expanded_stats_path_and_actual_tool_identity(self):
        with tempfile.TemporaryDirectory(prefix="rc01-ci-metrics-") as directory:
            work = Path(directory)
            executable = work / "cross-gcc"
            executable.write_text("#!/bin/sh\necho fixture-compiler-version\n")
            executable.chmod(0o755)
            fake_make = work / "make"
            fake_make.write_text("#!/bin/sh\nprintf 'target_cc=%s\\n' '" + str(executable) + "'\n")
            fake_make.chmod(0o755)
            ccache = work / "staging_dir/host/bin/ccache"
            ccache.parent.mkdir(parents=True)
            ccache.write_text("#!/bin/sh\n[ \"$CCACHE_STATSLOG\" = \"" + str(work / "metrics/ccache-stats.log")
                              + "\" ] || exit 4\nprintf '%s\\n' \"$*\"\n")
            ccache.chmod(0o755)
            environment = os.environ | {"GITHUB_ENV": str(work / "github-env"),
                                       "PATH": str(work) + os.pathsep + os.environ["PATH"],
                                       "RC01_COMPILER_MATCHED_KEY": "fixture-prefix-hit"}
            command = [sys.executable, "-B", str(ROOT / "scripts/rc01-ci-metrics.py")]
            subprocess.run(command + ["prepare", str(work / "metrics")], cwd=work,
                           env=environment, capture_output=True, check=True)
            name, _, value = (work / "github-env").read_text().strip().partition("=")
            self.assertEqual(name, "CCACHE_STATSLOG")
            self.assertEqual(value, str(work / "metrics/ccache-stats.log"))
            subprocess.run(command + ["report", str(work / "metrics")], cwd=work,
                           env=environment | {name: value}, capture_output=True, check=True)
            result = json.loads((work / "metrics/report.json").read_text())
            self.assertEqual(result["ccache"]["exit_code"], 0)
            self.assertEqual(result["ccache"]["stdout"], "--show-log-stats --verbose")
            self.assertEqual(result["tools"]["target_cc"]["sha256"], hashlib.sha256(executable.read_bytes()).hexdigest())
            self.assertEqual(result["cache_restore"]["RC01_COMPILER_MATCHED_KEY"], "fixture-prefix-hit")


if __name__ == "__main__":
    unittest.main()
