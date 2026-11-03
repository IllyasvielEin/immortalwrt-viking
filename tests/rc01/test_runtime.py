"""Exercise read-only preflight and fan fault handling against fake sysfs."""
from pathlib import Path
import os
import io
import shlex
import shutil
import tarfile
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "package/emortal/rc01-platform/files"
BUSYBOX = os.environ.get("BUSYBOX") or shutil.which("busybox")
DTC, FDTGET = shutil.which("dtc"), shutil.which("fdtget")


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-runtime-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.env = os.environ | {"RC01_SYSFS": str(self.work / "sys"), "RC01_FAN_LIBRARY": "1",
                                 "RC01_PROC": str(self.work / "proc"),
                                 "RC01_ENV_CONFIG": str(self.work / "fw_env.config")}

    def write(self, name, value):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + "\n")
        return path

    def shell(self, library, command):
        return subprocess.run(["sh", "-c", f'. "{FILES / library}"; {command}'], env=self.env,
                              capture_output=True, text=True)

    def fan(self):
        self.write("sys/class/hwmon/hwmon8/name", "emc2305")
        self.write("sys/class/hwmon/hwmon8/pwm1", 77)
        self.write("sys/class/hwmon/hwmon8/fan1_input", 960)
        self.write("sys/class/hwmon/hwmon8/fan1_fault", 0)
        self.write("sys/class/thermal/thermal_zone7/type", "nss-top-thermal")
        self.write("sys/class/thermal/thermal_zone7/temp", 45000)
        device = self.work / "sys/devices/i2c-4/4-002f"; device.mkdir(parents=True)
        (self.work / "sys/class/hwmon/hwmon8/device").symlink_to(device)

    def layout(self):
        self.write("proc/mtd", 'dev: size erasesize name\nmtd12: 00080000 00020000 "0:APPSBLENV"\nmtd41: 07800000 00020000 "rootfs"')
        for key, value in (("mtd41/size", 125829120), ("mtd41/erasesize", 131072),
                           ("mtd41/writesize", 2048), ("mtd12/size", 524288), ("mtd12/erasesize", 131072)):
            self.write("sys/class/mtd/" + key, value)
        self.write("sys/class/ubi/ubi0/mtd_num", 41)
        self.write("sys/class/ubi/ubi0_0/name", "kernel")
        self.write("sys/class/ubi/ubi0_1/name", "rootfs")
        self.write("fw_env.config", "/dev/mtd12 0x0 0x10000 0x20000 2")

    def preflight(self, crc=True):
        mock = '''fw_printenv() {
            [ "$FW_ENV_STRICT_CRC" = 1 ] || return 1
            case "$2" in machid) printf '8050301\\n';; config_name) return 1;; esac
        }; ''' if crc else 'fw_printenv() { return 1; }; '
        return self.shell("rc01.sh", mock + "rc01_check_layout")

    def test_fan_discovery_does_not_depend_on_bus_or_hwmon_number(self):
        self.fan()
        result = self.shell("rc01-fan-guard", "rc01_fan_find && rc01_fan_sample")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stopped_fan_is_a_fault(self):
        self.fan(); self.write("sys/class/hwmon/hwmon8/fan1_input", 0)
        self.assertNotEqual(self.shell("rc01-fan-guard", "rc01_fan_find && rc01_fan_sample").returncode, 0)

    def test_missing_or_malformed_temperature_is_a_fault(self):
        self.fan(); self.write("sys/class/thermal/thermal_zone7/temp", "not-a-temperature")
        self.assertNotEqual(self.shell("rc01-fan-guard", "rc01_fan_find && rc01_fan_sample").returncode, 0)

    def test_monitor_latches_full_speed_after_repeated_tach_failures(self):
        self.fan()
        command = '''logger() { printf '%s\\n' "$*"; }
        ticks=0
        sleep() {
            ticks=$((ticks + 1))
            [ "$ticks" -le 4 ] || return 1
            if [ "$ticks" -ge 2 ]; then printf '0\\n' > "$RC01_SYSFS/class/hwmon/hwmon8/fan1_input"; fi
        }
        rc01_fan_main
        '''
        result = self.shell("rc01-fan-guard", command)
        self.assertIn("full-speed floor latched", result.stdout)
        self.assertEqual((self.work / "sys/class/hwmon/hwmon8/pwm1").read_text().strip(), "255")

    def test_layout_accepts_renumbered_mtd_and_uppercase_names(self):
        self.layout()
        result = self.preflight()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_environment_size_is_rejected(self):
        self.layout(); self.write("fw_env.config", "/dev/mtd12 0x0 0x40000 0x20000 2")
        self.assertNotEqual(self.preflight().returncode, 0)

    def test_invalid_crc_is_rejected(self):
        self.layout()
        self.assertNotEqual(self.preflight(crc=False).returncode, 0)

    def test_stock_dual_slot_layout_is_rejected(self):
        self.layout(); self.write("sys/class/mtd/mtd41/size", 60 * 1024 * 1024)
        self.assertNotEqual(self.preflight().returncode, 0)

    def test_changed_rootfs_volume_id_is_rejected(self):
        self.layout(); self.write("sys/class/ubi/ubi0_1/name", "ubootenv")
        self.assertNotEqual(self.preflight().returncode, 0)

    def upgrade_image(self, kernel_names=("sysupgrade-xiaomi_be10000/kernel",)):
        self.layout()
        source = self.write("image.dts", '''/dts-v1/;
/ {
    configurations {
        config@al02-c4 { kernel = "kernel-1"; fdt = "fdt-1"; };
        config@al02-c6 { kernel = "kernel-1"; fdt = "fdt-1"; };
    };
};''')
        fit = self.work / "kernel.fit"
        subprocess.run([DTC, "-I", "dts", "-O", "dtb", "-o", str(fit), str(source)],
                       check=True, capture_output=True)
        image = self.work / "sysupgrade.bin"
        with tarfile.open(image, "w", format=tarfile.USTAR_FORMAT) as archive:
            entries = [(name, fit.read_bytes()) for name in kernel_names]
            entries.append(("sysupgrade-xiaomi_be10000/root", b"hsqs" + b"x" * 4092))
            for name, data in entries:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        tools = self.work / "tools"
        tools.mkdir(exist_ok=True)
        for applet in ("awk", "tar"):
            wrapper = tools / applet
            wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(BUSYBOX) + " " + applet + ' "$@"\n')
            wrapper.chmod(0o755)
        command = '. ' + shlex.quote(str(FILES / "rc01.sh")) + '; get_image() { cat "$1"; };'
        command += ' fw_printenv() { [ "$FW_ENV_STRICT_CRC" = 1 ] || return 1; case "$2" in machid) echo 8050301;; config_name) return 1;; esac; };'
        command += " rc01_check_image " + shlex.quote(str(image))
        return subprocess.run([BUSYBOX, "ash", "-c", command],
            env=self.env | {"PATH": str(tools) + os.pathsep + os.environ["PATH"]},
            capture_output=True, text=True)

    @unittest.skipUnless(BUSYBOX and DTC and FDTGET, "BusyBox, dtc and fdtget required for upgrade checks")
    def test_busybox_accepts_the_shipped_image_preflight(self):
        result = self.upgrade_image()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("bad regex", result.stderr)

    @unittest.skipUnless(BUSYBOX and DTC and FDTGET, "BusyBox, dtc and fdtget required for upgrade checks")
    def test_busybox_rejects_missing_nested_and_duplicate_kernel_members(self):
        for names in [(), ("sysupgrade-/kernel",), ("sysupgrade-device/nested/kernel",),
                      ("sysupgrade-xiaomi_be10000/kernel", "sysupgrade-xiaomi_be10000/kernel")]:
            with self.subTest(names=names):
                result = self.upgrade_image(names)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("expected exactly one sysupgrade kernel", result.stderr)
                self.assertNotIn("bad regex", result.stderr)


if __name__ == "__main__":
    unittest.main()
