"""honk, always built: configuration, controlled patch, validators, first boot and CI contract; offline."""
from pathlib import Path
import hashlib
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clean_env import clean_environ  # noqa: E402
import prepare_fixture as fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG = ROOT / "defconfig/qualcommbe-rc01.config"
PATCH = ROOT / "config/rc01/patches/honk/100-rc01-honk-source.patch"
UPSTREAM = ROOT / "tests/rc01/fixtures/daeuniverse/honk"
WORKFLOW = (ROOT / ".github/workflows/build-rc01.yml").read_text()
FEED_PIN = "e104a4930c5fc4f625f3355257626495685424f5"
HONK_TAG = "v0.1.0-beta.6"
HONK_COMMIT = "64848881b7304e250e7a25422f4f1d86a5975eca"
HONK_SHA256 = "cfddedeafa349d0bdfaebd792d6f15c9f31de46a051e5a0692749a74f69d8e6d"

EXPECTED_HONK_CONFIG = """\
CONFIG_KERNEL_DEBUG_INFO_BTF=y
CONFIG_KERNEL_CGROUPS=y
CONFIG_KERNEL_CGROUP_BPF=y
CONFIG_PACKAGE_kmod-sched-bpf=y
CONFIG_PACKAGE_kmod-veth=y
CONFIG_PACKAGE_kmod-nft-queue=y
CONFIG_PACKAGE_kmod-nfnetlink-queue=y
CONFIG_PACKAGE_libstdcpp=y
CONFIG_PACKAGE_v2ray-geoip=y
CONFIG_PACKAGE_v2ray-geosite=y
# CONFIG_PACKAGE_vmlinux-btf is not set
CONFIG_PACKAGE_honk=y
CONFIG_PACKAGE_luci-app-honk=y
CONFIG_PACKAGE_luci-lib-daeuniverse=y
CONFIG_PACKAGE_luci-i18n-daeuniverse-zh-cn=y
""".splitlines()


# Top-level env of .github/workflows/build-rc01.yml that a test process may inherit, plus the removed switch.
CI_ENVIRONMENT = {"RC01_GO_BOOTSTRAP_MODE": "external", "RC01_WITH_HONK": "true", "RC01_HONK_TAG": HONK_TAG,
                  "RC01_HONK_COMMIT": HONK_COMMIT, "RC01_HONK_RUST_STABLE": "1.99.1"}


def settings(path):
    return [line for line in path.read_text().splitlines() if line and not line.startswith("# ") or line.startswith("# CONFIG_")]


def symbol_of(line):
    return re.fullmatch(r"(?:# )?(CONFIG_\S+?)(?:=\S*| is not set)", line)[1]


def variable(text, name):
    values = [line.split(":=", 1)[1] for line in text.splitlines() if line.startswith(name + ":=")]
    if len(values) != 1:
        raise AssertionError(f"expected one {name}, got {values}")
    return values[0]


HONK_SYMBOLS = [symbol_of(line) for line in EXPECTED_HONK_CONFIG if line.endswith("=y")]


def selection(without=(), extra=""):
    """Everything the validator requires plus the reviewed honk selection, minus `without`."""
    script = (ROOT / "scripts/validate-rc01-config.sh").read_text()
    required = re.search(r"for symbol in \\\n(.*?)\ndo\n", script, re.S)[1]
    symbols = dict.fromkeys(re.findall(r"CONFIG_[\w-]+", required) + HONK_SYMBOLS)
    return "".join(f"{symbol}=y\n" for symbol in symbols if symbol not in without) + (
        'CONFIG_FEED_nikki=m\nCONFIG_FEED_daeuniverse=m\nCONFIG_GOLANG_BUILD_BOOTSTRAP=y\n'
        'CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT=""\n') + extra


class HonkBaseConfiguration(unittest.TestCase):
    def test_honk_selection_is_part_of_the_base_configuration_and_the_fragment_is_gone(self):
        self.assertFalse((ROOT / "config/rc01/honk.config").exists())
        lines = settings(BASE_CONFIG)
        for line in EXPECTED_HONK_CONFIG:
            with self.subTest(line=line):
                self.assertEqual(lines.count(line), 1)

    def test_no_symbol_is_stated_twice_or_contradictorily(self):
        names = [symbol_of(line) for line in settings(BASE_CONFIG)]
        self.assertEqual([name for name in set(names) if names.count(name) > 1], [])

    def test_unneeded_kernel_and_host_features_are_not_requested(self):
        text = BASE_CONFIG.read_text()
        for symbol in ("XDP_SOCKETS", "BPF_STREAM_PARSER", "KPROBES", "KPROBE_EVENTS", "BPF_EVENTS",
                       "BPF_TOOLCHAIN_HOST", "USE_LLVM_HOST", "kmod-xdp-sockets-diag"):
            self.assertNotIn(symbol, text)
        # Neither dae nor the vmlinux-btf packages come with honk.
        for package in ("dae", "daed", "luci-app-dae", "vmlinux-btf-builtin"):
            self.assertNotRegex(text, rf"^CONFIG_PACKAGE_{package}=", re.M)
        self.assertNotRegex(text, r"^CONFIG_PACKAGE_vmlinux-btf=", re.M)

    def test_every_symbol_exists_in_this_tree(self):
        kernel = (ROOT / "config/Config-kernel.in").read_text()
        modules = "".join(path.read_text() for path in (ROOT / "package/kernel/linux/modules").glob("*.mk"))
        for line in EXPECTED_HONK_CONFIG:
            name = symbol_of(line)[len("CONFIG_"):]
            with self.subTest(symbol=name):
                if name.startswith("KERNEL_"):
                    self.assertRegex(kernel, rf"(?m)^\s*config {name}$", "kernel option is not defined")
                elif name.startswith("PACKAGE_kmod-"):
                    self.assertIn(f"define KernelPackage/{name[len('PACKAGE_kmod-'):]}\n", modules)

    def test_honk_is_selected_and_the_feed_is_source_only(self):
        base = BASE_CONFIG.read_text()
        self.assertIn("CONFIG_FEED_daeuniverse=m\n", base)
        for package in ("honk", "luci-app-honk", "luci-lib-daeuniverse", "luci-i18n-daeuniverse-zh-cn"):
            self.assertIn(f"CONFIG_PACKAGE_{package}=y\n", base)
        self.assertNotIn("vmlinux-btf=y", base)
        # Full DWARF stays the base's own, never reduced.
        self.assertIn("CONFIG_KERNEL_DEBUG_INFO=y\n", base)
        self.assertNotIn("CONFIG_KERNEL_DEBUG_INFO_REDUCED=y", base)


class HonkPatchContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = (UPSTREAM / "Makefile").read_text()
        cls.patch = PATCH.read_text()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-honk-patch-")
        self.addCleanup(temporary.cleanup)
        self.feed = Path(temporary.name) / "feed"
        (self.feed / "honk").mkdir(parents=True)
        (self.feed / "honk/Makefile").write_text(self.upstream)
        subprocess.run(["git", "init", "--quiet", str(self.feed)], check=True)
        self.work = Path(temporary.name)

    def git(self, *arguments, check=True):
        return subprocess.run(["git", "-C", str(self.feed), *arguments], capture_output=True, text=True, check=check)

    def patched(self):
        self.git("apply", "--check", str(PATCH))
        self.git("apply", str(PATCH))
        return (self.feed / "honk/Makefile").read_text()

    def test_upstream_fixture_is_the_pinned_file(self):
        self.assertEqual(hashlib.sha256(self.upstream.encode()).hexdigest(),
                         "43605dd59d437727577edde4706416d2ac44ec3e537ad822b946cccdb754857f")
        self.assertEqual(variable(self.upstream, "PKG_RELEASE"), "2")
        self.assertIn("+vmlinux-btf", self.upstream)

    def test_patch_applies_to_the_pinned_recipe_once_and_reverses(self):
        patched = self.patched()
        self.assertNotEqual(patched, self.upstream)
        self.assertEqual(self.git("apply", "--reverse", "--check", str(PATCH), check=False).returncode, 0)
        self.assertNotEqual(self.git("apply", "--check", str(PATCH), check=False).returncode, 0)

    def test_patch_touches_only_the_honk_recipe(self):
        self.assertEqual(re.findall(r"^diff --git .*|^--- .*|^\+\+\+ .*", self.patch, re.M),
                         ["--- a/honk/Makefile", "+++ b/honk/Makefile"])

    def test_vmlinux_btf_is_no_longer_a_dependency(self):
        patched = self.patched()
        depends = re.search(r"^  DEPENDS:=(.*?)\nendef", patched, re.M | re.S)[1]
        self.assertNotIn("vmlinux-btf", depends)
        for dependency in ("+libstdcpp", "+kmod-veth", "+kmod-sched-core", "+kmod-sched-bpf", "+kmod-nft-core",
                           "+kmod-nft-queue", "+kmod-nfnetlink-queue", "+kmod-nf-conntrack",
                           "+v2ray-geoip", "+v2ray-geosite"):
            self.assertIn(dependency, depends)
        self.assertIn("/sys/kernel/btf/vmlinux", patched)

    def test_source_is_the_hashed_upstream_tag_tarball(self):
        patched = self.patched()
        self.assertEqual(variable(patched, "PKG_VERSION"), "0.1.0_beta6")
        self.assertEqual(variable(patched, "PKG_RELEASE"), "3")
        self.assertEqual(variable(patched, "HONK_GIT_TAG"), HONK_TAG)
        self.assertEqual(variable(patched, "PKG_SOURCE"), "$(PKG_NAME)-$(PKG_VERSION).tar.gz")
        self.assertEqual(variable(patched, "PKG_SOURCE_URL"),
                         "https://codeload.github.com/daeuniverse/honk/tar.gz/$(HONK_GIT_TAG)?")
        self.assertEqual(variable(patched, "PKG_HASH"), HONK_SHA256)
        self.assertNotIn("PKG_SOURCE_PROTO", patched)

    def test_no_external_source_tree_or_version_override_remains(self):
        patched = self.patched()
        for text in ("$(PKG_DIR)/src", "HONK_SRC:=", "define Build/Prepare", "$(if $(HONK_", "git clone"):
            self.assertNotIn(text, patched)
        # The upstream build script is still the compile step, driven by the package's own values.
        compile_step = re.search(r"define Build/Compile\n(.*?)\nendef", patched, re.S)[1]
        for text in ('HONK_SRC="$(PKG_BUILD_DIR)"', 'HONK_VERSION="$(PKG_VERSION)"', 'HONK_GIT_TAG="$(HONK_GIT_TAG)"',
                     'sh "$(PKG_DIR)/build.sh"'):
            self.assertIn(text, compile_step)

    @unittest.skipUnless(shutil.which("make"), "make is required to expand the recipe")
    def test_make_expands_the_build_directory_to_the_tarball_top_directory(self):
        patched = self.patched()
        top = self.work / "top"
        (top / "include").mkdir(parents=True)
        (top / "rules.mk").write_text("BUILD_DIR:=/build\nINCLUDE_DIR:=$(TOPDIR)/include\n")
        (top / "include/package.mk").write_text("BuildPackage=\n")
        (self.work / "print.mk").write_text("print:\n\t@echo '$(PKG_BUILD_DIR)'\n\t@echo '$(PKG_SOURCE)'\n"
                                            "\t@echo '$(PKG_SOURCE_URL)'\n\t@echo '$(PKG_HASH)'\n")
        result = subprocess.run(["make", "-s", "-f", str(self.feed / "honk/Makefile"), "-f", str(self.work / "print.mk"),
                                 f"TOPDIR={top}", "print"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # codeload unpacks honk-0.1.0-beta.6/ (verified on the real tarball); OpenWrt unpacks next to PKG_BUILD_DIR.
        self.assertEqual(result.stdout.splitlines(), [
            "/build/honk-0.1.0-beta.6", "honk-0.1.0_beta6.tar.gz",
            "https://codeload.github.com/daeuniverse/honk/tar.gz/v0.1.0-beta.6?", HONK_SHA256])
        self.assertIn("PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(patsubst v%,%,$(HONK_GIT_TAG))", patched)

    def test_provenance_header_agrees_with_the_recipe_and_the_workflow(self):
        header = self.patch.split("--- a/honk/Makefile", 1)[0]
        for text in (FEED_PIN, HONK_TAG, HONK_COMMIT, HONK_SHA256, "honk-0.1.0-beta.6/",
                     "https://codeload.github.com/daeuniverse/honk/tar.gz/" + HONK_TAG):
            self.assertIn(text, header)
        self.assertEqual(variable(self.patched(), "HONK_GIT_TAG"), re.search(r"^  RC01_HONK_TAG: (\S+)$", WORKFLOW, re.M)[1])
        self.assertEqual(HONK_COMMIT, re.search(r"^  RC01_HONK_COMMIT: (\S+)$", WORKFLOW, re.M)[1])
        feeds = (ROOT / "config/rc01/feeds.conf").read_text()
        self.assertIn(f"src-git daeuniverse https://github.com/daeuniverse/openwrt-packages.git^{FEED_PIN}\n", feeds)


class HonkConfigValidation(unittest.TestCase):
    """Run the real validator on a tree whose package/feeds links we control."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-honk-validate-")
        self.addCleanup(temporary.cleanup)
        self.project = fixture.ValidationProject(temporary.name)

    selection = staticmethod(selection)

    def validate(self, text, **environment):
        return self.project.validate(text, environment)

    def test_complete_selection_passes(self):
        result = self.validate(self.selection())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RC01 configuration validation passed", result.stdout)
        self.assertNotIn("variant", result.stdout)

    def test_every_honk_symbol_is_required(self):
        self.assertGreaterEqual(len(HONK_SYMBOLS), 14)
        for symbol in HONK_SYMBOLS:
            with self.subTest(missing=symbol):
                result = self.validate(self.selection(without=(symbol,)))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Required symbol is not enabled: {symbol}", result.stderr)

    def test_a_module_or_disabled_honk_package_is_not_enough(self):
        for symbol in HONK_SYMBOLS:
            for line in (f"{symbol}=m\n", f"# {symbol} is not set\n"):
                with self.subTest(symbol=symbol, line=line):
                    result = self.validate(self.selection(without=(symbol,), extra=line))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Required symbol is not enabled: {symbol}", result.stderr)

    def test_full_debug_info_is_required_and_reduced_is_rejected(self):
        # The base configuration owns these symbols; honk adds only BTF on top.
        text = self.selection()
        self.assertIn("CONFIG_KERNEL_DEBUG_INFO=y", text)
        self.assertEqual(self.validate(text).returncode, 0)
        result = self.validate(text + "CONFIG_KERNEL_DEBUG_INFO_REDUCED=y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("crash symbols need full debug information", result.stderr)
        missing = self.validate(self.selection(without=("CONFIG_KERNEL_DEBUG_INFO",)))
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Required symbol is not enabled: CONFIG_KERNEL_DEBUG_INFO", missing.stderr)

    def test_dae_and_every_vmlinux_btf_form_are_rejected(self):
        for package in ("dae", "daed", "luci-app-dae", "luci-app-daed", "luci-app-daede", "vmlinux-btf", "vmlinux-btf-builtin"):
            for value in ("y", "m"):
                with self.subTest(package=package, value=value):
                    result = self.validate(self.selection(extra=f"CONFIG_PACKAGE_{package}={value}\n"))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Unwanted package selected: {package}", result.stderr)

    def test_honk_must_come_from_the_pinned_daeuniverse_feed(self):
        self.project.link("other")
        result = self.validate(self.selection())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned daeuniverse source feed", result.stderr)

    def test_a_missing_feed_link_is_rejected(self):
        (self.project.root / "package/feeds/daeuniverse/honk").unlink()
        result = self.validate(self.selection())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned daeuniverse source feed", result.stderr)

    def test_the_removed_switch_does_not_influence_the_validator(self):
        for value in ("true", "false", "garbage"):
            with self.subTest(value=value):
                result = self.validate(self.selection(), RC01_WITH_HONK=value)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_daeuniverse_binary_feed_must_not_be_enabled(self):
        for text in (self.selection().replace("CONFIG_FEED_daeuniverse=m\n", ""),
                     self.selection().replace("CONFIG_FEED_daeuniverse=m", "CONFIG_FEED_daeuniverse=y")):
            result = self.validate(text)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("daeuniverse binary feed", result.stderr)


class HonkKernelValidation(unittest.TestCase):
    SCRIPT = ROOT / "scripts/validate-rc01-honk-kernel.sh"
    BUILTIN = "BPF BPF_SYSCALL BPF_JIT CGROUPS CGROUP_BPF NET_CLS_ACT NET_XGRESS DEBUG_INFO_BTF NET_NS NF_TABLES_INET".split()
    EITHER = "VETH NF_TABLES NFT_CT NFT_QUEUE NETFILTER_NETLINK_QUEUE NET_SCH_INGRESS NET_CLS_BPF".split()

    @classmethod
    def setUpClass(cls):
        if not (shutil.which("objcopy") and shutil.which("readelf")):
            raise unittest.SkipTest("objcopy and readelf are required to make and read an ELF with a .BTF section")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-honk-kernel-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        payload = self.work / "payload"
        payload.write_bytes(b"BTF fixture\n")
        self.elf = {}
        for name, rename in (("btf", ["--rename-section", ".data=.BTF"]), ("plain", [])):
            self.elf[name] = self.work / f"vmlinux-{name}"
            subprocess.run(["objcopy", "-I", "binary", "-O", "elf64-little", "-B", "i386:x86-64", *rename,
                            str(payload), str(self.elf[name])], check=True)

    def config(self, drop=(), extra="", modules=("m",) * 7):
        lines = [f"CONFIG_{s}=y" for s in self.BUILTIN if s not in drop]
        lines += [f"CONFIG_{s}={kind}" for s, kind in zip(self.EITHER, modules) if s not in drop]
        return "\n".join(lines) + "\n" + extra

    def run_script(self, config, vmlinux="btf"):
        path = self.work / "kernel.config"
        path.write_text(config)
        return subprocess.run(["sh", str(self.SCRIPT), str(path), str(self.elf[vmlinux])], capture_output=True, text=True)

    def test_modules_and_builtin_options_with_a_btf_section_pass(self):
        for modules in (("m",) * 7, ("y",) * 7):
            result = self.run_script(self.config(modules=modules))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("honk kernel prerequisites and vmlinux BTF verified", result.stdout)

    def test_each_builtin_option_must_be_y(self):
        for symbol in self.BUILTIN:
            for text in (self.config(drop=(symbol,)), self.config(drop=(symbol,), extra=f"CONFIG_{symbol}=m\n"),
                         self.config(drop=(symbol,), extra=f"# CONFIG_{symbol} is not set\n")):
                with self.subTest(symbol=symbol, text=text.splitlines()[-1]):
                    result = self.run_script(text)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"CONFIG_{symbol}=y", result.stderr)

    def test_each_module_option_needs_y_or_m(self):
        for symbol in self.EITHER:
            for extra in ("", f"# CONFIG_{symbol} is not set\n", f"CONFIG_{symbol}=n\n"):
                with self.subTest(symbol=symbol, extra=extra):
                    result = self.run_script(self.config(drop=(symbol,), extra=extra))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"CONFIG_{symbol}=y or =m", result.stderr)

    def test_reduced_debug_info_and_a_missing_btf_section_are_rejected(self):
        result = self.run_script(self.config(extra="CONFIG_DEBUG_INFO_REDUCED=y\n"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEBUG_INFO_REDUCED", result.stderr)
        result = self.run_script(self.config(), "plain")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no BTF section", result.stderr)

    def test_missing_arguments_and_files_fail_clearly(self):
        self.assertNotEqual(subprocess.run(["sh", str(self.SCRIPT)], capture_output=True, text=True).returncode, 0)
        result = subprocess.run(["sh", str(self.SCRIPT), str(self.work / "none"), str(self.elf["btf"])],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kernel configuration not found", result.stderr)
        (self.work / "kernel.config").write_text(self.config())
        result = subprocess.run(["sh", str(self.SCRIPT), str(self.work / "kernel.config"), str(self.work / "none")],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vmlinux not found", result.stderr)

    def test_listed_options_are_real_in_this_tree(self):
        modules = "".join(path.read_text() for path in (ROOT / "package/kernel/linux/modules").glob("*.mk"))
        netfilter = (ROOT / "include/netfilter.mk").read_text()
        # Module-type options come from kmod KCONFIG lists (sched-core: NET_CLS_ACT=y is built in).
        for symbol in ("NET_CLS_ACT=y", "NET_SCH_INGRESS", "NET_CLS_BPF", "VETH"):
            self.assertIn("CONFIG_" + symbol, modules)
        for symbol in ("NF_TABLES,", "NFT_CT,", "NFT_QUEUE,", "NETFILTER_NETLINK_QUEUE,"):
            self.assertIn("CONFIG_" + symbol, netfilter)
        self.assertIn("CONFIG_NET_XGRESS=y", (ROOT / "target/linux/qualcommbe/config-6.18").read_text())
        self.assertIn("CONFIG_NF_TABLES_INET=y", (ROOT / "target/linux/generic/config-6.18").read_text())


class HonkFirstBoot(unittest.TestCase):
    SOURCE = ROOT / "config/rc01/files/etc/uci-defaults/89-rc01-bypass"
    UPSTREAM_DEFAULTS = UPSTREAM / "files/honk.uci-defaults"
    STUBS = r'''board_name() { printf '%s\n' "$RC01_TEST_BOARD"; }
config_load() { :; }
config_foreach() { :; }
'''
    # Stateful UCI: set/get of honk.config.enabled in a file, every call logged; batch input is discarded.
    UCI = r'''uci() {
    printf 'uci %s\n' "$*" >> "$RC01_TEST_UCI_LOG"
    case "$1:$2" in
        -q:batch) cat > /dev/null ;;
        -q:get) [ ! -f "$RC01_TEST_STATE" ] || { grep -q . "$RC01_TEST_STATE" && cat "$RC01_TEST_STATE"; } ;;
        -q:set) printf '%s\n' "${3#*=}" > "$RC01_TEST_STATE" ;;
        *) : ;;
    esac
    return 0
}
'''

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-honk-first-boot-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.log = self.work / "uci.log"
        self.state = self.work / "enabled"
        self.init = self.work / "init"
        self.init.mkdir()
        (self.init / "honk").write_text("#!/bin/sh\nprintf 'honk %s\\n' \"$*\" >> \"$RC01_TEST_UCI_LOG\"\n")
        (self.init / "honk").chmod(0o755)

    def run_script(self, source, board="xiaomi,be10000", config_exists=True, **extra):
        config = self.work / "config-honk"
        if config_exists:
            config.write_text("config honk 'config'\n")
        source = source.replace("/etc/config/honk", str(config)).replace("/etc/init.d/", str(self.init) + "/")
        source = source.replace("/tmp/honk-autostart", str(self.work / "honk-autostart"))
        env = clean_environ() | {"RC01_TEST_BOARD": board, "RC01_TEST_UCI_LOG": str(self.log),
                            "RC01_TEST_STATE": str(self.state)} | extra
        result = subprocess.run(["sh"], input=source, text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def bypass(self, **options):
        source = self.SOURCE.read_text()
        self.assertEqual(source.count(". /lib/functions.sh"), 1)
        self.run_script(source.replace(". /lib/functions.sh", self.STUBS + self.UCI), **options)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_honk_is_disabled_and_committed_when_the_package_is_installed(self):
        calls = self.bypass()
        self.assertIn("uci -q set honk.config.enabled=0", calls)
        self.assertEqual(calls[-1], "uci -q commit honk")
        self.assertEqual(self.state.read_text().strip(), "0")
        self.assertEqual([c for c in calls if "honk" in c and "enabled=1" in c], [])

    def test_nothing_is_written_without_the_honk_package_or_on_another_board(self):
        calls = self.bypass(config_exists=False)
        self.assertEqual([c for c in calls if " honk" in c], [])
        self.assertFalse(self.state.exists())
        self.log.unlink(missing_ok=True)
        self.assertEqual(self.bypass(board="other,board"), [])

    def test_value_is_set_after_the_batched_defaults_and_before_exit(self):
        text = self.SOURCE.read_text()
        self.assertLess(text.index("commit uhttpd\nEOF"), text.index("honk.config.enabled='0'"))
        self.assertLess(text.index("honk.config.enabled='0'"), text.rindex("exit 0"))
        self.assertNotRegex(text, r"honk\.config\.enabled='?1")

    def test_package_autostart_keeps_the_disabled_value_and_enables_the_init_link(self):
        self.bypass()
        defaults = self.UPSTREAM_DEFAULTS.read_text()
        self.assertIn('if [ -z "$(uci -q get honk.config.enabled)" ]; then', defaults)
        self.log.unlink()
        self.run_script(self.UCI + defaults)
        calls = self.log.read_text().splitlines()
        self.assertNotIn("uci -q set honk.config.enabled=1", calls)
        self.assertEqual([c for c in calls if c.startswith("uci -q set")], [])
        self.assertEqual(self.state.read_text().strip(), "0")
        self.assertIn("honk enable", calls)

    def test_fresh_image_without_the_bypass_value_is_also_disabled_by_the_package(self):
        self.run_script(self.UCI + self.UPSTREAM_DEFAULTS.read_text())
        self.assertEqual(self.state.read_text().strip(), "0")


class HonkPrepare(unittest.TestCase):
    """Run the real scripts/prepare-rc01.sh against the fake feeds and make of prepare_fixture."""
    INSTALL = "feeds install -p daeuniverse honk luci-app-honk luci-lib-daeuniverse luci-i18n-daeuniverse-zh-cn"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-prepare-honk-")
        self.addCleanup(temporary.cleanup)
        # The base configuration is the repository's own, so honk comes only from it.
        self.project = fixture.make_project(Path(temporary.name), defconfig=BASE_CONFIG.read_text())

    def prepare(self, **environment):
        return fixture.run_prepare(self.project, sys.executable, environment)

    def config(self):
        return (self.project / ".config").read_text().splitlines()

    def recipe(self):
        return (self.project / "feeds/daeuniverse/honk/Makefile").read_text()

    def test_honk_is_installed_unconditionally_from_the_base_configuration(self):
        result, events = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.INSTALL, events)
        self.assertEqual(events.count(self.INSTALL), 1)
        self.assertIn("Applied to daeuniverse: config/rc01/patches/honk/100-rc01-honk-source.patch", result.stdout)
        self.assertIn("PKG_HASH:=" + HONK_SHA256, self.recipe())
        # Patched before indexing, installed before the base config is copied and validated.
        self.assertLess(events.index("feeds update -i"), events.index(self.INSTALL))
        self.assertLess(events.index(self.INSTALL), events.index("defconfig"))
        self.assertLess(events.index("defconfig"), events.index("validate"))
        everything = " ".join(event for event in events if event.startswith("feeds"))
        for forbidden in ("dae", "luci-app-dae", "vmlinux-btf", "vmlinux-btf-builtin"):
            self.assertNotRegex(everything, rf"(?<![\w-]){forbidden}(?![\w-])")
        config = self.config()
        for line in EXPECTED_HONK_CONFIG:
            self.assertEqual(config.count(line), 1, line)
        self.assertIn("CONFIG_KERNEL_DEBUG_INFO=y", config)
        self.assertIn("# CONFIG_KERNEL_DEBUG_INFO_REDUCED is not set", config)
        self.assertNotIn("CONFIG_KERNEL_DEBUG_INFO_REDUCED=y", config)

    def test_the_removed_switch_is_an_error_before_feeds_are_touched(self):
        for value in ("true", "false", "", "1", "yes"):
            with self.subTest(value=value):
                result, events = self.prepare(RC01_WITH_HONK=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("RC01_WITH_HONK was removed; honk is always built", result.stderr)
                self.assertEqual(events, [])

    def test_patch_drift_in_the_daeuniverse_feed_fails(self):
        makefile = self.project / "feeds/daeuniverse/honk/Makefile"
        drifted = makefile.read_text().replace("PKG_RELEASE:=2", "PKG_RELEASE:=9")
        self.assertNotEqual(drifted, makefile.read_text())
        makefile.write_text(drifted)
        result, events = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("patch does not apply", result.stderr)
        self.assertEqual(makefile.read_text(), drifted)
        self.assertNotIn("feeds update -i", events)

    def test_unpinned_daeuniverse_feed_is_rejected(self):
        feed = self.project / "feeds/daeuniverse"
        (feed / "extra").write_text("moves HEAD\n")
        fixture.git(feed, "add", "-A")
        fixture.git(feed, "commit", "--quiet", "-m", "unpinned")
        result, events = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Feed daeuniverse is not at its pinned commit", result.stderr)
        self.assertNotIn("feeds update -i", events)

    def test_prepare_has_no_variant_logic_left(self):
        script = (ROOT / "scripts/prepare-rc01.sh").read_text()
        self.assertNotIn("kconfig.pl", script)
        self.assertNotIn("honk.config", script)
        self.assertNotIn("with_honk", script)
        self.assertIn("sh scripts/validate-rc01-config.sh .config", script)


class CleanEnvironment(unittest.TestCase):
    """The workflow's top-level RC01_* values must never leak into what the tests run."""

    def test_helper_drops_every_rc01_variable_and_keeps_the_rest(self):
        with mock.patch.dict(os.environ, CI_ENVIRONMENT | {"RC01_ANYTHING_ELSE": "1", "KEEP_ME": "yes"}):
            environment = clean_environ()
        self.assertEqual([name for name in environment if name.startswith("RC01_")], [])
        self.assertEqual(environment["KEEP_ME"], "yes")
        self.assertIn("PATH", environment)

    def test_prepare_and_validation_ignore_ci_top_level_variables(self):
        with tempfile.TemporaryDirectory(prefix="rc01-clean-env-") as directory, \
                mock.patch.dict(os.environ, CI_ENVIRONMENT):
            work = Path(directory)
            (work / "prepare").mkdir()
            (work / "validate").mkdir()
            project = fixture.make_project(work / "prepare", defconfig=BASE_CONFIG.read_text())
            result, events = fixture.run_prepare(project, sys.executable)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(HonkPrepare.INSTALL, events)
            # The real validator on a complete selection passes whatever RC01_* the process inherited.
            validation = fixture.ValidationProject(work / "validate").validate(selection())
            self.assertEqual(validation.returncode, 0, validation.stderr)


class HonkWorkflowContract(unittest.TestCase):
    def step(self, name):
        match = re.search(rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |\Z)", WORKFLOW, re.M | re.S)
        self.assertIsNotNone(match, f"missing step {name}")
        return match[1]

    def test_there_is_no_honk_switch_variant_or_fragment(self):
        for text in ("include_honk", "RC01_WITH_HONK", "config/rc01/honk.config", "honk_build", "'honk' || 'nikki'"):
            self.assertNotIn(text, WORKFLOW)
        self.assertIn("sh scripts/prepare-rc01.sh", self.step("Prepare pinned feeds, controlled patches and configuration"))
        self.assertRegex(WORKFLOW, r"group: rc01-\$\{\{ github\.ref \}\}-\$\{\{ inputs\.go_bootstrap \}\}\n")

    def test_every_honk_step_runs_unconditionally_except_the_cold_cache_skip(self):
        for name in ("Install honk Rust toolchains and bpf-linker",):
            self.assertNotIn("if:", self.step(name))
        self.assertIn("if: ${{ !inputs.cold_cache }}\n", self.step("Restore Cargo registry for honk"))

    def test_cache_keys_depend_on_the_base_configuration_only(self):
        # The download and compiler cache keys and their first restore prefixes.
        hashed = [line for line in WORKFLOW.splitlines() if "defconfig/qualcommbe-rc01.config" in line]
        self.assertEqual(len(hashed), 4)
        for line in hashed:
            self.assertNotIn("honk", line)

    def test_toolchain_pins_are_in_one_place(self):
        env = WORKFLOW.split("\njobs:", 1)[0]
        for line in ("RC01_HONK_RUST_STABLE: 1.99.1", "RC01_HONK_RUST_NIGHTLY: nightly-2026-10-20",
                     "RC01_HONK_BPF_LINKER_VERSION: 0.11.0",
                     "RC01_HONK_BPF_LINKER_SHA256: 10f62ba9ab7e544d538370552660efcb4f1a19153d5752bbf0f6b51f3bada450"):
            self.assertEqual(env.count(line), 1, line)
        step = self.step("Install honk Rust toolchains and bpf-linker")
        for literal in ("1.99.1", "nightly-2026-10-20", "0.11.0", "10f62ba9"):
            self.assertNotIn(literal, step)

    def test_toolchain_step_installs_and_verifies_rust_and_bpf_linker(self):
        step = self.step("Install honk Rust toolchains and bpf-linker")
        for text in ('rustup toolchain install "$RC01_HONK_RUST_STABLE" --profile minimal --target aarch64-unknown-linux-musl',
                     'rustup toolchain install "$RC01_HONK_RUST_NIGHTLY" --profile minimal --component rust-src,llvm-tools',
                     "https://github.com/aya-rs/bpf-linker/releases/download/v$RC01_HONK_BPF_LINKER_VERSION/"
                     "bpf-linker-x86_64-unknown-linux-musl.tar.zst",
                     'sha256sum -c -', 'zstd -dc', '>> "$GITHUB_PATH"', "bpf-linker --version",
                     "rust-toolchain.toml channel", "crates/honk-ebpf/rust-toolchain.toml channel",
                     ".github/ci/pins.env BPF_LINKER_VERSION", ".github/ci/pins.env BPF_LINKER_SHA256"):
            self.assertIn(text, step)
        self.assertLess(step.index("sha256sum -c -"), step.index("zstd -dc"))
        # The pinned release binary reports 0.0.0; the sha256 pins the version.
        self.assertNotIn("--version | grep", step)

    def test_toolchain_step_runs_after_the_honk_source_download_and_before_the_build(self):
        names = re.findall(r"^      - name: (.*)$", WORKFLOW, re.M)
        install = names.index("Install honk Rust toolchains and bpf-linker")
        self.assertLess(names.index("Download package sources"), install)
        self.assertLess(names.index("Restore Cargo registry for honk"), install)
        self.assertLess(install, names.index("Build firmware"))

    def test_cargo_registry_cache_is_scoped_to_the_honk_version(self):
        step = self.step("Restore Cargo registry for honk")
        for path in ("~/.cargo/registry/index", "~/.cargo/registry/cache", "~/.cargo/git/db"):
            self.assertIn(path, step)
        self.assertIn("key: rc01-honk-cargo-${{ runner.os }}-${{ env.RC01_HONK_TAG }}", step)

    def test_build_dependencies_for_honk_are_installed(self):
        step = self.step("Install build dependencies")
        for package in ("cmake", "libclang-dev", "clang", "llvm", "pkg-config", "zstd"):
            self.assertRegex(step, rf"\b{re.escape(package)}\b")

    def test_honk_kernel_validation_always_runs_after_the_nand_check(self):
        step = self.step("Verify native driver artifacts and runtime scripts")
        nand = step.index('sh scripts/validate-rc01-nand.sh "$kernel_dir" "$kernel_dir/vmlinux"')
        honk = step.index('sh scripts/validate-rc01-honk-kernel.sh "$kernel_dir/.config" "$kernel_dir/vmlinux"')
        self.assertLess(nand, honk)
        self.assertNotIn("if ", step[nand:honk])

    def test_metadata_always_records_the_source_and_toolchains(self):
        step = self.step("Record build inputs")
        self.assertNotIn("RC01_WITH_HONK", step)
        for text in ('echo "honk=$(sed', 'echo "honk_tag=$RC01_HONK_TAG"',
                     'echo "honk_source_commit=$RC01_HONK_COMMIT"', "honk_source_sha256=", "honk_rust_stable=",
                     "honk_rust_nightly=", "honk_bpf_linker=", "honk_runtime=not-verified",
                     "for feed in packages luci routing nikki daeuniverse",
                     'git -C feeds/daeuniverse diff -- honk/Makefile > "$TARGET_DIR/rc01-honk-build.patch"'):
            self.assertIn(text, step)

    def test_no_honk_toolchain_value_leaks_into_the_openwrt_make_environment_under_the_recipe_names(self):
        env = WORKFLOW.split("\njobs:", 1)[0]
        for name in ("HONK_GIT_TAG", "HONK_VERSION", "HONK_SRC"):
            self.assertNotRegex(env, rf"^  {name}:", re.M)


class DaeuniversePublicNames(unittest.TestCase):
    def test_upstream_organisation_is_a_public_name_not_a_builder_identity(self):
        spec = importlib.util.spec_from_file_location("rc01_public_artifacts_honk", ROOT / "scripts/validate-rc01-public-artifacts.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertIn("daeuniverse", guard.PUBLIC_MAINTAINERS)
        previous = {name: os.environ.get(name) for name in ("GITHUB_REPOSITORY_OWNER", "GITHUB_ACTOR")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in previous.items()])
        os.environ["GITHUB_REPOSITORY_OWNER"] = os.environ["GITHUB_ACTOR"] = "daeuniverse"
        owner_patterns = guard.identities()
        for path in (PATCH, ROOT / "config/rc01/feeds.conf", BASE_CONFIG):
            with self.subTest(path=path.name):
                self.assertEqual(guard.inspect_text(path.read_text(), owner_patterns), set())


if __name__ == "__main__":
    unittest.main()
