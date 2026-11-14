"""Execute prepared bridge/PPE/EDMA C, observing destinations and copy counts.

Kernel allocation, netfilter, VLAN admission and switchdev notification APIs
are fixtures. Forwarding, clone suppression, MDB selection, PPE registration
and descriptor construction are extracted verbatim. This is an offline host
probe, not a running kernel or hardware forwarding test.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

KERNEL = os.environ.get("RC01_KERNEL_SOURCE")
HERE = Path(__file__).resolve().parent


def function(source, name):
    match = re.search(r"(?m)^(?:static\s+)?(?:inline\s+)?(?:void|bool|int|struct \w+\s*\*)\s*"
                      + re.escape(name) + r"\([^;]*?\)\s*\{", source)
    if not match:
        raise AssertionError(f"missing actual function: {name}")
    depth = 1
    for end in range(match.end(), len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if not depth:
            return source[match.start():end + 1] + "\n"
    raise AssertionError(f"unterminated function: {name}")


def compile_harness(kernel, work, mutation=None):
    work.mkdir(parents=True, exist_ok=True)
    ppe = kernel / "drivers/net/ethernet/qualcomm/ppe"
    bridge = kernel / "net/bridge"
    switchdev = (bridge / "br_switchdev.c").read_text()
    forwarding = (bridge / "br_forward.c").read_text()
    driver = (ppe / "ppe_bridge.c").read_text()
    edma = (ppe / "edma_tx.c").read_text()
    registration = function(driver, "ppe_bridge_port_join")
    if mutation == "old-registration":
        registration, count = re.subn(r"false, extack\);", "!vlan_aware, extack);", registration)
        if count != 1:
            raise AssertionError("negative control needs the fixed registration")
    elif mutation == "edma-only":
        registration = re.sub(r"false, extack\);", "!vlan_aware, extack);", registration)
        edma = edma.replace("if (unlikely(skb->offload_fwd_mark)) {", "if (false) {")
    elif mutation == "multicast-only":
        registration = re.sub(r"false, extack\);", "!vlan_aware, extack);", registration)
        edma = edma.replace("if (unlikely(skb->offload_fwd_mark)) {",
                            "if (skb->offload_fwd_mark && is_multicast_ether_addr(skb->data)) {")
    definitions = []
    for filename, prefix in ((ppe / "edma_tx.h", "EDMA_"),
                             (ppe / "ppe_config.h", "PPE_EDMA_SC_BYPASS_ID"),
                             (kernel / "include/linux/if_bridge.h", "BR_")):
        source = filename.read_text().split("#ifdef __LP64__", 1)[0]
        definitions.extend(macro for macro in re.findall(r"(?m)^#define (?:[^\n]*\\\n)*[^\n]*", source)
                           if re.match(r"#define " + prefix, macro))
    header = (ppe / "edma_port.h").read_text()
    definitions.append(re.search(r"enum \w+\s*\{\s*EDMA_PORT_FLOOD_UC,.*?\};", header, re.S)[0])
    definitions.append(re.search(r"struct edma_txdesc_pri \{.*?^\};",
                                 (ppe / "edma_tx.h").read_text(), re.S | re.M)[0])
    (work / "prepared-types.h").write_text("\n".join(definitions) + "\n")
    functions = []
    for name in ("nbp_switchdev_can_offload_tx_fwd", "br_switchdev_frame_uses_tx_fwd_offload",
                 "br_switchdev_frame_set_offload_fwd_mark", "nbp_switchdev_frame_mark_tx_fwd_offload",
                 "nbp_switchdev_frame_mark_tx_fwd_to_hwdom", "nbp_switchdev_allowed_egress",
                 "nbp_switchdev_add", "nbp_switchdev_del"):
        functions.append(function(switchdev, name))
    functions.extend((registration, function(driver, "ppe_bridge_tx_flood_update"),
                      function(edma, "edma_tx_fill_desc")))
    (work / "prepared-driver.h").write_text("\n".join(functions))
    names = ("should_deliver", "br_dev_queue_push_xmit", "br_forward_finish", "__br_forward",
             "deliver_clone", "br_forward", "maybe_deliver", "br_flood", "maybe_deliver_addr",
             "br_multicast_flood")
    (work / "prepared-forward.h").write_text("\n".join(function(forwarding, n) for n in names))
    binary = work / "bridge-tx-flood"
    result = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                             "-Wno-unused-parameter", "-I", str(work),
                             str(HERE / "bridge_tx_flood_harness.c"), "-o", str(binary)],
                            capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return binary


@unittest.skipUnless(KERNEL and shutil.which("cc"), "set RC01_KERNEL_SOURCE to a prepared tree; host cc required")
class BridgeTxFlood(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="rc01-bridge-tx-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.path = Path(cls.temporary.name)
        cls.kernel = Path(KERNEL)
        cls.binary = compile_harness(cls.kernel, cls.path / "current")

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_known_unicast_to_each_lan_is_single_destination(self):
        self.scenario("known")

    def test_unknown_unicast_reaches_all_eligible_ports_once(self):
        self.scenario("unknown")

    def test_broadcast_and_multicast_flood_flags(self):
        self.scenario("flood")

    def test_mdb_members_and_router_ports_exclude_unrelated_ports(self):
        self.scenario("mdb")

    def test_vlan_aware_registration_and_egress_tags(self):
        self.scenario("vlan")

    def test_hairpin_and_rx_hardware_domain_duplicate_suppression(self):
        self.scenario("boundaries")

    def test_rejoin_and_vlan_replay_keep_hardware_registration(self):
        self.scenario("registration")

    def test_old_registration_reproduces_known_unicast_replication(self):
        binary = compile_harness(self.kernel, self.path / "old", "old-registration")
        for scenario in ("known1", "known2"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(binary), scenario], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("word4=0x30060000", result.stderr)

    def test_edma_only_and_multicast_only_fixes_lose_unknown_unicast(self):
        for mutation in ("edma-only", "multicast-only"):
            with self.subTest(mutation=mutation):
                binary = compile_harness(self.kernel, self.path / mutation, mutation)
                result = subprocess.run([str(binary), "unknown"], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("copies=0 expected=1", result.stderr)

    def test_ppe_has_no_uncovered_offload_or_replay_registration(self):
        # Guard the scope audit: a new call site requires a runtime scenario,
        # rather than silently leaving another capability registration enabled.
        calls = []
        ppe = self.kernel / "drivers/net/ethernet/qualcomm/ppe"
        for source in ppe.glob("*.c"):
            text = re.sub(r"/\*.*?\*/|//[^\n]*", "", source.read_text(), flags=re.S)
            for name in re.findall(r"\b(switchdev_bridge_port_(?:offload|replay))\s*\(", text):
                calls.append((source.name, name))
        self.assertEqual(calls, [("ppe_bridge.c", "switchdev_bridge_port_offload")])


if __name__ == "__main__":
    unittest.main()
