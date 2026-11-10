"""Exercise the prepared NAND matcher and tables with host-only I/O stubs."""
from pathlib import Path
import ctypes
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rc01_platform_nand", ROOT / "scripts/validate-rc01-platform.py")
platform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform)
KERNEL = os.environ.get("RC01_KERNEL_SOURCE")


def definition(source, name):
    declaration = re.search(r"(?:static )?int\s+" + name + r"\([^;]*?\)\s*\{", source, re.S)
    if declaration is None:
        raise AssertionError(f"missing prepared definition: {name}")
    return declaration[0] + platform.function(source, name) + "}\n"


# These stubs represent only the kernel's unrelated operation selection and
# state. Both match functions, the SPINAND_ID macro and chip table entries below
# come verbatim from the actual prepared source, including the comparison/log.
STUBS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define ENOTSUPP EOPNOTSUPP
struct spinand_manufacturer;
struct spi_mem_op { int unused; };
struct spinand_op_variants { int unused; };
struct spinand_info {
 const char *model;
 struct { const u8 *id; unsigned len; enum spinand_readid_method method; } devid;
 uint64_t memorg;
 int eccreq, eccinfo, flags, select_target, configure_chip, set_cont_read;
 int fact_otp, user_otp, read_retries, set_read_retry;
 struct {
  const struct spinand_op_variants *read_cache, *write_cache, *update_cache;
 } op_variants;
};
struct nand_device { uint64_t memorg; };
struct spinand_device {
 struct { u8 data[8]; unsigned len; } id;
 struct nand_device nand;
 int eccinfo, flags, select_target, configure_chip, set_cont_read;
 const int *fact_otp, *user_otp;
 int read_retries, set_read_retry;
 struct { const struct spi_mem_op *read_cache, *write_cache, *update_cache; } ssdr_op_templates;
 const struct spinand_manufacturer *manufacturer;
};
struct spinand_manufacturer {
 u8 id;
 const struct spinand_info *chips;
 unsigned nchips;
};
static char diagnostic[512];
#define dev_info(unused, fmt, ...) snprintf(diagnostic, sizeof(diagnostic), fmt, __VA_ARGS__)
static struct nand_device *spinand_to_nand(struct spinand_device *s) { return &s->nand; }
static void nanddev_set_ecc_requirements(struct nand_device *n, const int *e) { (void)n; (void)e; }
static uint64_t nanddev_size(struct nand_device *n) { return n->memorg; }
static const struct spi_mem_op *spinand_select_op_variant(
 struct spinand_device *s, const struct spinand_op_variants *v) {
 static const struct spi_mem_op op;
 (void)s; (void)v;
 return &op;
}
/* A unique opaque organization value preserves which actual table entry
 * the real function assigned; geometry is checked by verify_nand itself. */
#define SPINAND_INFO(model_, id_, ...) { .model = model_, .devid = id_, .memorg = __LINE__ }
"""


@unittest.skipUnless(KERNEL, "set RC01_KERNEL_SOURCE to the prepared kernel")
class PreparedNand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = Path(KERNEL)
        cls.temp = tempfile.TemporaryDirectory(prefix="rc01-nand-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.path = Path(cls.temp.name)
        compiler = shutil.which("cc")
        if not compiler:
            raise AssertionError("prepared NAND regression requires host cc")
        cls.core = (cls.kernel / "drivers/mtd/nand/spi/core.c").read_text()
        cls.esmt = (cls.kernel / "drivers/mtd/nand/spi/esmt.c").read_text()
        giga = (cls.kernel / "drivers/mtd/nand/spi/gigadevice.c").read_text()
        header = (cls.kernel / "include/linux/mtd/spinand.h").read_text()
        enum = re.search(r"enum spinand_readid_method\s*\{.*?\};", header, re.S)[0]
        macro = re.search(r"^#define SPINAND_ID\([^\n]*\\\n(?:[^\n]*\\\n)*[^\n]*", header, re.M)[0]
        tables = []
        manufacturers = []
        for name, source, symbol in (("esmt_c8", cls.esmt, "SPINAND_MFR_ESMT_C8"),
                                     ("gigadevice", giga, "SPINAND_MFR_GIGADEVICE")):
            table = re.search(r"static const struct spinand_info " + name + r"_spinand_table\[\] = \{.*?\n\};",
                              source, re.S)[0]
            tables.append(table)
            vendor_id = re.search(r"#define\s+" + symbol + r"\s+(0x[0-9a-fA-F]+)", source)[1]
            manufacturers.append(
                f"static const struct spinand_manufacturer {name}_spinand_manufacturer = "
                f"{{ {vendor_id}, {name}_spinand_table, ARRAY_SIZE({name}_spinand_table) }};")
        names = re.findall(r"&((?:esmt_c8|gigadevice)_spinand_manufacturer)", cls.core)
        # There is one reference to each in the actual manufacturer list.
        if len(names) != 2:
            raise AssertionError("ambiguous prepared manufacturer order")
        manufacturer_array = ("static const struct spinand_manufacturer *spinand_manufacturers[] = {"
                              + ",".join("&" + name for name in names) + "};")
        source = enum + "\n" + STUBS + macro + "\n" + "\n".join(tables + manufacturers)
        source += manufacturer_array + definition(cls.core, "spinand_match_and_init")
        source += definition(cls.core, "spinand_manufacturer_match")
        source += r"""
const char *probe(const u8 *id, enum spinand_readid_method method) {
 struct spinand_device s = {0};
 diagnostic[0] = 0;
 memcpy(s.id.data, id, sizeof(s.id.data));
 if (spinand_manufacturer_match(&s, method))
  return NULL;
 /* Identify the exact entry selected by the real match, without parsing
  * the diagnostic as the source of truth for a match. */
 for (unsigned i = 0; i < s.manufacturer->nchips; i++) {
  const struct spinand_info *info = &s.manufacturer->chips[i];
  if (info->memorg == s.nand.memorg)
   return info->model;
 }
 return "unexpected match";
}
const char *probe_diagnostic(void) { return diagnostic; }
"""
        (cls.path / "probe.c").write_text(source)
        result = subprocess.run([compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                                 str(cls.path / "probe.c"), "-o", str(cls.path / "probe.so")],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.library = ctypes.CDLL(str(cls.path / "probe.so"))
        cls.library.probe.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
        cls.library.probe.restype = ctypes.c_char_p
        cls.library.probe_diagnostic.restype = ctypes.c_char_p

    def probe(self, hexadecimal, method=1):
        values = bytes.fromhex(hexadecimal).ljust(8, b"\0")
        result = self.library.probe((ctypes.c_ubyte * 8).from_buffer_copy(values), method)
        return result.decode() if result else None

    def test_qpic_id_matches_esmt_before_short_gigadevice_id(self):
        self.assertEqual(self.probe("c8517f7f"), "F50D2G41KA")
        self.assertIn(b"table-id=3 compared-id=3", self.library.probe_diagnostic())
        self.assertEqual(platform.verify_nand(self.kernel)["nand"].split(",")[0], "F50D2G41KA")

    def test_wrong_third_device_byte_cannot_match_esmt(self):
        self.assertNotEqual(self.probe("c8517f7e"), "F50D2G41KA")

    def test_wrong_manufacturer_or_readid_method_cannot_match_esmt(self):
        self.assertNotEqual(self.probe("aa517f7f"), "F50D2G41KA")
        self.assertNotEqual(self.probe("c8517f7f", method=0), "F50D2G41KA")

    def test_long_id_table_entry_keeps_and_compares_its_fourth_byte(self):
        entry = re.search(r'SPINAND_INFO\("F50L1G41LB",\s*SPINAND_ID\(([^)]+)\)', self.esmt)[1]
        self.assertEqual(len(re.findall(r"0x[0-9a-f]+", entry)), 4)
        self.assertEqual(self.probe("c8017f7f7f"), "F50L1G41LB")
        self.assertIn(b"table-id=4 compared-id=4", self.library.probe_diagnostic())
        self.assertNotEqual(self.probe("c8017f7f7e"), "F50L1G41LB")
        self.assertNotEqual(self.probe("c8017f7f00"), "F50L1G41LB")

    def test_prepared_guard_rejects_global_cap_wrong_log_and_old_table(self):
        variants = [
            ("core.c", "memcmp(id + 1, info->devid.id, info->devid.len)",
             "memcmp(id + 1, info->devid.id, min(3, info->devid.len))"),
            ("core.c", "(unsigned int)info->devid.len,\n\t\t\t (unsigned long long)nanddev_size",
             "(unsigned int)3,\n\t\t\t (unsigned long long)nanddev_size"),
            ("esmt.c", "0x51, 0x7f,\n\t\t\t\t0x7f)", "0x51, 0x7f,\n\t\t\t\t0x7f, 0x7f)"),
        ]
        for filename, before, after in variants:
            with self.subTest(regression=after), tempfile.TemporaryDirectory(prefix="rc01-nand-broken-") as temporary:
                tree = Path(temporary)
                for relative in ("drivers/mtd/nand/spi/core.c", "drivers/mtd/nand/spi/esmt.c",
                                 "drivers/spi/spi-qpic-snand.c"):
                    destination = tree / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.kernel / relative, destination)
                path = tree / "drivers/mtd/nand/spi" / filename
                text = path.read_text()
                self.assertIn(before, text, "negative fixture must change the real prepared source")
                path.write_text(text.replace(before, after, 1))
                with self.assertRaises(platform.images.VerificationError):
                    platform.verify_nand(tree)


if __name__ == "__main__":
    unittest.main()
