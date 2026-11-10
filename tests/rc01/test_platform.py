"""Source contracts and final DTS checks; never opens a device or reads ART."""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rc01_platform", ROOT / "scripts/validate-rc01-platform.py")
platform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform)
images = platform.images
KERNEL = os.environ.get("RC01_KERNEL_SOURCE")
WIRELESS = os.environ.get("RC01_WIRELESS_SOURCE")


class PlatformSources(unittest.TestCase):
    def test_calibration_paths_are_per_card_and_within_art(self):
        script = (ROOT / "target/linux/qualcommbe/ipq95xx/base-files/etc/hotplug.d/firmware/11-ath12k-caldata").read_text()
        script = script.replace('[ -e /lib/firmware/$FIRMWARE ] && exit 0', '')
        script = script.replace('. /lib/functions/caldata.sh', '')
        for hw in ("1", "2"):
            for card, offset in ((1, 0x1000), (2, 0x33000), (3, 0x65000)):
                env = os.environ | {"FIRMWARE": f"ath12k/QCN9274/hw{hw}.0/cal-pci-000{card}:01:00.0.bin"}
                stubs = 'board_name() { echo xiaomi,be10000; }; caldata_extract() { printf "%s %s %s\\n" "$1" "$2" "$3"; };\n'
                result = subprocess.run(["sh", "-c", stubs + script], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"0:art {hex(offset)} 0x2d000")
                self.assertLessEqual(offset + 0x2D000, 0x92000)

    def test_bdf_contains_the_three_rc01_board_ids(self):
        blob = (ROOT / "package/firmware/ipq-wifi/src/board-xiaomi_be10000.qcn9274").read_bytes()
        self.assertTrue(blob.startswith(b"QCA-ATH12K-BOARD"))
        ids = re.findall(rb"bus=pci,qmi-chip-id=0,qmi-board-id=(\d+),variant=Xiaomi-BE10000(?!-)", blob)
        self.assertCountEqual(ids, [b"1", b"4", b"2"])

    def test_envtools_keeps_five_argument_64k_mapping(self):
        source = (ROOT / "package/boot/uboot-tools/uboot-envtools/files/qualcommbe_ipq95xx").read_text()
        case = source[source.index('case "$board" in'):source.index('\nesac') + 6]
        stubs = 'board=xiaomi,be10000; ubootenv_add_mtd() { printf "%s\\n" "$@"; };\n'
        result = subprocess.run(["sh", "-c", stubs + case], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["0:appsblenv", "0x0", "0x10000", "0x20000", "2"])

    def test_wlan_board_registration_keeps_independent_cards(self):
        source = (ROOT / "target/linux/qualcommbe/ipq95xx/base-files/etc/board.d/02_network").read_text()
        function = re.search(r"ipq95xx_setup_wlan\(\)\s*\{.*?^\}", source, re.S | re.M)[0]
        script = 'DEV_PATH_PCIE0=pci1; DEV_PATH_PCIE1=pci2; DEV_PATH_PCIE2=pci3;\n'
        script += 'ucidef_add_wlan() { printf "%s:%s\\n" "$#" "$*"; };\n' + function
        script += '\nipq95xx_setup_wlan xiaomi,be10000\n'
        result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["1:pci1", "1:pci2", "1:pci3"])


@unittest.skipUnless(KERNEL and WIRELESS and shutil.which("dtc"), "set RC01_KERNEL_SOURCE and RC01_WIRELESS_SOURCE to prepared trees")
class PreparedPlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="rc01-platform-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.path = Path(cls.temp.name) / "rc01.dtb"
        platform.compile_dtb(Path(KERNEL), cls.path)
        cls.blob = cls.path.read_bytes()

    def compile(self, source):
        return subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-"], input=source,
                              capture_output=True, check=True).stdout

    def test_backport_socket_headers_cover_stable_and_mainline_apis(self):
        # Compile the actual prepared wrappers. The native API fragments come
        # from the prepared 6.18.60 headers; only unrelated kernel dependencies
        # are stubbed. Stable 6.18.57 gained the type without kernel_connect's
        # later mainline signature change, so these are separate boundaries.
        kernel = Path(KERNEL)
        wireless = Path(WIRELESS)
        native_socket = (kernel / "include/linux/socket.h").read_text()
        declaration = re.search(r"struct sockaddr_unsized \{[^}]+\};", native_socket)[0]
        prototype = re.search(r"int kernel_connect\([^;]+;",
                              (kernel / "include/linux/net.h").read_text(), re.S)[0]
        self.assertIn("struct sockaddr *addr", prototype)
        compiler = shutil.which("cc")
        self.assertIsNotNone(compiler, "post-build API regression requires host cc")
        versions = [((6, 12, 0), False, False), ((6, 18, 56), False, False),
                    ((6, 18, 57), True, False), ((6, 18, 60), True, False),
                    ((6, 19, 0), True, True), ((7, 2, 0), True, True)]
        for version, native_type, native_connect in versions:
            with self.subTest(version=version), tempfile.TemporaryDirectory(prefix="rc01-socket-api-") as temporary:
                root = Path(temporary)
                wrappers = root / "backport/linux"
                wrappers.mkdir(parents=True)
                for name in ("socket.h", "net.h", "version.h"):
                    shutil.copyfile(wireless / "backport-include/linux" / name, wrappers / name)
                native = root / "native/linux"
                native.mkdir(parents=True)
                code = version[0] * 65536 + version[1] * 256 + version[2]
                (native / "version.h").write_text(
                    f"#define LINUX_VERSION_CODE {code}\n"
                    "#define KERNEL_VERSION(a,b,c) (((a)<<16)+((b)<<8)+(c))\n")
                (native / "socket.h").write_text(
                    "#ifndef RC01_NATIVE_SOCKET_H\n#define RC01_NATIVE_SOCKET_H\n"
                    "typedef unsigned short __kernel_sa_family_t;\n"
                    "struct sockaddr { __kernel_sa_family_t sa_family; char sa_data[14]; };\n"
                    # This macro predates sockaddr_unsized and cannot serve as
                    # a feature test for the type, including on the old API.
                    "#define __sockaddr_check_size(size) ((void)0)\n"
                    + (declaration if native_type else "") + "\n#endif\n")
                connect = prototype.replace("struct sockaddr *addr", "struct sockaddr_unsized *addr") if native_connect else prototype
                (native / "net.h").write_text("#include <linux/socket.h>\nstruct socket;\n" + connect + "\n")
                (native / "static_key.h").write_text("")
                probe = root / "probe.c"
                probe.write_text(
                    '#include <stddef.h>\n#include <linux/net.h>\n'
                    '_Static_assert(offsetof(struct sockaddr_unsized, sa_data) == sizeof(__kernel_sa_family_t), "bad socket layout");\n'
                    'int probe(struct socket *sock, struct sockaddr_unsized *addr) {\n'
                    ' return kernel_connect(sock, addr, sizeof(*addr), 0);\n}\n')
                result = subprocess.run([compiler, "-std=gnu11", "-Werror", "-I", str(root / "backport"),
                                         "-I", str(root / "native"), "-c", str(probe), "-o", str(root / "probe.o")],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepared_cve_nand_pcs_and_wireless(self):
        self.assertEqual(platform.verify_kernel(Path(KERNEL))["cve_2026_98432_destructors"], 2)
        self.assertFalse(platform.verify_wireless(Path(WIRELESS))["hw1_mlo"])

    def test_actual_project_dtb(self):
        images.verify_thermal_dtb(self.blob)
        images.verify_ppe_dtb(self.blob)
        result = images.verify_platform_dtb(self.blob)
        self.assertEqual(result["cma_mib"], 320)
        self.assertEqual(result["radio_board_ids"], [1, 4, 2])

    def test_real_reserved_regions_bound_the_image(self):
        tree = images.Fdt(self.blob)
        for _, address, size in images.reserved_regions(tree):
            with self.subTest(address=hex(address)):
                with self.assertRaises(images.VerificationError):
                    images.verify_kernel_memory(tree, address, 1)
                with self.assertRaises(images.VerificationError):
                    images.verify_kernel_memory(tree, address - 1, 2)
        images.verify_kernel_memory(tree, 0x41000000, 0x49B00000 - 0x41000000)
        # The old 43 MiB Q6 carveout is now ordinary memory on PCIe-only RC01.
        images.verify_kernel_memory(tree, 0x4AB00000, 0x2B00000)

    def test_damaged_compiled_board_contracts_are_rejected(self):
        # fdtput edits the compiled tree itself, covering real phandles and paths.
        tree = images.Fdt(self.blob)
        one = lambda suffix: next(n for n in tree.nodes if n.endswith(suffix))
        variants = [("/chosen", "bootargs-append", "s", [" root=/dev/ubiblock0_1 cma=64M"]),
                    (one("/ethernet-phy@0"), "reset-assert-us", "i", ["1"]),
                    ("/reserved-memory/tzapp@49b00000", "reg", "x", ["0", "49b00000", "0", "500000"]),
                    (one("/pcie@18000000"), "perst-gpios", "x", ["0", "20", "1"])]
        for index, (node, prop, kind, values) in enumerate(variants):
            with self.subTest(prop=prop):
                path = Path(self.temp.name) / f"damaged-{index}.dtb"
                path.write_bytes(self.blob)
                subprocess.run(["fdtput", "-t", kind, str(path), node, prop, *values], check=True, capture_output=True)
                with self.assertRaises(images.VerificationError):
                    images.verify_platform_dtb(path.read_bytes())

    def test_slab_and_remoteproc_ownership_are_checked_in_compiled_dtb(self):
        tree = images.Fdt(self.blob)
        radio = next(n for n in tree.nodes if tree.nodes[n].get("compatible") == b"pci17cb,1109\0")
        remote = next(n for n in tree.nodes if tree.nodes[n].get("compatible") == b"qcom,ipq9574-wcss-sec-pil\0")
        variants = [(["-d", radio, "qcom,rx-skb-slab"], None),
                    (["-t", "s", remote, "status", "okay"], None),
                    (["-t", "x", remote, "memory-region", "dead"], None),
                    (["-t", "x", radio, "qcom,ath12k-fw-memory-mode", "1"], None)]
        for index, (arguments, _) in enumerate(variants):
            with self.subTest(arguments=arguments):
                path = Path(self.temp.name) / f"ownership-{index}.dtb"
                path.write_bytes(self.blob)
                # fdtput options precede the file, followed by node/property.
                options = arguments[:1] if arguments[0] == "-d" else arguments[:2]
                tail = arguments[len(options):]
                subprocess.run(["fdtput", *options, str(path), *tail], check=True, capture_output=True)
                with self.assertRaises(images.VerificationError):
                    images.verify_platform_dtb(path.read_bytes())

    def test_old_ds_gate_and_missing_ingress_configuration_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="rc01-wireless-reject-") as temporary:
            copied = Path(temporary) / "wireless"
            shutil.copytree(Path(WIRELESS) / "drivers/net/wireless/ath/ath12k",
                            copied / "drivers/net/wireless/ath/ath12k")
            platform.verify_wireless(copied)
            ds = copied / "drivers/net/wireless/ath/ath12k/wifi7/ppe_ds.c"
            ds.write_text(ds.read_text().replace("0x10098b57", "0x1003009f").replace("WBE.1.0-02903", "WBE.1.0.r4-00159"))
            with self.assertRaises(images.VerificationError):
                platform.verify_wireless(copied)
            config = Path(temporary) / ".config"
            text = "CONFIG_NETFILTER=y\nCONFIG_NETFILTER_INGRESS=y\nCONFIG_NET_INGRESS=y\nCONFIG_NF_FLOW_TABLE=m\n"
            config.write_text(text)
            platform.verify_kernel_config(config)
            config.write_text(text.replace("CONFIG_NETFILTER_INGRESS=y", "# CONFIG_NETFILTER_INGRESS is not set"))
            with self.assertRaises(images.VerificationError):
                platform.verify_kernel_config(config)


if __name__ == "__main__":
    unittest.main()
