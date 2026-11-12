"""Run actual bridge invalidation and notifier code with controlled kernel edges.

The host harness exercises peer moves/deletion and registration failures. It
cannot replace a target build or lockdep/traffic tests of the real workqueue.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = os.environ.get("RC01_KERNEL_SOURCE")
PPE = "drivers/net/ethernet/qualcomm/ppe/ppe_flowtable.c"


def function(source, name):
    pattern = (r"(?m)^(?:(?:static|inline)\s+)*(?:[\w]+(?:\s+|\s*\*\s*))+"
               + re.escape(name) + r"\s*\([^;]*?\)\s*\{")
    match = re.search(pattern, source)
    if not match:
        raise AssertionError(f"missing function {name}")
    start = match.end() - 1
    # Ignore braces inside comments and strings, retaining source offsets.
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda item: " " * len(item[0]), source, flags=re.S)
    depth = 1
    end = start + 1
    while depth:
        if masked[end] == "{":
            depth += 1
        elif masked[end] == "}":
            depth -= 1
        end += 1
    return source[match.start():end] + "\n"


def patch_postimage(path, filename):
    """Read complete newly added helper functions from their real patch."""
    parts = re.split(r"(?m)(?=^--- (?:a/|/dev/null))", path.read_text())
    for part in parts:
        if re.search(r"^\+\+\+ b/" + re.escape(filename) + r"(?:\s|$)", part, re.M):
            return "\n".join(line[1:] for line in part.splitlines()
                             if line.startswith((" ", "+")) and not line.startswith("+++"))
    raise AssertionError(f"missing {filename} in {path}")


def ppe_source(path):
    if KERNEL:
        return (Path(KERNEL) / PPE).read_text()
    # ppe_flowtable.c is created in 0507. Replay its actual ordered patches
    # without requiring a downloaded kernel for ordinary offline tests.
    for patch in sorted((ROOT / "target/linux/qualcommbe/patches-6.18").glob("*.patch")):
        selected = []
        for part in re.split(r"(?m)(?=^--- (?:a/|/dev/null))", patch.read_text()):
            if re.search(r"^\+\+\+ b/" + re.escape(PPE) + r"(?:\s|$)", part, re.M):
                selected.append(part)
        if selected:
            subprocess.run(["patch", "-s", "--batch", "--fuzz=0", "-p1"],
                           input="".join(selected), text=True, cwd=path,
                           capture_output=True, check=True)
    return (path / PPE).read_text()


class CompiledBridgeCode(unittest.TestCase):
    def run_c(self, source):
        compiler = shutil.which("cc")
        self.assertIsNotNone(compiler, "bridge failure-path regressions require host cc")
        with tempfile.TemporaryDirectory(prefix="rc01-bridge-c-") as temporary:
            path = Path(temporary)
            (path / "test.c").write_text(source)
            result = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                     "-Wno-unused-parameter", "-Wno-unused-function",
                                     str(path / "test.c"), "-o", str(path / "test")],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(path / "test")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ppe_peer_moves_policy_carrier_and_notifier_rollback(self):
        with tempfile.TemporaryDirectory(prefix="rc01-ppe-source-") as temporary:
            source = ppe_source(Path(temporary))
        funcs = "\n".join(function(source, name) for name in
                          ("ppe_ft_fdb_work", "ppe_ft_fdb_event", "ppe_ft_bridge_event",
                           "ppe_ft_account_event"))
        setup = function(source, "ppe_flowtable_setup")
        registration = setup[setup.index("\tret = register_netdevice_notifier"):
                             setup.index("\n\t/* The notifier reports only future")]
        destroy = function(source, "ppe_flowtable_destroy")
        teardown = destroy[destroy.index("\tunregister_switchdev_notifier"):
                           destroy.index("\tppe_ft_flow_drain")]
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint16_t u16;
#define container_of(ptr,type,member) ((type *)((char *)(ptr)-offsetof(type,member)))
#define NOTIFY_DONE 0
#define PPE_FLOW_FWD_TYPE_BRIDGE 1
#define NETDEV_UNREGISTER 1
#define NETDEV_DOWN 2
#define NETDEV_CHANGEUPPER 3
#define NETDEV_CHANGEADDR 4
#define NETDEV_CHANGEMTU 5
#define NETDEV_CHANGEINFODATA 6
#define NETDEV_CHANGE 7
#define SWITCHDEV_PORT_OBJ_ADD 10
#define SWITCHDEV_PORT_OBJ_DEL 11
#define SWITCHDEV_PORT_ATTR_SET 12
#define SWITCHDEV_FDB_ADD_TO_DEVICE 20
#define SWITCHDEV_FDB_DEL_TO_DEVICE 21
#define SWITCHDEV_OBJ_ID_PORT_VLAN 30
#define SWITCHDEV_ATTR_ID_PORT_STP_STATE 40
#define SWITCHDEV_ATTR_ID_PORT_BRIDGE_FLAGS 41
#define SWITCHDEV_ATTR_ID_BRIDGE_VLAN_FILTERING 42
#define SWITCHDEV_ATTR_ID_BRIDGE_VLAN_PROTOCOL 43
#define EAGAIN 11
#define IS_ERR(x) false
#define PTR_ERR(x) 0
struct net_device { struct net_device *master; bool bridge, vlan, carrier; };
struct notifier_block { int unused; };
struct work_struct { int pending; };
struct list_head { bool empty; };
struct rhashtable_iter { int unused; };
struct ppe_ft_flow {
    struct list_head zombie_node;
    struct { int fwd_type; } entry;
    struct net_device *path_dev[2];
    int vlan, rx_account, tx_account, invalidated;
    struct { struct { int ingress_cvid; struct { unsigned char src[6], dst[6]; } eth; } data; } rule;
};
struct variant { int num_flow_entries; };
struct ppe_flowtable;
struct ppe_device { struct variant *variant; struct ppe_flowtable *flowtable; };
struct ppe_flowtable {
    struct ppe_device *ppe_dev;
    struct ppe_ft_flow **flow_owners;
    struct { int cvid; } vlans[4];
    int lock, flows, *nexthops;
    struct notifier_block fdb_nb, bridge_nb, account_nb, ppp_nb;
    struct work_struct fdb_work;
};
struct switchdev_notifier_fdb_info { bool is_local; };
struct obj { int id; };
struct attr { int id; };
struct switchdev_notifier_port_obj_info { struct net_device *dev; struct obj *obj; };
struct switchdev_notifier_port_attr_info { struct net_device *dev; struct attr *attr; };
static struct net_device *src_port, *dst_port, *invalid_dev;
static int rtnl, flow_lock, rcu, invalid_calls, freed, rhashtable_destroyed;
static int reg_fail, reg_step, registered[4], cancellations;
static bool list_empty(const struct list_head *head) { return head->empty; }
static void rtnl_lock(void) { assert(!rtnl && !flow_lock); rtnl=1; }
static void rtnl_unlock(void) { assert(rtnl && !flow_lock); rtnl=0; }
static void mutex_lock(int *lock) { assert(!flow_lock); flow_lock=1; *lock=1; }
static void mutex_unlock(int *lock) { assert(flow_lock); flow_lock=0; *lock=0; }
static void rcu_read_lock(void) { assert(rtnl && flow_lock && !rcu); rcu=1; }
static void rcu_read_unlock(void) { assert(rcu); rcu=0; }
static struct net_device *netdev_master_upper_dev_get_rcu(struct net_device *dev) { return dev->master; }
static bool netif_is_bridge_master(struct net_device *dev) { return dev->bridge; }
static bool br_vlan_enabled(struct net_device *dev) { assert(dev->bridge); return dev->vlan; }
static struct net_device *br_fdb_find_port(struct net_device *master, const unsigned char *mac, u16 vid) {
    assert(rcu && rtnl && flow_lock); assert(vid == 0);
    return mac[0] == 1 ? src_port : dst_port;
}
static void ppe_ft_invalidate_locked(struct ppe_flowtable *ft, struct ppe_ft_flow *flow) {
    assert(flow_lock && !rcu); flow->invalidated++;
    /* Real invalidation may delete and release this owner immediately. */
    for (int i=0; i<ft->ppe_dev->variant->num_flow_entries; i++)
        if (ft->flow_owners[i] == flow) ft->flow_owners[i]=NULL;
}
static void ppe_ft_dev_invalidate(struct ppe_flowtable *ft, struct net_device *dev) {
    invalid_calls++; invalid_dev=dev;
}
static struct net_device *switchdev_notifier_info_to_dev(void *ptr) { return *(struct net_device **)ptr; }
static struct net_device *netdev_notifier_info_to_dev(void *ptr) { return ptr; }
static bool netif_carrier_ok(struct net_device *dev) { return dev->carrier; }
static void schedule_work(struct work_struct *work) { work->pending=1; }
static void cancel_work_sync(struct work_struct *work) {
    assert(!rtnl && !flow_lock && !registered[3]); work->pending=0; cancellations++;
}
static void rhashtable_walk_enter(int *table, struct rhashtable_iter *iter) {}
static void rhashtable_walk_start(struct rhashtable_iter *iter) {}
static void *rhashtable_walk_next(struct rhashtable_iter *iter) { return NULL; }
static void rhashtable_walk_stop(struct rhashtable_iter *iter) {}
static void rhashtable_walk_exit(struct rhashtable_iter *iter) {}
static void ppe_flow_account_prune(int *account, struct net_device *dev) {}
static int register_one(int id) { assert(!registered[id]); reg_step++; if (reg_step==reg_fail) return -123; registered[id]=1; return 0; }
static void unregister_one(int id) { assert(registered[id]); registered[id]=0; }
static int register_netdevice_notifier(struct notifier_block *nb) { return register_one(0); }
static void unregister_netdevice_notifier(struct notifier_block *nb) { unregister_one(0); }
static int register_switchdev_blocking_notifier(struct notifier_block *nb) { return register_one(1); }
static void unregister_switchdev_blocking_notifier(struct notifier_block *nb) { unregister_one(1); }
static int ppp_register_channel_notifier(struct notifier_block *nb) { return register_one(2); }
static void ppp_unregister_channel_notifier(struct notifier_block *nb) { unregister_one(2); }
static int register_switchdev_notifier(struct notifier_block *nb) { return register_one(3); }
static void unregister_switchdev_notifier(struct notifier_block *nb) { unregister_one(3); }
static void rhashtable_destroy(int *table) { rhashtable_destroyed++; }
static void kfree(void *ptr) { freed++; }
'''
        code += funcs
        code += ("\nstatic int register_actual(struct ppe_device *ppe_dev, struct ppe_flowtable *ft) {\n"
                 "int ret;\n" + registration + "\nreturn 0;\n}\n")
        code += "static void stop_actual(struct ppe_flowtable *ft) {\n" + teardown + "\n}\n"
        code += r'''
int main(void) {
    struct net_device bridge={.bridge=true}, input={.master=&bridge}, output={.master=&bridge}, other={0};
    struct variant variant={.num_flow_entries=5};
    struct ppe_device ppe={.variant=&variant};
    struct ppe_ft_flow *owners[5]={0};
    struct ppe_flowtable ft={.ppe_dev=&ppe, .flow_owners=owners};
    struct ppe_ft_flow learned={.zombie_node={true}, .entry={PPE_FLOW_FWD_TYPE_BRIDGE}, .path_dev={&input,&output}, .vlan=-1};
    struct ppe_ft_flow routed=learned, zombie=learned;
    learned.rule.data.eth.src[0]=1; learned.rule.data.eth.dst[0]=2;
    routed.entry.fwd_type=0; zombie.zombie_node.empty=false;
    src_port=&input; dst_port=&output;
    owners[0]=&learned; owners[1]=&routed; owners[2]=&zombie;
    ppe_ft_fdb_work(&ft.fdb_work);
    assert(!learned.invalidated && !routed.invalidated && !zombie.invalidated);
    /* Unrelated FDB learning preserves the active peer pair. */
    struct switchdev_notifier_fdb_info fdb={.is_local=false};
    ppe_ft_fdb_event(&ft.fdb_nb,SWITCHDEV_FDB_ADD_TO_DEVICE,&fdb);
    assert(ft.fdb_work.pending); ft.fdb_work.pending=0;
    ppe_ft_fdb_work(&ft.fdb_work); assert(!learned.invalidated);
    /* Move destination away, then delete source; both retire the old path. */
    dst_port=&other; ppe_ft_fdb_work(&ft.fdb_work);
    assert(learned.invalidated==1 && !owners[0]);
    owners[0]=&learned; dst_port=&output; src_port=NULL;
    ppe_ft_fdb_work(&ft.fdb_work); assert(learned.invalidated==2);
    /* Topology loss is also invalid, and zombies/routed entries remain untouched. */
    owners[0]=&learned; src_port=&input; output.master=NULL;
    ppe_ft_fdb_work(&ft.fdb_work); assert(learned.invalidated==3);
    assert(!routed.invalidated && !zombie.invalidated);
    /* A surviving stale owner must not read bridge-private VLAN state
       from a bond/VRF/non-bridge upper while rechecking topology. */
    owners[0]=&learned; input.master=&other; output.master=&other;
    ppe_ft_fdb_work(&ft.fdb_work); assert(learned.invalidated==4);
    fdb.is_local=true; ppe_ft_fdb_event(&ft.fdb_nb,SWITCHDEV_FDB_ADD_TO_DEVICE,&fdb);
    assert(!ft.fdb_work.pending);
    fdb.is_local=false; ppe_ft_fdb_event(&ft.fdb_nb,999,&fdb);
    assert(!ft.fdb_work.pending);
    ppe_ft_fdb_event(&ft.fdb_nb,SWITCHDEV_FDB_DEL_TO_DEVICE,&fdb);
    assert(ft.fdb_work.pending);
    struct obj obj={.id=SWITCHDEV_OBJ_ID_PORT_VLAN};
    struct switchdev_notifier_port_obj_info oi={.dev=&input,.obj=&obj};
    ppe_ft_bridge_event(&ft.bridge_nb,SWITCHDEV_PORT_OBJ_DEL,&oi);
    assert(invalid_calls==1 && invalid_dev==&input);
    obj.id=999; ppe_ft_bridge_event(&ft.bridge_nb,SWITCHDEV_PORT_OBJ_ADD,&oi);
    assert(invalid_calls==1);
    struct attr attr={.id=SWITCHDEV_ATTR_ID_BRIDGE_VLAN_FILTERING};
    struct switchdev_notifier_port_attr_info ai={.dev=&output,.attr=&attr};
    ppe_ft_bridge_event(&ft.bridge_nb,SWITCHDEV_PORT_ATTR_SET,&ai);
    assert(invalid_calls==2 && invalid_dev==&output);
    attr.id=999; ppe_ft_bridge_event(&ft.bridge_nb,SWITCHDEV_PORT_ATTR_SET,&ai);
    assert(invalid_calls==2);
    input.carrier=true; ppe_ft_account_event(&ft.account_nb,NETDEV_CHANGE,&input);
    assert(invalid_calls==2);
    input.carrier=false; ppe_ft_account_event(&ft.account_nb,NETDEV_CHANGE,&input);
    assert(invalid_calls==3 && invalid_dev==&input);
    /* Inject each registration failure: every successful prior registration
       is removed exactly once, before the actual failure cleanup frees ft. */
    for (int fail=1; fail<=4; fail++) {
        memset(registered,0,sizeof(registered)); reg_step=0; reg_fail=fail;
        freed=0; rhashtable_destroyed=0; ppe.flowtable=&ft;
        /* Actual setup initializes work before registering any notifier. */
        ft.fdb_work.pending=0;
        assert(register_actual(&ppe,&ft)==-123);
        for (int i=0;i<4;i++) assert(!registered[i]);
        assert(!ppe.flowtable && freed==3 && rhashtable_destroyed==1);
        assert(!ft.fdb_work.pending);
    }
    reg_step=0; reg_fail=0; ppe.flowtable=&ft;
    assert(!register_actual(&ppe,&ft));
    for (int i=0;i<4;i++) assert(registered[i]);
    stop_actual(&ft);
    for (int i=0;i<4;i++) assert(!registered[i]);
    assert(cancellations==1 && !ft.fdb_work.pending && !rtnl && !flow_lock);
    return 0;
}
'''
        self.run_c(code)

    def test_block_drain_retires_outside_rcu_and_keeps_other_blocks(self):
        # Actual 0683 count/forget/destroy/drain/release code. Hardware
        # retirement may sleep: it must see ft->lock held and no RCU reader
        # (the former rhashtable walk). This is not a lockdep/concurrency run.
        with tempfile.TemporaryDirectory(prefix="rc01-ppe-source-") as temporary:
            source = ppe_source(Path(temporary))
        funcs = "\n".join(function(source, name) for name in
                          ("ppe_ft_flow_count_inc", "ppe_ft_flow_count_dec",
                           "ppe_ft_flow_forget", "ppe_ft_flow_destroy_locked",
                           "ppe_ft_flow_drain", "ppe_ft_block_release"))
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct list_head { struct list_head *next, *prev; };
static void INIT_LIST_HEAD(struct list_head *l) { l->next=l->prev=l; }
static bool list_empty(const struct list_head *l) { return l->next==l; }
static void list_add_tail(struct list_head *n, struct list_head *h) {
    n->prev=h->prev; n->next=h; h->prev->next=n; h->prev=n; }
static void list_del_init(struct list_head *n) {
    n->prev->next=n->next; n->next->prev=n->prev; INIT_LIST_HEAD(n); }
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry_safe(pos,n,head,m) \
    for (pos=list_entry((head)->next,__typeof__(*pos),m), \
         n=list_entry(pos->m.next,__typeof__(*pos),m); &pos->m!=(head); \
         pos=n, n=list_entry(n->m.next,__typeof__(*n),m))
struct mutex { int held; };
static int rcu_depth;
static void unlock_guard(struct mutex **m) { assert((*m)->held); (*m)->held=0; }
static struct mutex *lock_guard(struct mutex *m) { assert(!m->held && !rcu_depth); m->held=1; return m; }
#define guard(type) struct mutex *__g __attribute__((cleanup(unlock_guard))) = lock_guard
#define lockdep_assert_held(m) assert((m)->held)
#define atomic_inc(v) ((*(v))++)
#define atomic_dec(v) ((*(v))--)
#define atomic_long_inc(v) ((*(v))++)
struct flow_block { int id; };
struct rhash_head { bool hashed; };
struct ppe_flow_entry { bool is_ipv6; };
struct ppe_ft_flow {
    struct rhash_head node; struct list_head active_node, zombie_node;
    struct flow_block *block; struct ppe_flow_entry entry; int ivp, vp_flow;
    bool hw_del_fails, retired, released, freed;
};
struct ppe_flowtable {
    struct mutex lock; int flows, ivp_unused;
    struct list_head active_flows, zombies;
    int ipv4_flows, ipv6_flows; long destroy_calls; void *ppe_dev;
};
struct ppe_ft_block { struct ppe_flowtable *ft; struct flow_block *block; };
static const int ppe_ft_params;
static int kfree_calls;
static void rhashtable_remove_fast(int *t, struct rhash_head *n, int p) { assert(n->hashed); n->hashed=false; }
static void ppe_vp_flow_del(void *d, int ivp, int *vp_flow) { }
static void ppe_ft_flow_owner_clear(struct ppe_flowtable *ft, struct ppe_ft_flow *f) { }
static void ppe_ft_flow_deps_put(struct ppe_flowtable *ft, struct ppe_ft_flow *f) { assert(!f->released); f->released=true; }
static bool ppe_ft_flow_hw_del(struct ppe_flowtable *ft, struct ppe_ft_flow *f) {
    /* Readback/delete can sleep: never under an RCU reader. */
    assert(ft->lock.held && !rcu_depth && !f->retired && !f->freed);
    f->retired=true; return !f->hw_del_fails; }
static void ppe_ft_flow_quarantine(struct ppe_flowtable *ft, struct ppe_ft_flow *f) {
    list_add_tail(&f->zombie_node,&ft->zombies); }
#define kfree_rcu(f,m) do { if (f) { assert(!(f)->freed); (f)->freed=true; } } while (0)
static void kfree(void *p) { kfree_calls++; }
'''
        code += funcs
        code += r'''
static void publish(struct ppe_flowtable *ft, struct ppe_ft_flow *f, struct flow_block *b, bool v6) {
    INIT_LIST_HEAD(&f->active_node); INIT_LIST_HEAD(&f->zombie_node);
    f->block=b; f->entry.is_ipv6=v6; f->node.hashed=true;
    ft->lock.held=1; ppe_ft_flow_count_inc(ft,f); ft->lock.held=0;
}
int main(void) {
    struct ppe_flowtable ft={0};
    struct flow_block b1={1}, b2={2};
    struct ppe_ft_flow a={0}, stuck={0}, gone={0}, other={0};
    INIT_LIST_HEAD(&ft.active_flows); INIT_LIST_HEAD(&ft.zombies);
    publish(&ft,&a,&b1,false); publish(&ft,&stuck,&b1,true);
    publish(&ft,&gone,&b1,false); publish(&ft,&other,&b2,false);
    stuck.hw_del_fails=true;
    assert(ft.ipv4_flows==3 && ft.ipv6_flows==1);
    /* A flow forgotten earlier (stale/replaced owner) is no longer visited. */
    ft.lock.held=1; ppe_ft_flow_forget(&ft,&gone); ft.lock.held=0;
    assert(gone.freed && !gone.retired && ft.ipv4_flows==2);

    struct ppe_ft_block *fb=&(struct ppe_ft_block){&ft,&b1};
    ppe_ft_block_release(fb);
    assert(kfree_calls==1 && !ft.lock.held && ft.destroy_calls==2);
    assert(a.retired && a.released && a.freed && !a.node.hashed);
    /* Failed hardware delete: leaves active list/counts, kept as zombie. */
    assert(stuck.retired && !stuck.released && !stuck.freed && !stuck.node.hashed);
    assert(ft.zombies.next==&stuck.zombie_node && ft.zombies.prev==&stuck.zombie_node);
    /* The other block's rule survives untouched. */
    assert(!other.retired && other.node.hashed);
    assert(ft.active_flows.next==&other.active_node && ft.active_flows.prev==&other.active_node);
    assert(ft.ipv4_flows==1 && ft.ipv6_flows==0);

    /* Final teardown drains everything left, without block accounting. */
    ppe_ft_flow_drain(&ft,NULL);
    assert(other.retired && other.freed && list_empty(&ft.active_flows));
    assert(ft.ipv4_flows==0 && ft.ipv6_flows==0 && ft.destroy_calls==2);
    assert(!stuck.freed && !list_empty(&ft.zombies) && !ft.lock.held);
    return 0;
}
'''
        self.run_c(code)

    def test_netifd_refresh_reaches_attached_ap_only(self):
        # Compile the actual helper and mock its existing setter boundary.
        # Source preparation/build verifies the other backend and caller hunks.
        path = ROOT / "package/network/config/netifd/patches/101-system-linux-refresh-attached-wireless-bridge-settings.patch"
        source = patch_postimage(path, "system-linux.c")
        helper = function(source, "system_bridge_refresh_wireless")
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
struct device { bool wireless; const char *ifname; };
static bool attached, bridge_exists;
static struct device expected, bridge;
static int calls, lookups;
static char *system_get_bridge(const char *name,char *buf,int buflen) {
    lookups++; assert(!strcmp(name,"wlan0")); return attached ? "br-lan" : NULL;
}
static struct device *device_get(const char *name,int create) {
    assert(!strcmp(name,"br-lan") && create==0); return bridge_exists ? &bridge : NULL;
}
static void system_bridge_set_wireless(struct device *br,struct device *dev) {
    assert(br==&bridge && dev==&expected); calls++;
}
'''
        code += helper
        code += r'''
int main(void) {
    expected=(struct device){.wireless=true,.ifname="wlan0"};
    attached=true; bridge_exists=true;
    system_bridge_refresh_wireless(&expected); assert(calls==1);
    system_bridge_refresh_wireless(&expected); assert(calls==2);
    attached=false; system_bridge_refresh_wireless(&expected); assert(calls==2);
    attached=true; bridge_exists=false; system_bridge_refresh_wireless(&expected); assert(calls==2);
    expected.wireless=false; lookups=0;
    system_bridge_refresh_wireless(&expected); assert(calls==2 && !lookups);
    return 0;
}
'''
        self.run_c(code)

    @unittest.skipUnless(KERNEL, "set RC01_KERNEL_SOURCE to the prepared kernel")
    def test_worker_drain_callers_do_not_hold_rtnl_or_flow_lock(self):
        ppe = Path(KERNEL) / "drivers/net/ethernet/qualcomm/ppe"
        # Both destroy entries are platform-driver callbacks, outside RTNL.
        # They may call helpers which take and release RTNL internally; those
        # calls must have returned before cancel_work_sync waits for the worker.
        source = (ppe / "ppe.c").read_text()
        callers = [function(source, name) for name in ("qcom_ppe_probe", "qcom_ppe_remove")]
        self.assertEqual(source.count("ppe_flowtable_destroy(ppe_dev);"), 2)
        for caller in callers:
            self.assertIn("ppe_flowtable_destroy(ppe_dev);", caller)
            self.assertNotIn("rtnl_lock(", caller)
            self.assertNotIn("mutex_lock(&ft->lock)", caller)
        flow = (ppe / "ppe_flowtable.c").read_text()
        setup = function(flow, "ppe_flowtable_setup")
        register = setup.index("register_switchdev_notifier(&ft->fdb_nb)")
        self.assertGreater(setup.index("rtnl_lock()"), register)
        destroy = function(flow, "ppe_flowtable_destroy")
        cancel = destroy.index("cancel_work_sync(&ft->fdb_work)")
        self.assertLess(destroy.index("unregister_switchdev_notifier(&ft->fdb_nb)"), cancel)
        self.assertNotIn("rtnl_lock(", destroy[:cancel])
        self.assertNotIn("mutex_lock(", destroy[:cancel])
        self.assertGreater(destroy.index("ppe_ft_flow_drain(ft, NULL)"), cancel)

    def test_tunnel_parameter_read_does_not_wait_for_rtnl(self):
        # Actual 0682 tunnel block and the existing pre-publication cleanup
        # tail of ppe_ft_flow_replace(); RTNL, VP, device and accounting
        # edges are stubs. This is not a lockdep or workqueue run.
        with tempfile.TemporaryDirectory(prefix="rc01-ppe-source-") as temporary:
            source = ppe_source(Path(temporary))
        replace = function(source, "ppe_ft_flow_replace")
        start = replace.index("\tif (tunnel_flow && !lwt_tunnel) {")
        stop = replace.index("\treject_reason = PPE_FT_REJECT_NAT;")
        tunnel = replace[start:stop]
        cleanup = replace[replace.index("err_prelock_ivp:"):replace.rindex("}")]
        # The read follows topology sampling and precedes the publication
        # lock; a tunnel flow is rejected before it could hold bridge RTNL.
        self.assertNotIn("rtnl_lock()", replace)
        self.assertNotIn("ft->lock", tunnel)
        self.assertLess(replace.index("mutex_unlock(&ft->lock);"), start)
        self.assertLess(stop, replace.index("mutex_lock(&ft->lock);", stop))
        self.assertNotIn("mutex_lock(", replace[replace.index("mutex_unlock(&ft->lock);"):start])
        bridge = replace[replace.index("bridge_rtnl = true;"):]
        self.assertIn("(tunnel_flow ||", bridge[:bridge.index("goto err_tdev;")])
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define EAGAIN 11
#define EOPNOTSUPP 95
#define PPE_FT_REJECT_MAX 4
struct in6_addr { uint32_t s6_addr32[4]; };
struct ip6_tnl_flow_offload_key { uint32_t addr; uint16_t port; };
struct ip6_tnl_flow_offload_info { struct in6_addr local, remote; };
struct net_device { int refs; };
struct account { int refs; };
struct ppe_flowtable { int ppe_dev; long rejects[PPE_FT_REJECT_MAX]; };
struct ppe_ft_flow {
    int vp, ivp, paths; struct net_device *egress_dev;
    struct account rx_account, tx_account;
    struct { uint16_t sport, dport; struct { uint32_t ip4; } sip, dip; } entry;
};
static int rtnl_held, rtnl_contended, offload_calls, offload_ret, vp_puts[8], frees;
static uint32_t seen_addr; static uint16_t seen_port;
static bool rtnl_trylock(void) { if (rtnl_contended) return false; assert(!rtnl_held); rtnl_held = 1; return true; }
static void rtnl_unlock(void) { assert(rtnl_held); rtnl_held = 0; }
static int dev_ip6_tnl_flow_offload(struct net_device *dev, const struct ip6_tnl_flow_offload_key *key,
                                    struct ip6_tnl_flow_offload_info *info)
{
    assert(rtnl_held && dev); offload_calls++; seen_addr = key->addr; seen_port = key->port;
    if (!offload_ret) { info->local.s6_addr32[0] = 1; info->remote.s6_addr32[0] = 2; }
    return offload_ret;
}
static bool ipv6_addr_any(const struct in6_addr *a) { return !a->s6_addr32[0]; }
static void ppe_vp_put(int dev, int vp) { (void)dev; vp_puts[vp]++; }
static void dev_put(struct net_device *dev) { dev->refs--; }
static void ppe_ft_path_put(struct ppe_ft_flow *flow) { flow->paths--; }
static void ppe_flow_account_put(struct account *account) { account->refs--; }
static void atomic_long_inc(long *v) { (*v)++; }
#define kfree_rcu(p, f) (frees += !!(p))

static int replace_tail(struct ppe_flowtable *ft, struct ppe_ft_flow *flow,
                        struct net_device *tdev, bool tunnel_flow, bool lwt_tunnel,
                        bool tunnel_encap, int *published)
{
    struct ip6_tnl_flow_offload_info tnl_info;
    int reject_reason = 2, ret = 0;
    bool bridge_rtnl = false;

    memset(&tnl_info, 0, sizeof(tnl_info));
    (void)tunnel_flow;
    /* err_free is entered by earlier, omitted rejects. */
    if (!flow) {
        goto err_free;
    }
@TUNNEL@
    *published = 1;
    return 0;
@CLEANUP@
}

static struct net_device tdev, egress;
static struct ppe_flowtable ft;
static struct ppe_ft_flow flow;
static int published;

static int run(bool contended, int result, bool lwt)
{
    memset(&ft, 0, sizeof(ft)); memset(vp_puts, 0, sizeof(vp_puts));
    flow = (struct ppe_ft_flow){ .vp = 3, .ivp = 5, .paths = 1, .egress_dev = &egress,
                                 .rx_account = { 1 }, .tx_account = { 1 } };
    flow.entry.sip.ip4 = 0x0a000001; flow.entry.dip.ip4 = 0xc0000201;
    flow.entry.sport = 1234; flow.entry.dport = 443;
    tdev.refs = 1; egress.refs = 1; rtnl_held = 0; rtnl_contended = contended;
    offload_calls = 0; offload_ret = result; frees = 0; published = 0;
    return replace_tail(&ft, &flow, &tdev, true, lwt, true, &published);
}

static void assert_cleaned(void)
{
    assert(!published && vp_puts[3] == 1 && vp_puts[5] == 1 && tdev.refs == 0);
    assert(flow.paths == 0 && flow.rx_account.refs == 0 && flow.tx_account.refs == 0);
    assert(egress.refs == 0 && frees == 1 && ft.rejects[2] == 1);
}

int main(void)
{
    /* Contended RTNL: give up this offload through the existing cleanup. */
    assert(run(true, 0, false) == -EAGAIN);
    assert(!rtnl_held && offload_calls == 0);
    assert_cleaned();

    /* Uncontended: the read runs under RTNL, released before publication. */
    assert(run(false, 0, false) == 0);
    assert(published && !rtnl_held && offload_calls == 1);
    assert(seen_addr == 0xc0000201 && seen_port == 443);
    assert(tdev.refs == 1 && vp_puts[3] == 0 && vp_puts[5] == 0 && frees == 0);

    /* An existing lookup failure still unlocks and cleans up once. */
    assert(run(false, -EOPNOTSUPP, false) == -EOPNOTSUPP);
    assert(!rtnl_held && offload_calls == 1);
    assert_cleaned();

    /* LWT tunnels do not read ip6tnl parameters. */
    assert(run(true, 0, true) == 0);
    assert(published && offload_calls == 0 && !rtnl_held);
    return 0;
}
'''.replace("@TUNNEL@", tunnel).replace("@CLEANUP@", cleanup)
        self.run_c(code)

    @unittest.skipUnless(KERNEL, "set RC01_KERNEL_SOURCE to the prepared kernel")
    def test_flow_cleanup_preserves_namespace_and_matches_both_upper_paths(self):
        source = (Path(KERNEL) / "net/netfilter/nf_flow_table_core.c").read_text()
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
struct net_device { int ifindex, net; };
struct flow_offload_tuple { int iifidx, num_uppers, upper_ifidx[8]; };
struct nf_flowtable { int unused; };
struct flow_offload { int ct, dead; struct {struct flow_offload_tuple tuple;} tuplehash[2]; };
static int nf_ct_net(int ct) { return ct; }
static int dev_net(struct net_device *dev) { return dev->net; }
static bool net_eq(int a,int b) { return a==b; }
static void flow_offload_teardown(struct flow_offload *flow) { flow->dead=1; }
'''
        code += function(source, "nf_flow_tuple_uses_dev")
        code += function(source, "nf_flow_table_do_cleanup")
        code += r'''
int main(void) {
    struct flow_offload flow={.ct=1};
    struct net_device dev={.ifindex=20,.net=1};
    flow.tuplehash[0].tuple.iifidx=2; flow.tuplehash[1].tuple.iifidx=3;
    nf_flow_table_do_cleanup(NULL,&flow,&dev); assert(!flow.dead);
    flow.tuplehash[1].tuple.num_uppers=2;
    flow.tuplehash[1].tuple.upper_ifidx[1]=20;
    nf_flow_table_do_cleanup(NULL,&flow,&dev); assert(flow.dead);
    flow.dead=0; dev.net=2;
    nf_flow_table_do_cleanup(NULL,&flow,&dev); assert(!flow.dead);
    dev.net=1; flow.tuplehash[1].tuple.upper_ifidx[1]=99;
    flow.tuplehash[0].tuple.num_uppers=1; flow.tuplehash[0].tuple.upper_ifidx[0]=20;
    nf_flow_table_do_cleanup(NULL,&flow,&dev); assert(flow.dead);
    flow.dead=0; flow.tuplehash[0].tuple.num_uppers=0;
    flow.tuplehash[1].tuple.iifidx=20;
    nf_flow_table_do_cleanup(NULL,&flow,&dev); assert(flow.dead);
    flow.dead=0; nf_flow_table_do_cleanup(NULL,&flow,NULL); assert(flow.dead);
    return 0;
}
'''
        self.run_c(code)

    @unittest.skipUnless(KERNEL, "set RC01_KERNEL_SOURCE to the prepared kernel")
    def test_routed_flows_hold_dst_and_revalidate_route_and_neighbour(self):
        # Actual 769 functions and struct definitions; dst/neighbour/bridge
        # edges are stubs. GC scheduling, teardown and HW deletion are the
        # unchanged existing paths and are not run here.
        root = Path(KERNEL)
        header = (root / "include/net/netfilter/nf_flow_table.h").read_text()
        core = (root / "net/netfilter/nf_flow_table_core.c").read_text()
        path = (root / "net/netfilter/nf_flow_table_path.c").read_text()

        def block(source, start):
            match = re.search(r"(?ms)^" + re.escape(start) + r" \{.*?^\};\n", source)
            self.assertIsNotNone(match, start)
            return match[0]
        tuple_struct = block(header, "struct flow_offload_tuple")
        # Lookup keys end at __hash; the cached route must not join them.
        self.assertLess(tuple_struct.index("__hash;"), tuple_struct.index("*route_cache;"))
        types = "".join(block(header, name) for name in
                        ("enum flow_offload_xmit_type", "struct flow_offload_tuple",
                         "struct nf_flow_route"))
        types += block(path, "struct nft_forward_info")
        funcs = "".join(function(core, name) for name in
                        ("flow_offload_dst_cookie", "nft_route_dst_fetch",
                         "flow_offload_fill_route", "nft_flow_dst_release",
                         "flow_offload_route_init", "flow_offload_route_release",
                         "nf_flow_route_valid", "nf_flow_dst_valid"))
        funcs += function(path, "nft_dev_forward_path")
        code = r'''
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16, __u16, __be16; typedef uint32_t u32;
#define ETH_ALEN 6
#define NET_DEVICE_PATH_STACK_MAX 5
#define NF_FLOW_TABLE_ENCAP_MAX 2
#define NF_FLOW_TABLE_UPPER_MAX (NET_DEVICE_PATH_STACK_MAX - 1)
#define BIT(n) (1U << (n))
#define WARN_ON_ONCE(x) assert(!(x))
#define ARPHRD_ETHER 1
#define ARPHRD_PPP 512
enum { NFPROTO_IPV4 = 2, NFPROTO_IPV6 = 10 };
enum { NUD_INCOMPLETE=1, NUD_REACHABLE=2, NUD_STALE=4, NUD_DELAY=8, NUD_PROBE=0x10,
       NUD_FAILED=0x20, NUD_NOARP=0x40, NUD_PERMANENT=0x80,
       NUD_VALID=NUD_PERMANENT|NUD_NOARP|NUD_REACHABLE|NUD_PROBE|NUD_STALE|NUD_DELAY };
enum flow_offload_tuple_dir { FLOW_OFFLOAD_DIR_ORIGINAL, FLOW_OFFLOAD_DIR_REPLY, FLOW_OFFLOAD_DIR_MAX };
enum ip_conntrack_dir { IP_CT_DIR_ORIGINAL, IP_CT_DIR_REPLY };
enum { NF_FLOW_OFFLOAD_UNSPEC, NF_FLOW_OFFLOAD_ROUTE };
struct net_device { int ifindex; unsigned short type; };
struct neighbour { int refcnt, lock; u8 nud_state; u8 ha[ETH_ALEN]; };
struct dst_entry { int refcnt; bool obsolete; u32 cookie; struct net_device *dev; struct neighbour *neigh; };
union nf_inet_addr { u32 all[4]; struct in_addr ip; struct in6_addr in6; };
struct nf_conn { struct { struct { struct { union nf_inet_addr u3; } src; } tuple; } tuplehash[2]; };
'''
        code += types + r'''
struct flow_offload { struct { struct flow_offload_tuple tuple; } tuplehash[2]; struct nf_conn *ct; int type; };
struct net_device_path_stack { int unused; };
struct net { int unused; };
struct nf_flowtable { struct net *net; };
struct nft_flowtable { struct nf_flowtable data; };
struct nf_br_utils { bool (*fdb_valid_dst)(struct net_device *, const u8 *, u16); };
static const struct nf_br_utils *nf_br_utils_hook;
static struct neighbour *current_neigh;
static const void *expected_daddr;
static int lookups, fdb_calls, rcu_depth;
static struct net_device fdb_dev = {.ifindex = 30, .type = ARPHRD_ETHER};
static bool fdb_ok;
static bool is_valid_ether_addr(const u8 *a) {
    static const u8 zero[ETH_ALEN]; return !(a[0] & 1) && memcmp(a, zero, ETH_ALEN);
}
static void ether_addr_copy(u8 *d, const u8 *s) { memcpy(d, s, ETH_ALEN); }
static bool ether_addr_equal(const u8 *a, const u8 *b) {
    assert(!current_neigh || b != current_neigh->ha || current_neigh->lock);
    return !memcmp(a, b, ETH_ALEN);
}
static u32 ip_dst_mtu_maybe_forward(struct dst_entry *d, bool f) { return 1500; }
static u32 ip6_dst_mtu_maybe_forward(struct dst_entry *d, bool f) { return 1500; }
#define dst_rt6_info(d) (d)
static u32 rt6_get_cookie(struct dst_entry *d) { return d->cookie; }
static void dst_release(struct dst_entry *d) { if (d) { assert(d->refcnt > 0); d->refcnt--; } }
static struct dst_entry *dst_check(struct dst_entry *d, u32 cookie) {
    return !d->obsolete && d->cookie == cookie ? d : NULL;
}
static struct neighbour *dst_neigh_lookup(struct dst_entry *d, const void *daddr) {
    lookups++; assert(daddr == expected_daddr);
    if (d->neigh) d->neigh->refcnt++;
    return current_neigh = d->neigh;
}
static void neigh_release(struct neighbour *n) { assert(!n->lock && n->refcnt > 0); n->refcnt--; current_neigh = NULL; }
#define read_lock_bh(l) (assert(!*(l)), *(l) = 1)
#define read_unlock_bh(l) (assert(*(l)), *(l) = 0)
#define read_pnet(p) (*(p))
#define rcu_dereference(p) (p)
static void rcu_read_lock(void) { rcu_depth++; }
static void rcu_read_unlock(void) { rcu_depth--; }
static struct net_device *dev_get_by_index_rcu(struct net *net, u32 idx) { assert(rcu_depth); return idx == 30 ? &fdb_dev : NULL; }
static struct net_device *__vlan_find_dev_deep_rcu(struct net_device *d, __be16 p, u16 id) { return d; }
static bool fdb_valid_dst(struct net_device *d, const u8 *mac, u16 vid) { fdb_calls++; return fdb_ok; }
static const struct nf_br_utils br_utils = {.fdb_valid_dst = fdb_valid_dst};
/* Path discovery edges: neighbour MAC first, then PPPoE rewrites h_dest. */
static u8 disc_ha[ETH_ALEN], disc_h_dest[ETH_ALEN];
static enum flow_offload_xmit_type disc_xmit;
static struct net_device disc_dev = {.ifindex = 7, .type = ARPHRD_ETHER};
static int nft_dev_fill_forward_path(const struct nf_flow_route *r, const struct dst_entry *d,
        const struct nf_conn *ct, enum ip_conntrack_dir dir, u8 *ha, struct net_device_path_stack *s) {
    memcpy(ha, disc_ha, ETH_ALEN); return 0;
}
static int nft_dev_path_info(const struct net_device_path_stack *s, struct nft_forward_info *info,
        unsigned char *ha, struct nf_flowtable *ft) {
    memcpy(info->h_dest, disc_h_dest, ETH_ALEN);
    info->indev = info->outdev = info->hw_outdev = &disc_dev;
    info->xmit_type = disc_xmit; return 0;
}
static bool nft_flowtable_find_dev(const struct net_device *d, struct nft_flowtable *ft) { return true; }
'''
        code += funcs + r'''
static const u8 GW[ETH_ALEN] = {2,0,0,0,0,1}, GW2[ETH_ALEN] = {2,0,0,0,0,2},
                PEER[ETH_ALEN] = {2,0,0,0,0,9}, HOST[ETH_ALEN] = {2,0,0,0,0,5};
static bool valid(struct flow_offload *f, int dir) {
    struct nf_flowtable table = {0};
    bool v = nf_flow_dst_valid(&table, f, dir);
    assert(!current_neigh && !rcu_depth);
    return v;
}
int main(void) {
    struct net_device eth = {.ifindex = 3, .type = ARPHRD_ETHER}, ppp = {.ifindex = 4, .type = ARPHRD_PPP};
    struct neighbour n0 = {.nud_state = NUD_REACHABLE}, n1 = {.nud_state = NUD_STALE};
    memcpy(n0.ha, GW, ETH_ALEN); memcpy(n1.ha, HOST, ETH_ALEN);
    nf_br_utils_hook = &br_utils;

    /* Discovery: h_neigh keeps the neighbour MAC; PPPoE h_dest stays separate. */
    struct nf_flow_route route = {0};
    struct nft_flowtable ft = {0};
    memcpy(disc_ha, GW, ETH_ALEN); memcpy(disc_h_dest, PEER, ETH_ALEN); disc_xmit = FLOW_OFFLOAD_XMIT_DIRECT;
    assert(nft_dev_forward_path(&route, NULL, IP_CT_DIR_ORIGINAL, &ft) == 0);
    assert(!memcmp(route.tuple[0].out.h_neigh, GW, ETH_ALEN) && !memcmp(route.tuple[0].out.h_dest, PEER, ETH_ALEN));
    disc_xmit = FLOW_OFFLOAD_XMIT_NEIGH;
    assert(nft_dev_forward_path(&route, NULL, IP_CT_DIR_REPLY, &ft) == 0);
    assert(!is_valid_ether_addr(route.tuple[1].out.h_neigh));

    /* IPv4 DIRECT both ways (NAT: reply source differs from original dst).
     * The routed reference moves to the flow; no extra hold. */
    struct nf_conn ct = {0};
    ct.tuplehash[1].tuple.src.u3.ip.s_addr = 0x0a000002;  /* post-NAT original-dir next hop */
    ct.tuplehash[0].tuple.src.u3.ip.s_addr = 0xc0a80105;
    struct dst_entry d0 = {.refcnt = 1, .dev = &eth, .neigh = &n0}, d1 = {.refcnt = 1, .dev = &eth, .neigh = &n1};
    struct flow_offload f = {.ct = &ct};
    f.tuplehash[0].tuple.l3proto = f.tuplehash[1].tuple.l3proto = NFPROTO_IPV4;
    memset(&route, 0, sizeof(route));
    route.tuple[0].dst = &d0; route.tuple[1].dst = &d1;
    route.tuple[0].xmit_type = route.tuple[1].xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
    memcpy(route.tuple[0].out.h_neigh, GW, ETH_ALEN); memcpy(route.tuple[0].out.h_dest, PEER, ETH_ALEN);
    memcpy(route.tuple[1].out.h_dest, HOST, ETH_ALEN);  /* legacy xt: no h_neigh */
    flow_offload_route_init(&f, &route);
    assert(f.type == NF_FLOW_OFFLOAD_ROUTE && !route.tuple[0].dst && !route.tuple[1].dst);
    assert(f.tuplehash[0].tuple.route_cache == &d0 && f.tuplehash[1].tuple.route_cache == &d1);
    assert(d0.refcnt == 1 && d1.refcnt == 1);
    assert(f.tuplehash[0].tuple.neigh_valid && !memcmp(f.tuplehash[0].tuple.neigh_ha, GW, ETH_ALEN));
    assert(!memcmp(f.tuplehash[1].tuple.neigh_ha, HOST, ETH_ALEN));

    expected_daddr = &ct.tuplehash[1].tuple.src.u3;
    assert(valid(&f, 0));
    u8 states[] = {NUD_STALE, NUD_DELAY, NUD_PROBE, NUD_PERMANENT};
    for (unsigned i = 0; i < sizeof(states); i++) { n0.nud_state = states[i]; assert(valid(&f, 0)); }
    n0.nud_state = NUD_FAILED; assert(!valid(&f, 0));
    n0.nud_state = NUD_INCOMPLETE; assert(!valid(&f, 0));
    n0.nud_state = NUD_REACHABLE; memcpy(n0.ha, GW2, ETH_ALEN); assert(!valid(&f, 0));
    memcpy(n0.ha, GW, ETH_ALEN); d0.neigh = NULL; assert(!valid(&f, 0));
    d0.neigh = &n0; d0.obsolete = true; lookups = 0; assert(!valid(&f, 0) && !lookups);
    d0.obsolete = false;
    expected_daddr = &ct.tuplehash[0].tuple.src.u3;
    assert(valid(&f, 1));
    assert(n0.refcnt == 0 && n1.refcnt == 0 && !n0.lock && !n1.lock);
    flow_offload_route_release(&f);
    assert(d0.refcnt == 0 && d1.refcnt == 0);

    /* IPv6 DIRECT: the cookie recorded at fill time invalidates on change. */
    struct dst_entry d6 = {.refcnt = 1, .cookie = 77, .dev = &eth, .neigh = &n0};
    struct dst_entry d6r = {.refcnt = 1, .cookie = 5, .dev = &eth, .neigh = &n1};
    struct flow_offload f6 = {.ct = &ct};
    f6.tuplehash[0].tuple.l3proto = f6.tuplehash[1].tuple.l3proto = NFPROTO_IPV6;
    memset(&route, 0, sizeof(route));
    route.tuple[0].dst = &d6; route.tuple[1].dst = &d6r;
    route.tuple[0].xmit_type = route.tuple[1].xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
    memcpy(route.tuple[0].out.h_neigh, GW, ETH_ALEN); memcpy(route.tuple[1].out.h_neigh, HOST, ETH_ALEN);
    flow_offload_route_init(&f6, &route);
    assert(f6.tuplehash[0].tuple.route_cookie == 77);
    expected_daddr = &ct.tuplehash[1].tuple.src.u3;
    assert(valid(&f6, 0));
    d6.cookie = 78; assert(!valid(&f6, 0));
    flow_offload_route_release(&f6);
    assert(d6.refcnt == 0 && d6r.refcnt == 0);

    /* PPPoE (non-Ethernet dst dev): route kept, no invented neighbour. */
    struct dst_entry dp = {.refcnt = 1, .dev = &ppp}, dpr = {.refcnt = 1, .dev = &eth, .neigh = &n1};
    struct flow_offload fp = {.ct = &ct};
    memset(&route, 0, sizeof(route));
    route.tuple[0].dst = &dp; route.tuple[1].dst = &dpr;
    route.tuple[0].xmit_type = FLOW_OFFLOAD_XMIT_DIRECT; route.tuple[1].xmit_type = FLOW_OFFLOAD_XMIT_NEIGH;
    memcpy(route.tuple[0].out.h_dest, PEER, ETH_ALEN);
    flow_offload_route_init(&fp, &route);
    assert(!fp.tuplehash[0].tuple.neigh_valid && fp.tuplehash[0].tuple.route_cache == &dp);
    lookups = 0; assert(valid(&fp, 0) && !lookups);
    dp.obsolete = true; assert(!valid(&fp, 0)); dp.obsolete = false;
    /* NEIGH keeps its own dst_cache and cookie check; no route_cache. */
    assert(!fp.tuplehash[1].tuple.route_cache && fp.tuplehash[1].tuple.dst_cache == &dpr);
    assert(valid(&fp, 1) && !lookups);
    dpr.obsolete = true; assert(!valid(&fp, 1));
    flow_offload_route_release(&fp);
    assert(dp.refcnt == 0 && dpr.refcnt == 0);

    /* Pure bridge flow (kzalloc'd, no route): only the existing FDB check. */
    struct flow_offload fb = {.ct = &ct};
    fb.tuplehash[0].tuple.xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
    fb.tuplehash[0].tuple.bridge_path = 1;
    fb.tuplehash[0].tuple.out.hw_ifidx = 30;
    fdb_ok = true; fdb_calls = 0; lookups = 0;
    assert(valid(&fb, 0) && fdb_calls == 1 && !lookups);
    fdb_ok = false; assert(!valid(&fb, 0) && fdb_calls == 2);
    flow_offload_route_release(&fb);  /* NULL route_cache is safe */

    /* Routed flow out through a bridge: route first, then FDB. */
    struct dst_entry db = {.refcnt = 1, .dev = &eth, .neigh = &n0}, dbr = {.refcnt = 1, .dev = &eth, .neigh = &n1};
    struct flow_offload fr = {.ct = &ct};
    memset(&route, 0, sizeof(route));
    route.tuple[0].dst = &db; route.tuple[1].dst = &dbr;
    route.tuple[0].xmit_type = route.tuple[1].xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
    route.tuple[0].out.bridge_path = 1; route.tuple[0].out.hw_ifindex = 30;
    memcpy(route.tuple[0].out.h_neigh, GW, ETH_ALEN);
    flow_offload_route_init(&fr, &route);
    expected_daddr = &ct.tuplehash[1].tuple.src.u3;
    fdb_ok = true; fdb_calls = 0;
    assert(valid(&fr, 0) && fdb_calls == 1);
    memcpy(n0.ha, GW2, ETH_ALEN); assert(!valid(&fr, 0) && fdb_calls == 1);
    flow_offload_route_release(&fr);
    assert(db.refcnt == 0 && dbr.refcnt == 0 && n0.refcnt == 0 && n1.refcnt == 0);
    return 0;
}
'''
        self.run_c(code)


if __name__ == "__main__":
    unittest.main()
