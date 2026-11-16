/* Deterministic host scheduling of the real group reset owner. I/O is simulated;
 * locks, asynchronous READY and mac80211 completion run on pthreads. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
typedef uint64_t u64;
typedef unsigned int u32;
struct mutex { pthread_mutex_t m; };
#define MUTEX_INITIALIZER {PTHREAD_MUTEX_INITIALIZER}
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->m)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->m)); }
#define spin_lock_bh mutex_lock
#define spin_unlock_bh mutex_unlock
#define spin_lock_irqsave(m,f) do { f=0; mutex_lock(m); } while(0)
#define spin_unlock_irqrestore(m,f) do { (void)f; mutex_unlock(m); } while(0)
#define lockdep_assert_held(p) ((void)0)
#define READ_ONCE(x) __atomic_load_n(&(x),__ATOMIC_ACQUIRE)
#define WRITE_ONCE(x,v) __atomic_store_n(&(x),(v),__ATOMIC_RELEASE)
#define cmpxchg(p,old,new) __sync_val_compare_and_swap(p,old,new)
#define WARN_ON(x) (!!(x))
#define ath12k_dbg(...) ((void)0)
#define ath12k_warn(...) ((void)0)
#define ath12k_info(...) ((void)0)
#define ath12k_err(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
typedef atomic_int atomic_t;
#define atomic_read(p) atomic_load(p)
#define atomic_set(p,n) atomic_store(p,n)
#define atomic_inc(p) atomic_fetch_add(p,1)
#define atomic_dec(p) atomic_fetch_sub(p,1)
static bool test_bit(int n, unsigned long *p) { return (__atomic_load_n(p,__ATOMIC_ACQUIRE)&(1UL<<n))!=0; }
static void set_bit(int n,unsigned long *p) { __atomic_fetch_or(p,1UL<<n,__ATOMIC_RELEASE); }
static void clear_bit(int n,unsigned long *p) { __atomic_fetch_and(p,~(1UL<<n),__ATOMIC_RELEASE); }
static bool test_and_clear_bit(int n,unsigned long *p) { return (__atomic_fetch_and(p,~(1UL<<n),__ATOMIC_ACQ_REL)&(1UL<<n))!=0; }
static unsigned long ticks(void) { struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000UL+t.tv_nsec/1000000; }
#define jiffies ticks()
#define time_before(a,b) ((long)((a)-(b))<0)
#define msecs_to_jiffies(x) (x)
#define ATH12K_RESET_TIMEOUT_HZ 500
#define ATH12K_QMI_WLANFW_TIMEOUT_MS 100
#define wait_event_timeout(wq,condition,budget) ({ unsigned long end=ticks()+(budget); int ok=0; (void)(wq); do { if(condition) {ok=1;break;} usleep(1000); } while(time_before(ticks(),end));ok; })
#define wake_up_all(wq) ((void)(wq))
#define complete(p) (++*(p))
#define complete_all(p) (++*(p))
#define TARGET_NUM_VDEVS(ab) 4
#define ATH12K_GROUP_FLAG_UNREGISTER 0
#define ATH12K_GROUP_FLAG_DS_QUARANTINED 1
#define ATH12K_FLAG_CRASH_FLUSH 0
#define ATH12K_FLAG_RECOVERY 1
#define ATH12K_FLAG_UNREGISTERING 2
#define ATH12K_HW_STATE_ON 1
#define ATH12K_HW_STATE_OFF 0
#define ATH12K_HW_STATE_RESTARTING 2
#define MLO_GLOBAL_MEM_REGION_TYPE 8
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
enum ath12k_group_recovery { ATH12K_GROUP_RECOVERY_IDLE, ATH12K_GROUP_RECOVERY_DRAIN, ATH12K_GROUP_RECOVERY_BOOT, ATH12K_GROUP_RECOVERY_FAILED };
struct work_struct { int kind; };
struct ath12k_base;
struct wiphy { struct mutex lock; };
struct ieee80211_hw { struct wiphy *wiphy; struct ath12k_hw *ah; };
struct radio { struct ath12k_base *ab; };
struct ath12k_hw { struct radio radio[1]; struct ieee80211_hw *hw; int state; unsigned long recovery_flags; };
struct target_mem_chunk { struct { void *addr; } v; uint64_t paddr; bool dma_allocated; int type; };
struct ath12k_qmi { struct target_mem_chunk target_mem[2]; bool mlo_mem_ref; bool events_stopped, handle_initialized; int retained_non_mlo;u64 active_gen; };
struct ath12k_base { struct ath12k_hw_group *ag; struct mutex core_lock,base_lock,recovery_lock;
 struct work_struct reset_work,restart_work,dump_work; int workqueue_aux; unsigned long dev_flags;
 bool is_reset,powered,core_live,pdev_live,dp_live,recovery_stopped,hw_group_ref,mac_delivery_stopped;int fw_mode;
 int num_radios,free_vdev_stats_id_map; uint64_t free_vdev_map;
 struct ath12k_qmi qmi; atomic_t reset_count,fail_cont_count; int reset_complete; int id; bool hal_live; };
struct ath12k_hw_group { struct mutex mutex; unsigned long flags; int num_devices,num_hw,num_started;
 struct ath12k_base *ab[3];struct ath12k_hw *ah[3];
 enum ath12k_group_recovery recovery_state;struct ath12k_base *recovery_owner;
 int recovery_error,recovery_wait;bool boot_ready;atomic_t reconfig_pending;
 struct { int num_users; } mlo_mem; };
static struct ath12k_hw_group group={.mutex=MUTEX_INITIALIZER};
static struct ath12k_base bases[3];static struct ath12k_hw hws[3];
static struct ieee80211_hw ieee[3];static struct wiphy wiphys[3];
static atomic_int powerups, powerdowns, releases, stopcores, stop_pdevs, clears, reg_drains, ready_sent, business_done;
static atomic_int paused, resume_owner, owner_done;
static int fail_power, crash_boot, ds_debt, pause_drain, suppress_ready, slow_business, exit_during_dp;
static int core_start_calls,ce_deinits;
static pthread_t fw_thread,business_threads[3]; static int have_fw, business_count;
static void ath12k_core_recovery_error(struct ath12k_base *,int);
static void ath12k_core_reconfig_complete(struct ath12k_hw *);
static void ath12k_core_queue_reset(struct ath12k_base *);
static void ath12k_core_group_close(struct ath12k_hw_group *);
static int ath12k_core_qmi_firmware_ready(struct ath12k_base *);
static void assert_closed(void) { for(int i=0;i<3;i++) assert(group.ab[i]->qmi.events_stopped); }
static void ath12k_reg_events_close(struct ath12k_base *ab) { (void)ab; }
static void ath12k_qmi_close(struct ath12k_base *ab) { ab->qmi.events_stopped=true; }
static void ath12k_reg_events_drain(struct ath12k_base *ab) { (void)ab;assert_closed();atomic_fetch_add(&reg_drains,1); }
static void ath12k_qmi_drain(struct ath12k_base *ab) {
 assert_closed();
 if(pause_drain && ab->id==0 && !atomic_exchange(&paused,1)) while(!atomic_load(&resume_owner)) usleep(1000);
}
static void cancel_work_sync(struct work_struct *w) { assert(w->kind!=1); }
static void flush_work(struct work_struct *w) { assert(w->kind==3); }
static void ath12k_core_mac_delivery_close(struct ath12k_base *ab) { (void)ab;assert_closed(); }
static void ath12k_hif_irq_disable(struct ath12k_base *ab) { (void)ab; }
static void ath12k_dp_ds_stop_group(struct ath12k_hw_group *ag) { (void)ag; }
static void ath12k_core_pdev_destroy(struct ath12k_base *ab) {
 assert_closed();if(ab->pdev_live) {ab->pdev_live=false;atomic_fetch_add(&stop_pdevs,1);}
}
#define ath12k_ab_to_dp(ab) (ab)
static bool ath12k_dp_cmn_device_deinit(struct ath12k_base *ab) {
 if(ds_debt && ab->id==1) {set_bit(ATH12K_GROUP_FLAG_DS_QUARANTINED,&group.flags);return false;} return true;
}
static void ath12k_core_to_group_ref_put(struct ath12k_base *ab) { if(ab->hw_group_ref) {ab->hw_group_ref=false;ab->ag->num_started--;} }
static void ath12k_core_stop(struct ath12k_base *ab) {
 assert_closed(); if(!ab->core_live) return; ab->core_live=false;
 ab->dp_live=!ath12k_dp_cmn_device_deinit(ab);ath12k_core_to_group_ref_put(ab);atomic_fetch_add(&stopcores,1);
}
static void ath12k_hif_power_down(struct ath12k_base *ab,bool suspend) { (void)ab;(void)suspend;atomic_fetch_add(&powerdowns,1); }
static void ath12k_qmi_release(struct ath12k_base *ab) {
 assert(ab->qmi.events_stopped);if(ab->qmi.handle_initialized) {ab->qmi.handle_initialized=false;atomic_fetch_add(&releases,1);}
}
static void ath12k_hal_srng_deinit(struct ath12k_base *ab) { assert(!ab->dp_live);ab->hal_live=false; }
static int ath12k_hal_srng_init(struct ath12k_base *ab) { assert(!ab->dp_live && !ab->hal_live);ab->hal_live=true;return 0; }
static void ath12k_qmi_put_mlo_mem_ref(struct ath12k_base *ab) { if(ab->qmi.mlo_mem_ref) {ab->qmi.mlo_mem_ref=false;assert(group.mlo_mem.num_users>0);group.mlo_mem.num_users--;} }
static void ath12k_qmi_reset_mlo_mem(struct ath12k_hw_group *ag) {
 assert(!ag->num_started && !ag->mlo_mem.num_users);
 for(int i=0;i<3;i++) assert(!ag->ab[i]->qmi.target_mem[0].v.addr);
 atomic_fetch_add(&clears,1);
}
static int ath12k_qmi_reopen_service(struct ath12k_base *ab) {
 assert(!ab->qmi.handle_initialized && ab->qmi.events_stopped);
 assert(ab->qmi.retained_non_mlo==100+ab->id);
 ab->qmi.handle_initialized=true;ab->qmi.events_stopped=false;return 0;
}
static int ath12k_reg_events_open(struct ath12k_base *ab) { return test_bit(ATH12K_FLAG_UNREGISTERING,&ab->dev_flags)?-ESHUTDOWN:0; }
static struct ath12k_hw_group *ath12k_ab_to_ag(struct ath12k_base *ab) {return ab->ag;}
static int ath12k_core_start_firmware(struct ath12k_base *ab,int mode) {(void)ab;(void)mode;return 0;}
static int ath12k_ce_init_pipes(struct ath12k_base *ab) {(void)ab;return 0;}
static void ath12k_ce_deinit_pipes(struct ath12k_base *ab) {(void)ab;ce_deinits++;}
static int ath12k_dp_cmn_device_init(struct ath12k_base *ab) {
 if(exit_during_dp) {mutex_lock(&group.mutex);ath12k_core_group_close(&group);mutex_unlock(&group.mutex);}
 (void)ab;return 0;
}
static bool ath12k_qmi_generation_current(struct ath12k_qmi *q,u64 gen) {(void)gen;return !q->events_stopped;}
static int ath12k_core_start(struct ath12k_base *ab) {core_start_calls++;ab->core_live=ab->hw_group_ref=true;group.num_started++;return 0;}
static bool ath12k_core_hw_group_start_ready(struct ath12k_hw_group *ag) {return ag->num_started==ag->num_devices;}
static int ath12k_core_hw_group_start(struct ath12k_hw_group *ag) {
 for(int i=0;i<3;i++) ag->ab[i]->pdev_live=true;
 atomic_store(&ready_sent,1);return 0;
}
static void ath12k_core_trigger_partner(struct ath12k_base *ab) {(void)ab;}
static void ath12k_dp_reoq_lut_addr_reset(struct ath12k_base *ab) {(void)ab;}
static void ath12k_qmi_firmware_stop(struct ath12k_base *ab) {(void)ab;}
static void *send_ready(void *unused) {
 (void)unused;usleep(10000);
 for(int i=0;i<3;i++) assert(!ath12k_core_qmi_firmware_ready(&bases[i]));
 return NULL;
}
static int ath12k_hif_power_up(struct ath12k_base *ab) {
 int n=atomic_fetch_add(&powerups,1)+1;
 if(crash_boot && n==crash_boot) ath12k_core_queue_reset(ab);
 if(fail_power && n==fail_power) return -EIO;
 if(n==3 && !suppress_ready) {assert(!pthread_create(&fw_thread,NULL,send_ready,NULL));have_fw=1;}
 return 0;
}
static void ath12k_coredump_collect(struct ath12k_base *ab) { (void)ab;assert_closed(); }
static void ath12k_core_pre_reconfigure_recovery(struct ath12k_base *ab) { (void)ab; }
static void ath12k_core_post_reconfigure_recovery(struct ath12k_base *ab) { (void)ab;for(int i=0;i<3;i++) if(hws[i].state==ATH12K_HW_STATE_ON) hws[i].state=ATH12K_HW_STATE_RESTARTING; }
#define wiphy_lock(w) mutex_lock(&(w)->lock)
#define wiphy_unlock(w) mutex_unlock(&(w)->lock)
static void *business(void *arg) {
 struct ath12k_hw *ah=arg;usleep(slow_business?800000:30000);
 wiphy_lock(ah->hw->wiphy);ath12k_core_reconfig_complete(ah);atomic_fetch_add(&business_done,1);wiphy_unlock(ah->hw->wiphy);return NULL;
}
static void ieee80211_restart_hw(struct ieee80211_hw *hw) { assert(atomic_load(&ready_sent));assert(!pthread_create(&business_threads[business_count++],NULL,business,hw->ah)); }
static int queue_work(int q,struct work_struct *w) { (void)q;(void)w;assert(!"BOOT must signal, not enqueue a reset behind itself");return 0; }
#include "recovery-functions.inc"
static void *run_owner(void *arg) { ath12k_core_reset(&((struct ath12k_base *)arg)->reset_work);atomic_store(&owner_done,1);return NULL; }
int main(int argc,char **argv) {
 assert(argc==2); const char *scenario=argv[1];
 fail_power=!strcmp(scenario,"power-fail")?2:0;
 crash_boot=!strcmp(scenario,"boot-crash")?2:0;
 ds_debt=!strcmp(scenario,"ds-debt");pause_drain=!strcmp(scenario,"double-crash");
 suppress_ready=!strcmp(scenario,"remove")||!strcmp(scenario,"boot-timeout");slow_business=!strcmp(scenario,"business-timeout");
 group.num_devices=group.num_hw=3;group.num_started=2;group.mlo_mem.num_users=3;
 for(int i=0;i<3;i++) {
  struct ath12k_base *ab=&bases[i];ab->id=i;ab->ag=&group;group.ab[i]=ab;
  pthread_mutex_init(&ab->base_lock.m,NULL);pthread_mutex_init(&ab->core_lock.m,NULL);pthread_mutex_init(&ab->recovery_lock.m,NULL);
  ab->reset_work.kind=1;ab->restart_work.kind=2;ab->dump_work.kind=3;
  ab->powered=ab->hal_live=ab->qmi.handle_initialized=true;
  ab->core_live=ab->pdev_live=ab->dp_live=ab->hw_group_ref=i<2;
  ab->qmi.mlo_mem_ref=true;ab->qmi.retained_non_mlo=100+i;
  ab->qmi.target_mem[0].type=MLO_GLOBAL_MEM_REGION_TYPE;ab->qmi.target_mem[0].v.addr=(void *)1;
  ab->qmi.target_mem[1].type=9;ab->qmi.target_mem[1].v.addr=(void *)2;
  pthread_mutex_init(&wiphys[i].lock.m,NULL);ieee[i].wiphy=&wiphys[i];ieee[i].ah=&hws[i];
  hws[i].radio[0].ab=ab;hws[i].hw=&ieee[i];hws[i].state=i==2?ATH12K_HW_STATE_OFF:ATH12K_HW_STATE_ON;group.ah[i]=&hws[i];
 }
 if(!strcmp(scenario,"ready-exit")) {
  for(int i=0;i<3;i++) {bases[i].core_live=bases[i].dp_live=bases[i].pdev_live=bases[i].hw_group_ref=false;bases[i].qmi.events_stopped=false;}
  group.num_started=0;exit_during_dp=1;
  assert(ath12k_core_qmi_firmware_ready(&bases[0])==-ESHUTDOWN);
  assert(!bases[0].dp_live && !bases[0].core_live && !core_start_calls && ce_deinits==1);return 0;
 }
 pthread_t owner;assert(!pthread_create(&owner,NULL,run_owner,&bases[0]));
 if(pause_drain) {while(!atomic_load(&paused)) usleep(1000);ath12k_core_reset(&bases[1].reset_work);assert(!atomic_load(&powerdowns));atomic_store(&resume_owner,1);}
 if(!strcmp(scenario,"remove")) {
  while(atomic_load(&powerups)<3) usleep(1000);
  mutex_lock(&group.mutex);ath12k_core_group_close(&group);mutex_unlock(&group.mutex);
 }
 assert(!pthread_join(owner,NULL));if(have_fw) assert(!pthread_join(fw_thread,NULL));
 if(!strcmp(scenario,"normal") || pause_drain) {
  assert(group.recovery_state==ATH12K_GROUP_RECOVERY_IDLE);
  assert(atomic_load(&business_done)==2 && !atomic_read(&group.reconfig_pending));
  assert(atomic_load(&clears)==1 && atomic_load(&powerdowns)==3 && atomic_load(&releases)==3);
  assert(atomic_load(&powerups)==3 && group.num_started==3 && !group.mlo_mem.num_users);
  for(int i=0;i<3;i++) assert(!bases[i].is_reset && bases[i].powered && bases[i].core_live && bases[i].qmi.target_mem[1].v.addr==(void *)2);
 } else if(!strcmp(scenario,"remove")) {
  assert(group.recovery_state==ATH12K_GROUP_RECOVERY_BOOT);assert(atomic_load(&powerdowns)==3);
 } else {
  assert(group.recovery_state==ATH12K_GROUP_RECOVERY_FAILED);
  if(ds_debt) {assert(!atomic_load(&powerups));assert(bases[1].dp_live && bases[1].hal_live);}
  else if(slow_business) {assert(atomic_read(&group.reconfig_pending)==2);assert(bases[0].core_live && bases[0].pdev_live);}
  else for(int i=0;i<3;i++) assert(!bases[i].powered && !bases[i].core_live && !bases[i].dp_live);
 }
 for(int i=0;i<business_count;i++) assert(!pthread_join(business_threads[i],NULL));
 puts("host-group-recovery:ok");
}
