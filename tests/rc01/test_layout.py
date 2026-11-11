"""Verify the shared contract and fail-closed runtime/build projections."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('layout_contract', ROOT / 'scripts/rc01-layout.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class LayoutContract(unittest.TestCase):
    def test_current_projection_matches_contract(self):
        data = layout.load_layout()
        layout.check_generated(data)
        self.assertEqual(data['partitions']['rootfs']['offset'], 24903680)
        self.assertEqual(data['partitions']['rootfs']['bytes'], 241434624)

    def test_invalid_layouts_cannot_generate_runtime_contract(self):
        original = layout.load_layout()
        changes = [('rootfs', 'bytes', 125829120), ('rootfs', 'offset', 24641536),
                   ('bdata', 'offset', 150470656), ('license', 'bytes', 0),
                   ('rootfs', 'index', 24)]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'layout.json'
            for part, field, value in changes:
                with self.subTest(part=part, field=field):
                    data = copy.deepcopy(original)
                    data['partitions'][part][field] = value
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        layout.load_layout(path)

    def test_projection_drift_is_rejected(self):
        data = layout.load_layout()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make = root / 'config/rc01/layout.mk'
            shell = root / 'package/emortal/rc01-platform/files/rc01-layout.sh'
            make.parent.mkdir(parents=True)
            shell.parent.mkdir(parents=True)
            make.write_text(layout.make_projection(data))
            shell.write_text(layout.shell_projection(data))
            layout.check_generated(data, root)
            shell.write_text(shell.read_text().replace('241434624', '125829120'))
            with self.assertRaisesRegex(ValueError, 'projection drift'):
                layout.check_generated(data, root)


@unittest.skipUnless(__import__('shutil').which('mksquashfs') and __import__('shutil').which('unsquashfs'), 'SquashFS tools required')
class ShippedRuntime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rc01-shipped-runtime-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tree = self.root / 'tree'
        self.files = {
            'usr/share/rc01/layout.json': ROOT / 'config/rc01/layout.json',
            'lib/upgrade/rc01-layout.sh': ROOT / 'package/emortal/rc01-platform/files/rc01-layout.sh',
            'lib/upgrade/rc01.sh': ROOT / 'package/emortal/rc01-platform/files/rc01.sh',
            'usr/share/ucode/rc01/platform.uc': ROOT / 'package/emortal/rc01-platform/files/platform.uc',
            'lib/upgrade/platform.sh': ROOT / 'target/linux/qualcommbe/ipq95xx/base-files/lib/upgrade/platform.sh',
        }
        for name, source in self.files.items():
            target = self.tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        s = importlib.util.spec_from_file_location('image_runtime', ROOT / 'scripts/verify-rc01-images.py')
        self.images = importlib.util.module_from_spec(s)
        s.loader.exec_module(self.images)

    def squashfs(self):
        import subprocess
        destination = self.root / 'root.squashfs'
        subprocess.run(['mksquashfs', str(self.tree), str(destination), '-noappend', '-processors', '1', '-comp', 'gzip'],
                       check=True, capture_output=True)
        return destination.read_bytes()

    def test_actual_filesystem_contains_current_layout_and_preflight(self):
        result = self.images.verify_runtime_payload(self.squashfs())
        self.assertEqual(result['runtime_layout_files_verified'], 5)

    def test_stale_shipped_contract_is_rejected(self):
        (self.tree / 'usr/share/rc01/layout.json').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'shipped runtime layout/preflight mismatch'):
            self.images.verify_runtime_payload(self.squashfs())

    def test_missing_preflight_projection_is_rejected(self):
        (self.tree / 'lib/upgrade/rc01-layout.sh').unlink()
        with self.assertRaisesRegex(ValueError, 'shipped runtime layout/preflight mismatch'):
            self.images.verify_runtime_payload(self.squashfs())
