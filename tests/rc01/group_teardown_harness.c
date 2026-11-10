/* Real prepared outer core/QMI functions; host kernel I/O and fault injection. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>
#include <stdatomic.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t dma_addr_t;
typedef uint64_t phys_addr_t;
#define __iomem
#define GFP_KERNEL 0
#define GFP_ATOMIC 0
#define __GFP_NOWARN 0
#define U32_MAX UINT32_MAX
#define U8_MAX UINT8_MAX
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define ATH12K_FLAG_FIXED_MEM_REGION 0
#define ATH12K_FLAG_UNREGISTERING 1
#define ATH12K_GROUP_FLAG_UNREGISTER 0
#define ATH12K_INVALID_DEVICE_ID 255
#define ATH12K_INVALID_GROUP_ID 255
#define ATH12K_MAX_DEVICES 4
#define ATH12K_HW_QCN9274_HW10 10
#define ATH12K_HW_QCN9274_HW20 20
#define ATH12K_FW_FEATURE_MLO 1
#define ATH12K_FIRMWARE_MODE_FTM 1
#define ATH12K_DBG_BOOT 0
#define ATH12K_QMI_RESP_LEN_MAX 1
#define QMI_SERVICE_ID_WLFW 1
#define ATH12K_QMI_WLFW_SERVICE_VERS_V01 1
#define fallthrough __attribute__((fallthrough))
#define ath12k_warn(ab, ...) ((void)(ab))
#define ath12k_err(ab, ...) ((void)(ab))
#define ath12k_info(ab, ...) ((void)(ab))
#define ath12k_dbg(ab, level, ...) ((void)(ab))
#define WARN_ON(x) (!!(x))
#define WARN_ON_ONCE(x) (!!(x))
#define kzalloc(size, flags) host_kzalloc(size)
#define kzalloc_obj(obj, ...) host_kzalloc(sizeof(obj))
#define kfree(ptr) host_kfree(ptr)

struct mutex { pthread_mutex_t native; bool initialized, held; pthread_t owner; };
static struct mutex ath12k_hw_group_mutex;
static _Thread_local int thread_role;
static void scheduling_global_attempt(void);
static void mutex_init(struct mutex *m)
{
	pthread_mutexattr_t attr; assert(!pthread_mutexattr_init(&attr));
	assert(!pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_ERRORCHECK));
	assert(!pthread_mutex_init(&m->native,&attr));assert(!pthread_mutexattr_destroy(&attr));
	m->initialized=true;m->held=false;
}
static bool mutex_owned(struct mutex *m) { return m->initialized && m->held && pthread_equal(m->owner,pthread_self()); }
static void mutex_lock(struct mutex *m)
{
	assert(m->initialized);
	if(m==&ath12k_hw_group_mutex)scheduling_global_attempt();
	assert(!pthread_mutex_lock(&m->native));m->owner=pthread_self();m->held=true;
}
static void mutex_unlock(struct mutex *m)
{ assert(mutex_owned(m));m->held=false;assert(!pthread_mutex_unlock(&m->native)); }
#define lockdep_assert_held(m) assert(mutex_owned(m))
#define lockdep_assert_not_held(m) assert(!mutex_owned(m))
typedef struct { bool initialized, held; } spinlock_t;
static void spin_lock_init(spinlock_t *s) { s->initialized = true; s->held = false; }
static void spin_lock(spinlock_t *s) { assert(s->initialized && !s->held); s->held = true; }
static void spin_unlock(spinlock_t *s) { assert(s->initialized && s->held); s->held = false; }
static void set_bit(int bit, unsigned long *word) { *word |= 1UL << bit; }
static bool test_bit(int bit, const unsigned long *word) { return !!(*word & (1UL << bit)); }
struct list_head { struct list_head *next, *prev; };
static void INIT_LIST_HEAD(struct list_head *head) { head->next = head->prev = head; }
static void list_add(struct list_head *node,struct list_head *head)
{ node->next=head->next;node->prev=head;head->next->prev=node;head->next=node; }
static void list_add_tail(struct list_head *node, struct list_head *head)
{ node->prev = head->prev; node->next = head; head->prev->next = node; head->prev = node; }
static void list_del(struct list_head *node) { node->prev->next = node->next; node->next->prev = node->prev; }
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define list_entry(ptr, type, member) container_of(ptr, type, member)
#define list_for_each_entry(pos,head,member) \
 for(pos=list_entry((head)->next,__typeof__(*pos),member); &pos->member!=(head); pos=list_entry(pos->member.next,__typeof__(*pos),member))
#define list_for_each_entry_safe(pos, tmp, head, member) \
	for (pos = list_entry((head)->next, __typeof__(*pos), member), \
	     tmp = list_entry(pos->member.next, __typeof__(*pos), member); \
	     &pos->member != (head); pos = tmp, tmp = list_entry(tmp->member.next, __typeof__(*tmp), member))
struct work_struct { bool initialized, pending; void (*func)(struct work_struct *); int cancels; };
#define INIT_WORK(work, callback) do { assert(!(work)->initialized); (work)->initialized = true; (work)->func = callback; } while (0)
struct workqueue_struct { bool alive; int id; };
struct completion { int initialized; };
struct timer_list { bool initialized; };
typedef struct { int initialized; } wait_queue_head_t;
#define init_completion(c) ((c)->initialized = 1)
#define init_waitqueue_head(w) ((w)->initialized = 1)
#define timer_setup(timer, fn, flags) ((timer)->initialized = true)
#define timer_delete_sync(timer) assert((timer)->initialized)
struct qmi_handle { bool alive; };
struct target_info { int unused; };
struct device_node { bool declared[4]; struct reserved_mem *regions[4]; u32 host_offset; int index,refs; };
struct device { struct device_node *of_node; int refs, index; };
struct reserved_mem { phys_addr_t base; size_t size; };
enum ath12k_bus { ATH12K_BUS_PCI = 1, ATH12K_BUS_AHB = 2 };
enum ath12k_qmi_event_type { ATH12K_QMI_EVENT_HOST_CAP = 1 };
struct ath12k_qmi_driver_event { struct list_head list; enum ath12k_qmi_event_type type; void *data; };
struct ath12k_base;
#include "prepared-memory-types.h"
struct ath12k_hw_group {
	struct mutex mutex;
	unsigned long flags;
	int id, num_devices, num_probed, num_started;
	struct list_head list;
	struct device_node *wsi_node[4];
	bool mlo_capable;
	struct ath12k_base *ab[4];
	struct ath12k_mlo_memory mlo_mem;
#include "prepared-group-extra.h"
};
struct ath12k_base {
	struct ath12k_qmi qmi;
	struct device *dev;
	struct ath12k_hw_group *ag;
	struct mutex core_lock;
	unsigned long dev_flags;
	struct { enum ath12k_bus bus; } hif;
	int fw_mode, target_mem_mode, device_id, hw_rev;
	struct ath12k_wsi_info {int index;} wsi_info;
	bool single_chip_mlo_support, hw_group_ref;
	struct workqueue_struct *workqueue, *workqueue_aux;
	struct completion driver_recovery, reset_complete, htc_suspend, restart_completed;
	struct { struct completion wakeup_completed; } wow;
	spinlock_t base_lock;
	wait_queue_head_t peer_mapping_wq;
	struct { wait_queue_head_t tx_credits_wq; } wmi_ab;
	struct work_struct restart_work, reset_work, rfkill_work, dump_work, update_11d_work;
	struct timer_list rx_replenish_retry;
#include "prepared-base-extra.h"
};

static struct ath12k_hw_group *current_group;
static struct ath12k_base *all_ab[4];
static int all_count, ath12k_ftm_mode, handle_releases, queue_destroys, queue_posts;
static int power_ups[4], power_downs[4], debug_create[4], debug_destroy[4], reg_frees[4];
static int core_ref_puts, unassigns, aux_frees, m3_frees;
static atomic_int notifiers;
static struct list_head ath12k_hw_group_list;
static const int ath12k_qmi_ops = 0, ath12k_qmi_msg_handlers[] = {0};
static int fail_device = -1, fail_stage;
enum { FAIL_NONE, FAIL_HANDLE, FAIL_WQ, FAIL_LOOKUP, FAIL_POWER };
static int active_device = -1;
static bool teardown_active;
static void assert_unlocked(void)
{
	if (current_group) assert(!mutex_owned(&current_group->mutex));
	for (int i = 0; i < all_count; i++) if (all_ab[i]) assert(!mutex_owned(&all_ab[i]->core_lock));
}
static void assert_group_stopped(void)
{
	assert(current_group->mutex.held);
	for (int i = 0; i < current_group->num_devices; i++) {
		struct ath12k_base *ab = current_group->ab[i];
		if (!ab) continue;
		assert(test_bit(ATH12K_FLAG_UNREGISTERING, &ab->dev_flags));
		assert(ab->qmi.events_stopped && !ab->qmi.handle_initialized && !ab->qmi.event_wq);
		assert(!ab->reset_work.pending && !ab->restart_work.pending && !ab->dump_work.pending);
	}
}
static bool cancel_work_sync(struct work_struct *work)
{
	for(int i=0;i<all_count;i++)if(all_ab[i] &&
	   (work==&all_ab[i]->qmi.event_work || work==&all_ab[i]->reset_work ||
	    work==&all_ab[i]->restart_work || work==&all_ab[i]->dump_work))current_group=all_ab[i]->ag;
	assert_unlocked();
	assert(work->initialized && work->func); /* R1: reject zero work. */
	if (mutex_owned(&ath12k_hw_group_mutex)) {
		teardown_active = true;
		for (int i = 0; i < current_group->num_devices; i++)
			if (current_group->ab[i]) assert(test_bit(ATH12K_FLAG_UNREGISTERING, &current_group->ab[i]->dev_flags));
	}
	bool pending = work->pending; work->pending = false; work->cancels++; return pending;
}
static struct workqueue_struct queues[32];
static int queue_count;
static struct workqueue_struct *new_queue(void)
{
	assert(queue_count < 32); struct workqueue_struct *q = &queues[queue_count++];
	q->alive = true; q->id = queue_count; return q;
}
static struct workqueue_struct *create_singlethread_workqueue(const char *name) { (void)name; return new_queue(); }
static struct workqueue_struct *alloc_ordered_workqueue(const char *name, int flags)
{ (void)name; (void)flags; return active_device == fail_device && fail_stage == FAIL_WQ ? NULL : new_queue(); }
static void destroy_workqueue(struct workqueue_struct *q)
{ assert_unlocked(); assert(q && q->alive); q->alive = false; queue_destroys++; }
static bool queue_work(struct workqueue_struct *q, struct work_struct *work)
{ assert(q && q->alive && work->initialized); queue_posts++; work->pending = true; return true; }
static int qmi_handle_init(struct qmi_handle *handle, int len, const int *ops, const int *handlers)
{
	(void)len; (void)ops; (void)handlers;
	struct ath12k_qmi *qmi = container_of(handle, struct ath12k_qmi, handle);
	active_device = qmi->ab->dev->index;
	if (active_device == fail_device && fail_stage == FAIL_HANDLE) return -EIO;
	assert(!handle->alive); handle->alive = true; return 0;
}
static void scheduling_pause(int stage);
static void qmi_handle_release(struct qmi_handle *handle)
{ assert_unlocked();scheduling_pause(3);assert(handle->alive);handle->alive=false;handle_releases++; }
static int qmi_add_lookup(struct qmi_handle *handle, int service, int version, int instance)
{ (void)service; (void)version; (void)instance; assert(handle->alive); return active_device == fail_device && fail_stage == FAIL_LOOKUP ? -EIO : 0; }

static void ath12k_qmi_driver_event_work(struct work_struct *work) { (void)work; }
static void ath12k_core_restart(struct work_struct *work) { (void)work; }
static void ath12k_core_reset(struct work_struct *work) { (void)work; }
static void ath12k_rfkill_work(struct work_struct *work) { (void)work; }
static void ath12k_coredump_upload(struct work_struct *work) { (void)work; }
static void ath12k_update_11d(struct work_struct *work) { (void)work; }
static int ath12k_wmi_alloc(void) { return 0; }
static void ath12k_wmi_free(void) {}
static void ath12k_fw_unmap(struct ath12k_base *ab) { (void)ab; }
static int ath12k_core_panic_notifier_register(struct ath12k_base *ab) { (void)ab; notifiers++; return 0; }
static void ath12k_core_panic_notifier_unregister(struct ath12k_base *ab) { (void)ab; assert(notifiers > 0); notifiers--; }
static struct ath12k_hw_group *ath12k_ab_to_ag(struct ath12k_base *ab) { return ab->ag; }
static struct ath12k_base *ath12k_ab_to_dp(struct ath12k_base *ab) { return ab; }
static void scheduling_pause(int stage);
static void ath12k_dp_cmn_hw_group_assign(struct ath12k_base *ab,struct ath12k_hw_group *ag)
{ (void)ab;lockdep_assert_held(&ath12k_hw_group_mutex);lockdep_assert_held(&ag->mutex);current_group=ag;scheduling_pause(1); }
static void ath12k_dp_cmn_hw_group_unassign(struct ath12k_base *ab, struct ath12k_hw_group *ag)
{ (void)ab; lockdep_assert_held(&ag->mutex);lockdep_assert_held(&ath12k_hw_group_mutex);unassigns++; }
static bool ath12k_fw_feature_supported(struct ath12k_base *ab,int feature) { (void)ab;(void)feature;return true; }
static int dt_members;
static struct device_node nodes[4];
static struct device_node *of_node_get(struct device_node *node) { if(node)node->refs++;return node; }
static void of_node_put(struct device_node *node) { if(node){assert(node->refs>1);node->refs--;} }
static bool of_property_read_bool(struct device_node *node,const char *name)
{ assert(!strcmp(name,"qcom,wsi-controller"));scheduling_pause(2);return node->index==0; }
static int ath12k_core_get_wsi_info(struct ath12k_hw_group *ag,struct ath12k_base *ab)
{ (void)ab;for(int i=0;i<dt_members;i++)ag->wsi_node[i]=of_node_get(&nodes[i]);ag->num_devices=dt_members;return 0; }
static void ath12k_core_hw_group_stop(struct ath12k_hw_group *ag)
{ lockdep_assert_held(&ag->mutex); }
static void ath12k_core_stop(struct ath12k_base *ab)
{
 lockdep_assert_held(&ab->ag->mutex); lockdep_assert_held(&ab->core_lock);
 assert(ab->hw_group_ref); assert(ab->ag->num_started > 0);
 ab->hw_group_ref=false; ab->ag->num_started--;
}
static void ath12k_core_to_group_ref_put(struct ath12k_base *ab)
{
	lockdep_assert_held(&ab->ag->mutex); core_ref_puts++;
	if (ab->hw_group_ref) { assert(ab->ag->num_started > 0); ab->ag->num_started--; ab->hw_group_ref = false; }
}
static void ath12k_debugfs_soc_create(struct ath12k_base *ab) { debug_create[ab->dev->index]++; }
static void ath12k_debugfs_soc_destroy(struct ath12k_base *ab)
{ assert(debug_destroy[ab->dev->index] < debug_create[ab->dev->index]); debug_destroy[ab->dev->index]++; }
static int ath12k_hif_power_up(struct ath12k_base *ab)
{ power_ups[ab->dev->index]++; return ab->dev->index == fail_device && fail_stage == FAIL_POWER ? -EIO : 0; }
static void ath12k_hif_power_down(struct ath12k_base *ab, bool suspend)
{ (void)suspend; assert_group_stopped(); power_downs[ab->dev->index]++; }
static void ath12k_reg_free(struct ath12k_base *ab) { assert_group_stopped(); reg_frees[ab->dev->index]++; }
static void ath12k_qmi_aux_uc_free(struct ath12k_base *ab) { lockdep_assert_held(&ab->ag->mutex); aux_frees++; }
static void ath12k_qmi_m3_free(struct ath12k_base *ab) { lockdep_assert_held(&ab->ag->mutex); m3_frees++; }
static struct device *get_device(struct device *dev) { assert(dev->refs > 0); dev->refs++; return dev; }
static void put_device(struct device *dev) { if (dev) { assert(dev->refs > 1); dev->refs--; } }
static int region_index(const char *name)
{
	static const char *const names[] = {"host-ddr-mem", "m3-dump", "q6-caldb", "mlo-global-mem"};
	for (int i = 0; i < 4; i++) if (!strcmp(name, names[i])) return i;
	return -1;
}
static int of_property_match_string(struct device_node *node, const char *property, const char *name)
{ (void)property; int i = region_index(name); return node && i >= 0 && node->declared[i] ? i : -EINVAL; }
static int of_property_read_u32(struct device_node *node, const char *property, u32 *out)
{ (void)property; if (!node) return -EINVAL; *out = node->host_offset; return 0; }
static struct reserved_mem *ath12k_core_get_rmem_by_name(struct ath12k_base *ab, const char *name)
{ int i = region_index(name); return ab->dev->of_node && i >= 0 && ab->dev->of_node->declared[i] ? ab->dev->of_node->regions[i] : NULL; }
static struct allocation { void *ptr; size_t size; struct device *dev; dma_addr_t paddr; bool fixed; } allocations[64];
static int alloc_count, dma_frees, unmaps;
static void *allocate(size_t size, struct device *dev, dma_addr_t address, bool fixed)
{
	assert(current_group->mutex.held && size && size < 4096);
	void *ptr = malloc(size); assert(ptr && alloc_count < 64);
	if (!fixed) address = (uintptr_t)ptr;
	allocations[alloc_count++] = (struct allocation){ptr,size,dev,address,fixed}; return ptr;
}
static void *dma_alloc_coherent(struct device *dev, size_t size, dma_addr_t *address, int flags)
{ (void)flags; void *ptr = allocate(size,dev,0,false); *address = (uintptr_t)ptr; return ptr; }
static void dma_free_coherent(struct device *dev, size_t size, void *ptr, dma_addr_t address)
{
	lockdep_assert_held(&current_group->mutex);
	if (teardown_active) assert_group_stopped();
	for (int i = 0; i < alloc_count; i++) if (allocations[i].ptr == ptr) {
		assert(!allocations[i].fixed && allocations[i].dev == dev && allocations[i].size == size && allocations[i].paddr == address);
		free(ptr); allocations[i].ptr = NULL; dma_frees++; return;
	}
	assert(!"unowned DMA free");
}
static void *ioremap(phys_addr_t address, size_t size) { return allocate(size,NULL,address,true); }
static void iounmap(void *ptr)
{
	lockdep_assert_held(&current_group->mutex);
	if (teardown_active) assert_group_stopped();
	for (int i = 0; i < alloc_count; i++) if (allocations[i].ptr == ptr) {
		assert(allocations[i].fixed); free(ptr); allocations[i].ptr = NULL; unmaps++; return;
	}
	assert(!"unowned fixed free");
}
static void memset_io(void *ptr, int value, size_t size) { memset(ptr,value,size); }
static int alive(void) { int n=0;for(int i=0;i<alloc_count;i++)if(allocations[i].ptr)n++;return n; }

static struct group_allocation {struct ath12k_hw_group *ptr;bool freed;} group_allocations[32];
static int group_alloc_count,group_free_count;
static void *host_kzalloc(size_t size)
{
	void *ptr=calloc(1,size);assert(ptr);
	if(size==sizeof(struct ath12k_hw_group)) {
		lockdep_assert_held(&ath12k_hw_group_mutex);assert(group_alloc_count<32);
		group_allocations[group_alloc_count++]=(struct group_allocation){ptr,false};
	}
	return ptr;
}
static void host_kfree(void *ptr)
{
	for(int i=0;i<group_alloc_count;i++)if(group_allocations[i].ptr==ptr && !group_allocations[i].freed) {
		struct ath12k_hw_group *ag=ptr;lockdep_assert_held(&ath12k_hw_group_mutex);
		assert(!ag->num_probed && !ag->mutex.held && !ag->num_started && !ag->mlo_mem.num_users);
		struct ath12k_hw_group *listed;
		list_for_each_entry(listed,&ath12k_hw_group_list,list)assert(listed!=ag);
		group_allocations[i].freed=true;group_free_count++;if(current_group==ag)current_group=NULL;
		assert(!pthread_mutex_destroy(&ag->mutex.native));
		free(ptr);return;
	}
	free(ptr);
}

#include "prepared-memory-functions.h"
#include "prepared-qmi-functions.h"
#include "prepared-core-functions.h"

static struct device devices[4];
static void group_setup(int members)
{
	mutex_init(&ath12k_hw_group_mutex);INIT_LIST_HEAD(&ath12k_hw_group_list);dt_members=members;
	for(int i=0;i<members;i++) {
		nodes[i].index=i;nodes[i].refs=1;
		devices[i]=(struct device){.of_node=&nodes[i],.refs=1,.index=i};
		all_ab[i]=ath12k_core_alloc(&devices[i],0,ATH12K_BUS_PCI);assert(all_ab[i]);
		all_ab[i]->hw_rev=ATH12K_HW_QCN9274_HW20;
		all_count++;assert(all_ab[i]->qmi.event_work.initialized);
	}
}
static void borrow(struct ath12k_base *ab, u32 size)
{
	ab->qmi.mem_seg_count=1;ab->qmi.target_mem[0].type=MLO_GLOBAL_MEM_REGION_TYPE;ab->qmi.target_mem[0].size=size;
	assert(!ath12k_qmi_alloc_target_mem_chunk(ab));
}
static void assert_all_clean(void)
{
	assert(!alive());
	for(int i=0;i<group_alloc_count;i++)if(!group_allocations[i].freed) {
		assert(!group_allocations[i].ptr->mlo_mem.num_users && !group_allocations[i].ptr->num_started);
	}
	for(int i=0;i<all_count;i++) {
		assert(devices[i].refs==1 && !all_ab[i]->soc_created);
		assert(!all_ab[i]->qmi.ab && !all_ab[i]->qmi.handle_initialized && !all_ab[i]->qmi.event_wq);
		assert(all_ab[i]->qmi.events_stopped);
	}
}
static void free_all(void)
{
	for(int i=0;i<all_count;i++) {ath12k_core_free(all_ab[i]);all_ab[i]=NULL;}
	for(int i=0;i<queue_count;i++)assert(!queues[i].alive);
	assert(group_alloc_count==group_free_count);
	assert(ath12k_hw_group_list.next==&ath12k_hw_group_list);
	for(int i=0;i<dt_members;i++)assert(nodes[i].refs==1);
}
static void normal(void)
{
	group_setup(2);
	assert(!ath12k_core_init(all_ab[0]));assert(!all_ab[0]->qmi.ab);
	assert(!ath12k_core_init(all_ab[1]));
	borrow(all_ab[0],64);borrow(all_ab[1],64);
	assert(current_group->mlo_mem.num_users==2 && !current_group->num_started && devices[0].refs==2);
	for(int i=0;i<2;i++) {all_ab[i]->reset_work.pending=true;all_ab[i]->restart_work.pending=true;all_ab[i]->dump_work.pending=true;}
	ath12k_core_hw_group_cleanup(current_group); /* Both borrowers are pre-start. */
	ath12k_core_deinit(all_ab[0]);
	assert_all_clean();assert(dma_frees==1 && devices[0].refs==1);
	assert(power_downs[0]==1 && power_downs[1]==1 && handle_releases==2);
	ath12k_core_deinit(all_ab[1]);assert_all_clean();
	assert(dma_frees==1 && power_downs[0]==1 && power_downs[1]==1 && handle_releases==2);
	assert(!notifiers && unassigns==2);free_all();
}
static void early_remove(void)
{
	group_setup(2);
	assert(!ath12k_core_init(all_ab[0]));
	assert(!all_ab[0]->qmi.ab && !all_ab[0]->soc_created);
	ath12k_core_hw_group_cleanup(current_group); /* Must not core_stop a non-started member. */
	ath12k_core_deinit(all_ab[0]);
	assert(!handle_releases && !power_downs[0] && !power_downs[1]);
	assert(all_ab[0]->qmi.event_work.cancels==1);
	assert_all_clean();assert(!notifiers);free_all();
}
static void create_failure(int stage)
{
	group_setup(2);fail_device=1;fail_stage=stage;
	assert(!ath12k_core_init(all_ab[0]));
	assert(ath12k_core_init(all_ab[1])<0);
	assert_all_clean();assert(power_downs[0]==1 && !power_downs[1]);
	assert(handle_releases==(stage==FAIL_HANDLE?1:2));
	assert(debug_create[0]==1 && debug_destroy[0]==1);
	assert(debug_create[1]==(stage==FAIL_POWER?1:0));
	assert(debug_destroy[1]==debug_create[1]);
	/* The earlier successful probe later removes after rollback. */
	ath12k_core_deinit(all_ab[0]);assert_all_clean();
	assert(power_downs[0]==1 && !power_downs[1] && !notifiers);free_all();
}
static void free_resource(void)
{
	group_setup(1);assert(!ath12k_core_init(all_ab[0]));borrow(all_ab[0],64);
	mutex_lock(&current_group->mutex);ath12k_qmi_free_resource(all_ab[0]);mutex_unlock(&current_group->mutex);
	assert(!alive() && devices[0].refs==1);
	ath12k_core_deinit(all_ab[0]);assert_all_clean();free_all();
}
static void late_event(void)
{
	group_setup(2);assert(!ath12k_core_init(all_ab[0]));assert(!ath12k_core_init(all_ab[1]));
	assert(!ath12k_qmi_driver_event_post(&all_ab[1]->qmi,ATH12K_QMI_EVENT_HOST_CAP,NULL));
	assert(queue_posts==1 && all_ab[1]->qmi.event_work.pending);
	/* One member can be stopped while the partner emits a late HOST_CAP. */
	ath12k_qmi_stop_service(all_ab[1]);
	assert(ath12k_qmi_driver_event_post(&all_ab[1]->qmi,ATH12K_QMI_EVENT_HOST_CAP,NULL)==-ESHUTDOWN);
	assert(queue_posts==1);
	ath12k_core_deinit(all_ab[0]);
	assert(ath12k_qmi_driver_event_post(&all_ab[1]->qmi,ATH12K_QMI_EVENT_HOST_CAP,NULL)==-ESHUTDOWN);
	assert(queue_posts==1);
	ath12k_core_deinit(all_ab[1]);assert_all_clean();free_all();
}
static void unassign_errors(void)
{
	group_setup(2);assert(!ath12k_core_init(all_ab[0]));
	all_ab[0]->device_id=9;
	mutex_lock(&ath12k_hw_group_mutex);ath12k_core_hw_group_unassign(all_ab[0]);mutex_unlock(&ath12k_hw_group_mutex);
	assert(!current_group->mutex.held && !ath12k_hw_group_mutex.held && current_group->num_probed==1);
	all_ab[0]->device_id=1;
	mutex_lock(&ath12k_hw_group_mutex);ath12k_core_hw_group_unassign(all_ab[0]);mutex_unlock(&ath12k_hw_group_mutex);
	assert(!current_group->mutex.held && !ath12k_hw_group_mutex.held && current_group->num_probed==1);
	all_ab[0]->device_id=0;
	ath12k_core_deinit(all_ab[0]);assert_all_clean();free_all();
}
/* Deterministic interleavings use condition variables, not timing sleeps.
 * The hooks are in DT/datapath I/O stubs reached by the real functions.
 */
static pthread_mutex_t schedule_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t schedule_cond=PTHREAD_COND_INITIALIZER;
static int paused_stage,pause_stage,pause_role,global_attempts,completed_roles;
static bool release_pause;
static void scheduling_global_attempt(void)
{
	if(!thread_role)return;
	assert(!pthread_mutex_lock(&schedule_mutex));
	if(thread_role>=2)global_attempts|=1<<thread_role;
	assert(!pthread_cond_broadcast(&schedule_cond));
	assert(!pthread_mutex_unlock(&schedule_mutex));
}
static void scheduling_pause(int stage)
{
	if(!thread_role)return;
	assert(!pthread_mutex_lock(&schedule_mutex));
	if(thread_role==pause_role && stage==pause_stage && !paused_stage) {
		paused_stage=stage;assert(!pthread_cond_broadcast(&schedule_cond));
		while(!release_pause)assert(!pthread_cond_wait(&schedule_cond,&schedule_mutex));
	}
	assert(!pthread_mutex_unlock(&schedule_mutex));
}
static void wait_schedule(bool pause,int role)
{
	assert(!pthread_mutex_lock(&schedule_mutex));
	struct timespec deadline;assert(!clock_gettime(CLOCK_REALTIME,&deadline));deadline.tv_sec+=10;
	while(pause ? !paused_stage : !(global_attempts&(1<<role)))
		assert(!pthread_cond_timedwait(&schedule_cond,&schedule_mutex,&deadline));
	assert(!pthread_mutex_unlock(&schedule_mutex));
}
static void release_schedule(void)
{
	assert(!pthread_mutex_lock(&schedule_mutex));release_pause=true;
	assert(!pthread_cond_broadcast(&schedule_cond));assert(!pthread_mutex_unlock(&schedule_mutex));
}
struct thread_args {int role,member;bool probe;int result;};
static void *run_operation(void *opaque)
{
	struct thread_args *args=opaque;thread_role=args->role;
	if(args->probe)args->result=ath12k_core_init(all_ab[args->member]);
	else {
		ath12k_core_hw_group_cleanup(all_ab[args->member]->ag);
		ath12k_core_deinit(all_ab[args->member]);args->result=0;
	}
	assert(!pthread_mutex_lock(&schedule_mutex));completed_roles|=1<<thread_role;
	assert(!pthread_cond_broadcast(&schedule_cond));assert(!pthread_mutex_unlock(&schedule_mutex));return NULL;
}
static void assert_waiter_blocked(int role)
{
	wait_schedule(false,role);
	assert(pthread_mutex_trylock(&ath12k_hw_group_mutex.native)==EBUSY);
	assert(!pthread_mutex_lock(&schedule_mutex));assert(!(completed_roles&(1<<role)));
	assert(!pthread_mutex_unlock(&schedule_mutex));
}
static void probe_remove_interleaving(int stage)
{
	group_setup(2);assert(!ath12k_core_init(all_ab[0]));
	struct ath12k_hw_group *original=all_ab[0]->ag;
	pause_stage=stage;pause_role=1;
	struct thread_args probe={1,1,true,0},remove={2,0,false,0};pthread_t probe_thread,remove_thread;
	assert(!pthread_create(&probe_thread,NULL,run_operation,&probe));wait_schedule(true,0);
	/* At stage 1 the real assign has already published B. At stage 2 the
	 * real find/exit check reached get_wsi_index but has not published B.
	 */
	assert(original->num_probed==(stage==1?2:1));
	assert(!pthread_create(&remove_thread,NULL,run_operation,&remove));assert_waiter_blocked(2);
	assert(!group_free_count);release_schedule();
	assert(!pthread_join(probe_thread,NULL));assert(!pthread_join(remove_thread,NULL));
	assert(!probe.result && !remove.result && !group_free_count);
	assert(all_ab[1]->ag==original && original->num_probed==1);
	assert(!all_ab[1]->qmi.handle_initialized && all_ab[1]->qmi.events_stopped);
	assert(power_ups[0]==1 && power_ups[1]==1 && power_downs[0]==1 && power_downs[1]==1);
	ath12k_core_deinit(all_ab[1]);assert_all_clean();assert(group_free_count==1);free_all();
}
static void remove_waiters(void)
{
	group_setup(2);assert(!ath12k_core_init(all_ab[0]));assert(!ath12k_core_init(all_ab[1]));
	borrow(all_ab[0],64);borrow(all_ab[1],64);
	pause_stage=3;pause_role=2;
	struct thread_args first={2,0,false,0},second={3,1,false,0};pthread_t a,b;
	assert(!pthread_create(&a,NULL,run_operation,&first));wait_schedule(true,0);
	assert(!pthread_create(&b,NULL,run_operation,&second));assert_waiter_blocked(3);
	assert(!group_free_count);release_schedule();assert(!pthread_join(a,NULL));assert(!pthread_join(b,NULL));
	assert_all_clean();assert(group_free_count==1 && dma_frees==1 && unassigns==2);free_all();
}
static void probe_after_exit(void)
{
	group_setup(2);assert(!ath12k_core_init(all_ab[0]));
	struct ath12k_hw_group *exiting=all_ab[0]->ag;
	ath12k_core_hw_group_cleanup(exiting);
	assert(test_bit(ATH12K_GROUP_FLAG_UNREGISTER,&exiting->flags));
	assert(!ath12k_core_init(all_ab[1]));
	assert(all_ab[1]->ag!=exiting && all_ab[1]->ag->num_devices==1 && group_alloc_count==2);
	assert(!all_ab[0]->qmi.ab && all_ab[1]->soc_created);
	ath12k_core_deinit(all_ab[0]);assert(group_free_count==1);
	assert(all_ab[1]->qmi.handle_initialized && all_ab[1]->soc_created && !power_downs[1]);
	ath12k_core_deinit(all_ab[1]);assert_all_clean();assert(group_free_count==2);free_all();
}
int main(int argc,char **argv)
{
	assert(argc==2);
	if(!strcmp(argv[1],"normal"))normal();
	else if(!strcmp(argv[1],"early-remove"))early_remove();
	else if(!strcmp(argv[1],"fail-handle"))create_failure(FAIL_HANDLE);
	else if(!strcmp(argv[1],"fail-workqueue"))create_failure(FAIL_WQ);
	else if(!strcmp(argv[1],"fail-lookup"))create_failure(FAIL_LOOKUP);
	else if(!strcmp(argv[1],"fail-power-up"))create_failure(FAIL_POWER);
	else if(!strcmp(argv[1],"free-resource"))free_resource();
	else if(!strcmp(argv[1],"late-event"))late_event();
	else if(!strcmp(argv[1],"unassign-errors"))unassign_errors();
	else if(!strcmp(argv[1],"probe-published-remove"))probe_remove_interleaving(1);
	else if(!strcmp(argv[1],"probe-found-remove"))probe_remove_interleaving(2);
	else if(!strcmp(argv[1],"remove-waiters"))remove_waiters();
	else if(!strcmp(argv[1],"probe-after-exit"))probe_after_exit();
	else assert(!"unknown scenario");
	return 0;
}
