/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Host-only kernel/provider I/O substitutes. All DS policy, binding, peer
 * fallback, stop/cleanup and HAL index functions are included verbatim from
 * RC01_WIRELESS_SOURCE by test_ds_lifecycle.py. No packet/radio/firmware or
 * real parallel execution is represented by these substitutes.
 */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef long long atomic64_t;
typedef int spinlock_t;
typedef uintptr_t dma_addr_t;
#define ETH_ALEN 6
#define ETH_HLEN 14
#define ETH_P_PAE 0x888e
#define NF_ACCEPT 1
#define NF_STOLEN 2
#define NFPROTO_NETDEV 5
#define NF_NETDEV_INGRESS 0
#define NL80211_IFTYPE_AP 3
#define ATH12K_MAX_DEVICES 3
#define ATH12K_HW_QCN9274_HW10 1
#define ATH12K_FLAG_CRASH_FLUSH 0
#define ATH12K_FLAG_UNREGISTERING 1
#define ATH12K_FLAG_QMI_FAIL 2
#define ATH12K_FLAG_QMI_FW_READY_COMPLETE 3
#define ATH12K_FLAG_RECOVERY 4
#define ATH12K_PCI_FLAG_INIT_DONE 0
#define HAL_TCL_ENCAP_TYPE_ETHERNET 2
#define HAL_SRNG_DIR_SRC 0
#define HAL_SRNG_DIR_DST 1
#define HAL_SRNG_REG_GRP_R0 0
#define HAL_SRNG_REG_GRP_R2 1
#define GFP_KERNEL 0
#define U16_MAX UINT16_MAX
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) ((UINT32_MAX >> (31 - (h))) & (UINT32_MAX << (l)))
#define FIELD_PREP(mask, value) (((u32)(value) << __builtin_ctz(mask)) & (mask))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))
#define lockdep_assert_wiphy(x) ((void)0)
#define lockdep_assert_held(x) assert((x)->held)
#define spin_lock_bh(x) ((void)(x))
#define spin_unlock_bh(x) ((void)(x))
#define spin_lock_irqsave(x, flags) ((void)(x), (flags) = 0)
#define spin_unlock_irqrestore(x, flags) ((void)(x), (void)(flags))
#define dma_wmb() ((void)0)
#define ath12k_warn(...) ((void)0)
#define ath12k_err(ab, ...) ((void)(ab))
#define ath12k_dbg(ab, ...) ((void)(ab))
#define DECLARE_HASHTABLE(name, bits) struct hlist_node *name

struct mutex { int held; };
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
struct list_head { struct list_head *next, *prev; };
static void INIT_LIST_HEAD(struct list_head *h) { h->next = h->prev = h; }
static void list_add_tail(struct list_head *n, struct list_head *h)
{
	n->next = h; n->prev = h->prev; h->prev->next = n; h->prev = n;
}
static void list_del(struct list_head *n) { n->next->prev = n->prev; n->prev->next = n->next; }
#define list_for_each_entry(pos, head, member) \
	for (struct list_head *_node = (head)->next; \
	     _node != (head) && ((pos) = container_of(_node, __typeof__(*(pos)), member), 1); \
	     _node = _node->next)
#define list_for_each_entry_safe(pos, tmp, head, member) \
	for (struct list_head *_node = (head)->next, *_next; \
	     _node != (head) && (_next = _node->next, \
	     (pos) = container_of(_node, __typeof__(*(pos)), member), (tmp) = NULL, (void)(tmp), 1); \
	     _node = _next)
struct hlist_node { struct hlist_node *next, **prev; };
#define hash_add(table, node, key) do { \
	(node)->next = (table); (node)->prev = &(table); \
	if (table) { (table)->prev = &(node)->next; } (table) = (node); \
} while (0)
static void hash_del(struct hlist_node *node)
{
	*node->prev = node->next;
	if (node->next) node->next->prev = node->prev;
}
#define hash_for_each_possible(table, pos, member, key) \
	for (struct hlist_node *_node = (table); \
	     _node && ((pos) = container_of(_node, __typeof__(*(pos)), member), 1); \
	     _node = _node->next)
static unsigned long *unregister_race_flags;
static bool inject_unregister;
static int unregister_checks;
static bool test_bit(int n, const unsigned long *v)
{
	bool result = !!(*v & BIT(n));
	/* Simulate remove beginning between the QMI worker's two flag checks. */
	if (inject_unregister && n == ATH12K_FLAG_UNREGISTERING && v == unregister_race_flags &&
	    ++unregister_checks == 1)
		*unregister_race_flags |= BIT(n);
	return result;
}
static void set_bit(int n, unsigned long *v) { *v |= BIT(n); }
static void clear_bit(int n, unsigned long *v) { *v &= ~BIT(n); }
static bool test_and_set_bit(int n, unsigned long *v)
{
	bool old = test_bit(n, v); set_bit(n, v); return old;
}
#define for_each_set_bit(n, value, size) for ((n) = 0; (n) < (size); (n)++) if (test_bit(n, value))
struct work_struct { void (*function)(struct work_struct *); bool queued; };
#define INIT_WORK(work, fn) do { (work)->function = (fn); (work)->queued = false; } while (0)
#define spin_lock_init(lock) (*(lock) = 0)
static struct work_struct *running_work;
static int queue_count;
static void record_cancel(struct work_struct *work);
static void record_queue(struct work_struct *work);
static struct work_struct *current_work(void) { return running_work; }
static void cancel_work_sync(struct work_struct *work) { assert(work->function); record_cancel(work); work->queued = false; }
static bool queue_work(void *queue, struct work_struct *work)
{
	(void)queue; work->queued = true; queue_count++; record_queue(work); return true;
}
static void usleep_range(unsigned a, unsigned b) { (void)a; (void)b; }
static void atomic64_inc(atomic64_t *v) { (*v)++; }
struct wireless_dev;
struct net_device { u8 dev_addr[6]; struct wireless_dev *ieee80211_ptr; int refs; };
struct wireless_dev { struct net_device *netdev; };
struct ieee80211_vif { int type; struct wireless_dev *wdev; };
struct ieee80211_sta { bool mlo; };
struct ath12k_vif { struct ieee80211_vif *vif; };
struct ath12k_sta { struct ieee80211_sta *sta; bool enable_4addr; };
struct ath12k_base;
struct ath12k_pci;
#include "prepared_enums.inc"
struct ath12k_qmi {
	struct ath12k_base *ab;
	struct work_struct event_work;
	bool events_stopped;
	int event_lock;
	struct list_head event_list;
};
struct ath12k_qmi_driver_event {
	struct list_head list; enum ath12k_qmi_event_type type; void *data;
};
struct ath12k { struct ath12k_base *ab; };
struct ath12k_link_vif { struct ath12k_vif *ahvif; struct ath12k *ar; u32 vdev_id; };
struct ath12k_link_sta { struct ath12k_sta *ahsta; u8 addr[6]; };
static struct wireless_dev *ieee80211_vif_to_wdev(struct ieee80211_vif *v) { return v->wdev; }
static struct ieee80211_sta *ath12k_ahsta_to_sta(struct ath12k_sta *s) { return s->sta; }
struct sk_buff { u16 protocol; u8 *data, *mac; unsigned headroom; bool mac_set; };
struct ethhdr { u8 h_dest[6], h_source[6]; u16 h_proto; };
struct nf_hook_state { struct net_device *in; };
struct nf_hook_ops {
	unsigned (*hook)(void *, struct sk_buff *, const struct nf_hook_state *);
	void *priv; int pf, hooknum; struct net_device *dev; int priority;
};
static bool ether_addr_equal(const u8 *a, const u8 *b) { return !memcmp(a, b, 6); }
static void ether_addr_copy(u8 *a, const u8 *b) { memcpy(a, b, 6); }
static u64 ether_addr_to_u64(const u8 *a) { u64 v = 0; memcpy(&v, a, 6); return v; }
static u16 cpu_to_be16(u16 v) { return __builtin_bswap16(v); }
static bool skb_mac_header_was_set(struct sk_buff *s) { return s->mac_set; }
static u8 *skb_mac_header(struct sk_buff *s) { return s->mac; }
static unsigned skb_headroom(struct sk_buff *s) { return s->headroom; }
static struct ethhdr *eth_hdr(struct sk_buff *s) { return (struct ethhdr *)s->mac; }
struct hal_srng {
	unsigned entry_size, ring_size, ring_dir, hwreg_base[2]; int lock;
	union {
		struct { u32 hp, reap_hp, cached_tp, *tp_addr; } src_ring;
		struct { u32 tp, cached_hp, *hp_addr; } dst_ring;
	} u;
};
struct dp_srng { int ring_id; };
struct reg_layout { u32 tcl1_ring_misc, tcl1_ring_base_lsb, reo1_ring_misc, reo1_ring_base_lsb, reo2ppe_dest_info; };
struct ath12k_hal { struct reg_layout *regs; struct hal_srng srng_list[3]; };
struct ath12k_ds;
struct ath12k_dp;
struct ath12k_hw_group { struct { struct ath12k_dp *dp[ATH12K_MAX_DEVICES]; } dp_hw_grp; };
struct ath12k_base {
	struct ath12k_hal hal; struct ath12k_hw_group *ag; struct ath12k_dp *dp;
	unsigned long dev_flags; void *workqueue_aux; unsigned device_id, hw_rev;
	struct ath12k_pci *pci; struct ath12k_qmi qmi;
	struct work_struct reset_work, restart_work, dump_work;
	bool is_reset; void *workqueue;
};
struct ath12k_dp_ops { u32 (*tx_get_vdev_bank_config)(struct ath12k_base *, struct ath12k_link_vif *); };
struct ath12k_dp {
	struct ath12k_base *ab; struct ath12k_ds *ds; struct ath12k_hal *hal;
	const struct ath12k_dp_ops *ops; bool ds_quarantined;
};
struct qcom_ppe_ds { bool leased[8], detached; };
#include "prepared_types.inc"
struct mhi_controller {
	bool prepared, powered, channels, freed; unsigned timeout_ms; void *irq;
};
struct family_ops { void (*arch_deinit)(struct ath12k_base *); };
struct ath12k_pci {
	struct ath12k_base *ab; struct mhi_controller *mhi_ctrl;
	unsigned long flags, mhi_state; const struct family_ops *device_family_ops;
};
struct pci_dev { struct ath12k_base *ab; };

static int clock_sequence, unregister_sequence, synchronize_sequence, final_put_sequence;
static int hooks, hook_register_calls, hook_unregister_calls, nf_error, stop_error, unregister_error;
static int bank_gets, bank_puts, vp_allocs, vp_frees, bank_live, vp_live;
static int direct_requests, cpu_requests, provider_stops, provider_unregisters, detach_calls;
static int delivered_calls, skb_freed, wmi_enables, wmi_disables;
static bool deliver_ok = true;
static void *dev_net(struct net_device *dev) { assert(dev); return dev; }
static void dev_hold(struct net_device *dev) { dev->refs++; }
static void dev_put(struct net_device *dev)
{
	assert(dev && dev->refs > 1);
	dev->refs--;
	if (dev->refs == 1) {
		assert(!hooks);
		final_put_sequence = ++clock_sequence;
	}
}
static int nf_register_net_hook(void *net, struct nf_hook_ops *hook)
{
	(void)net; hook_register_calls++;
	assert(hook->dev && hook->hook && hook->priv);
	assert(hook->pf == NFPROTO_NETDEV && hook->hooknum == NF_NETDEV_INGRESS && hook->priority == INT_MIN);
	if (nf_error) return nf_error;
	hooks++; return 0;
}
static void nf_unregister_net_hook(void *net, struct nf_hook_ops *hook)
{
	(void)net; (void)hook; assert(hooks > 0); hooks--; hook_unregister_calls++;
	unregister_sequence = ++clock_sequence;
}
static void synchronize_net(void) { synchronize_sequence = ++clock_sequence; }
static bool cfg80211_rx_control_port(struct net_device *dev, struct sk_buff *skb, bool unencrypted, int link)
{
	(void)dev; (void)skb; assert(!unencrypted && link == -1); delivered_calls++; return deliver_ok;
}
static void dev_kfree_skb_any(struct sk_buff *skb) { (void)skb; skb_freed++; }
static void *kzalloc(size_t bytes, int flags) { (void)flags; return calloc(1, bytes); }
static void kfree(void *pointer) { free(pointer); }
static struct ath12k_dp *ath12k_ab_to_dp(struct ath12k_base *ab) { return ab->dp; }
static int ath12k_dp_tx_get_bank_config(struct ath12k_dp *dp, u32 config)
{
	(void)dp; (void)config; bank_gets++; bank_live++; return 3;
}
static void ath12k_dp_tx_put_bank_profile(struct ath12k_dp *dp, int bank)
{
	(void)dp; assert(bank == 3 && bank_live > 0); bank_puts++; bank_live--;
}
static int qcom_ppe_ds_vp_alloc(struct qcom_ppe_ds *h, struct net_device *dev)
{
	(void)dev;
	for (int i = 0; i < 8; i++) if (!h->leased[i]) {
		h->leased[i] = true; vp_allocs++; vp_live++; return i + 64;
	}
	return -ENOSPC;
}
static int qcom_ppe_ds_vp_free(struct qcom_ppe_ds *h, int port)
{
	assert(port >= 64 && port < 72 && h->leased[port - 64]);
	h->leased[port - 64] = false; vp_frees++; vp_live--; return 0;
}
static int qcom_ppe_ds_vp_route(struct qcom_ppe_ds *h, int port, bool direct)
{
	assert(h && h->leased[port - 64]); if (direct) direct_requests++; else cpu_requests++; return 0;
}
static int qcom_ppe_ds_start(struct qcom_ppe_ds *h) { (void)h; return 0; }
static int qcom_ppe_ds_stop(struct qcom_ppe_ds *h) { (void)h; provider_stops++; return stop_error; }
static int qcom_ppe_ds_unregister(struct qcom_ppe_ds *h)
{
	provider_unregisters++;
	if (unregister_error) return unregister_error;
	/* The provider owns its VP leases and releases them on successful
	 * unregister; the client owns bank/netdev references. */
	for (int i = 0; i < 8; i++) if (h->leased[i]) qcom_ppe_ds_vp_free(h, i + 64);
	return 0;
}
static void qcom_ppe_ds_detach(struct qcom_ppe_ds *h) { h->detached = true; detach_calls++; }
static void qcom_ppe_ds_sync(struct qcom_ppe_ds *h) { (void)h; }
static int ath12k_wmi_config_peer_ds(struct ath12k *ar, const u8 *addr, u32 vdev, int port, bool direct)
{
	(void)ar; (void)addr; (void)vdev; (void)port;
	if (direct) wmi_enables++; else wmi_disables++;
	return 0;
}
static int ath12k_dp_rx_htt_ds_cfg(struct ath12k_base *ab) { (void)ab; return 0; }
static void ath12k_dp_ds_quarantine_group(struct ath12k_hw_group *group);
static void ath12k_dp_srng_cleanup(struct ath12k_base *ab, struct dp_srng *ring) { (void)ab; (void)ring; }
static void ath12k_ds_free_buffers(struct ath12k_ds *ds) { (void)ds; }
static void debugfs_remove(struct dentry *debugfs) { (void)debugfs; }
static int ath12k_wifi7_ds_stop(struct ath12k_dp *dp);
static void ath12k_wifi7_ds_quarantine(struct ath12k_dp *dp);
static void ath12k_mhi_stop(struct ath12k_pci *ab_pci, bool suspend);
static void ath12k_pci_power_down(struct ath12k_base *ab, bool suspend);
static void ath12k_qmi_driver_event_work(struct work_struct *work);
static int mhi_prepares, mhi_unprepares, mhi_powers, mhi_poweroffs, mhi_keeps;
static int mhi_prepare_error, mhi_power_error, pci_wakes, pci_resets, firmware_ready_calls;
static int drain_clock, reset_drained, event_drained, restart_drained, dump_drained, group_cleanups;
static int core_deinits, core_frees, restart_queues;
static bool complete_event_at_reset;
static bool clear_qmi_during_reset;
static int mhi_prepare_for_power_up(struct mhi_controller *m)
{
	mhi_prepares++;
	if (mhi_prepare_error) return mhi_prepare_error;
	assert(!m->prepared); m->prepared = true; return 0;
}
static int mhi_sync_power_up(struct mhi_controller *m)
{
	mhi_powers++;
	if (mhi_power_error) return mhi_power_error;
	assert(m->prepared && !m->powered); m->powered = m->channels = true; return 0;
}
static void mhi_unprepare_after_power_down(struct mhi_controller *m)
{
	assert(m->prepared && !m->powered); m->prepared = false; mhi_unprepares++;
}
static void mhi_power_down(struct mhi_controller *m, bool graceful)
{
	assert(m->powered); (void)graceful; m->powered = m->channels = false; mhi_poweroffs++;
}
static void mhi_power_down_keep_dev(struct mhi_controller *m, bool graceful)
{
	assert(m->powered); (void)graceful; m->powered = false; mhi_keeps++;
}
static int mhi_pm_suspend(struct mhi_controller *m) { (void)m; return 0; }
static int mhi_pm_resume(struct mhi_controller *m) { (void)m; return 0; }
static int mhi_force_rddm_mode(struct mhi_controller *m) { (void)m; return 0; }
static void mhi_unregister_controller(struct mhi_controller *m)
{
	assert(!m->prepared && !m->powered); m->channels = false;
}
static void mhi_free_controller(struct mhi_controller *m)
{
	assert(!m->prepared && !m->powered && !m->channels && !m->freed); m->freed = true;
}
static struct ath12k_pci *ath12k_pci_priv(struct ath12k_base *ab) { return ab->pci; }
static void ath12k_pci_aspm_restore(struct ath12k_pci *pci) { (void)pci; }
static void ath12k_pci_force_wake(struct ath12k_base *ab) { (void)ab; pci_wakes++; }
static void ath12k_pci_msi_disable(struct ath12k_pci *pci) { (void)pci; }
static void ath12k_pci_sw_reset(struct ath12k_base *ab, bool poweron)
{
	(void)ab; assert(!poweron); pci_resets++;
}
static struct ath12k_base *pci_get_drvdata(struct pci_dev *pdev) { return pdev->ab; }
static void ath12k_pci_set_irq_affinity_hint(struct ath12k_pci *pci, void *hint) { (void)pci; assert(!hint); }
static void ath12k_core_hw_group_cleanup(struct ath12k_hw_group *group) { (void)group; group_cleanups++; }
static void ath12k_core_deinit(struct ath12k_base *ab)
{
	/* Assert the preconditions at the real PCI remove's teardown boundary. */
	assert(test_bit(ATH12K_FLAG_UNREGISTERING, &ab->dev_flags));
	assert(reset_drained && reset_drained < event_drained && event_drained < restart_drained &&
	       restart_drained < dump_drained);
	core_deinits++;
}
static void ath12k_core_free(struct ath12k_base *ab)
{
	assert(!ab->restart_work.queued && !ab->qmi.event_work.queued); core_frees++;
}
static void ath12k_pci_free_irq(struct ath12k_base *ab) { (void)ab; }
static void ath12k_pci_msi_free(struct ath12k_pci *pci) { (void)pci; }
static void ath12k_pci_free_region(struct ath12k_pci *pci) { (void)pci; }
static void ath12k_hal_srng_deinit(struct ath12k_base *ab) { (void)ab; }
static void ath12k_ce_free_pipes(struct ath12k_base *ab) { (void)ab; }
static void arch_deinit(struct ath12k_base *ab) { (void)ab; }
static bool list_empty(struct list_head *head) { return head->next == head; }
#define list_first_entry(head, type, member) container_of((head)->next, type, member)
#define spin_lock(x) ((void)(x))
#define spin_unlock(x) ((void)(x))
static int ath12k_qmi_event_server_arrive(struct ath12k_qmi *qmi) { (void)qmi; return 0; }
static int ath12k_qmi_event_mem_request(struct ath12k_qmi *qmi) { (void)qmi; return 0; }
static int ath12k_qmi_event_load_bdf(struct ath12k_qmi *qmi) { (void)qmi; return 0; }
static int ath12k_qmi_event_host_cap(struct ath12k_qmi *qmi) { (void)qmi; return 0; }
static int ath12k_core_qmi_firmware_ready(struct ath12k_base *ab) { (void)ab; firmware_ready_calls++; return 0; }
static void ath12k_hal_dump_srng_stats(struct ath12k_base *ab) { (void)ab; }

struct register_cell { u32 reg, value; };
static struct register_cell mmio[64];
static int mmio_count, halt_attempt, fail_halt, control_writes, pointer_writes;
static unsigned poll_delay, poll_timeout, poll_reads;
static bool expect_halt, halt_observed;
static struct register_cell *cell(u32 reg)
{
	for (int i = 0; i < mmio_count; i++) if (mmio[i].reg == reg) return &mmio[i];
	assert(mmio_count < 64); mmio[mmio_count].reg = reg; return &mmio[mmio_count++];
}
static u32 ath12k_hif_read32(struct ath12k_base *ab, u32 reg)
{
	(void)ab; u32 value = cell(reg)->value;
	if (reg == HAL_SEQ_WCSS_UMAC_TCL_REG + HAL_TCL1_RING_CMN_CTRL_REG &&
	    value & HAL_TCL_PPE2TCL_RING_HALT && halt_attempt != fail_halt) {
		value |= HAL_TCL_PPE2TCL_RING_HALT_STATUS;
		halt_observed = true;
	}
	return value;
}
static void ath12k_hif_write32(struct ath12k_base *ab, u32 reg, u32 value)
{
	(void)ab; cell(reg)->value = value;
	if (reg == HAL_SEQ_WCSS_UMAC_TCL_REG + HAL_TCL1_RING_CMN_CTRL_REG) {
		control_writes++;
		if (value & HAL_TCL_PPE2TCL_RING_HALT) { halt_attempt++; halt_observed = false; }
	}
	if (reg >= 0x2000 && reg < 0x2100) {
		if (expect_halt) {
			assert(cell(HAL_SEQ_WCSS_UMAC_TCL_REG + HAL_TCL1_RING_CMN_CTRL_REG)->value &
			       HAL_TCL_PPE2TCL_RING_HALT);
			assert(halt_observed);
		}
		pointer_writes++;
	}
}
/* Virtual time models the poll primitive only. The delays, timeout, condition,
 * early return and restoration sequence are supplied by the actual HAL body. */
#define read_poll_timeout(op, value, condition, delay, timeout, sleep_before, args...) ({ \
	poll_delay = (delay); poll_timeout = (timeout); assert(!(sleep_before)); \
	assert((delay) > 0 && (timeout) > 0); \
	unsigned _elapsed = 0; int _result = -ETIMEDOUT; \
	do { (value) = op(args); poll_reads++; if (condition) { _result = 0; break; } \
	     _elapsed += (delay); } while (_elapsed <= (timeout)); \
	_result; \
})
#include "prepared_functions.inc"

static void ath12k_dp_ds_quarantine_group(struct ath12k_hw_group *group)
{
	for (int i = 0; i < ATH12K_MAX_DEVICES; i++)
		if (group->dp_hw_grp.dp[i]) ath12k_wifi7_ds_quarantine(group->dp_hw_grp.dp[i]);
}
static struct reg_layout layout = { .tcl1_ring_misc = 0x10, .reo1_ring_misc = 0x20 };
static struct ath12k_base base;
static struct ath12k_dp dp;
static struct ath12k_hw_group group;
static struct qcom_ppe_ds provider;
static struct net_device netdev;
static struct wireless_dev wdev;
static struct ieee80211_vif ivif;
static struct ath12k_vif vif;
static struct ath12k ar;
static struct ath12k_link_vif arvif;
static struct ieee80211_sta ista;
static struct ath12k_sta sta;
static struct ath12k_link_sta arsta;
static u32 ring_writeback[2];
static u32 bank_config(struct ath12k_base *ab, struct ath12k_link_vif *v) { (void)ab; (void)v; return 0; }
static const struct ath12k_dp_ops ops = { .tx_get_vdev_bank_config = bank_config };
static void record_cancel(struct work_struct *work)
{
	if (work == &base.reset_work) {
		reset_drained = ++drain_clock;
		if (clear_qmi_during_reset) clear_bit(ATH12K_FLAG_QMI_FAIL, &base.dev_flags);
		if (complete_event_at_reset) {
			complete_event_at_reset = false;
			ath12k_qmi_driver_event_work(&base.qmi.event_work);
		}
	} else if (work == &base.qmi.event_work) event_drained = ++drain_clock;
	else if (work == &base.restart_work) restart_drained = ++drain_clock;
	else if (work == &base.dump_work) dump_drained = ++drain_clock;
}
static void record_queue(struct work_struct *work)
{
	if (work == &base.restart_work) restart_queues++;
}
static struct ath12k_ds *initialize(void)
{
	memset(&base, 0, sizeof(base)); memset(&dp, 0, sizeof(dp));
	memset(&group, 0, sizeof(group)); memset(&provider, 0, sizeof(provider));
	memset(&netdev, 0, sizeof(netdev)); memset(&mmio, 0, sizeof(mmio));
	mmio_count = halt_attempt = fail_halt = control_writes = pointer_writes = 0;
	poll_delay = poll_timeout = poll_reads = 0;
	expect_halt = halt_observed = false;
	netdev.refs = 1; netdev.dev_addr[0] = 2; netdev.dev_addr[5] = 1;
	wdev.netdev = &netdev; netdev.ieee80211_ptr = &wdev;
	ivif.type = NL80211_IFTYPE_AP; ivif.wdev = &wdev; vif.vif = &ivif;
	ar.ab = &base; arvif.ar = &ar; arvif.ahvif = &vif; arvif.vdev_id = 7;
	ista.mlo = false; sta.sta = &ista; sta.enable_4addr = false; arsta.ahsta = &sta;
	memcpy(arsta.addr, "\x02\x01\x02\x03\x04\x05", 6);
	base.hw_rev = ATH12K_HW_QCN9274_HW10; base.hal.regs = &layout;
	base.ag = &group; base.dp = &dp; dp.ab = &base; dp.hal = &base.hal; dp.ops = &ops;
	group.dp_hw_grp.dp[0] = &dp;
	struct ath12k_ds *ds = calloc(1, sizeof(*ds)); assert(ds);
	ds->dp = &dp; ds->handle = &provider; dp.ds = ds;
	ds->fault_work.function = ath12k_ds_fault_work;
	INIT_LIST_HEAD(&ds->peers);
	ds->tcl.ring_id = 0; ds->reo.ring_id = 1;
	for (int i = 0; i < 2; i++) {
		struct hal_srng *ring = &dp.hal->srng_list[i];
		ring->entry_size = 4; ring->ring_size = 4096;
		ring->ring_dir = i ? HAL_SRNG_DIR_DST : HAL_SRNG_DIR_SRC;
		ring->hwreg_base[0] = 0x1000 + i * 0x100;
		ring->hwreg_base[1] = 0x2000 + i * 0x10;
		cell(ring->hwreg_base[0] + (i ? 0x20 : 0x10))->value = 0x5e5;
		ring_writeback[i] = 777;
		if (i) { ring->u.dst_ring.tp = 777; ring->u.dst_ring.hp_addr = &ring_writeback[i]; }
		else { ring->u.src_ring.hp = 777; ring->u.src_ring.tp_addr = &ring_writeback[i]; }
	}
	cell(HAL_SEQ_WCSS_UMAC_TCL_REG + HAL_TCL1_RING_CMN_CTRL_REG)->value = 0xa1;
	return ds;
}
static void run_fault(struct ath12k_ds *ds)
{
	assert(ds->fault_work.queued);
	running_work = &ds->fault_work; running_work->queued = false;
	running_work->function(running_work); running_work = NULL;
}
static void no_leaks(void)
{
	assert(!dp.ds && !bank_live && !vp_live && !hooks && netdev.refs == 1);
	assert(bank_gets == bank_puts && vp_allocs == vp_frees);
}
static void hook_failure(bool existing)
{
	struct ath12k_ds *ds = initialize();
	if (existing) {
		/* First track a CPU-only 4addr peer, then remove that fallback.
		 * Its later authorization reaches the existing-peer bind call. */
		sta.enable_4addr = true;
		assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, false));
		assert(!vp_allocs && netdev.refs == 2);
		sta.enable_4addr = false;
	}
	nf_error = -EIO;
	assert(ath12k_ds_peer_update(&ar, &arvif, &arsta, true) == -EIO);
	assert(ds->faulted && !ds->stopped && queue_count == 1);
	assert(ds->vps[0].reserved && !ds->vps[0].control_registered);
	assert(!ds->vps[0].direct && !direct_requests && !wmi_enables);
	assert(bank_live == 1 && vp_live == 1 && netdev.refs == 3);
	int allocations = vp_allocs;
	/* A second peer callback cannot reuse the unregistered VP. */
	assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, true));
	assert(vp_allocs == allocations && !direct_requests);
	ath12k_ds_fault(ds); assert(queue_count == 1);
	run_fault(ds); assert(ds->stopped && cpu_requests && provider_stops == 1);
	assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
	assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
	nf_error = 0;
	ds = initialize();
	assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, true));
	assert(ds->vps[0].control_registered && ds->vps[0].direct && hooks == 1);
	assert(!ath12k_wifi7_ds_stop(&dp));
	assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
	assert(hook_unregister_calls == 1 && synchronize_sequence > unregister_sequence);
	assert(final_put_sequence > synchronize_sequence);
}
static void quarantine(bool hook_failure)
{
	struct ath12k_ds *ds = initialize();
	if (hook_failure) nf_error = -EIO;
	assert(ath12k_ds_peer_update(&ar, &arvif, &arsta, true) == (hook_failure ? -EIO : 0));
	stop_error = -EBUSY; unregister_error = -EBUSY;
	ath12k_ds_fault(ds); run_fault(ds);
	assert(ds->quarantined && provider.detached && !ds->stopped);
	int allocations = vp_allocs, banks = bank_gets;
	assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, true));
	assert(vp_allocs == allocations && bank_gets == banks);
	ath12k_wifi7_ds_vif_remove(&arvif, true, false);
	assert(!hooks && netdev.refs == 1 && !ds->vps[0].vif && !ds->vps[0].dev);
	assert(ds->vps[0].reserved && bank_live == 1 && vp_live == 1);
	assert(ath12k_wifi7_ds_cleanup(&dp) == -EIO);
	assert(ath12k_wifi7_ds_cleanup(&dp) == -EIO);
	assert(!hooks && netdev.refs == 1 && bank_puts == 0 && vp_frees == 0);
	assert(!ds->dp && ds->vps[0].reserved);
	if (hook_failure)
		assert(!hook_unregister_calls && !synchronize_sequence && final_put_sequence > 0);
	else
		assert(synchronize_sequence > unregister_sequence && final_put_sequence > synchronize_sequence);
	free(ds); /* Host process fixture; no claim that quarantined DMA is freed. */
}
static void vif_remove(void)
{
	struct ath12k_ds *ds = initialize();
	assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, true));
	/* Make the test ring empty so the real bounded drain observes equality. */
	ring_writeback[0] = 800;
	ds->dp->hal->srng_list[0].u.src_ring.hp = ring_writeback[0];
	ath12k_wifi7_ds_vif_remove(&arvif, true, false);
	assert(!hooks && ds->vps[0].closing && ds->vps[0].drained);
	ath12k_wifi7_ds_vif_remove(&arvif, false, true);
	assert(!ds->vps[0].reserved && !bank_live && !vp_live && netdev.refs == 1);
	assert(synchronize_sequence > unregister_sequence && final_put_sequence > synchronize_sequence);
	assert(!ath12k_wifi7_ds_stop(&dp)); assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
}
static void unregister_failure(void)
{
	struct ath12k_ds *ds = initialize();
	assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, true));
	assert(!ath12k_wifi7_ds_stop(&dp));
	unregister_error = -EBUSY;
	assert(ath12k_wifi7_ds_cleanup(&dp) == -EIO);
	assert(ds->quarantined && dp.ds_quarantined && provider.detached);
	assert(!ds->dp && !hooks && netdev.refs == 1);
	assert(bank_live == 1 && vp_live == 1 && !bank_puts && !vp_frees);
	assert(synchronize_sequence > unregister_sequence && final_put_sequence > synchronize_sequence);
	assert(ath12k_wifi7_ds_cleanup(&dp) == -EIO);
	assert(provider_unregisters == 1 && netdev.refs == 1);
	free(ds);
}
static void control_rx(void)
{
	struct ath12k_ds *ds = initialize();
	assert(!ath12k_ds_peer_update(&ar, &arvif, &arsta, true));
	struct ath12k_ds_peer *peer = container_of(ds->peers.next, struct ath12k_ds_peer, list);
	u8 storage[64] = {0}; struct ethhdr *eth = (struct ethhdr *)storage;
	memcpy(eth->h_dest, netdev.dev_addr, 6); memcpy(eth->h_source, arsta.addr, 6);
	struct sk_buff skb = { .protocol = cpu_to_be16(ETH_P_PAE), .mac = storage,
		.data = storage + ETH_HLEN, .headroom = ETH_HLEN, .mac_set = true };
	struct nf_hook_state state = { .in = &netdev };
	struct net_device other = netdev;
	assert(ath12k_ds_control_rx(ds, &skb, &state) == NF_STOLEN);
	assert(ds->control_rx == 1 && skb_freed == 1 && delivered_calls == 1);
#define ACCEPT_WITH(statement, restore) do { \
	int _freed = skb_freed; statement; \
	assert(ath12k_ds_control_rx(ds, &skb, &state) == NF_ACCEPT); \
	assert(skb_freed == _freed && ds->control_rx == 1); restore; \
} while (0)
	ACCEPT_WITH(deliver_ok = false, deliver_ok = true);
	ACCEPT_WITH(peer->authorized = false, peer->authorized = true);
	ACCEPT_WITH(peer->enabled = false, peer->enabled = true);
	ACCEPT_WITH(peer->dev = NULL, peer->dev = &netdev);
	ACCEPT_WITH(state.in = &other, state.in = &netdev);
	ACCEPT_WITH(eth->h_source[5] ^= 1, eth->h_source[5] ^= 1);
	ACCEPT_WITH(eth->h_dest[5] ^= 1, eth->h_dest[5] ^= 1);
	ACCEPT_WITH(netdev.ieee80211_ptr = NULL, netdev.ieee80211_ptr = &wdev);
	ACCEPT_WITH(ds->faulted = true, ds->faulted = false);
	ACCEPT_WITH(ds->stopped = true, ds->stopped = false);
	ACCEPT_WITH(skb.mac_set = false, skb.mac_set = true);
	ACCEPT_WITH(skb.headroom = 1, skb.headroom = ETH_HLEN);
	ACCEPT_WITH(skb.data = storage + 1, skb.data = storage + ETH_HLEN);
	ACCEPT_WITH(skb.protocol = 0, skb.protocol = cpu_to_be16(ETH_P_PAE));
	ACCEPT_WITH(state.in = NULL, state.in = &netdev);
	assert(delivered_calls == 2); /* only matching successful and rejected deliveries */
	assert(!ath12k_wifi7_ds_stop(&dp)); assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
}
static void indices(int fail, bool zero)
{
	struct ath12k_ds *ds = initialize();
	ds->hw1_reo = 55; fail_halt = fail;
	expect_halt = !zero;
	u16 tcl = zero ? 0 : 12, reo = zero ? 0 : 23;
	int ret = ath12k_ds_start_indices(ds, tcl, reo);
	assert(cell(HAL_SEQ_WCSS_UMAC_TCL_REG + HAL_TCL1_RING_CMN_CTRL_REG)->value == 0xa1);
	assert(cell(0x1010)->value == 0x5e5 && cell(0x1120)->value == 0x5e5);
	if (fail) {
		assert(ret == -ETIMEDOUT && ds->hw1_reo == 55);
		assert(halt_attempt == fail && control_writes == fail * 2);
		assert(poll_delay == 1000 && poll_timeout == 100000 && poll_reads >= 100 && poll_reads <= 103);
		assert(ring_writeback[1] == 777 && ds->dp->hal->srng_list[1].u.dst_ring.tp == 777);
		assert(ring_writeback[0] == (fail == 1 ? 777 : tcl * 4));
		assert(pointer_writes == (fail == 1 ? 0 : 2));
	} else {
		assert(!ret && ds->hw1_reo == reo);
		assert(ring_writeback[0] == tcl * 4 && ring_writeback[1] == reo * 4);
		struct hal_srng *src = &dp.hal->srng_list[0], *dst = &dp.hal->srng_list[1];
		assert(src->u.src_ring.hp == tcl * 4 && src->u.src_ring.cached_tp == tcl * 4);
		assert(src->u.src_ring.reap_hp == ((tcl * 4U + 4096U - 4U) % 4096U));
		assert(dst->u.dst_ring.tp == reo * 4 && dst->u.dst_ring.cached_hp == reo * 4);
		assert(pointer_writes == 4);
		assert(halt_attempt == (zero ? 0 : 2) && control_writes == (zero ? 0 : 4));
		assert(poll_reads == (zero ? 0 : 2));
		if (!zero) assert(poll_delay == 1000 && poll_timeout == 100000);
	}
	assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
}
static void indices_mixed(void)
{
	struct ath12k_ds *ds = initialize();
	/* The zero TCL restore skips HALT; the following nonzero REO restore
	 * must still HALT and only then publish the combined result. */
	assert(!ath12k_ds_start_indices(ds, 0, 23));
	assert(!ring_writeback[0] && ring_writeback[1] == 23 * 4 && ds->hw1_reo == 23);
	assert(halt_attempt == 1 && control_writes == 2 && poll_reads == 1);
	assert(cell(HAL_SEQ_WCSS_UMAC_TCL_REG + HAL_TCL1_RING_CMN_CTRL_REG)->value == 0xa1);
	assert(!ath12k_wifi7_ds_cleanup(&dp)); no_leaks();
}
static struct ath12k_pci mhi_pci;
static struct mhi_controller controller;
static const struct family_ops family = { .arch_deinit = arch_deinit };
static void initialize_mhi(void)
{
	memset(&base, 0, sizeof(base)); memset(&mhi_pci, 0, sizeof(mhi_pci));
	memset(&controller, 0, sizeof(controller));
	controller.irq = malloc(4); assert(controller.irq);
	base.pci = &mhi_pci; base.qmi.ab = &base; mhi_pci.ab = &base;
	ath12k_qmi_init_work(&base);
	INIT_WORK(&base.reset_work, ath12k_qmi_driver_event_work);
	INIT_WORK(&base.restart_work, ath12k_qmi_driver_event_work);
	INIT_WORK(&base.dump_work, ath12k_qmi_driver_event_work);
	mhi_pci.mhi_ctrl = &controller; mhi_pci.device_family_ops = &family;
	INIT_LIST_HEAD(&base.qmi.event_list);
}
static void event_fw_ready(void)
{
	struct ath12k_qmi_driver_event *event = calloc(1, sizeof(*event)); assert(event);
	event->type = ATH12K_QMI_EVENT_FW_READY; list_add_tail(&event->list, &base.qmi.event_list);
	base.qmi.event_work.queued = true;
}
static void mhi_stop_cases(void)
{
	initialize_mhi();
	ath12k_mhi_stop(&mhi_pci, false); assert(!mhi_unprepares && !mhi_poweroffs);
	mhi_prepare_error = -EIO;
	assert(ath12k_mhi_start(&mhi_pci) == -EIO);
	ath12k_mhi_stop(&mhi_pci, false); assert(!mhi_unprepares && !mhi_poweroffs);
	mhi_prepare_error = 0; mhi_power_error = -EIO;
	assert(ath12k_mhi_start(&mhi_pci) == -EIO);
	assert(controller.prepared && !controller.powered && test_bit(ATH12K_MHI_INIT, &mhi_pci.mhi_state));
	ath12k_mhi_stop(&mhi_pci, false); assert(mhi_unprepares == 1 && !mhi_poweroffs);
	ath12k_mhi_stop(&mhi_pci, false); assert(mhi_unprepares == 1);
	mhi_power_error = 0; assert(!ath12k_mhi_start(&mhi_pci));
	assert(controller.timeout_ms == MHI_TIMEOUT_DEFAULT_MS && controller.powered && controller.channels);
	ath12k_mhi_stop(&mhi_pci, false); assert(mhi_unprepares == 2 && mhi_poweroffs == 1);
	ath12k_mhi_stop(&mhi_pci, false); assert(mhi_unprepares == 2 && mhi_poweroffs == 1);
	ath12k_mhi_unregister(&mhi_pci); assert(controller.freed && !mhi_pci.mhi_ctrl);
}
static void mhi_suspend_case(void)
{
	initialize_mhi(); assert(!ath12k_mhi_start(&mhi_pci));
	ath12k_mhi_stop(&mhi_pci, true);
	assert(mhi_keeps == 1 && !mhi_poweroffs && controller.channels && !controller.powered && !controller.prepared);
	ath12k_mhi_stop(&mhi_pci, true); assert(mhi_keeps == 1 && mhi_unprepares == 1);
	ath12k_mhi_unregister(&mhi_pci); assert(controller.freed && !controller.channels);
}
static void pci_power_down_case(void)
{
	initialize_mhi(); assert(!ath12k_mhi_start(&mhi_pci));
	assert(!test_bit(ATH12K_PCI_FLAG_INIT_DONE, &mhi_pci.flags));
	ath12k_pci_power_down(&base, false);
	assert(pci_wakes == 1 && pci_resets == 1 && mhi_poweroffs == 1 && mhi_unprepares == 1);
	ath12k_pci_power_down(&base, false); assert(pci_wakes == 1 && pci_resets == 1);
	ath12k_mhi_unregister(&mhi_pci); assert(controller.freed);
}
static void pci_remove_case(bool qmi_fail)
{
	initialize_mhi(); assert(!ath12k_mhi_start(&mhi_pci));
	if (qmi_fail) set_bit(ATH12K_FLAG_QMI_FAIL, &base.dev_flags);
	/* A QMI worker already beyond its guard may clear the error flag while
	 * cancel waits. Remove must retain the branch decision made at entry. */
	clear_qmi_during_reset = qmi_fail;
	set_bit(ATH12K_FLAG_QMI_FW_READY_COMPLETE, &base.dev_flags);
	event_fw_ready(); complete_event_at_reset = true;
	/* Existing queued recovery must also be canceled by either branch. */
	base.restart_work.queued = true;
	struct pci_dev pdev = { .ab = &base };
	ath12k_pci_remove(&pdev);
	assert(core_deinits == 1 && core_frees == 1 && !mhi_pci.mhi_ctrl && controller.freed);
	assert(!restart_queues && !firmware_ready_calls && list_empty(&base.qmi.event_list));
	assert(group_cleanups == (qmi_fail ? 0 : 1));
	assert(pci_wakes == (qmi_fail ? 1 : 0));
}
static void fw_ready_case(void)
{
	initialize_mhi();
	set_bit(ATH12K_FLAG_QMI_FW_READY_COMPLETE, &base.dev_flags);
	/* A normal ready event may queue recovery. */
	event_fw_ready(); ath12k_qmi_driver_event_work(&base.qmi.event_work);
	assert(restart_queues == 1 && base.restart_work.queued);
	base.restart_work.queued = false;
	/* Begin remove after the worker's outer check but before FW_READY's
	 * inner check; actual worker must refuse to queue another restart. */
	event_fw_ready(); inject_unregister = true; unregister_race_flags = &base.dev_flags;
	ath12k_qmi_driver_event_work(&base.qmi.event_work); inject_unregister = false;
	assert(unregister_checks >= 2 && restart_queues == 1 && !base.restart_work.queued);
	assert(!firmware_ready_calls && list_empty(&base.qmi.event_list));
	ath12k_mhi_unregister(&mhi_pci);
}
int main(int argc, char **argv)
{
	assert(argc == 2);
	if (!strcmp(argv[1], "new-hook-failure")) hook_failure(false);
	else if (!strcmp(argv[1], "existing-hook-failure")) hook_failure(true);
	else if (!strcmp(argv[1], "quarantine")) quarantine(false);
	else if (!strcmp(argv[1], "hook-failure-quarantine")) quarantine(true);
	else if (!strcmp(argv[1], "unregister-failure")) unregister_failure();
	else if (!strcmp(argv[1], "vif-remove")) vif_remove();
	else if (!strcmp(argv[1], "control-rx")) control_rx();
	else if (!strcmp(argv[1], "indices-success")) indices(0, false);
	else if (!strcmp(argv[1], "indices-zero")) indices(0, true);
	else if (!strcmp(argv[1], "indices-mixed")) indices_mixed();
	else if (!strcmp(argv[1], "indices-first-timeout")) indices(1, false);
	else if (!strcmp(argv[1], "indices-second-timeout")) indices(2, false);
	else if (!strcmp(argv[1], "mhi-stop")) mhi_stop_cases();
	else if (!strcmp(argv[1], "mhi-suspend")) mhi_suspend_case();
	else if (!strcmp(argv[1], "pci-power-down")) pci_power_down_case();
	else if (!strcmp(argv[1], "pci-remove")) pci_remove_case(false);
	else if (!strcmp(argv[1], "pci-qmi-fail-remove")) pci_remove_case(true);
	else if (!strcmp(argv[1], "qmi-fw-ready")) fw_ready_case();
	else return 2;
	puts("host-ds-lifecycle:ok");
	return 0;
}
