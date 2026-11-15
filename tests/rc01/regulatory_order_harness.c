/* The actual request/queue functions are extracted into prepared-regulatory.h. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint8_t u8;
#define GFP_ATOMIC 0
#define REGULATORY_WIPHY_SELF_MANAGED 1
#define ATH12K_REG_UPDATE_TIMEOUT_HZ 1
#define ATH12K_MIN_6GHZ_FREQ 5925
#define WMI_HOST_WLAN_2GHZ_CAP 1
#define WMI_HOST_WLAN_5GHZ_CAP 2
#define ATH12K_HW_STATE_ON 1
#define WARN_ON(c) (c)
#define WARN(c, ...) (c)
#define IS_ERR(p) false
#define PTR_ERR(p) (-ENOMEM)
#define max(a,b) ((a) > (b) ? (a) : (b))
#define min(a,b) ((a) < (b) ? (a) : (b))
#define ASSERT_RTNL() ((void)0)
#define lockdep_assert_wiphy(w) ((void)0)
#define spin_lock(p) ((void)(p))
#define spin_unlock(p) ((void)(p))
#define spin_lock_bh(p) ((void)(p))
#define spin_unlock_bh(p) ((void)(p))
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define rcu_dereference(p) (p)
#define rcu_assign_pointer(p,v) ((p)=(v))
#define ath12k_warn(...) ((void)0)
#define kfree(p) free((void *)(p))
#define kzalloc(n,f) calloc(1,n)
struct ieee80211_reg_rule { unsigned int flags; };
struct ieee80211_regdomain { unsigned int n_reg_rules, dfs_region; char alpha2[2]; struct ieee80211_reg_rule reg_rules[]; };
struct cfg80211_registered_device { const struct ieee80211_regdomain *requested_regd; };
struct wiphy { unsigned int regulatory_flags; const struct ieee80211_regdomain *regd; struct cfg80211_registered_device *rdev; };
struct ieee80211_hw { struct wiphy *wiphy; };
struct ath12k_hw { struct ieee80211_hw *hw; bool regd_updated, use_6ghz_regd; int state; };
struct freq_range { u32 start_freq,end_freq; };
struct ath12k_wmi_hal_reg_capabilities_ext_arg { u32 low_2ghz_chan,high_2ghz_chan,low_5ghz_chan,high_5ghz_chan; };
struct ath12k_base { struct ath12k_wmi_hal_reg_capabilities_ext_arg hal_reg_cap[3]; struct { bool single_pdev_only; } *hw_params; struct freq_range reg_freq_2ghz,reg_freq_5ghz,reg_freq_6ghz; int base_lock; struct ieee80211_regdomain *default_regd[3],*new_regd[3]; };
struct ath12k { struct ath12k_base *ab; struct ath12k_hw *ah; int regd_update_completed,pdev_idx; bool supports_6ghz; struct freq_range freq_range; struct {struct {u32 supported_bands; struct {u32 phy_id;} band[3];}cap;} *pdev; };
enum nl80211_band { TEST_BAND };
struct regulatory_request { int unused; };
static int reg_requests_lock,reg_work,scheduled;
static struct ieee80211_regdomain ath12k_world_regd = {.alpha2={'0','0'}};
static struct ath12k_hw *ath12k_ar_to_ah(struct ath12k *ar) { return ar->ah; }
static long wait_for_completion_timeout(int *c,int t) { return 1; }
static void ath12k_mac_update_freq_range(struct ath12k *ar,u32 lo,u32 hi) { ar->freq_range=(struct freq_range){lo,hi}; }
static struct cfg80211_registered_device *wiphy_to_rdev(struct wiphy *w) { return w->rdev; }
static bool is_valid_rd(struct ieee80211_regdomain *r) { return r->alpha2[0]!='!'; }
static void print_regdomain_info(struct ieee80211_regdomain *r) { }
static const struct ieee80211_regdomain *get_wiphy_regdom(struct wiphy *w) {return w->regd;}
static void rcu_free_regdom(const struct ieee80211_regdomain *r) {kfree(r);}
static struct ieee80211_regdomain *reg_copy_regd(struct ieee80211_regdomain *r) {
 size_t n=sizeof(*r)+r->n_reg_rules*sizeof(r->reg_rules[0]); void *p=malloc(n); assert(p); memcpy(p,r,n);return p;
}
static void schedule_work(int *w) { scheduled++; }
#include "prepared-regulatory.h"
static void request(struct ath12k *ar, char country, int expected) {
 struct ieee80211_regdomain rd={.alpha2={country,country}};
 ar->ab->new_regd[0]=&rd;
 /* Each request is a new firmware country event, not another pdev in the
  * same event; resetting this flag is the event boundary under test. */
 ar->ah->regd_updated=false;
 assert(ath12k_regd_update(ar,false)==expected);
}
int main(int argc,char **argv) {
 struct ieee80211_regdomain a={.alpha2={'A','A'}};
 struct cfg80211_registered_device rdev={};
 struct wiphy w={.regulatory_flags=1,.regd=reg_copy_regd(&a),.rdev=&rdev};
 struct ieee80211_hw hw={&w};struct ath12k_hw ah={.hw=&hw,.state=1};
 struct ath12k_base ab={};struct ath12k ar={.ab=&ab,.ah=&ah};
 __typeof__(*ar.pdev) pdev={}; ar.pdev=&pdev;
 request(&ar,'B',0);
 assert(w.regd->alpha2[0]=='A' && rdev.requested_regd->alpha2[0]=='B');
 char expected;
 if (!strcmp(argv[1],"aba")) { request(&ar,'A',0);expected='A'; }
 else if (!strcmp(argv[1],"latest")) { request(&ar,'C',0);expected='C'; }
 else { request(&ar,'!',-EINVAL);expected='B'; }
 reg_process_self_managed_hint(&w);
 if (w.regd->alpha2[0]!=expected) {fprintf(stderr,"published %c instead of %c\n",w.regd->alpha2[0],expected);return 1;}
 assert(rdev.requested_regd==NULL && scheduled>0);kfree(w.regd);return 0;
}
