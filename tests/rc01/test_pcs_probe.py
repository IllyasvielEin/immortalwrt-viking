"""Exercise the probe extracted from the real PCS patch, including controls."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_pcs_clock import ROOT, PATCH, added_source, definition

BASE = "a0b1b29b091dc120892b5945d4da1a7653ece19c"


def compile_probe(source, directory):
    directory.mkdir()
    (directory / "probe.inc").write_text(definition(source, "ipq9574_pcs_probe"))
    compiler = shutil.which("cc")
    if not compiler:
        raise AssertionError("PCS probe regression requires host cc")
    executable = directory / "pcs-probe"
    subprocess.run([compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-Wno-unused-function",
                    "-I", str(directory), str(ROOT / "tests/rc01/pcs_probe_harness.c"),
                    "-o", str(executable)], check=True, capture_output=True, text=True)
    return executable


class PcsProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = ROOT / "build_dir/pcs-pilot"
        output.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="probe-", dir=output)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.path = Path(cls.temporary.name)
        cls.source = added_source()
        cls.executable = compile_probe(cls.source, cls.path / "current")

    def run_case(self, scenario, executable=None, rejected=False):
        result = subprocess.run([str(executable or self.executable), scenario],
                                capture_output=True, text=True)
        if rejected:
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("behavior assertion:", result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_xpcs_and_original_baseline(self):
        self.run_case("missing")
        patch = subprocess.run(["git", "show", f"{BASE}:{PATCH.relative_to(ROOT)}"],
                               cwd=ROOT, check=True, capture_output=True, text=True).stdout
        source = "\n".join(line[1:] for line in patch.split(
            "+++ b/drivers/net/pcs/pcs-qcom-ipq9574.c\n", 1)[1].split(
                "\ndiff --git ", 1)[0].splitlines() if line.startswith("+"))
        baseline = compile_probe(source, self.path / "baseline")
        self.run_case("missing", baseline, rejected=True)

    def test_reset_framework_disabled(self):
        self.run_case("disabled")

    def test_xpcs_provider_defer(self):
        self.run_case("defer")

    def test_xpcs_other_error(self):
        self.run_case("io")

    def test_legacy_unnamed_single_reset(self):
        self.run_case("legacy")

    def test_three_resets(self):
        self.run_case("triple")

    def test_optional_errors(self):
        for scenario in ("sys-error", "soft-error"):
            with self.subTest(scenario=scenario):
                self.run_case(scenario)

    def test_optional_getter_mutation(self):
        statement = "devm_reset_control_get_exclusive(dev, NULL)"
        self.assertEqual(self.source.count(statement), 1)
        mutant = self.source.replace(statement, "devm_reset_control_get_optional(dev, NULL)")
        self.run_case("missing", compile_probe(mutant, self.path / "optional"), rejected=True)

    def test_named_xpcs_mutation(self):
        statement = "devm_reset_control_get_exclusive(dev, NULL)"
        self.assertEqual(self.source.count(statement), 1)
        mutant = self.source.replace(statement, 'devm_reset_control_get_exclusive(dev, "xpcs")')
        self.run_case("legacy", compile_probe(mutant, self.path / "named"), rejected=True)


if __name__ == "__main__":
    unittest.main()
