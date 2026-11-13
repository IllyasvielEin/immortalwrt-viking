"""Run actual PCS functions with CCF caching and register/clock fault injection."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "target/linux/qualcommbe/patches-6.18/0313-net-pcs-ipq9574-modes-and-bringup.patch"


def added_source():
    text = PATCH.read_text().split("+++ b/drivers/net/pcs/pcs-qcom-ipq9574.c\n", 1)[1]
    text = text.split("\ndiff --git ", 1)[0]
    return "\n".join(line[1:] for line in text.splitlines() if line.startswith("+")) + "\n"


def definition(source, name, record=False):
    pattern = (r"(?m)^(?:struct|static const struct clk_ops) " + re.escape(name)
               + r"(?:\s*=)?\s*\{.*?^\};" if record else
               r"(?m)^static (?:unsigned long|int|void|struct clk_hw \*)\s*"
               + re.escape(name) + r"\([^;]*?\)\s*\{")
    match = re.search(pattern, source, re.S)
    if not match:
        raise AssertionError(f"missing actual PCS definition: {name}")
    if record:
        return match[0] + "\n"
    depth = 1
    for end in range(match.end(), len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[match.start():end + 1] + "\n"
    raise AssertionError(f"unterminated PCS definition: {name}")


def compile_harness(source, path):
    path.mkdir(parents=True, exist_ok=True)
    macros = "\n".join(re.findall(r"(?m)^#define (?:[^\n]*\\\n)*[^\n]*", source))
    types = "".join(definition(source, name, True) for name in ("ipq_pcs_mii", "ipq_pcs"))
    functions = []
    names = ["ipq_pcs_mii_clks_enable", "ipq_pcs_mii_clks_disable",
             "ipq_pcs_restore_port_clks", "ipq_pcs_config_mode",
             "ipq_pcs_config_2500basex", "ipq_pcs_config_usxgmii",
             "ipq_pcs_enable", "ipq_pcs_disable", "ipq_pcs_clk_rate_get",
             "ipq_pcs_rx_clk_recalc_rate", "ipq_pcs_tx_clk_recalc_rate",
             "ipq_pcs_clk_determine_rate"]
    for name in names:
        # The old driver has no paired-clock helpers; retain the ability to
        # compile that complete source as a negative control outside CI.
        if name.startswith("ipq_pcs_mii_clks_") and name not in source:
            continue
        functions.append(definition(source, name))
    functions.extend(definition(source, name, True) for name in
                     ("ipq_pcs_rx_clk_ops", "ipq_pcs_tx_clk_ops"))
    functions.extend(definition(source, name) for name in
                     ("ipq_pcs_clk_hw_get", "ipq_pcs_clk_register"))
    (path / "prepared_types.inc").write_text(macros + "\n" + types)
    (path / "prepared_functions.inc").write_text("\n".join(functions))
    compiler = shutil.which("cc")
    if not compiler:
        raise AssertionError("PCS clock regression requires host cc")
    executable = path / "pcs-clock"
    result = subprocess.run([compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                             "-Wno-unused-parameter", "-Wno-unused-but-set-variable",
                             "-I", str(path), str(ROOT / "tests/rc01/pcs_clock_harness.c"),
                             "-o", str(executable)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return executable


class PcsClockState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="rc01-pcs-clock-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.path = Path(cls.temporary.name)
        kernel = os.environ.get("RC01_KERNEL_SOURCE")
        cls.source = ((Path(kernel) / "drivers/net/pcs/pcs-qcom-ipq9574.c").read_text()
                      if kernel else added_source())
        cls.executable = compile_harness(cls.source, cls.path / "current")

    def check_scenario(self, name):
        result = subprocess.run([str(self.executable), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cold_qxgmii_publishes_parent_before_ccf_recalc(self):
        self.check_scenario("cold")

    def test_clock_mode_transitions_and_repeated_reconfiguration(self):
        self.check_scenario("transitions")

    def test_failed_transition_cannot_reuse_previous_mode_cache(self):
        self.check_scenario("retry")

    def test_qxgmii_faults_retry_shared_and_per_channel_setup(self):
        self.check_scenario("qx-faults")

    def test_usxgmii_faults_keep_clock_ownership_balanced(self):
        self.check_scenario("usx-faults")

    def test_partial_clock_enable_unwinds_and_can_be_disabled(self):
        self.check_scenario("enable-failure")

    def test_late_parent_publication_is_rejected(self):
        statement = "WRITE_ONCE(qpcs->clock_rate, rate);"
        self.assertEqual(self.source.count(statement), 1)
        mutant = self.source.replace(statement, "/* fault: publish too late */")
        mutant = mutant.replace("qpcs->interface = interface;",
                                statement + "\n\tqpcs->interface = interface;")
        executable = compile_harness(mutant, self.path / "late-rate")
        result = subprocess.run([str(executable), "cold"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "late CCF publication escaped detection")

    def test_stale_previous_mode_is_rejected(self):
        statement = "qpcs->interface = PHY_INTERFACE_MODE_NA;"
        self.assertIn(statement, self.source)
        mutant = self.source.replace(statement, "/* fault: retain old mode */", 1)
        executable = compile_harness(mutant, self.path / "stale-mode")
        result = subprocess.run([str(executable), "retry"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "failed mode transition escaped detection")

    def test_failed_shared_setup_cache_is_rejected(self):
        statement = "err_mode:\n\tqpcs->interface = PHY_INTERFACE_MODE_NA;"
        self.assertIn(statement, self.source)
        mutant = self.source.replace(statement, "err_mode: /* fault: keep incomplete shared setup */")
        executable = compile_harness(mutant, self.path / "shared-setup")
        result = subprocess.run([str(executable), "qx-faults"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "incomplete shared setup escaped detection")

    def test_partial_clock_reference_leak_is_rejected(self):
        statement = "clk_disable_unprepare(mii->rx_clk);"
        self.assertIn(statement, self.source)
        mutant = self.source.replace(statement, "/* fault: leak the enabled RX clock */", 1)
        executable = compile_harness(mutant, self.path / "clock-leak")
        result = subprocess.run([str(executable), "enable-failure"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "partial clock enable leak escaped detection")


if __name__ == "__main__":
    unittest.main()
