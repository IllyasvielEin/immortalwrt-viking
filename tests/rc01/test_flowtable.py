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
        self.assertGreater(destroy.index("ppe_ft_flow_drain(ft)"), cancel)

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


if __name__ == "__main__":
    unittest.main()
