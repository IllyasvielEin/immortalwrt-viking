"""Run a host C fixture against real dnsmasq sources with all package patches.

After a package build, discover a .prepared* tree under build_dir automatically.
For a separately prepared tree, set RC01_DNSMASQ_SOURCE to its dnsmasq root:
  RC01_DNSMASQ_SOURCE=/tmp/dnsmasq-2.93 python3 -B -m unittest discover \
      -s tests/rc01 -p 'test_dnsmasq_*.py' -v
No source is downloaded, patched, or written by this test. Before prepare it skips
explicitly. HOSTCC selects the host compiler (default cc); optional
RC01_DNSMASQ_CFLAGS can enable -fsanitize=address,undefined.
"""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("dnsmasq_blockdata.c")
SOURCES = ("blockdata.c", "rfc1035.c", "cache.c", "util.c", "rrfilter.c")


class DnsmasqBlockdataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        explicit = os.environ.get("RC01_DNSMASQ_SOURCE")
        if explicit:
            source = Path(explicit).resolve()
        else:
            prepared = sorted(
                (path for path in ROOT.glob("build_dir/target-*/dnsmasq-*/dnsmasq-*")
                 if (path / "src/blockdata.c").is_file()
                 and any(path.glob(".prepared*"))),
                key=lambda path: (path.parent.name != "dnsmasq-full", str(path)),
            )
            if not prepared:
                raise unittest.SkipTest(
                    "dnsmasq prepared source unavailable; prepare/build the package "
                    "or set RC01_DNSMASQ_SOURCE to a tree with all package patches applied"
                )
            source = prepared[0]

        for name in (*SOURCES, "dnsmasq.h"):
            if not (source / "src" / name).is_file():
                raise AssertionError(f"incomplete dnsmasq source: {source / 'src' / name}")

        compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
        if not compiler or not shutil.which(compiler[0]):
            raise unittest.SkipTest("dnsmasq C regression requires a host C compiler (HOSTCC/cc)")
        temporary = tempfile.TemporaryDirectory(prefix="rc01-dnsmasq-test-")
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / "dnsmasq-blockdata"
        command = compiler + [
            "-std=gnu99", "-O1", "-g", "-ffunction-sections", "-fdata-sections",
            "-DNO_DHCP", "-DNO_TFTP", "-DNO_SCRIPT", "-DNO_AUTH",
            "-DNO_IPSET", "-DNO_INOTIFY", "-I", str(source / "src"),
        ] + shlex.split(os.environ.get("RC01_DNSMASQ_CFLAGS", "")) + [
            str(FIXTURE), *(str(source / "src" / name) for name in SOURCES),
            "-Wl,--gc-sections", "-Wl,--wrap=whine_malloc_real", "-o", str(cls.binary),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(f"dnsmasq fixture compilation failed against {source}:\n"
                                 f"{result.stdout}{result.stderr}")

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}{result.stderr}")

    def test_empty_allocation_expands_across_blocks(self):
        self.run_case("empty")

    def test_positive_allocation_and_ipc_read_round_trip(self):
        self.run_case("positive")

    def test_zero_length_ipc_read_returns_empty_block_without_consuming_data(self):
        self.run_case("zero-read")

    def test_ipc_is_drained_when_first_allocation_fails(self):
        self.run_case("oom-first")

    def test_ipc_is_drained_when_partial_allocation_fails(self):
        self.run_case("oom-partial")

    def test_plain_nxdomain_caches_soa_and_negative_ttl_without_neg_ttl(self):
        self.run_case("nxdomain")

    def test_plain_nodata_caches_soa_and_negative_ttl_without_neg_ttl(self):
        self.run_case("nodata")

    def test_mx_answer_is_cached(self):
        self.run_case("mx")

    def test_srv_answer_is_cached(self):
        self.run_case("srv")

    def test_svcb_answer_is_cached(self):
        self.run_case("svcb")

    def test_zero_length_rr_survives_real_child_parent_ipc(self):
        self.run_case("empty-rr-ipc")

    def test_soa_owner_must_be_a_label_ancestor(self):
        self.run_case("unrelated-soa")


if __name__ == "__main__":
    unittest.main()
