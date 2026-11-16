#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <pthread.h>
#include <errno.h>
#define READ_ONCE(x) __atomic_load_n(&(x),__ATOMIC_ACQUIRE)
#define cmpxchg(p,a,b) __sync_val_compare_and_swap(p,a,b)
#define wake_up_all(p) ((void)(p))
#define ath12k_dbg(...) ((void)0)
#define ath12k_warn(...) ((void)0)
#define ATH12K_FLAG_CRASH_FLUSH 0
#define ATH12K_FLAG_RECOVERY 1
#define ATH12K_GROUP_FLAG_UNREGISTER 0
struct lock { pthread_mutex_t m; bool held; };
static void lock(struct lock *l) {assert(!pthread_mutex_lock(&l->m));l->held=true;}
static void unlock(struct lock *l) {l->held=false;assert(!pthread_mutex_unlock(&l->m));}
#define spin_lock_irqsave(l,f) do {f=0;lock(l);} while(0)
#define spin_unlock_irqrestore(l,f) do {(void)f;unlock(l);} while(0)
#define spin_lock lock
#define spin_unlock unlock
static void set_bit(int n,unsigned long *p) {*p|=1UL<<n;}
static bool test_bit(int n,unsigned long *p) {return (*p&(1UL<<n))!=0;}
struct work_struct {int unused;};
enum ath12k_group_recovery { ATH12K_GROUP_RECOVERY_IDLE, ATH12K_GROUP_RECOVERY_DRAIN,ATH12K_GROUP_RECOVERY_BOOT,ATH12K_GROUP_RECOVERY_FAILED };
enum mhi_callback {MHI_CB_SYS_ERROR,MHI_CB_EE_RDDM,MHI_CB_EE_MISSION_MODE};
struct ath12k_hw_group {struct lock recovery_state_lock;unsigned long flags;
 enum ath12k_group_recovery recovery_state;int recovery_error,recovery_wait;};
struct ath12k_pci {enum mhi_callback mhi_pre_cb;};
struct ath12k_base {struct ath12k_hw_group *ag;struct lock recovery_lock;bool recovery_stopped;
 unsigned long dev_flags;struct work_struct reset_work;int workqueue_aux;struct ath12k_pci pci;};
struct mhi_controller {struct ath12k_base *cntrl_dev;};
#define dev_get_drvdata(p) (p)
#define ath12k_pci_priv(p) (&(p)->pci)
static struct ath12k_base *test_base;static int queued;
static int queue_work(int queue,struct work_struct *work) {(void)queue;assert(work==&test_base->reset_work && test_base->recovery_lock.held);queued++;return 1;}
#include "admission-functions.inc"
static void *callback(void *arg) {ath12k_mhi_op_status_cb(arg,MHI_CB_EE_RDDM);return NULL;}
int main(void) {
 struct ath12k_hw_group ag={};struct ath12k_base ab={.ag=&ag};struct mhi_controller ctrl={.cntrl_dev=&ab};test_base=&ab;
 pthread_mutex_init(&ab.recovery_lock.m,NULL);ab.pci.mhi_pre_cb=MHI_CB_EE_MISSION_MODE;
 pthread_mutex_init(&ag.recovery_state_lock.m,NULL);
 /* Callback waits at the actual admission lock; close wins before test+queue. */
 lock(&ab.recovery_lock);pthread_t t;assert(!pthread_create(&t,NULL,callback,&ctrl));
 ab.recovery_stopped=true;unlock(&ab.recovery_lock);assert(!pthread_join(t,NULL));assert(!queued);
 ab.recovery_stopped=false;ath12k_mhi_op_status_cb(&ctrl,MHI_CB_EE_MISSION_MODE);
 ath12k_mhi_op_status_cb(&ctrl,MHI_CB_EE_RDDM);assert(queued==1);
 ath12k_mhi_op_status_cb(&ctrl,MHI_CB_EE_RDDM);assert(queued==1);
 ag.recovery_state=ATH12K_GROUP_RECOVERY_BOOT;
 ath12k_mhi_op_status_cb(&ctrl,MHI_CB_EE_MISSION_MODE);ath12k_mhi_op_status_cb(&ctrl,MHI_CB_EE_RDDM);
 assert(ag.recovery_error==-EIO && queued==1);
 ag.recovery_error=0;ath12k_core_queue_reset(&ab);assert(ag.recovery_error==-EIO && queued==1);
 ag.recovery_state=ATH12K_GROUP_RECOVERY_DRAIN;ath12k_core_queue_reset(&ab);assert(queued==1);
 ab.recovery_stopped=true;ag.recovery_state=ATH12K_GROUP_RECOVERY_IDLE;ath12k_core_queue_reset(&ab);assert(queued==1);
 /* A non-crash error in IDLE is not a recovery request. */
 ab.recovery_stopped=false;ath12k_core_recovery_error(&ab,-ENOMEM);assert(queued==1);
 set_bit(ATH12K_GROUP_FLAG_UNREGISTER,&ag.flags);
 ath12k_core_recovery_error(&ab,-ENOMEM);assert(queued==1);
}
