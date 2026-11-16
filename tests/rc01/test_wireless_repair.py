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
    def compile_run(self, body):
        with tempfile.TemporaryDirectory(prefix='rc01-wireless-repair-') as temp:
            path = Path(temp)
            (path / 'test.c').write_text(body)
            result = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror',
                                     '-Wno-unused-function', '-Wno-unused-variable',
                                     str(path / 'test.c'), '-o', str(path / 'test')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
 int core_lock, htc; const struct params *hw_params; };
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


if __name__ == '__main__':
    unittest.main()
