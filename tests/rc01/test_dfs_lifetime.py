"""Actual DFS entry/helper bodies with pthread locks and controlled scheduling.

This does not emulate firmware ACKs, RF, cfg80211 channel enforcement, or
kernel workqueue/lockdep implementation. ARM64 object checks are separate.
"""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_group_teardown import function as extract_function


def function(source, name):
    body = extract_function(source, name)
    if body.startswith(name + "("):
        position = source.index(body)
        previous = source[:position].rstrip("\n").rsplit("\n", 1)[-1]
        body = previous + "\n" + body
    return body

ROOT = Path(__file__).resolve().parents[2]
WIRELESS = os.environ.get('RC01_WIRELESS_SOURCE')


@unittest.skipUnless(WIRELESS and shutil.which('cc'), 'prepared wireless source and cc required')
class DFSLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rc01-dfs-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        src = Path(WIRELESS)
        reg = (src / 'drivers/net/wireless/ath/ath12k/reg.c').read_text()
        reg_h = (src / 'drivers/net/wireless/ath/ath12k/reg.h').read_text()
        wmi_h = (src / 'drivers/net/wireless/ath/ath12k/wmi.h').read_text()
        nl = (src / 'include/uapi/linux/nl80211.h').read_text()
        cls.types = ''
        for text, names in [(reg_h, ['ath12k_dfs_region', 'ath12k_reg_cc_code', 'ath12k_reg_phy_bitmap']), (wmi_h, ['wmi_reg_6g_ap_type', 'wmi_reg_6g_client_type']), (nl, ['nl80211_reg_rule_flags'])]:
            for name in names:
                cls.types += re.search(r'enum ' + name + r' \{.*?^\};', text, re.S | re.M)[0] + '\n'
        for name in ['ath12k_reg_rule', 'ath12k_reg_info']:
            cls.types += re.search(r'struct ' + name + r' \{.*?^\};', reg_h, re.S | re.M)[0] + '\n'
        cls.types += '\n'.join(re.findall(r'^#define REGULATORY_CHAN_[^\n]+', wmi_h, re.M)) + '\n'
        mac = (src / 'drivers/net/wireless/ath/ath12k/mac.c').read_text()
        core = (src / 'drivers/net/wireless/ath/ath12k/core.c').read_text()
        wmi = (src / 'drivers/net/wireless/ath/ath12k/wmi.c').read_text()
        cfg = (src / 'net/wireless/reg.c').read_text()
        prefix = function(cfg, 'reg_process_self_managed_hint')
        prefix = prefix[:prefix.index('\n\tfor (band =')] + '\n}\n'
        code = (function(cfg, '__regulatory_set_wiphy_regd') + prefix +
                function(cfg, 'regulatory_set_wiphy_regd') +
                function(cfg, 'regulatory_set_wiphy_regd_sync'))
        names = ['ath12k_map_fw_dfs_region', 'ath12k_get_bw_reg_flags',
                 'ath12k_map_fw_reg_flags', 'ath12k_map_fw_phy_flags',
                 'ath12k_reg_get_regdom_str', 'ath12k_reg_adjust_bw',
                 'ath12k_reg_update_rule', 'ath12k_reg_update_weather_radar_band',
                 'ath12k_reg_update_freq_range', 'ath12k_reg_ap_pwr_convert',
                 'ath12k_reg_build_regd', 'ath12k_reg_reset_reg_info',
                 'ath12k_reg_doubt_locked', 'ath12k_reg_doubt',
                 'ath12k_reg_user_skip', 'ath12k_reg_sender_get',
                 'ath12k_reg_sender_put', 'ath12k_reg_senders_idle',
                 'ath12k_reg_source', 'ath12k_reg_events_close',
                 'ath12k_reg_events_drain', 'ath12k_reg_events_open',
                 'ath12k_reg_update_chan_list_admitted', 'ath12k_reg_update_chan_list',
                 'ath12k_regd_update_chan_list_work', 'ath12k_reg_notifier', 'ath12k_copy_regd',
                 'ath12k_reg_wmm_equal', 'ath12k_reg_equal',
                 'ath12k_regd_update', 'ath12k_reg_validate_reg_info',
                 'ath12k_reg_handle_chan_list', 'ath12k_reg_rebuild',
                 'ath12k_reg_register_country', 'ath12k_reg_restore_country']
        code += ''.join(function(reg, n) for n in names)
        code += function(wmi, 'ath12k_reg_chan_list_event')
        code += function(wmi, 'ath12k_reg_11d_new_cc_event')
        code += function(core, 'ath12k_update_11d')
        code += function(mac, 'ath12k_mac_11d_scan_start')
        cls.binary = cls.build(code, 'current')
        # Concrete negative controls: reallow second init and replace sync with async.
        cls.second_init = cls.build(code.replace('if (init && ah->regd_init_done)',
                                                'if (false)'), 'second-init')
        cls.asynchronous = cls.build(code.replace('ret = regulatory_set_wiphy_regd_sync(hw->wiphy, regd_copy);',
                                                  'ret = regulatory_set_wiphy_regd(hw->wiphy, regd_copy);'), 'async')

    @classmethod
    def build(cls, code, name):
        directory = cls.work / name
        directory.mkdir()
        (directory / 'prepared-dfs-types.h').write_text(cls.types)
        (directory / 'prepared-dfs.h').write_text(code)
        binary = directory / 'dfs'
        result = subprocess.run(['cc', '-std=gnu11', '-pthread', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-function', '-I', str(directory),
                        str(ROOT / 'tests/rc01/dfs_lifetime_harness.c'), '-o', str(binary)],
                       capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return binary

    def run_case(self, case):
        r = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_recovery_equal_rules_still_publish(self): self.run_case('recovery-equal')

    def test_init_pending_consumed_second_init(self): self.run_case('init')
    def test_sync_aba_on_off_preserves_dfs(self): self.run_case('aba')
    def test_all_fields_and_wmm(self): self.run_case('fields')
    def test_fixed_source_ext_and_non_source_rebuild(self): self.run_case('source')
    def test_sender_paused_then_close_drain(self): self.run_case('drain')
    def test_wmi_replacement_waits_for_real_builder_borrow(self): self.run_case('ownership')
    def test_running_pending_requeue_drained(self): self.run_case('running')
    def test_close_during_private_parse(self): self.run_case('parse')
    def test_late_initial_boot_rule_published_without_second_init(self): self.run_case('initial-late')
    def test_queue_close_and_recovery_boot_open(self): self.run_case('boot')
    def test_real_11d_event_before_send_and_user_batches(self): self.run_case('11d')
    def test_direct_and_queued_channel_sender_gate(self): self.run_case('channel')
    def test_11d_scan_producer_admission_and_failure(self): self.run_case('scan')
    def test_partial_radio_send_failure_invalidates_batch(self): self.run_case('partial')
    def test_user_doubt_cas_and_failure_retry(self): self.run_case('user')
    def test_sync_enomem_and_builder_rejection(self): self.run_case('failure')

    def test_second_init_negative(self):
        r = subprocess.run([str(self.second_init), 'init'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b'EALREADY', r.stderr)

    def test_async_negative(self):
        r = subprocess.run([str(self.asynchronous), 'aba'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b'published', r.stderr)
