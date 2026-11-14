/* SPDX-License-Identifier: GPL-2.0-only */
/* Real bridge/PPE/EDMA functions; kernel services and hardware are fixtures. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) ((~0UL << (l)) & (~0UL >> (63 - (h))))
#define FIELD_PREP(mask, val) (((unsigned long)(val) << __builtin_ctzl(mask)) & (mask))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define likely(x) (x)
#define unlikely(x) (x)
#define static_branch_unlikely(x) (*(x) != 0)
#define static_branch_inc(x) (++*(x))
#define static_branch_dec(x) (--*(x))
#define set_bit(n, p) (*(p) |= BIT(n))
#define test_bit(n, p) (!!(*(p) & BIT(n)))
#define WARN_ON(x) (x)
#define NL_SET_ERR_MSG_MOD(...) ((void)0)
#define rcu_access_pointer(x) (x)
#define rcu_dereference(x) (x)
#define GFP_ATOMIC 0
#define ETH_ALEN 6
#define ETH_HLEN 14
#define CHECKSUM_PARTIAL 1
#define BR_STATE_FORWARDING 3
#define SKB_GSO_TCPV4 1
#define SKB_GSO_TCPV6 2
#define CONFIG_BRIDGE_IGMP_SNOOPING 1
#define BR_MCAST_DIR_TX 0
#define MCAST_INCLUDE 1
#define MDB_PG_FLAGS_BLOCKED 1
#define NFPROTO_BRIDGE 0
#define NF_BR_POST_ROUTING 0
#define NF_BR_FORWARD 0
#define NF_BR_LOCAL_OUT 0
#define NF_HOOK(pf, hook, net, sk, skb, in, out, fn) \
	((void)(hook), (void)(in), (void)(out), fn(net, sk, skb))
#define IS_ERR(p) ((intptr_t)(p) < 0)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define EXPORT_SYMBOL_GPL(x)
#define DEV_STATS_INC(dev, field) ((void)(dev))

#include "prepared-types.h"

struct net { int unused; };
struct sock { int unused; };
struct netlink_ext_ack { int unused; };
struct ethhdr { unsigned char h_dest[6], h_source[6]; u16 h_proto; };
struct netdev_phys_item_id { int id; };
struct net_bridge;
struct net_bridge_port;
struct net_device {
	int id;
	void *priv;
	struct net_bridge_port *port;
	bool vlan_aware, offloaded, txmac, ingress_filter;
};
struct br_input_skb_cb {
	struct net_device *brdev;
	unsigned long fwd_hwdoms;
	int src_hwdom, backup_nhid;
	bool tx_fwd_offload, proxyarp_replied;
};
struct skb_shared_info { int gso_type, gso_size; };
struct sk_buff {
	struct net_device *dev;
	struct br_input_skb_cb cb;
	struct ethhdr eth;
	void *data;
	int ip_summed, protocol;
	bool offload_fwd_mark, igmp;
	int vlan_tag;
	struct skb_shared_info shinfo;
};
#define BR_INPUT_SKB_CB(skb) ((struct br_input_skb_cb *)&(skb)->cb)
struct net_bridge_port {
	struct net_device *dev;
	struct net_bridge *br;
	struct net_bridge_port *next, *backup_port;
	unsigned long flags;
	int state, hwdom, offload_count, backup_nhid;
	struct netdev_phys_item_id ppid;
	bool vlan_allowed, isolated;
	int vlan_tag;
};
struct net_bridge { struct net_device *dev; struct net_bridge_port *port_list; };
struct net_bridge_vlan_group { const struct net_bridge_port *port; };
struct hlist_node { struct hlist_node *next; struct net_bridge_port *port; };
struct net_bridge_port_group {
	struct { struct net_bridge_port *port; } key;
	struct net_bridge_port_group *next;
	int filter_mode, flags;
	unsigned char eth_addr[6];
};
struct net_bridge_mdb_entry {
	struct net_bridge_port_group *ports;
	struct { int proto; } addr;
};
struct net_bridge_mcast { struct hlist_node *routers; };
enum br_pkt_type { BR_PKT_UNICAST, BR_PKT_MULTICAST, BR_PKT_BROADCAST };
enum skb_drop_reason { SKB_DROP_REASON_NO_TX_TARGET, SKB_DROP_REASON_NOMEM,
	SKB_DROP_REASON_NOT_SPECIFIED };
#define list_for_each_entry_rcu(p, head, member) for ((p) = *(head); (p); (p) = (p)->next)
struct ppe_port { int port_id; };
struct edma_port_priv { struct ppe_port *ppe_port; u32 br_flood[EDMA_PORT_FLOOD_MAX]; };
struct edma_port_tx_stats { int syncp; unsigned int tx_tso_pkts; };
struct ppe_device { int num_ports; };
struct ppe_bridge_vsi { u32 members; int vid; };
struct ppe_bridge_mdb { int vsi; u32 ports; struct ppe_bridge_mdb *next; };
struct ppe_bridge {
	struct ppe_device *ppe_dev;
	struct {
		struct net_device *bridge, *dev;
		int vsi, pvid;
		unsigned long flags;
		bool mrouter, forwarding;
	} ports[4];
	struct ppe_bridge_vsi vsis[1];
	struct ppe_bridge_mdb *mdb_ht;
	int switchdev_nb, switchdev_blocking_nb, fdb_wq, fdb_sync;
};
#define hash_for_each(head, bkt, entry, node) \
	for ((bkt) = 0, (entry) = (head); (bkt) == 0 && (entry); (entry) = (entry)->next)
#define PPE_BRIDGE_SUPPORTED_FLAGS (BR_LEARNING | BR_FLOOD | BR_MCAST_FLOOD | BR_BCAST_FLOOD)
#define PPE_BRIDGE_FDB_SYNC_INTERVAL 1

static int br_switchdev_tx_fwd_offload;
static int registrations, vlan_replays;
static struct net dummy_net;
static struct net_device devices[4];
static struct net_bridge_port ports[4];
static struct ppe_port physical_ports[4];
static struct edma_port_priv edma_ports[4];
static struct net_bridge bridge;
static struct ppe_device ppe_device;
static struct ppe_bridge ppe_bridge;
static struct ppe_bridge_mdb hw_mdb;
static struct net_bridge_vlan_group vlan_group;
static int copies[4], tags[4], descriptors;
static u32 last_word4;

static bool br_vlan_enabled(struct net_device *dev) { return dev->vlan_aware; }
static int ppe_bridge_port_id(struct net_device *dev) { return dev->id; }
static int ppe_bridge_vsi_get(struct ppe_bridge *pb, struct net_device *br, int vid) { return 0; }
static int ppe_bridge_vsi_hw_update(struct ppe_bridge *pb, int vsi) { return 0; }
static int ppe_bridge_vsi_eg_update(struct ppe_bridge *pb, int vsi) { return 0; }
static int ppe_port_vsi_set(struct ppe_device *pd, int port, int vsi) { return 0; }
static int ppe_port_vlan_ingress_filter_set(struct ppe_device *pd, int port, bool on)
{ devices[port].ingress_filter = on; return 0; }
static int ppe_port_bridge_txmac_set(struct ppe_device *pd, int port, bool on)
{ devices[port].txmac = on; return 0; }
static void ppe_bridge_port_offload_mark_set(struct net_device *dev, bool on) { dev->offloaded = on; }
static void ppe_bridge_port_tx_flood_clear(struct net_device *dev)
{ memset(edma_ports[dev->id].br_flood, 0, sizeof(edma_ports[dev->id].br_flood)); }
static void ppe_bridge_port_vlans_flush(struct ppe_bridge *pb, int port) { }
static void queue_delayed_work(int wq, int *work, int delay) { }
static void *netdev_priv(struct net_device *dev) { return dev->priv; }
static bool netdev_phys_item_id_same(const struct netdev_phys_item_id *a,
				    const struct netdev_phys_item_id *b) { return a->id == b->id; }
static int nbp_switchdev_hwdom_set(struct net_bridge_port *p) { p->hwdom = 1; return 0; }
static void nbp_switchdev_hwdom_put(struct net_bridge_port *p) { p->hwdom = 0; }
static int switchdev_bridge_port_offload(struct net_device *, struct net_device *, void *,
					 int *, int *, bool, struct netlink_ext_ack *);
static u8 br_multicast_igmp_type(const struct sk_buff *skb) { return skb->igmp; }
static bool is_broadcast_ether_addr(const unsigned char *addr)
{ static const unsigned char bc[6] = {255,255,255,255,255,255}; return !memcmp(addr, bc, 6); }
static bool is_multicast_ether_addr(const unsigned char *addr) { return addr[0] & 1; }
static bool is_link_local_ether_addr(const unsigned char *addr) { return false; }
static bool ether_addr_equal(const unsigned char *a, const unsigned char *b) { return !memcmp(a,b,6); }
static bool skb_is_gso(struct sk_buff *skb) { return skb->shinfo.gso_size != 0; }
#define skb_shinfo(skb) (&(skb)->shinfo)
#define u64_stats_update_begin(...) ((void)0)
#define u64_stats_update_end(...) ((void)0)

#include "prepared-driver.h"

/* Switchdev's notification/replay plumbing is stubbed; capability consumption
 * uses the actual bridge nbp_switchdev_add(), including its static key. */
static int switchdev_bridge_port_offload(struct net_device *dev, struct net_device *brport,
		void *ctx, int *nb, int *blocking_nb, bool tx_fwd_offload, struct netlink_ext_ack *extack)
{
	struct ppe_bridge *pb = ctx;
	registrations++;
	assert(dev->offloaded && dev->txmac);
	assert(pb->ports[dev->id].dev == dev);
	if (br_vlan_enabled(pb->ports[dev->id].bridge)) {
		/* Replay requires the join state to be visible before notification. */
		assert(pb->ports[dev->id].vsi == -1 && dev->ingress_filter);
		vlan_replays++;
	}
	return nbp_switchdev_add(dev->port, (struct netdev_phys_item_id){1}, tx_fwd_offload, extack);
}

static struct net_bridge_vlan_group *nbp_vlan_group_rcu(const struct net_bridge_port *p)
{ vlan_group.port = p; return &vlan_group; }
static bool br_mst_is_enabled(const struct net_bridge_port *p) { return false; }
static bool br_allowed_egress(struct net_bridge_vlan_group *vg, const struct sk_buff *skb)
{ return vg->port->vlan_allowed; }
static bool br_skb_isolated(const struct net_bridge_port *p, const struct sk_buff *skb) { return p->isolated; }
static struct net *dev_net(struct net_device *dev) { return &dummy_net; }
static void skb_push(struct sk_buff *skb, int len) { }
static void __skb_push(struct sk_buff *skb, int len) { }
static void __skb_pull(struct sk_buff *skb, int len) { }
static bool is_skb_forwardable(struct net_device *dev, struct sk_buff *skb) { return true; }
static void br_drop_fake_rtable(struct sk_buff *skb) { }
static bool eth_type_vlan(int protocol) { return false; }
static int vlan_get_protocol_and_depth(struct sk_buff *skb, int proto, int *depth) { return 1; }
static void skb_set_network_header(struct sk_buff *skb, int depth) { }
static void skb_clear_tstamp(struct sk_buff *skb) { }
static bool skb_warn_if_lro(struct sk_buff *skb) { return false; }
static void skb_forward_csum(struct sk_buff *skb) { }
static bool netpoll_tx_running(struct net_device *dev) { return false; }
static void br_netpoll_send_skb(const struct net_bridge_port *p, struct sk_buff *skb) { abort(); }
static bool netif_carrier_ok(struct net_device *dev) { return true; }
static bool netif_running(struct net_device *dev) { return true; }
static void kfree_skb(struct sk_buff *skb) { free(skb); }
static void kfree_skb_reason(struct sk_buff *skb, enum skb_drop_reason reason) { free(skb); }
static struct sk_buff *skb_clone(struct sk_buff *skb, int flags)
{ struct sk_buff *copy = malloc(sizeof(*copy)); assert(copy); *copy = *skb; copy->data = &copy->eth; return copy; }
static struct sk_buff *pskb_copy(struct sk_buff *skb, int flags) { return skb_clone(skb, flags); }
static struct ethhdr *eth_hdr(struct sk_buff *skb) { return &skb->eth; }
static struct sk_buff *br_handle_vlan(struct net_bridge *br, const struct net_bridge_port *p,
				     struct net_bridge_vlan_group *vg, struct sk_buff *skb)
{
	/* VLAN lookup/tag manipulation is a fixture; actual should_deliver and
	 * __br_forward still decide whether/when this per-port operation runs. */
	if (!p->vlan_allowed) { free(skb); return NULL; }
	skb->vlan_tag = p->vlan_tag;
	return skb;
}
static void br_multicast_count(struct net_bridge *br, struct net_bridge_port *p,
			       struct sk_buff *skb, int type, int dir) { }
static void br_tc_skb_miss_set(struct sk_buff *skb, bool miss) { }
static bool br_is_neigh_suppress_enabled(struct net_bridge_port *p, u16 vid) { return false; }
static struct hlist_node *br_multicast_get_first_rport_node(struct net_bridge_mcast *ctx, struct sk_buff *skb)
{ return ctx->routers; }
static struct net_bridge_port *br_multicast_rport_from_node_skb(struct hlist_node *node, struct sk_buff *skb)
{ return node ? node->port : NULL; }
static struct hlist_node *hlist_next_rcu(struct hlist_node *node) { return node->next; }
static bool br_multicast_should_handle_mode(struct net_bridge_mcast *ctx, int proto) { return false; }
static bool br_multicast_is_star_g(void *addr) { return false; }

/* Hardware destination decoder: observe each descriptor's destination using
 * the documented word4 wire fields, independently of the fill macros. */
static void dev_queue_xmit(struct sk_buff *skb)
{
	struct edma_txdesc_pri txd = {0};
	edma_tx_fill_desc(&edma_ports[skb->dev->id], &txd, skb, NULL, -1);
	u32 type = txd.word4 >> 28, destination = (txd.word4 >> 16) & 0xfff;
	last_word4 = txd.word4;
	descriptors++;
	for (int i = 1; i <= 3; i++) {
		if ((type == 2 && destination == (u32)i) || (type == 3 && (destination & BIT(i)))) {
			copies[i]++;
			tags[i] = skb->vlan_tag;
		}
	}
	assert(type == 2 || type == 3);
	free(skb);
}

#include "prepared-forward.h"

static void reset_output(void)
{ memset(copies, 0, sizeof(copies)); memset(tags, 0, sizeof(tags)); descriptors = 0; last_word4 = 0; }
static void setup(bool vlan_aware)
{
	memset(devices, 0, sizeof(devices)); memset(ports, 0, sizeof(ports));
	memset(&ppe_bridge, 0, sizeof(ppe_bridge)); memset(edma_ports, 0, sizeof(edma_ports));
	br_switchdev_tx_fwd_offload = registrations = vlan_replays = 0;
	bridge = (struct net_bridge){ .dev = &devices[0], .port_list = &ports[1] };
	ppe_device.num_ports = 4;
	ppe_bridge.ppe_dev = &ppe_device;
	devices[0].vlan_aware = vlan_aware;
	for (int i = 1; i <= 3; i++) {
		devices[i].id = i; devices[i].port = &ports[i]; devices[i].priv = &edma_ports[i];
		physical_ports[i].port_id = i; edma_ports[i].ppe_port = &physical_ports[i];
		ports[i] = (struct net_bridge_port){ .dev = &devices[i], .br = &bridge,
			.next = i < 3 ? &ports[i + 1] : NULL, .flags = PPE_BRIDGE_SUPPORTED_FLAGS,
			.state = BR_STATE_FORWARDING, .vlan_allowed = true };
		assert(!ppe_bridge_port_join(&ppe_bridge, &devices[i], &devices[0], NULL));
		ppe_bridge.ports[i].forwarding = true;
	}
	assert(registrations == 3);
	/* LAN3 is an inactive member initially; LAN1 and LAN2 form the 0x6
	 * reproduction. Individual scenarios enable the third eligible exit. */
	ports[3].state = 0;
	ppe_bridge.ports[3].forwarding = false;
	ppe_bridge_tx_flood_update(&ppe_bridge, 0);
	reset_output();
}
static struct sk_buff *packet(int ingress, int kind)
{
	struct sk_buff *skb = calloc(1, sizeof(*skb)); assert(skb);
	skb->dev = &devices[ingress]; skb->cb.brdev = &devices[0];
	skb->eth.h_dest[0] = kind == BR_PKT_UNICAST ? 2 : 1;
	skb->eth.h_dest[5] = 0x42;
	if (kind == BR_PKT_BROADCAST) memset(skb->eth.h_dest, 255, 6);
	skb->data = &skb->eth;
	return skb;
}
static void expect(int a, int b, int c)
{
	int wanted[] = {0, a, b, c};
	for (int i = 1; i <= 3; i++) if (copies[i] != wanted[i]) {
		fprintf(stderr, "port%d copies=%d expected=%d word4=0x%08x descriptors=%d\n",
			i, copies[i], wanted[i], last_word4, descriptors);
		exit(1);
	}
}
static void enable_third(void)
{ ports[3].state = BR_STATE_FORWARDING; ppe_bridge.ports[3].forwarding = true; ppe_bridge_tx_flood_update(&ppe_bridge, 0); }
static void known_to(int target)
{
	for (int other_offload = 0; other_offload <= 1; other_offload++) {
		setup(false);
		if (other_offload) br_switchdev_tx_fwd_offload++;
		/* FDB lookup has selected exactly one target; no DA-based flood
		 * inference is performed by this fixture. */
		br_forward(&ports[target], packet(0, BR_PKT_UNICAST), false, true);
		expect(target == 1, target == 2, 0);
		assert(descriptors == 1);
		reset_output();
		/* A software-forwarded/punted frame also has one FDB destination. */
		br_forward(&ports[target], packet(target == 1 ? 2 : 1, BR_PKT_UNICAST), false, false);
		expect(target == 1, target == 2, 0);
	}
}
static void unknown(void)
{
	setup(false); enable_third();
	br_flood(&bridge, packet(0, BR_PKT_UNICAST), BR_PKT_UNICAST, false, true, 0);
	expect(1,1,1);
	reset_output(); ports[2].flags &= ~BR_FLOOD;
	br_flood(&bridge, packet(0, BR_PKT_UNICAST), BR_PKT_UNICAST, false, true, 0);
	expect(1,0,1);
}
static void flood(void)
{
	for (int kind = BR_PKT_MULTICAST; kind <= BR_PKT_BROADCAST; kind++) {
		setup(false); enable_third();
		br_flood(&bridge, packet(0, kind), kind, false, true, 0); expect(1,1,1);
		reset_output();
		ports[2].flags &= ~(BR_MCAST_FLOOD | BR_BCAST_FLOOD);
		/* Local-origin broadcast/multicast intentionally ignores flood-off. */
		br_flood(&bridge, packet(0, kind), kind, false, true, 0); expect(1,1,1);
		reset_output();
		br_flood(&bridge, packet(1, kind), kind, false, false, 0); expect(0,0,1);
	}
}
static void mdb(void)
{
	setup(false); enable_third();
	/* Hardware's generic MC bitmap includes unrelated flood/group ports.
	 * The selected MDB contains only LAN2, with optional LAN3 router. */
	hw_mdb = (struct ppe_bridge_mdb){.vsi = 0, .ports = BIT(1) | BIT(2) | BIT(3)};
	ppe_bridge.mdb_ht = &hw_mdb; ppe_bridge_tx_flood_update(&ppe_bridge, 0);
	struct net_bridge_port_group group = {.key.port = &ports[2]};
	struct net_bridge_mdb_entry entry = {.ports = &group};
	struct net_bridge_mcast ctx = {0};
	br_multicast_flood(&entry, packet(0, BR_PKT_MULTICAST), &ctx, false, true); expect(0,1,0);
	reset_output();
	struct hlist_node router = {.port = &ports[3]}; ctx.routers = &router;
	br_multicast_flood(&entry, packet(0, BR_PKT_MULTICAST), &ctx, false, true); expect(0,1,1);
	reset_output();
	router.port = &ports[2];
	br_multicast_flood(&entry, packet(0, BR_PKT_MULTICAST), &ctx, false, true); expect(0,1,0);
	reset_output(); ctx.routers = NULL; group.flags = MDB_PG_FLAGS_BLOCKED;
	br_multicast_flood(&entry, packet(0, BR_PKT_MULTICAST), &ctx, false, true); expect(0,0,0);
	reset_output(); group.flags = 0; ports[2].flags |= BR_MULTICAST_TO_UNICAST;
	group.eth_addr[0] = 2;
	br_multicast_flood(&entry, packet(0, BR_PKT_MULTICAST), &ctx, false, true); expect(0,1,0);
}
static void vlan(void)
{
	setup(true); enable_third();
	assert(vlan_replays == 3);
	ports[1].vlan_tag = 0; ports[2].vlan_tag = 42; ports[3].vlan_allowed = false;
	br_flood(&bridge, packet(0, BR_PKT_UNICAST), BR_PKT_UNICAST, false, true, 42);
	expect(1,1,0); assert(tags[1] == 0 && tags[2] == 42 && descriptors == 2);
	for (int target = 1; target <= 2; target++) {
		reset_output();
		br_forward(&ports[target], packet(0, BR_PKT_UNICAST), false, true);
		expect(target == 1, target == 2, 0);
	}
}
static void boundaries(void)
{
	setup(false);
	br_forward(&ports[1], packet(1, BR_PKT_UNICAST), false, false); expect(0,0,0);
	reset_output(); ports[1].flags |= BR_HAIRPIN_MODE;
	br_forward(&ports[1], packet(1, BR_PKT_UNICAST), false, false); expect(1,0,0);
	reset_output();
	br_flood(&bridge, packet(1, BR_PKT_UNICAST), BR_PKT_UNICAST, false, false, 0); expect(1,1,0);
	reset_output();
	/* A hardware-forwarded RX already reached the same hardware domain.
	 * Declining TX offload must preserve RX duplicate suppression. */
	struct sk_buff *skb = packet(3, BR_PKT_UNICAST);
	skb->offload_fwd_mark = true; skb->cb.src_hwdom = 1;
	br_flood(&bridge, skb, BR_PKT_UNICAST, false, false, 0); expect(0,0,0);
	reset_output();
	ports[2].hwdom = 2;
	skb = packet(3, BR_PKT_UNICAST); skb->offload_fwd_mark = true; skb->cb.src_hwdom = 1;
	br_flood(&bridge, skb, BR_PKT_UNICAST, false, false, 0); expect(0,1,0);
	reset_output();
	ports[1].isolated = true; ports[2].state = 0;
	br_flood(&bridge, packet(0, BR_PKT_UNICAST), BR_PKT_UNICAST, false, true, 0); expect(0,0,0);
	setup(false);
	skb = packet(0, BR_PKT_MULTICAST); skb->igmp = true;
	br_flood(&bridge, skb, BR_PKT_MULTICAST, false, true, 0); expect(1,1,0);
}
static void registration(void)
{
	for (int aware = 0; aware <= 1; aware++) {
		setup(aware);
		for (int i = 1; i <= 3; i++) {
			assert(devices[i].offloaded && devices[i].txmac && ports[i].hwdom);
			assert(!(ports[i].flags & BR_TX_FWD_OFFLOAD));
			nbp_switchdev_del(&ports[i]);
			assert(!ports[i].offload_count);
			assert(!ppe_bridge_port_join(&ppe_bridge, &devices[i], &devices[0], NULL));
			assert(devices[i].offloaded && devices[i].txmac && ports[i].hwdom);
			assert(!(ports[i].flags & BR_TX_FWD_OFFLOAD));
		}
		assert(!br_switchdev_tx_fwd_offload);
		assert(registrations == 6 && vlan_replays == (aware ? 6 : 0));
	}
}
int main(int argc, char **argv)
{
	struct rlimit no_core = {0,0}; setrlimit(RLIMIT_CORE, &no_core);
	assert(argc == 2);
	if (!strcmp(argv[1], "known")) { known_to(1); known_to(2); }
	else if (!strcmp(argv[1], "known1")) known_to(1);
	else if (!strcmp(argv[1], "known2")) known_to(2);
	else if (!strcmp(argv[1], "unknown")) unknown();
	else if (!strcmp(argv[1], "flood")) flood();
	else if (!strcmp(argv[1], "mdb")) mdb();
	else if (!strcmp(argv[1], "vlan")) vlan();
	else if (!strcmp(argv[1], "boundaries")) boundaries();
	else if (!strcmp(argv[1], "registration")) registration();
	else return 2;
	return 0;
}
