#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint8_t u8;
#define ATH12K_FLAG_CRASH_FLUSH 0
#define WMI_MLO_CMD_TIMEOUT_HZ 1
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define lockdep_assert_held(m) assert(*(m))
#define ath12k_warn(...) ((void)0)
#define ath12k_dbg(...) ((void)0)
#define ath12k_err(...) ((void)0)
#define reinit_completion(c) (*(c)=0)
struct ath12k_base {unsigned long dev_flags;};
struct ath12k {struct ath12k_base *ab;bool mlo_setup_live,mlo_teardown_pending;int pdev_idx,mlo_teardown_done;u32 mlo_teardown_status;};
struct ath12k_hw {struct ath12k radio[2];};
struct ath12k_hw_group {int num_hw,mutex;struct ath12k_hw *ah[2];};
#define for_each_ar(ah,ar,j) for((j)=0;(j)<2 && ((ar)=&(ah)->radio[j],1);(j)++)
static int nsetup,fail_at,submits,waits,expected;
static bool test_bit(int n,unsigned long *p) {return (*p&(1UL<<n))!=0;}
static int __ath12k_mac_mlo_setup(struct ath12k *ar) {if(++nsetup==fail_at)return -EIO;ar->mlo_setup_live=true;return 0;}
static u8 ath12k_get_num_partner_link(struct ath12k *ar) {(void)ar;return 3;}
static int ath12k_wmi_mlo_teardown(struct ath12k *ar) {submits++;ar->mlo_teardown_done=1;return 0;}
static unsigned long wait_for_completion_timeout(int *done,int timeout) {(void)timeout;assert(submits==expected);waits++;return *done;}
void ath12k_mac_mlo_teardown(struct ath12k_hw_group *ag);
#include "mlo-functions.inc"
int main(void) {
 struct ath12k_base base={};struct ath12k_hw hw[2]={};struct ath12k_hw_group ag={.num_hw=2,.mutex=1,.ah={&hw[0],&hw[1]}};
 for(int i=0;i<2;i++)for(int j=0;j<2;j++)hw[i].radio[j].ab=&base;
 for(int fail=1;fail<=4;fail++) {
  nsetup=submits=waits=0;fail_at=fail;expected=fail-1;
  assert(ath12k_mac_mlo_setup(&ag)==-EIO);assert(submits==expected && waits==expected);
  ath12k_mac_mlo_teardown(&ag);assert(submits==expected);
 }
 nsetup=submits=waits=0;fail_at=0;expected=4;assert(!ath12k_mac_mlo_setup(&ag));
 ath12k_mac_mlo_teardown(&ag);ath12k_mac_mlo_teardown(&ag);assert(submits==4 && waits==4);
 nsetup=submits=waits=0;assert(!ath12k_mac_mlo_setup(&ag));base.dev_flags=1;
 ath12k_mac_mlo_teardown(&ag);assert(!submits && !waits);
}
