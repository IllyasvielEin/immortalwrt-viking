from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / "package/emortal/rc01-platform/files/rc01-pstore.init"


class Pstore(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-pstore-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = self.root / "pstore"
        self.store.mkdir()
        self.archive = self.root / "archive"
        (self.root / "release").write_text("DISTRIB_REVISION='r1'\nDISTRIB_DESCRIPTION='RC01 test'\n")
        (self.root / "boot_id").write_text("12345678-1234-5678-9abc-123456789abc\n")
        self.mounts = self.root / "mounts"
        self.mounts.write_text("/dev/ubi0_2 /overlay ubifs rw,noatime 0 0\n")
        self.script = (INIT.read_text().replace("/sys/fs/pstore", str(self.store))
                       .replace("/overlay/rc01-pstore", str(self.archive))
                       .replace("/etc/openwrt_release", str(self.root / "release"))
                       .replace("/proc/sys/kernel/random/boot_id", str(self.root / "boot_id"))
                       .replace("/proc/mounts", str(self.mounts)))

    def boot(self, stub=""):
        script = self.script + '\nfsync() { :; }\nlogger() { echo "$*"; }\n' + stub + "\nboot"
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True)

    def test_archive_and_clear(self):
        files = {"dmesg-ramoops-0": "panic", "console-ramoops-0": "console", "pmsg-ramoops-0": "message"}
        for name, data in files.items():
            (self.store / name).write_text(data)
        result = self.boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.store.iterdir()), [])
        dest = self.archive / "00000001-12345678"
        for name, data in files.items():
            self.assertEqual((dest / name).read_text(), data)
        meta = (dest / "meta").read_text()
        self.assertIn((self.root / "release").read_text(), meta)
        self.assertIn("boot_id=" + (self.root / "boot_id").read_text(), meta)

    def test_copy_failure_keeps_source(self):
        record = self.store / "dmesg-ramoops-0"
        record.write_text("panic")
        result = self.boot("cp() { return 1; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to archive pstore", result.stdout)
        self.assertEqual(record.read_text(), "panic")

    def test_ram_overlay_keeps_source(self):
        self.mounts.write_text("tmpfs /overlay tmpfs rw 0 0\n")
        record = self.store / "dmesg-ramoops-0"
        record.write_text("panic")
        result = self.boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.archive.exists())
        self.assertEqual(record.read_text(), "panic")

    def test_fsync_failure_keeps_source(self):
        record = self.store / "dmesg-ramoops-0"
        record.write_text("panic")
        result = self.boot("fsync() { return 1; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(record.read_text(), "panic")
        self.assertEqual(list(self.archive.iterdir()), [])

    def test_console_only(self):
        (self.store / "console-ramoops-0").touch()
        (self.store / "pmsg-ramoops-0").touch()
        result = self.boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.store.iterdir()), [self.store / "pmsg-ramoops-0"])

    def test_rotate_ten_archives(self):
        self.archive.mkdir()
        for seq in range(3, 14):
            (self.archive / f"{seq:08d}-abcdef01").mkdir()
        (self.store / "dmesg-ramoops-0").touch()
        result = self.boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = [f"{seq:08d}-abcdef01" for seq in range(5, 14)] + ["00000014-12345678"]
        self.assertEqual(sorted(p.name for p in self.archive.iterdir()), expected)
