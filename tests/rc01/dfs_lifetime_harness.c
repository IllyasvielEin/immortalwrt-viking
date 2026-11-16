/* SPDX-License-Identifier: BSD-3-Clause-Clear */
/* Extracted production function bodies; actual mutexes, waiters and CAS. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <time.h>
#include <limits.h>
typedef uint32_t u32;
typedef uint8_t u8;
typedef uint16_t u16;
typedef int8_t s8;
typedef _Atomic int atomic_t;
typedef pthread_mutex_t spinlock_t;
#define ATH12K_REG_DOUBT (-1)
#define ATH12K_REG_USER_ONLY 0x10000
#define IEEE80211_NUM_ACS 4
#define MAX_RADIOS 3
#define GFP_ATOMIC 0
#define ATH12K_REG_UPDATE_TIMEOUT_HZ 1
#define ATH12K_MIN_6GHZ_FREQ 5925
#define WMI_HOST_WLAN_2GHZ_CAP 1
#define WMI_HOST_WLAN_5GHZ_CAP 2
#define ATH12K_HW_STATE_ON 1
#define ATH12K_HW_STATE_RESTARTING 2
#define ATH12K_SCAN_STARTING 1
#define ATH12K_SCAN_RUNNING 2
#define ATH12K_SCAN_TIMEOUT_HZ 1
#define IEEE80211_CHAN_DISABLED 1
#define IEEE80211_CHAN_RADAR 2
#define IEEE80211_CHAN_NO_IR 4
#define KHZ_TO_MHZ(n) ((n)/1000)
#define MODE_11G 1
#define MODE_11A 2
#define kzalloc_flex(obj,member,n) calloc(1,sizeof(obj)+(n)*sizeof((obj).member[0]))
#define ATH12K_FLAG_REGISTERED 0
#define ATH12K_FLAG_UNREGISTERING 1
#define REGULATORY_WIPHY_SELF_MANAGED 1
#define ATH12K_11D_IDLE 0
#define ATH12K_11D_PREPARING 1
#define ATH12K_11D_RUNNING 2
#define ATH12K_11D_INVALID_VDEV_ID UINT32_MAX
#define ATH12K_SCAN_11D_INTERVAL 10
#define WMI_TLV_SERVICE_11D_OFFLOAD 0
#define WMI_TAG_11D_NEW_COUNTRY_EVENT 0
#define NL80211_REGDOM_SET_BY_DRIVER 1
#define NL80211_REGDOM_SET_BY_USER 2
#define NL80211_USER_REG_HINT_USER 1
#define ALPHA_IS_SET 1
#define NL80211_RRF_NO_HT40 (NL80211_RRF_NO_HT40MINUS | NL80211_RRF_NO_HT40PLUS)
#define BIT(n) (1U << (n))
#define REG_ALPHA2_LEN 2
#define MHZ_TO_KHZ(n) ((n)*1000)
#define DBI_TO_MBI(n) ((n)*100)
#define DBM_TO_MBM(n) ((n)*100)
#define min_t(t,a,b) min((t)(a),(t)(b))
#define ETSI_WEATHER_RADAR_BAND_LOW 5590
#define ETSI_WEATHER_RADAR_BAND_HIGH 5650
#define ETSI_WEATHER_RADAR_BAND_CAC_TIMEOUT 600000
#include "prepared-dfs-types.h"
static void *test_alloc(size_t size);
#define kzalloc_obj(x,f) test_alloc(sizeof(x))
#define kzalloc(n,f) test_alloc(n)
#define kfree(p) free((void *)(p))
#define max(a,b) ((a)>(b)?(a):(b))
#define min(a,b) ((a)<(b)?(a):(b))
#define IS_ENABLED(x) true
#define WARN_ON(x) (x)
#define WARN(x,...) (x)
#define IS_ERR(p) ((intptr_t)(p) == -ENOMEM)
#define PTR_ERR(p) (-ENOMEM)
#define ath12k_warn(...) ((void)0)
#define ath12k_dbg(...) ((void)0)
#define rcu_dereference_protected(p,c) (assert(c), (p))
#define rcu_assign_pointer(p,v) ((p)=(v))
#define atomic_read(p) atomic_load(p)
#define atomic_set(p,v) atomic_store(p,v)
#define test_bit(n,p) (!!(*(p)&(1ul<<(n))))
static _Thread_local spinlock_t *held_base,*lock_stack[8];
static _Thread_local unsigned lock_depth;
static _Thread_local bool have_rtnl,have_wiphy;
static spinlock_t rtnl=PTHREAD_MUTEX_INITIALIZER;
static void spin_lock_bh(spinlock_t *p) {for(unsigned i=0;i<lock_depth;i++)assert(lock_stack[i]!=p);assert(lock_depth<8);pthread_mutex_lock(p);lock_stack[lock_depth++]=p;held_base=p;}
static void spin_unlock_bh(spinlock_t *p) {assert(held_base==p);--lock_depth;held_base=lock_depth?lock_stack[lock_depth-1]:NULL;pthread_mutex_unlock(p);}
#define spin_lock(p) pthread_mutex_lock(p)
#define spin_unlock(p) pthread_mutex_unlock(p)
#define lockdep_assert_held(p) assert(held_base==(p))
#define lockdep_assert_wiphy(w) assert(have_wiphy)
#define lockdep_is_held(p) (have_wiphy)
#define ASSERT_RTNL() assert(have_rtnl)
static void rtnl_lock(void) {assert(!held_base && !have_wiphy);pthread_mutex_lock(&rtnl);have_rtnl=true;}
static void rtnl_unlock(void) {assert(!have_wiphy);have_rtnl=false;pthread_mutex_unlock(&rtnl);}
struct list_head {struct list_head *next,*prev;};
#define LIST_HEAD(n) struct list_head n={&n,&n}
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define INIT_LIST_HEAD(h) ((h)->next=(h)->prev=(h))
#define list_first_entry_or_null(h,t,m) ((h)->next==(h)?NULL:container_of((h)->next,t,m))
static void list_add_tail(struct list_head *n,struct list_head *h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
static void list_del(struct list_head *n) {n->prev->next=n->next;n->next->prev=n->prev;}
static void list_splice_tail_init(struct list_head *a,struct list_head *b) {
 if(a->next!=a){a->next->prev=b->prev;a->prev->next=b;b->prev->next=a->next;b->prev=a->prev;a->next=a->prev=a;}
}
#define list_for_each_entry_safe(p,n,h,m) for(p=container_of((h)->next,__typeof__(*p),m),n=container_of(p->m.next,__typeof__(*p),m);&p->m!=(h);p=n,n=container_of(n->m.next,__typeof__(*n),m))
struct work_struct {bool pending,running;int queues;pthread_mutex_t lock;pthread_cond_t cond;void (*fn)(void *);void *arg;};
static int system_wq;
#define system_unbound_wq (&system_wq)
static void work_init(struct work_struct *w){pthread_mutex_init(&w->lock,NULL);pthread_cond_init(&w->cond,NULL);}
static bool queue_work(int *q,struct work_struct *w) {pthread_mutex_lock(&w->lock);w->queues++;bool r=!w->pending;w->pending=true;pthread_mutex_unlock(&w->lock);return r;}
static void cancel_work_sync(struct work_struct *w) {assert(!held_base && !have_rtnl && !have_wiphy);pthread_mutex_lock(&w->lock);w->pending=false;while(w->running)pthread_cond_wait(&w->cond,&w->lock);pthread_mutex_unlock(&w->lock);}
static void *run_work(void *p){struct work_struct *w=p;pthread_mutex_lock(&w->lock);if(!w->pending){pthread_mutex_unlock(&w->lock);return NULL;}w->pending=false;w->running=true;pthread_mutex_unlock(&w->lock);w->fn(w->arg);pthread_mutex_lock(&w->lock);w->running=false;pthread_cond_broadcast(&w->cond);pthread_mutex_unlock(&w->lock);return NULL;}
struct waitqueue {pthread_mutex_t lock;pthread_cond_t cond;};
static void wake_up_all(struct waitqueue *w) {pthread_mutex_lock(&w->lock);pthread_cond_broadcast(&w->cond);pthread_mutex_unlock(&w->lock);}
/* Kernel wait_event prepares its waiter before testing. Test the condition
 * outside the waitqueue mutex to avoid reversing base_lock -> wakeup. */
#define wait_event(w,c) do {while(!(c)){struct timespec t;clock_gettime(CLOCK_REALTIME,&t);t.tv_nsec+=1000000;if(t.tv_nsec>=1000000000){t.tv_sec++;t.tv_nsec-=1000000000;}pthread_mutex_lock(&(w).lock);pthread_cond_timedwait(&(w).cond,&(w).lock,&t);pthread_mutex_unlock(&(w).lock);}} while(0)
struct completion {int done;};
static long wait_for_completion_timeout(struct completion *c,int t) {return c->done;}
static void reinit_completion(struct completion *c){c->done=0;}
static void complete_all(struct completion *c){c->done=1;}
#define complete complete_all
struct ieee80211_freq_range {u32 start_freq_khz,end_freq_khz,max_bandwidth_khz;};
struct ieee80211_power_rule {u32 max_antenna_gain,max_eirp;};
struct ieee80211_wmm_ac {uint16_t cw_min,cw_max,cot;u8 aifsn;};
struct ieee80211_wmm_rule {struct ieee80211_wmm_ac client[4],ap[4];};
struct ieee80211_reg_rule {struct ieee80211_freq_range freq_range;struct ieee80211_power_rule power_rule;struct ieee80211_wmm_rule wmm_rule;u32 flags,dfs_cac_ms;bool has_wmm;int8_t psd;};
struct ieee80211_regdomain {u32 n_reg_rules;char alpha2[3];int dfs_region;struct ieee80211_reg_rule reg_rules[];};
struct cfg80211_registered_device {const struct ieee80211_regdomain *requested_regd;};
struct ath12k_hw;
struct ieee80211_channel {u32 center_freq;int flags,max_power,max_reg_power,max_antenna_gain,band;};
struct ieee80211_supported_band {struct ieee80211_channel *channels;int n_channels;};
struct wiphy {struct ieee80211_supported_band *bands[3];unsigned regulatory_flags;const struct ieee80211_regdomain *regd;struct cfg80211_registered_device *rdev;pthread_mutex_t mtx;struct ath12k_hw *ah;int dfs_state,entered;};
struct ieee80211_hw {struct wiphy *wiphy;};
struct ath12k_reg_freq {u32 start_freq,end_freq;};
struct ath12k_wmi_hal_reg_capabilities_ext_arg {u32 low_2ghz_chan,high_2ghz_chan,low_5ghz_chan,high_5ghz_chan;};
struct ath12k;
struct ath12k_pdev {struct ath12k *ar;};
struct ath12k_hw {struct ieee80211_hw *hw;bool regd_init_done;atomic_t reg_user_country;int state,num_radio;struct ath12k *radio[3];};
struct ath12k_base {struct {unsigned long svc_map[1];}wmi_ab;int *workqueue;struct ath12k_wmi_hal_reg_capabilities_ext_arg hal_reg_cap[3];struct {bool single_pdev_only,current_cc_support;int num_rxdma_per_pdev;} *hw_params;struct ath12k_reg_freq reg_freq_2ghz,reg_freq_5ghz,reg_freq_6ghz;spinlock_t base_lock;struct ieee80211_regdomain *default_regd[3],*new_regd[3];struct ath12k_reg_info *reg_info[3];struct ath12k_pdev pdevs[3];bool reg_events_stopped,is_reset;unsigned reg_senders;struct waitqueue reg_senders_wait;unsigned long dev_flags;int num_radios,dfs_region;char new_alpha2[2];struct work_struct update_11d_work;};
struct ath12k {struct {struct ieee80211_supported_band sbands[3];}mac;struct {int state;struct completion completed;}scan;struct ath12k_base *ab;struct ath12k_hw *ah;struct completion regd_update_completed,completed_11d_scan;u32 vdev_id_11d_scan;int state_11d,pdev_idx;bool supports_6ghz,ext,reg_work_initialized,regdom_set_by_user;char alpha2[2];spinlock_t data_lock;struct work_struct regd_update_work,regd_channel_update_work;struct list_head regd_channel_update_queue;struct ath12k_reg_freq freq_range;struct {int pdev_id;struct {u32 supported_bands;struct {u32 phy_id;}band[3];}cap;} *pdev;};
struct ath12k_wmi_channel_arg {bool allow_ht,allow_vht,allow_he,dfs_set,is_chan_passive,psc_channel;int mhz,cfreq1,minpower,maxpower,maxregpower,antennamax,phy_mode;};
struct ath12k_wmi_scan_chan_list_arg {struct list_head list;int pdev_id,nallchans;struct ath12k_wmi_channel_arg channel[];};
struct ath12k_wmi_init_country_arg {int flags;struct {char alpha2[3];} cc_info;};
struct wmi_set_current_country_arg {char alpha2[2];};
enum nl80211_band {NL80211_BAND_2GHZ,NL80211_BAND_5GHZ,NL80211_BAND_6GHZ,NUM_NL80211_BANDS};
enum wmi_vdev_type {WMI_VDEV_TYPE_UNSPEC,WMI_VDEV_TYPE_STA,WMI_VDEV_TYPE_AP};
enum ieee80211_ap_reg_power {IEEE80211_REG_UNSET_AP,IEEE80211_REG_LPI_AP,IEEE80211_REG_SP_AP,IEEE80211_REG_VLP_AP};
enum nl80211_dfs_regions {NL80211_DFS_UNSET,NL80211_DFS_FCC,NL80211_DFS_ETSI,NL80211_DFS_JP};
enum ath12k_reg_status {ATH12K_REG_STATUS_VALID,ATH12K_REG_STATUS_DROP,ATH12K_REG_STATUS_FALLBACK};
struct regulatory_request {int initiator,user_reg_hint_type;char alpha2[2];};
struct sk_buff {struct ath12k_reg_info info;int parse_result;char new_alpha2[2];};
struct wmi_11d_new_cc_event {char new_alpha2[2];};
static const void **ath12k_wmi_tlv_parse(struct ath12k_base *ab,struct sk_buff *skb){if(skb->parse_result)return (void *)(intptr_t)-ENOMEM;const void **tb=malloc(sizeof(void *));tb[0]=skb->new_alpha2;return tb;}
#define for_each_ar(ah,ar,i) for(i=0;i<(ah)->num_radio && ((ar)=(ah)->radio[i],true);i++)
static struct ath12k *ath12k_ah_to_ar(struct ath12k_hw *ah,int i){return ah->radio[i];}
static struct ieee80211_hw *ath12k_ar_to_hw(struct ath12k *ar){return ar->ah->hw;}
static struct ath12k_hw *ath12k_ar_to_ah(struct ath12k *ar){return ar->ah;}
static struct ath12k_hw *ath12k_hw_to_ah(struct ieee80211_hw *hw){return hw->wiphy->ah;}
static struct ieee80211_hw *wiphy_to_ieee80211_hw(struct wiphy *w){return w->ah->hw;}
static void wiphy_lock(struct wiphy *w){assert(have_rtnl && !held_base && !have_wiphy);pthread_mutex_lock(&w->mtx);have_wiphy=true;}
static void wiphy_unlock(struct wiphy *w){assert(have_wiphy && !held_base);have_wiphy=false;pthread_mutex_unlock(&w->mtx);}
static void ath12k_mac_update_freq_range(struct ath12k *ar,u32 lo,u32 hi){assert(held_base);ar->freq_range=(struct ath12k_reg_freq){lo,hi};}
static bool ath12k_wmi_supports_6ghz_cc_ext(struct ath12k *ar){return ar->supports_6ghz && ar->ext;}
static int country_sends,send_failure,send_failure_at,sync_publications,init_submissions,copy_fail;
static atomic_t pause_send,send_entered,release_send,parse_pause,parse_entered,parse_release;
static atomic_t alloc_pause,alloc_entered,alloc_release,writer_done;
static void spin_pause(atomic_t *ready,atomic_t *release){atomic_store(ready,1);while(!atomic_load(release)){};}
static void *test_alloc(size_t size){if(held_base && atomic_exchange(&alloc_pause,0))spin_pause(&alloc_entered,&alloc_release);return calloc(1,size);}
static int ath12k_wmi_send_set_current_country_cmd(struct ath12k *ar,struct wmi_set_current_country_arg *arg){country_sends++;assert(ar->ab->reg_senders);if(atomic_load(&pause_send))spin_pause(&send_entered,&release_send);return country_sends==send_failure_at?-EIO:send_failure;}
static int ath12k_wmi_send_init_country_cmd(struct ath12k *ar,struct ath12k_wmi_init_country_arg *arg){struct wmi_set_current_country_arg a;return ath12k_wmi_send_set_current_country_cmd(ar,&a);}
static void ath12k_mac_11d_scan_stop(struct ath12k *ar){assert(have_wiphy && ar->ab->reg_senders);}
static int ath12k_reg_update_chan_list(struct ath12k *ar,bool wait);
static int channel_sends;
static int ath12k_wmi_send_scan_chan_list_cmd(struct ath12k *ar,struct ath12k_wmi_scan_chan_list_arg *arg){assert(ar->ab->reg_senders);channel_sends++;return send_failure;}
static bool cfg80211_channel_is_psc(struct ieee80211_channel *ch){return ch->center_freq==5975;}
static struct cfg80211_registered_device *wiphy_to_rdev(struct wiphy *w){return w->rdev;}
static bool is_valid_rd(struct ieee80211_regdomain *r){return r->alpha2[0]!='!';}
static void print_regdomain_info(struct ieee80211_regdomain *r){}
static const struct ieee80211_regdomain *get_wiphy_regdom(struct wiphy *w){return w->regd;}
static void rcu_free_regdom(const struct ieee80211_regdomain *r){free((void *)r);}
static struct ieee80211_regdomain *reg_copy_regd(struct ieee80211_regdomain *r){if(copy_fail)return (void *)(intptr_t)-ENOMEM;size_t n=sizeof(*r)+r->n_reg_rules*sizeof(r->reg_rules[0]);void *p=malloc(n);assert(p);memcpy(p,r,n);return p;}
static int reg_requests_lock_dummy,reg_work;
static pthread_mutex_t reg_requests_lock=PTHREAD_MUTEX_INITIALIZER;
static void schedule_work(int *w){init_submissions++;}
static void reg_check_channels(void){sync_publications++;}
static struct ieee80211_regdomain ath12k_world_regd={.alpha2="00"};
static int ath12k_pull_reg_chan_list_ext_update_ev(struct ath12k_base *ab,struct sk_buff *skb,struct ath12k_reg_info *info){*info=skb->info;if(info->num_2g_reg_rules){info->reg_rules_2g_ptr=malloc(sizeof(struct ath12k_reg_rule));*info->reg_rules_2g_ptr=(struct ath12k_reg_rule){.start_freq=2402,.end_freq=2482,.max_bw=40,.reg_power=20};}if(atomic_load(&parse_pause))spin_pause(&parse_entered,&parse_release);return skb->parse_result;}
static atomic_t *cas_doubt;
static bool cas_try(atomic_t *p,int *old,int next){if(cas_doubt){atomic_store(cas_doubt,-1);cas_doubt=NULL;}return atomic_compare_exchange_strong(p,old,next);}
#define atomic_try_cmpxchg cas_try
struct wmi_11d_scan_start_arg {u32 vdev_id,start_interval_msec,scan_period_msec;};
static bool ap_active;
static int scan_sends;
static bool ath12k_mac_vif_ap_active_any(struct ath12k_base *ab){return ap_active;}
static int ath12k_wmi_send_11d_scan_start_cmd(struct ath12k *ar,struct wmi_11d_scan_start_arg *arg){assert(ar->ab->reg_senders);scan_sends++;return send_failure;}
#include "prepared-dfs.h"
struct fixture {struct ath12k_hw ah;struct ath12k_base ab;struct ath12k ar[3];struct ieee80211_hw hw;struct wiphy w;struct cfg80211_registered_device cfg;__typeof__(*(((struct ath12k *)0)->pdev)) pd[3];__typeof__(*(((struct ath12k_base *)0)->hw_params)) params;};
static struct fixture f;
static struct ieee80211_regdomain *rd(char c){struct ieee80211_regdomain a={.alpha2={c,c,0}};return reg_copy_regd(&a);}
static void setup(void){
 memset(&f,0,sizeof(f));pthread_mutex_init(&f.ab.base_lock,NULL);pthread_mutex_init(&f.w.mtx,NULL);pthread_mutex_init(&f.ab.reg_senders_wait.lock,NULL);pthread_cond_init(&f.ab.reg_senders_wait.cond,NULL);
 work_init(&f.ab.update_11d_work);
 f.hw.wiphy=&f.w;f.w.ah=&f.ah;f.w.rdev=&f.cfg;f.w.regulatory_flags=1;f.ah.hw=&f.hw;f.ah.num_radio=3;f.ah.state=1;f.ab.num_radios=3;f.ab.hw_params=&f.params;f.params.current_cc_support=true;f.ab.reg_events_stopped=true;
 for(int i=0;i<3;i++){f.ah.radio[i]=&f.ar[i];f.ab.pdevs[i].ar=&f.ar[i];f.ar[i].ab=&f.ab;f.ar[i].ah=&f.ah;f.ar[i].pdev_idx=i;f.ar[i].pdev=&f.pd[i];f.ar[i].reg_work_initialized=true;work_init(&f.ar[i].regd_update_work);work_init(&f.ar[i].regd_channel_update_work);f.ar[i].regd_update_completed.done=1;pthread_mutex_init(&f.ar[i].data_lock,NULL);f.ar[i].regd_channel_update_queue=(struct list_head){&f.ar[i].regd_channel_update_queue,&f.ar[i].regd_channel_update_queue};}
 assert(ath12k_reg_events_open(&f.ab)==0);
}
static void consume(void){rtnl_lock();wiphy_lock(&f.w);reg_process_self_managed_hint(&f.w);wiphy_unlock(&f.w);rtnl_unlock();}
static void request(char c){f.ab.new_regd[0]=rd(c);f.ar[0].regd_update_completed.done=1;assert(ath12k_regd_update(&f.ar[0],false)==0);}
static void hint(char c){struct regulatory_request r={.initiator=NL80211_REGDOM_SET_BY_USER,.user_reg_hint_type=NL80211_USER_REG_HINT_USER,.alpha2={c,c}};rtnl_lock();ath12k_reg_notifier(&f.w,&r);rtnl_unlock();}
static void publish(void *p){ath12k_regd_update(p,false);}
static void *sender(void *p){ath12k_reg_restore_country(&f.ar[0]);return NULL;}
static atomic_t drain_started,drain_finished;
static void *drainer(void *p){atomic_store(&drain_started,1);ath12k_reg_events_drain(&f.ab);atomic_store(&drain_finished,1);return NULL;}
static void *parser(void *p){int *result=malloc(sizeof(int));*result=ath12k_reg_chan_list_event(&f.ab,p);atomic_store(&writer_done,1);return result;}
static void *rebuild_thread(void *p){int *r=malloc(sizeof(int));*r=ath12k_reg_rebuild(&f.ah,WMI_VDEV_TYPE_AP,IEEE80211_REG_UNSET_AP);return r;}
static void user_case(void){
 hint('A');assert(country_sends==3);hint('A');assert(country_sends==3);
 hint('B');assert(country_sends==6);hint('A');assert(country_sends==9);
 /* USER read wins, but invalidation wins the CAS; it must still send. */
 cas_doubt=&f.ah.reg_user_country;hint('A');assert(country_sends==12);assert(atomic_load(&f.ah.reg_user_country)==-1);
 hint('A');assert(country_sends==15);
 /* External/11d C then USER B/A remains DOUBT, paying every real WMI send. */
 memcpy(f.ar[0].alpha2,"CC",2);assert(ath12k_reg_restore_country(&f.ar[0])==0);hint('B');hint('A');assert(country_sends==22);
 send_failure=-EIO;hint('A');assert(country_sends==25);send_failure=0;hint('A');assert(country_sends==28);assert(atomic_load(&f.ah.reg_user_country)==-1);
 ath12k_reg_events_close(&f.ab);assert(ath12k_reg_events_open(&f.ab)==0);assert(atomic_load(&f.ah.reg_user_country)==-1);
}
int main(int argc,char **argv){assert(argc==2);setup();const char *which=argv[1];
 if(!strcmp(which,"init")){
  f.ab.default_regd[0]=rd('A');assert(ath12k_regd_update(&f.ar[0],true)==0);assert(init_submissions==1 && !f.w.regd && f.cfg.requested_regd);
  request('B');assert(f.w.regd->alpha2[0]=='B' && !f.cfg.requested_regd);consume();assert(f.w.regd->alpha2[0]=='B');
  /* Published A -> illegal init B pending -> runtime A skip is the old trap. */
  request('A');f.ab.default_regd[0]=rd('B');int r=ath12k_regd_update(&f.ar[0],true);if(r!=-EALREADY){fprintf(stderr,"second init must be EALREADY\n");return 1;}
  request('A');consume();assert(f.w.regd->alpha2[0]=='A' && init_submissions==1);
 }else if(!strcmp(which,"aba")){
  f.w.regd=rd('A');f.ah.regd_init_done=true;f.w.dfs_state=2;f.w.entered=123;
  request('B');request('A');consume();if(f.w.regd->alpha2[0]!='A'){fprintf(stderr,"published stale B instead of A\n");return 1;}
  assert(sync_publications==2);f.w.dfs_state=2;f.w.entered=123;
  request('A');f.ah.state=0;request('A');assert(sync_publications==2 && f.w.dfs_state==2 && f.w.entered==123);
  f.ah.state=1;f.w.dfs_state=1;request('A');assert(sync_publications==2 && f.w.dfs_state==1 && f.w.entered==123);
 }else if(!strcmp(which,"fields")){
  size_t n=sizeof(struct ieee80211_regdomain)+sizeof(struct ieee80211_reg_rule);struct ieee80211_regdomain *a=calloc(1,n),*b=calloc(1,n);a->n_reg_rules=b->n_reg_rules=1;memcpy(a->alpha2,"AA",3);memcpy(b->alpha2,"AA",3);assert(ath12k_reg_equal(a,b));
#define FIELD(x) do {b->reg_rules[0].x++;assert(!ath12k_reg_equal(a,b));b->reg_rules[0].x--;}while(0)
  FIELD(freq_range.start_freq_khz);FIELD(freq_range.end_freq_khz);FIELD(freq_range.max_bandwidth_khz);FIELD(power_rule.max_antenna_gain);FIELD(power_rule.max_eirp);FIELD(flags);FIELD(dfs_cac_ms);FIELD(psd);b->reg_rules[0].has_wmm=true;assert(!ath12k_reg_equal(a,b));a->reg_rules[0].has_wmm=true;
  for(int j=0;j<4;j++){FIELD(wmm_rule.client[j].cw_min);FIELD(wmm_rule.client[j].cw_max);FIELD(wmm_rule.client[j].cot);FIELD(wmm_rule.client[j].aifsn);FIELD(wmm_rule.ap[j].cw_min);FIELD(wmm_rule.ap[j].cw_max);FIELD(wmm_rule.ap[j].cot);FIELD(wmm_rule.ap[j].aifsn);}
  a->reg_rules[0].has_wmm=b->reg_rules[0].has_wmm=false;b->reg_rules[0].wmm_rule.client[0].aifsn=3;assert(ath12k_reg_equal(a,b));b->alpha2[2]=42;assert(ath12k_reg_equal(a,b));b->dfs_region=1;assert(!ath12k_reg_equal(a,b));b->dfs_region=0;b->alpha2[0]='B';assert(!ath12k_reg_equal(a,b));
 }else if(!strcmp(which,"source")){
  f.ah.num_radio=0;assert(ath12k_reg_rebuild(&f.ah,WMI_VDEV_TYPE_AP,IEEE80211_REG_UNSET_AP)==-ENODEV);f.ah.num_radio=3;f.ar[1].supports_6ghz=f.ar[2].supports_6ghz=true;f.ar[2].ext=true;assert(ath12k_reg_source(&f.ah)==&f.ar[1]);assert(ath12k_reg_rebuild(&f.ah,WMI_VDEV_TYPE_AP,IEEE80211_REG_UNSET_AP)==-EOPNOTSUPP);assert(!f.ab.default_regd[2]);
  f.ar[1].ext=true;f.ab.reg_info[1]=calloc(1,sizeof(struct ath12k_reg_info));*f.ab.reg_info[1]=(struct ath12k_reg_info){.phy_id=1,.alpha2="SS",.is_ext_reg_event=true,.num_2g_reg_rules=1,.max_bw_2g=40};f.ab.reg_info[1]->reg_rules_2g_ptr=calloc(1,sizeof(struct ath12k_reg_rule));*f.ab.reg_info[1]->reg_rules_2g_ptr=(struct ath12k_reg_rule){.start_freq=2402,.end_freq=2482,.max_bw=40,.reg_power=20};
  f.ab.dev_flags=1;assert(ath12k_reg_rebuild(f.ar[2].ah,WMI_VDEV_TYPE_STA,IEEE80211_REG_LPI_AP)==0);assert(f.ab.new_regd[1] && !f.ab.new_regd[2] && f.ar[1].regd_update_work.pending);
  assert(ath12k_reg_rebuild(&f.ah,WMI_VDEV_TYPE_AP,IEEE80211_REG_UNSET_AP)==0);assert(f.ab.new_regd[1]);
  f.ab.default_regd[1]=rd('S');assert(ath12k_regd_update(&f.ar[0],true)==0);assert(init_submissions==0);assert(ath12k_regd_update(&f.ar[1],true)==0);assert(ath12k_regd_update(&f.ar[2],true)==0);assert(init_submissions==1);
 }else if(!strcmp(which,"drain")){
  pthread_t s,d;memcpy(f.ar[0].alpha2,"AA",2);atomic_store(&pause_send,1);pthread_create(&s,NULL,sender,NULL);while(!atomic_load(&send_entered)){};
  ath12k_reg_events_close(&f.ab);assert(!ath12k_reg_sender_get(&f.ab));pthread_create(&d,NULL,drainer,NULL);while(!atomic_load(&drain_started)){};
  struct timespec t={.tv_nsec=20000000};nanosleep(&t,NULL);assert(!atomic_load(&drain_finished) && f.ab.reg_senders==1);atomic_store(&release_send,1);pthread_join(s,NULL);pthread_join(d,NULL);assert(atomic_load(&drain_finished) && !f.ab.reg_senders);
 }else if(!strcmp(which,"ownership")){
  f.ab.dev_flags=1;f.ar[0].supports_6ghz=f.ar[0].ext=true;f.ab.reg_info[0]=calloc(1,sizeof(struct ath12k_reg_info));
  *f.ab.reg_info[0]=(struct ath12k_reg_info){.phy_id=0,.alpha2="AA",.is_ext_reg_event=true,.num_2g_reg_rules=1,.max_bw_2g=40};
  f.ab.reg_info[0]->reg_rules_2g_ptr=calloc(1,sizeof(struct ath12k_reg_rule));*f.ab.reg_info[0]->reg_rules_2g_ptr=(struct ath12k_reg_rule){.start_freq=2402,.end_freq=2482,.max_bw=40};
  pthread_t reader,writer;atomic_store(&alloc_pause,1);pthread_create(&reader,NULL,rebuild_thread,NULL);while(!atomic_load(&alloc_entered)){};
  struct sk_buff skb={.info={.phy_id=0,.alpha2="BB",.num_2g_reg_rules=1,.max_bw_2g=20}};
  atomic_store(&parse_pause,1);atomic_store(&parse_release,1);pthread_create(&writer,NULL,parser,&skb);while(!atomic_load(&parse_entered)){};
  struct timespec t={.tv_nsec=20000000};nanosleep(&t,NULL);assert(!atomic_load(&writer_done));atomic_store(&alloc_release,1);
  void *r,*w;pthread_join(reader,&r);pthread_join(writer,&w);assert(*(int *)r==0 && *(int *)w==0);free(r);free(w);
  assert(f.ab.new_regd[0]->alpha2[0]=='B' && f.ab.reg_freq_2ghz.start_freq==2402 && f.ab.reg_freq_2ghz.end_freq==2482);
 }else if(!strcmp(which,"running")){
  pthread_t worker,d;struct work_struct *w=&f.ar[0].regd_update_work;w->fn=publish;w->arg=&f.ar[0];f.ab.dev_flags=1;
  struct sk_buff skb={.info={.phy_id=0,.alpha2="BB",.num_2g_reg_rules=1,.max_bw_2g=40}};assert(ath12k_reg_chan_list_event(&f.ab,&skb)==0);
  rtnl_lock();pthread_create(&worker,NULL,run_work,w);
  /* Publisher admitted but waiting for RTNL. */
  for(;;){spin_lock_bh(&f.ab.base_lock);bool entered=f.ab.reg_senders!=0;spin_unlock_bh(&f.ab.base_lock);if(entered)break;}
  skb.info.alpha2[0]=skb.info.alpha2[1]='C';assert(ath12k_reg_chan_list_event(&f.ab,&skb)==0);assert(w->pending);
  ath12k_reg_events_close(&f.ab);pthread_create(&d,NULL,drainer,NULL);while(!atomic_load(&drain_started)){};
  struct timespec t={.tv_nsec=20000000};nanosleep(&t,NULL);assert(!atomic_load(&drain_finished));rtnl_unlock();pthread_join(worker,NULL);pthread_join(d,NULL);
  assert(!w->pending && !w->running && !f.ab.reg_senders && !f.w.regd);
 }else if(!strcmp(which,"parse")){
  pthread_t t;struct sk_buff skb={.info={.phy_id=0,.alpha2="BB",.num_2g_reg_rules=1,.max_bw_2g=40}};atomic_store(&parse_pause,1);pthread_create(&t,NULL,parser,&skb);while(!atomic_load(&parse_entered)){};ath12k_reg_events_close(&f.ab);ath12k_reg_events_drain(&f.ab);atomic_store(&parse_release,1);void *r;pthread_join(t,&r);assert(*(int *)r==-ESHUTDOWN);free(r);assert(!f.ab.reg_info[0] && !f.ar[0].regd_update_work.pending);
 }else if(!strcmp(which,"initial-late")){
  f.ab.default_regd[0]=rd('A');assert(ath12k_regd_update(&f.ar[0],true)==0);consume();
  /* Registration country command can return its event before group_start
   * marks REGISTERED; retain default-only BOOT routing, then republish it. */
  struct sk_buff skb={.info={.phy_id=0,.alpha2="BB",.num_2g_reg_rules=1,.max_bw_2g=40}};assert(ath12k_reg_chan_list_event(&f.ab,&skb)==0);assert(!f.ab.new_regd[0] && !f.ar[0].regd_update_work.pending);
  f.ab.dev_flags=1;assert(ath12k_reg_events_open(&f.ab)==0 && f.ar[0].regd_update_work.pending);assert(ath12k_regd_update(&f.ar[0],false)==0);assert(f.w.regd->alpha2[0]=='B' && init_submissions==1);
 }else if(!strcmp(which,"boot")){
  struct sk_buff skb={.info={.phy_id=0,.alpha2="BB",.num_2g_reg_rules=1,.max_bw_2g=40}};f.ab.dev_flags=1;f.ab.is_reset=true;assert(ath12k_reg_chan_list_event(&f.ab,&skb)==0);assert(f.ab.new_regd[0] && !f.ar[0].regd_update_work.pending);assert(ath12k_reg_events_open(&f.ab)==0);assert(!f.ar[0].regd_update_work.pending);
  spin_lock_bh(&f.ab.base_lock);f.ab.is_reset=false;spin_unlock_bh(&f.ab.base_lock);assert(ath12k_reg_events_open(&f.ab)==0);assert(f.ar[0].regd_update_work.pending);ath12k_reg_events_close(&f.ab);ath12k_reg_events_drain(&f.ab);assert(!f.ar[0].regd_update_work.pending);assert(ath12k_reg_chan_list_event(&f.ab,&skb)==-ESHUTDOWN);f.ab.dev_flags|=2;assert(ath12k_reg_events_open(&f.ab)==-ESHUTDOWN && f.ab.reg_events_stopped);
 }else if(!strcmp(which,"channel")){
  struct ieee80211_channel channels[2]={{.center_freq=2412,.max_power=20},{.center_freq=2462,.flags=IEEE80211_CHAN_DISABLED}};
  struct ieee80211_supported_band band={.channels=channels,.n_channels=2};f.w.bands[0]=&band;f.ar[0].mac.sbands[0]=band;f.ar[0].freq_range=(struct ath12k_reg_freq){2400000,2500000};
  assert(ath12k_reg_update_chan_list(&f.ar[0],false)==0 && channel_sends==1 && !f.ab.reg_senders);
  assert(ath12k_reg_update_chan_list(&f.ar[0],true)==0 && f.ar[0].regd_channel_update_work.pending);ath12k_regd_update_chan_list_work(&f.ar[0].regd_channel_update_work);assert(channel_sends==2 && f.ar[0].regd_channel_update_queue.next==&f.ar[0].regd_channel_update_queue);
  send_failure=-EIO;assert(ath12k_reg_update_chan_list(&f.ar[0],false)==-EIO && !f.ab.reg_senders && atomic_load(&f.ah.reg_user_country)==-1);send_failure=0;
  assert(ath12k_reg_update_chan_list(&f.ar[0],true)==0);ath12k_reg_events_close(&f.ab);assert(ath12k_reg_update_chan_list(&f.ar[0],false)==-ESHUTDOWN);ath12k_reg_events_drain(&f.ab);assert(!f.ar[0].regd_channel_update_work.pending && f.ar[0].regd_channel_update_queue.next==&f.ar[0].regd_channel_update_queue);
 }else if(!strcmp(which,"scan")){
  f.ab.wmi_ab.svc_map[0]=1;f.ar[0].vdev_id_11d_scan=ATH12K_11D_INVALID_VDEV_ID;f.ar[0].state_11d=ATH12K_11D_PREPARING;
  rtnl_lock();wiphy_lock(&f.w);ap_active=true;ath12k_mac_11d_scan_start(&f.ar[0],7);assert(!scan_sends && !f.ab.reg_senders);
  ap_active=false;send_failure=-EIO;f.ar[0].state_11d=ATH12K_11D_PREPARING;ath12k_mac_11d_scan_start(&f.ar[0],7);assert(scan_sends==1 && f.ar[0].completed_11d_scan.done && !f.ab.reg_senders && atomic_load(&f.ah.reg_user_country)==-1);
  send_failure=0;f.ar[0].state_11d=ATH12K_11D_PREPARING;ath12k_mac_11d_scan_start(&f.ar[0],7);assert(scan_sends==2 && f.ar[0].state_11d==ATH12K_11D_RUNNING);ath12k_reg_events_close(&f.ab);ath12k_mac_11d_scan_start(&f.ar[0],8);assert(scan_sends==2 && !f.ab.reg_senders);wiphy_unlock(&f.w);rtnl_unlock();
 }else if(!strcmp(which,"11d")){
  hint('A');assert(country_sends==3);struct sk_buff skb={.new_alpha2={'C','C'}};
  assert(ath12k_reg_11d_new_cc_event(&f.ab,&skb)==0);assert(f.ab.update_11d_work.pending && atomic_load(&f.ah.reg_user_country)==-1);
  /* USER interleaved before the queued 11d sender cannot hit the cache. */
  hint('A');assert(country_sends==6);ath12k_update_11d(&f.ab.update_11d_work);assert(country_sends==9);hint('B');hint('A');assert(country_sends==15 && atomic_load(&f.ah.reg_user_country)==-1);
  ath12k_reg_events_close(&f.ab);assert(ath12k_reg_11d_new_cc_event(&f.ab,&skb)==-ESHUTDOWN);
 }else if(!strcmp(which,"partial")){
  send_failure_at=2;hint('A');assert(country_sends==3 && atomic_load(&f.ah.reg_user_country)==-1);send_failure_at=0;hint('A');assert(country_sends==6);
  struct sk_buff skb={.info={.phy_id=1,.status_code=REG_SET_CC_STATUS_FAIL,.alpha2="AA"}};assert(ath12k_reg_chan_list_event(&f.ab,&skb)==-EINVAL);hint('A');assert(country_sends==9);
 }else if(!strcmp(which,"user")){user_case();
 }else if(!strcmp(which,"failure")){
  f.w.regd=rd('A');f.ab.new_regd[0]=rd('B');copy_fail=1;assert(ath12k_regd_update(&f.ar[0],false)==-ENOMEM);assert(f.w.regd->alpha2[0]=='A' && !f.ab.reg_senders && atomic_load(&f.ah.reg_user_country)==-1);copy_fail=0;request('B');assert(f.w.regd->alpha2[0]=='B');
  struct sk_buff skb={.info={.phy_id=0,.alpha2="CC"}};assert(ath12k_reg_chan_list_event(&f.ab,&skb)==-ENOMEM);assert(f.ab.reg_info[0] && !f.ab.reg_senders);skb.info.num_2g_reg_rules=1;skb.info.max_bw_2g=40;assert(ath12k_reg_chan_list_event(&f.ab,&skb)==0);skb.info.status_code=1;assert(ath12k_reg_chan_list_event(&f.ab,&skb)==-EINVAL);assert(atomic_load(&f.ah.reg_user_country)==-1);
 }else{assert(false);}
 return 0;
}
