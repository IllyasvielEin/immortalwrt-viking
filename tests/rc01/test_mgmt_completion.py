"""MAC completion admission/drain using actual prepared entrypoint bodies.

The completion body below the admission point is a stub: these tests prove
barrier ordering, not IDR internals, hardware quiescence or kernel RCU semantics.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_group_teardown import function

SOURCE = os.environ.get('RC01_WIRELESS_SOURCE')
HERE = Path(__file__).resolve().parent

@unittest.skipUnless(SOURCE and shutil.which('cc'), 'prepared wireless source and cc required')
class MgmtCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rc01-mgmt-')
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        driver = Path(SOURCE) / 'drivers/net/wireless/ath/ath12k'
        code = function((driver / 'wmi.c').read_text(), 'ath12k_mgmt_tx_compl_event')
        code += function((driver / 'core.c').read_text(), 'ath12k_core_mac_delivery_close')
        variants = {
            'current': code,
            'no-gate': code.replace('if (READ_ONCE(ab->mac_delivery_stopped))', 'if (false)'),
            'no-drain': code.replace('synchronize_rcu();', '/* omitted barrier */'),
        }
        cls.binaries = {}
        for name, body in variants.items():
            path = root / name
            path.mkdir()
            (path / 'prepared-mgmt.h').write_text(body)
            binary = path / 'probe'
            subprocess.run(['cc', '-std=gnu11', '-pthread', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', '-Wno-unused-function', '-I', str(path),
                            str(HERE / 'mgmt_completion_harness.c'), '-o', str(binary)], check=True)
            cls.binaries[name] = binary

    def check_case(self, case, variant='current', negative=False):
        result = subprocess.run([str(self.binaries[variant]), case], capture_output=True, timeout=10)
        if negative:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Assertion', result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_closed_completion_does_not_touch_idr(self): self.check_case('closed')
    def test_normal_and_reopened_completion(self): self.check_case('open')
    def test_close_waits_for_admitted_completion(self): self.check_case('drain')
    def test_missing_gate_negative(self): self.check_case('closed', 'no-gate', True)
    def test_missing_drain_negative(self): self.check_case('drain', 'no-drain', True)
