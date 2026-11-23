"""Run the CI build driver with a tiny make executable; never compile firmware."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import hashlib
import importlib.util
import re
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


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/build-rc01.yml").read_text()

    def test_no_dae_replay_or_source_lock_remains(self):
        for text in ("include_dae", "RC01_WITH_DAE", "software_updates", "RC01_UPDATE_MODE", "replay_run_id",
                     "RC01_REPLAY", ".rc01-sources", "software-source-lock", "dae.config", "daede", "dae-test",
                     "validate-rc01-dae-kernel"):
            self.assertNotIn(text, self.text)
        inputs = self.text.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(set(re.findall(r"^      (\w+):$", inputs, re.M)), {"go_bootstrap", "cold_cache"})

    def test_prepare_step_uses_pinned_feeds_and_the_default_source_date_epoch(self):
        step = self.text.split("sh scripts/prepare-rc01.sh", 1)[1].split("- name:", 1)[0]
        self.assertIn("get_source_date_epoch.sh", step)
        self.assertIn("config/rc01/patches/**", self.text)

    def test_build_metadata_records_feed_commits_and_patch_digests(self):
        for text in ("for feed in packages luci routing nikki", "git -C \"feeds/$feed\" rev-parse HEAD",
                     "sha256sum \"$patch\"", "source_commit=$(git rev-parse HEAD)"):
            self.assertIn(text, self.text)

    def test_artifact_name_has_a_single_fixed_variant(self):
        self.assertIn("name: rc01-identity-preserved-dhcp-complete-nikki-${{ github.run_number }}", self.text)


class ConfigValidation(unittest.TestCase):
    """Run the real validator on a configuration made from its own required list."""

    def config(self, extra=""):
        script = (ROOT / "scripts/validate-rc01-config.sh").read_text()
        required = re.search(r"for symbol in \\\n(.*?)\ndo\n", script, re.S)[1]
        symbols = re.findall(r"CONFIG_[\w-]+", required)
        self.assertGreater(len(symbols), 50)
        return "".join(f"{symbol}=y\n" for symbol in symbols) + (
            'CONFIG_FEED_nikki=m\nCONFIG_GOLANG_BUILD_BOOTSTRAP=y\nCONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT=""\n') + extra

    def validate(self, text):
        with tempfile.TemporaryDirectory(prefix="rc01-config-") as directory:
            path = Path(directory) / ".config"
            path.write_text(text)
            return subprocess.run(["sh", str(ROOT / "scripts/validate-rc01-config.sh"), str(path)],
                                  capture_output=True, text=True, env=os.environ | {"RC01_GO_BOOTSTRAP_ROOT": ""})

    def test_required_selection_passes_without_any_dae_feed(self):
        result = self.validate(self.config())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dae", result.stdout)

    def test_dae_daed_and_every_ui_or_btf_form_are_rejected(self):
        for package in ("dae", "daed", "daed-geoip", "daed-geosite", "luci-app-dae", "luci-app-daed",
                        "luci-app-daede", "luci-i18n-daed-zh-cn", "vmlinux-btf"):
            for value in ("y", "m"):
                with self.subTest(package=package, value=value):
                    result = self.validate(self.config(f"CONFIG_PACKAGE_{package}={value}\n"))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Unwanted package selected: {package}", result.stderr)

    def test_full_debug_info_is_part_of_the_base_configuration(self):
        base = (ROOT / "defconfig/qualcommbe-rc01.config").read_text().splitlines()
        self.assertIn("CONFIG_KERNEL_DEBUG_INFO=y", base)
        self.assertIn("# CONFIG_KERNEL_DEBUG_INFO_REDUCED is not set", base)
        self.assertNotIn("CONFIG_KERNEL_DEBUG_INFO_REDUCED=y", base)

    def test_debug_info_is_required_and_reduced_debug_info_rejected(self):
        self.assertEqual(self.validate(self.config()).returncode, 0)
        without = "".join(line for line in self.config().splitlines(True) if line != "CONFIG_KERNEL_DEBUG_INFO=y\n")
        result = self.validate(without)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required symbol is not enabled: CONFIG_KERNEL_DEBUG_INFO", result.stderr)
        result = self.validate(self.config("CONFIG_KERNEL_DEBUG_INFO_REDUCED=y\n"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("crash symbols need full debug information", result.stderr)

    def test_a_daede_feed_is_neither_required_nor_configured(self):
        self.assertNotIn("CONFIG_FEED_daede", (ROOT / "defconfig/qualcommbe-rc01.config").read_text())
        self.assertNotIn("CONFIG_FEED_daede", (ROOT / "scripts/validate-rc01-config.sh").read_text())


if __name__ == "__main__":
    unittest.main()
