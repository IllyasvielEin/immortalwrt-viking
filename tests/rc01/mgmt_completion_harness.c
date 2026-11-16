/* Real completion/close functions; pthread rwlock models an RCU grace period.
 * This checks admission and drain ordering, not Linux RCU or device DMA. */
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <sched.h>
#include <string.h>
struct ath12k { int unused; };
struct ath12k_base { pthread_mutex_t base_lock; atomic_bool mac_delivery_stopped; int rfkill_work; };
struct sk_buff { int unused; };
struct wmi_mgmt_tx_compl_event { uint32_t pdev_id, desc_id, status, ack_rssi; };
static struct ath12k_base ab = { .base_lock = PTHREAD_MUTEX_INITIALIZER };
static struct ath12k ar;
static struct sk_buff skb;
static pthread_rwlock_t readers = PTHREAD_RWLOCK_INITIALIZER;
static atomic_int calls, entered, release_reader, sync_entered, closed;
static bool pause_reader;
#define READ_ONCE(x) atomic_load(&(x))
#define spin_lock_bh(x) pthread_mutex_lock(x)
#define spin_unlock_bh(x) pthread_mutex_unlock(x)
#define le32_to_cpu(x) (x)
#define ath12k_warn(...) ((void)0)
#define ath12k_dbg(...) ((void)0)
static void rcu_read_lock(void) { pthread_rwlock_rdlock(&readers); }
static void rcu_read_unlock(void) { pthread_rwlock_unlock(&readers); }
static void synchronize_rcu(void) {
 atomic_store(&sync_entered, 1);
 pthread_rwlock_wrlock(&readers);
 pthread_rwlock_unlock(&readers);
}
static void cancel_work_sync(int *work) { (void)work; }
static int ath12k_pull_mgmt_tx_compl_param_tlv(struct ath12k_base *b, struct sk_buff *s, struct wmi_mgmt_tx_compl_event *p) { return 0; }
static struct ath12k *ath12k_mac_get_ar_by_pdev_id(struct ath12k_base *b, unsigned int id) { return &ar; }
static void wmi_process_mgmt_tx_comp(struct ath12k *a, unsigned int id, unsigned int status, unsigned int rssi) {
 atomic_fetch_add(&calls, 1);
 atomic_store(&entered, 1);
 if (pause_reader) while (!atomic_load(&release_reader)) sched_yield();
}
#include "prepared-mgmt.h"
static void *complete_thread(void *p) { ath12k_mgmt_tx_compl_event(&ab, &skb); return NULL; }
static void *close_thread(void *p) { ath12k_core_mac_delivery_close(&ab); atomic_store(&closed, 1); return NULL; }
int main(int argc, char **argv) {
 assert(argc == 2);
 if (!strcmp(argv[1], "closed")) {
  ath12k_core_mac_delivery_close(&ab);
  ath12k_mgmt_tx_compl_event(&ab, &skb);
  assert(atomic_load(&calls) == 0);
 } else if (!strcmp(argv[1], "open")) {
  ath12k_mgmt_tx_compl_event(&ab, &skb);
  assert(atomic_load(&calls) == 1);
  ath12k_core_mac_delivery_close(&ab);
  atomic_store(&ab.mac_delivery_stopped, false);
  ath12k_mgmt_tx_compl_event(&ab, &skb);
  assert(atomic_load(&calls) == 2);
 } else {
  pthread_t reader, closer;
  pause_reader = true;
  pthread_create(&reader, NULL, complete_thread, NULL);
  while (!atomic_load(&entered)) sched_yield();
  pthread_create(&closer, NULL, close_thread, NULL);
  while (!atomic_load(&sync_entered) && !atomic_load(&closed)) sched_yield();
  assert(!atomic_load(&closed));
  atomic_store(&release_reader, 1);
  pthread_join(reader, NULL); pthread_join(closer, NULL);
  assert(atomic_load(&closed));
  ath12k_mgmt_tx_compl_event(&ab, &skb);
  assert(atomic_load(&calls) == 1);
 }
 return 0;
}
