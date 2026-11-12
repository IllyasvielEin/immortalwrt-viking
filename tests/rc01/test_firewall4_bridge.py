"""Render the patched firewall4 bridge offload rules with real ucode.

fw4's own mocklib replaces fs/ubus/uci; only /sys/class/net, the bridge
master links and network.device status are fixtures. No nft, ip or ubus is
run. This checks generated rule text, not nft acceptance or packet behaviour.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "package/network/config/firewall4"
UCODE = os.environ.get("UCODE") or shutil.which("ucode")
# Pristine e6f600c tree (repository patches are applied to a copy) or the
# prepared build_dir tree, which already carries them.
SOURCE = os.environ.get("RC01_FIREWALL4_SOURCE")

# Bridge ports: br-lan holds lan1, lan2 and sfp; br-vlan holds VLAN lan1.20 on
# the same lower port lan1; br-test holds lan4.
PORTS = {"lan1": "br-lan", "lan2": "br-lan", "sfp": "br-lan",
         "lan1.20": "br-vlan", "lan4": "br-test"}


class PackageDependency(unittest.TestCase):
    def test_ibrname_meta_is_packaged(self):
        makefile = (PACKAGE / "Makefile").read_text()
        self.assertIn("+kmod-nft-bridge", makefile)
        self.assertIn("PKG_SOURCE_VERSION:=e6f600c458bda7bc2d87b07e78fd06b50c9d7131", makefile)
        # meta ibrname needs nft_meta_bridge, which only kmod-nft-bridge ships.
        netfilter = (ROOT / "include/netfilter.mk").read_text()
        self.assertTrue("call nf_add,NFT_BRIDGE,CONFIG_NFT_BRIDGE_META, $(P_EBT)nft_meta_bridge"
                        in netfilter)
        modules = (ROOT / "package/kernel/linux/modules/netfilter.mk").read_text()
        self.assertRegex(modules, r"define KernelPackage/nft-bridge\n(?:.+\n)*?\t\$\(KCONFIG_NFT_BRIDGE\)")
        # The old helper takes (devstatus, devname); no WDS prefix argument.
        patch = (PACKAGE / "patches/004-scope-bridge-flowtable-offload.patch").read_text()
        self.assertIn("+\t\t\t\tpush(ports, ...resolve_lower_devices(devstatus, entry));", patch)


@unittest.skipUnless(UCODE, "ucode is required to render firewall4")
@unittest.skipUnless(SOURCE, "set RC01_FIREWALL4_SOURCE to the firewall4 e6f600c tree")
class BridgeScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="rc01-fw4-bridge-")
        cls.addClassCleanup(cls.temporary.cleanup)
        source = Path(SOURCE)
        fw4 = source / "root/usr/share/ucode/fw4.uc"
        if "resolve_bridge_offload" not in fw4.read_text():
            copy = Path(cls.temporary.name) / "src"
            shutil.copytree(source, copy)
            for patch in sorted((PACKAGE / "patches").glob("*.patch")):
                subprocess.run(["patch", "-s", "--batch", "--fuzz=0", "-p1", "-i", str(patch)],
                               cwd=copy, check=True, capture_output=True, text=True)
            source = copy
        cls.source = source

    def render(self, defaults, ports=PORTS):
        fixture = Path(tempfile.mkdtemp(dir=self.temporary.name))
        config = json.loads((self.source / "tests/mocks/uci/firewall.json").read_text())
        config["defaults"].update(defaults)
        files = {
            "uci/firewall.json": config,
            "fs/opendir~_sys_class_net.json": ["eth0", "eth1", "br-lan", *ports],
            "fs/opendir~_sys_class_net_lan1_20.json": ["lower_lan1"],
        }
        status = json.loads((self.source / "tests/mocks/ubus/network.device~status.json").read_text())
        status["lan1.20"] = {"devtype": "vlan", "present": True}
        files["ubus/network.device~status.json"] = status
        for name, data in files.items():
            (fixture / name).parent.mkdir(parents=True, exist_ok=True)
            (fixture / name).write_text(json.dumps(data))
        links = {}
        for port, bridge in ports.items():
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", f"/sys/class/net/{port}/brport")
            (fixture / f"fs/open~{safe}.txt").write_text("1\n")
            links[f"/sys/class/net/{port}/master"] = f"../../../virtual/net/{bridge}"
        script = ("{%\nlet fs = require('fs'), links = " + json.dumps(links) + ";\n"
                  "fs.readlink = (path) => links[path];\n"
                  "include('./root/usr/share/firewall4/main.uc', {\n"
                  "\tgetenv: (name) => (name == 'ACTION') ? 'print' : null\n});\n%}\n")
        result = subprocess.run(
            [UCODE, "-S", "-T,", "-L", "./tests/lib", "-L", "./root/usr/share/ucode",
             "-D", "MOCK_SEARCH_PATH=" + json.dumps([str(fixture), "./tests/mocks"]),
             "-l", "mocklib", "-l", "fw4", "-"],
            input=script, cwd=self.source, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("[E]", result.stderr)
        start = result.stdout.index("table bridge fw4\nflush table bridge fw4\n")
        return result.stdout[start:]

    def scope(self, output):
        devices = re.search(r'flowtable fb \{.*?devices = \{? ?([^;]*?) ?\}?;', output, re.S)
        bridges = re.search(r"meta ibrname \{? ?(.*?) ?\}? meta l4proto", output)
        if not devices:
            self.assertNotIn("chain flow_offload", output)
            return None
        return (re.findall(r'"([^"]+)"', devices[1]), re.findall(r'"([^"]+)"', bridges[1]))

    def test_default_discovers_every_bridge_after_forward_filters(self):
        output = self.render({})
        self.assertIn(
            "\tchain flow_offload {\n"
            "\t\ttype filter hook forward priority 2147483647; policy accept;\n"
            '\t\tmeta ibrname { "br-lan", "br-test", "br-vlan" } meta l4proto { tcp, udp } flow offload @fb;\n'
            "\t}\n", output)
        self.assertIn('devices = { "lan1", "lan2", "lan4", "sfp" };', output)
        self.assertIn("flags offload;", output)
        self.assertNotIn("chain forward", output)

    def test_allow_exclude_and_disable(self):
        cases = [
            ({"flow_offloading_bridge_devices": ["br-lan"]},
             (["lan1", "lan2", "sfp"], ["br-lan"])),
            # VLAN bridge on lan1 stays outside: lan1 is hooked for br-lan,
            # and the ibrname match leaves br-vlan traffic uncached.
            ({"flow_offloading_bridge_devices": ["br-vlan"]}, (["lan1"], ["br-vlan"])),
            ({"flow_offloading_bridge_exclude": ["br-test"]},
             (["lan1", "lan2", "sfp"], ["br-lan", "br-vlan"])),
            # Exclusion wins over an allow entry for the same bridge.
            ({"flow_offloading_bridge_devices": ["br-lan", "br-vlan"],
              "flow_offloading_bridge_exclude": "br-vlan"}, (["lan1", "lan2", "sfp"], ["br-lan"])),
            ({"flow_offloading_bridge_devices": "br-missing"}, None),
            ({"flow_offloading_bridge": "0"}, None),
            ({"flow_offloading": "0"}, None),
        ]
        for defaults, expected in cases:
            with self.subTest(defaults=defaults):
                self.assertEqual(self.scope(self.render(defaults)), expected)

    def test_single_bridge_can_be_excluded_and_no_bridge_renders_nothing(self):
        single = {"lan1": "br-lan", "sfp": "br-lan"}
        self.assertEqual(self.scope(self.render({}, single)), (["lan1", "sfp"], ["br-lan"]))
        self.assertIsNone(self.scope(self.render({"flow_offloading_bridge_exclude": "br-lan"}, single)))
        self.assertIsNone(self.scope(self.render({"flow_offloading_bridge": "0"}, single)))
        self.assertIsNone(self.scope(self.render({}, {})))


if __name__ == "__main__":
    unittest.main()
