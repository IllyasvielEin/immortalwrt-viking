"""Fault injection into the maintained, prepared ath12k functions (no devices)."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_ds_lifecycle import declaration

SOURCE = os.environ.get('RC01_WIRELESS_SOURCE')


@unittest.skipUnless(SOURCE, 'set RC01_WIRELESS_SOURCE to prepared backports')
class WirelessRepair(unittest.TestCase):
    def compile_run(self, body, scenarios=((),), rejected=None):
        with tempfile.TemporaryDirectory(prefix='rc01-wireless-repair-') as temp:
            path = Path(temp)
            (path / 'test.c').write_text(body)
            result = subprocess.run(['cc', '-std=gnu11', '-pthread', '-Wall', '-Werror',
                                     '-Wno-unused-function', '-Wno-unused-variable',
                                     str(path / 'test.c'), '-o', str(path / 'test')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for args in scenarios:
                result = subprocess.run([str(path / 'test'), *args], capture_output=True,
                                        text=True, timeout=10)
                if rejected is None:
                    self.assertEqual(result.returncode, 0, str(args) + result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(rejected, result.stderr)

    def test_hash_failure_restores_group_ref_and_unwinds_without_hash_destroy(self):
        source = (Path(SOURCE) / 'drivers/net/wireless/ath/ath12k/core.c').read_text()
        definitions = ''.join(declaration(source, name) for name in
                              ('ath12k_core_to_group_ref_get',
                               'ath12k_core_to_group_ref_put', 'ath12k_core_start'))
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
struct ath12k_hw_group { int mutex, num_started, id; };
struct params { bool single_pdev_only; };
struct ath12k_base { struct ath12k_hw_group *ag; bool hw_group_ref;
 bool core_live; int core_lock, htc; const struct params *hw_params; };
static int hash_result, reo, hif, wmi;
#define lockdep_assert_held(...) ((void)0)
#define ath12k_dbg(...) ((void)0)
#define ath12k_warn(...) ((void)0)
#define ath12k_err(...) ((void)0)
#define ath12k_wmi_attach(...) 0
#define ath12k_htc_init(...) 0
#define ath12k_hif_start(...) 0
#define ath12k_htc_wait_target(...) 0
#define ath12k_dp_htt_connect(...) 0
#define ath12k_wmi_connect(...) 0
#define ath12k_htc_start(...) 0
#define ath12k_wmi_wait_for_service_ready(...) 0
#define ath12k_hal_cc_config(...) ((void)0)
#define ath12k_dp_rx_pdev_reo_setup(...) 0
#define ath12k_wmi_cmd_init(...) 0
#define ath12k_wmi_wait_for_unified_ready(...) 0
#define ath12k_wmi_set_hw_mode(...) 0
#define ath12k_dp_tx_htt_h2t_ver_req_msg(...) 0
#define ath12k_acpi_set_dsm_func(...) ((void)0)
#define ath12k_link_sta_rhash_tbl_init(...) hash_result
#define ath12k_dp_rx_pdev_reo_cleanup(...) (++reo)
#define ath12k_hif_stop(...) (++hif)
#define ath12k_wmi_detach(...) (++wmi)
'''
        suffix = r'''
int main(void) {
 struct ath12k_hw_group ag = {.num_started = 2};
 struct params params = {};
 struct ath12k_base ab = {.ag = &ag, .hw_params = &params};
 hash_result = -ENOMEM;
 assert(ath12k_core_start(&ab) == -ENOMEM);
 assert(!ab.hw_group_ref && ag.num_started == 2);
 assert(reo == 1 && hif == 1 && wmi == 1);
 hash_result = -EINVAL;
 assert(ath12k_core_start(&ab) == -EINVAL);
 assert(!ab.hw_group_ref && ag.num_started == 2);
 hash_result = 0;
 assert(ath12k_core_start(&ab) == 0);
 assert(ab.hw_group_ref && ag.num_started == 3);
 ath12k_core_to_group_ref_put(&ab);
 ath12k_core_to_group_ref_put(&ab);
 assert(ag.num_started == 2);
}
'''
        self.compile_run(prefix + definitions + suffix)

    def test_request_mem_payload_owned_once_and_worker_serialized(self):
        source = (Path(SOURCE) / 'drivers/net/wireless/ath/ath12k/qmi.c').read_text()
        functions = ''.join(declaration(source, name) for name in
                            ('ath12k_qmi_indication_current', 'ath12k_qmi_driver_event_post_data',
                             'ath12k_qmi_event_mem_request',
                             'ath12k_qmi_msg_mem_request_cb'))
        functions = declaration(source, 'ath12k_qmi_peer', True) + functions
        harness = (Path(__file__).parent / 'wireless_mem_harness.c').read_text()
        self.compile_run(harness.replace('#include "mem-functions.inc"', functions))

    def test_ce_partial_failure_irq_depth_and_hal_dma_debt(self):
        ath = Path(SOURCE) / 'drivers/net/wireless/ath/ath12k'
        functions = ''
        for file, names in (
                ('ce.c', ('ath12k_ce_deinit_pipes', 'ath12k_ce_init_pipes')),
                ('ahb.c', ('ath12k_ahb_ext_grp_disable',
                           '__ath12k_ahb_ext_irq_disable', 'ath12k_ahb_ext_grp_enable')),
                ('hal.c', ('ath12k_hal_srng_init', 'ath12k_hal_srng_deinit'))):
            source = (ath / file).read_text()
            functions += ''.join(declaration(source, name) for name in names)
        harness = (Path(__file__).parent / 'wireless_resources_harness.c').read_text()
        self.compile_run(harness.replace('#include "resources-functions.inc"', functions))

    def test_group_owner_prestart_borrower_failure_exit_and_business_completion(self):
        ath = Path(SOURCE) / 'drivers/net/wireless/ath/ath12k'
        source = (ath / 'core.c').read_text()
        functions = ''.join(declaration(source, name) for name in (
            'ath12k_core_qmi_firmware_ready', 'ath12k_core_recovery_error',
            'ath12k_core_reconfig_complete',
            'ath12k_core_queue_reset', 'ath12k_core_recovery_exiting',
            'ath12k_core_recovery_close', 'ath12k_core_recovery_drain',
            'ath12k_core_recovery_stop', 'ath12k_core_reset', 'ath12k_core_group_close'))
        functions += declaration((ath / 'qmi.c').read_text(), 'ath12k_qmi_detach_mlo_mem')
        functions = ('void ath12k_qmi_detach_mlo_mem(struct ath12k_base *ab);\n'
                     + functions)
        harness = (Path(__file__).parent / 'wireless_recovery_harness.c').read_text()
        self.compile_run(harness.replace('#include "recovery-functions.inc"', functions),
                         [(case,) for case in ('normal', 'double-crash', 'power-fail',
                          'boot-crash', 'ds-debt', 'remove', 'boot-timeout',
                          'business-timeout', 'ready-exit')])
        mutant = functions.replace('\tath12k_core_recovery_close(ag);',
                                   '\t/* negative control: omit group close */', 1)
        self.compile_run(harness.replace('#include "recovery-functions.inc"', mutant),
                         [('normal',)], rejected='events_stopped')

    def test_qrtr_lookup_instance_generation_duplicate_and_inflight_peer(self):
        source = (Path(SOURCE) / 'drivers/net/wireless/ath/ath12k/qmi.c').read_text()
        functions = declaration(source, 'ath12k_qmi_peer', True)
        functions += ''.join(declaration(source, name) for name in (
            'ath12k_qmi_generation_current', 'ath12k_qmi_indication_current',
            'ath12k_qmi_driver_event_post_data', 'ath12k_qmi_event_mem_request',
            'ath12k_qmi_ops_new_server', 'ath12k_qmi_ops_del_server',
            'ath12k_qmi_driver_event_work'))
        common = (Path(__file__).parent / 'wireless_mem_harness.c').read_text()
        common = common[:common.index('#include "mem-functions.inc"')]
        harness = (Path(__file__).parent / 'wireless_qrtr_harness.c').read_text()
        self.compile_run(common + harness.replace('#include "qrtr-functions.inc"', functions))
        mutant = functions.replace('if (peer->gen == qmi->server_gen)', 'if (true)')
        self.compile_run(common + harness.replace('#include "qrtr-functions.inc"', mutant),
                         rejected='q->server_present')

    def test_thermal_partial_setup_and_rfkill_failure_restore_pdev_resources(self):
        ath = Path(SOURCE) / 'drivers/net/wireless/ath/ath12k'
        functions = ''
        for file, names in (
                ('thermal.c', ('ath12k_thermal_setup_radio', 'ath12k_thermal_cleanup_radio',
                               'ath12k_thermal_register', 'ath12k_thermal_unregister')),
                ('core.c', ('ath12k_core_pdev_create', 'ath12k_core_pdev_destroy',
                            'ath12k_core_device_setup', 'ath12k_core_device_cleanup'))):
            functions += ''.join(declaration((ath / file).read_text(), name) for name in names)
        harness = (Path(__file__).parent / 'wireless_pdev_harness.c').read_text()
        self.compile_run(harness.replace('#include "pdev-functions.inc"', functions))

    def test_mhi_irq_admission_close_race_and_boot_crash(self):
        ath = Path(SOURCE) / 'drivers/net/wireless/ath/ath12k'
        functions = ''.join(declaration((ath / 'core.c').read_text(), name) for name in
                            ('ath12k_core_recovery_error', 'ath12k_core_queue_reset'))
        functions += declaration((ath / 'mhi.c').read_text(), 'ath12k_mhi_op_status_cb')
        harness = (Path(__file__).parent / 'wireless_admission_harness.c').read_text()
        self.compile_run(harness.replace('#include "admission-functions.inc"', functions))

    def test_mlo_partial_current_ah_unwind_and_duplicate_teardown(self):
        source = (Path(SOURCE) / 'drivers/net/wireless/ath/ath12k/mac.c').read_text()
        functions = ''.join(declaration(source, name) for name in
                            ('__ath12k_mac_mlo_teardown', 'ath12k_mac_mlo_setup',
                             'ath12k_mac_mlo_teardown'))
        harness = (Path(__file__).parent / 'wireless_mlo_harness.c').read_text()
        self.compile_run(harness.replace('#include "mlo-functions.inc"', functions))

    def test_mac_partial_channels_wait_for_group_owner_cleanup(self):
        source = (Path(SOURCE) / 'drivers/net/wireless/ath/ath12k/mac.c').read_text()
        functions = ''.join(declaration(source, name) for name in
                            ('ath12k_mac_cleanup_unregister', 'ath12k_mac_hw_unregister',
                             'ath12k_mac_setup_register'))
        harness = (Path(__file__).parent / 'wireless_mac_resources_harness.c').read_text()
        self.compile_run(harness.replace('#include "mac-resources-functions.inc"', functions))


if __name__ == '__main__':
    unittest.main()
