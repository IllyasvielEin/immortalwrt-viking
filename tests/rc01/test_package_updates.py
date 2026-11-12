"""Offline checks against the real pinned recipe objects; no feed update/build."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/patch-rc01-packages.py"
PATCHES = ROOT / "config/rc01/patches/packages"
MANIFEST = json.loads((PATCHES / "manifest.json").read_text())["packages"]
OLD_TIME = 'PKG_BUILD_TIME:=$(shell date -u -Iseconds)'
NEW_TIME = 'PKG_BUILD_TIME:=$(shell date -u -d "@$(SOURCE_DATE_EPOCH)" -Iseconds)'


def variable(text, name):
    values = [line.split(":=", 1)[1] for line in text.splitlines() if line.startswith(name + ":=")]
    if len(values) != 1:
        raise AssertionError(f"expected one {name}, got {values}")
    return values[0]


class PackageRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals = {}
        for item in MANIFEST:
            result = subprocess.run(["git", "-C", str(ROOT / "feeds" / item["feed"]),
                                     "show", item["feed_revision"] + ":" + item["path"]],
                                    capture_output=True, text=True)
            if result.returncode:
                raise unittest.SkipTest("pinned feed git objects required; these tests never fetch them")
            cls.originals[item["feed"]] = result.stdout

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-package-test-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.feeds = {}
        for item in MANIFEST:
            name = item["feed"]
            feed = self.work / name
            subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(ROOT / "feeds" / name), str(feed)], check=True)
            # Detach only the disposable clone; never checkout the shared feed.
            (feed / ".git/HEAD").write_text(item["feed_revision"] + "\n")
            recipe = feed / item["path"]
            recipe.parent.mkdir(parents=True)
            recipe.write_text(self.originals[name].replace(OLD_TIME, NEW_TIME))
            (recipe.parent / "unrelated.keep").write_bytes(b"preserve local files\n")
            self.feeds[name] = feed

    def apply(self):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.feeds["packages"]),
                               str(self.feeds["nikki"])], capture_output=True, text=True,
                              env=os.environ | {"GOTOOLCHAIN": "local", "GOSUMDB": "sum.golang.org"})

    def recipes(self):
        return {item["feed"]: self.feeds[item["feed"]] / item["path"] for item in MANIFEST}

    def snapshot(self):
        return {str(p.relative_to(self.work)): p.read_bytes()
                for feed in self.feeds.values() for p in feed.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(feed).parts}

    def assert_refused_unchanged(self, message):
        before = self.snapshot()
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_locked_baselines_exact_versions_hashes_and_idempotence(self):
        for item in MANIFEST:
            self.assertEqual(hashlib.sha256(self.recipes()[item["feed"]].read_bytes()).hexdigest(),
                             item["baseline_sha256"])
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        for item in MANIFEST:
            path = self.recipes()[item["feed"]]
            text = path.read_text()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["applied_sha256"])
            self.assertEqual(variable(text, "PKG_VERSION"), item["version"])
            key = "PKG_HASH" if item["feed"] == "packages" else "PKG_MIRROR_HASH"
            self.assertEqual(variable(text, key), item["source_sha256"])
            if item["feed"] == "packages":
                self.assertEqual(variable(text, "PKG_RELEASE"), "1")
                self.assertEqual(variable(text, "PKG_SOURCE_URL"),
                                 "https://codeload.github.com/tailscale/tailscale/tar.gz/v$(PKG_VERSION)?")
            else:
                self.assertEqual(variable(text, "PKG_SOURCE_VERSION"), item["source_commit"])
                self.assertEqual(variable(text, "PKG_BUILD_VERSION"), "v1.19.34")
                self.assertIn(NEW_TIME, text)
            # The complete recipe must differ only in these reviewed fields.
            old = dict(line.split(":=", 1) for line in self.originals[item["feed"]].replace(OLD_TIME, NEW_TIME).splitlines() if ":=" in line)
            new = dict(line.split(":=", 1) for line in text.splitlines() if ":=" in line)
            changed = {key for key in old | new if old.get(key) != new.get(key)}
            self.assertEqual(changed, {"PKG_VERSION", "PKG_RELEASE", "PKG_HASH"} if item["feed"] == "packages"
                             else {"PKG_VERSION", "PKG_SOURCE_VERSION", "PKG_MIRROR_HASH", "PKG_BUILD_VERSION"})
            self.assertEqual(path.parent.joinpath("unrelated.keep").read_bytes(), b"preserve local files\n")
        before = self.snapshot()
        mtimes = {name: p.stat().st_mtime_ns for name, p in self.recipes().items()}
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({name: p.stat().st_mtime_ns for name, p in self.recipes().items()}, mtimes)

    def test_each_wrong_feed_revision_refuses_before_any_recipe_write(self):
        for item in MANIFEST:
            with self.subTest(feed=item["feed"]):
                head = self.feeds[item["feed"]] / ".git/HEAD"
                correct = head.read_text()
                head.write_text("0" * 40 + "\n")
                self.assert_refused_unchanged("Unexpected " + item["feed"] + " revision")
                head.write_text(correct)

    def test_drift_outside_patch_context_is_not_overwritten(self):
        for name, path in self.recipes().items():
            with self.subTest(feed=name):
                original = path.read_bytes()
                path.write_bytes(original + b"# local recipe customization\n")
                self.assert_refused_unchanged("Unexpected recipe baseline")
                path.write_bytes(original)

    def test_partial_update_and_postimage_drift_refuse_without_other_writes(self):
        path = self.recipes()["nikki"]
        original = path.read_text()
        path.write_text(original.replace("PKG_VERSION:=1.19.32", "PKG_VERSION:=1.19.34"))
        self.assert_refused_unchanged("Unexpected recipe baseline")
        path.write_text(original)
        self.assertEqual(self.apply().returncode, 0)
        path.write_text(path.read_text().replace("with_gvisor", "without_gvisor"))
        self.assert_refused_unchanged("Unexpected recipe baseline")

    def test_mixed_already_applied_and_baseline_can_resume(self):
        tailscale = self.recipes()["packages"]
        original = tailscale.read_bytes()
        self.assertEqual(self.apply().returncode, 0)
        tailscale.write_bytes(original)
        self.assertEqual(self.apply().returncode, 0)
        for item in MANIFEST:
            self.assertEqual(hashlib.sha256(self.recipes()[item["feed"]].read_bytes()).hexdigest(),
                             item["applied_sha256"])

    def test_timestamp_guard_is_required_before_and_after_update(self):
        path = self.recipes()["nikki"]
        for applied in (False, True):
            if applied:
                self.assertEqual(self.apply().returncode, 0)
            original = path.read_text()
            for timestamp in (OLD_TIME, "PKG_BUILD_TIME:=unknown", NEW_TIME + "\n" + NEW_TIME):
                with self.subTest(applied=applied, timestamp=timestamp):
                    path.write_text(original.replace(NEW_TIME, timestamp))
                    self.assert_refused_unchanged("SOURCE_DATE_EPOCH timestamp guard")
                    path.write_text(original)

    def test_real_prepare_timestamp_guard_and_hook_replay_together(self):
        project = self.work / "prepare-fixture"
        (project / "scripts").mkdir(parents=True)
        (project / "feeds").mkdir()
        shutil.copy2(SCRIPT, project / "scripts" / SCRIPT.name)
        shutil.copytree(PATCHES, project / "config/rc01/patches/packages")
        for name, feed in self.feeds.items():
            (project / "feeds" / name).symlink_to(feed, target_is_directory=True)
        path = self.recipes()["nikki"]
        path.write_text(self.originals["nikki"])
        prepare = (ROOT / "scripts/prepare-rc01.sh").read_text()
        end = prepare.index("\nfi\npython3 -B scripts/rc01-software-sources.py finish-locked")
        section = prepare[prepare.index("core_makefile="):end]
        for _ in range(2):
            result = subprocess.run(["sh", "-eu", "-c", section], cwd=project,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(NEW_TIME, path.read_text())
            self.assertEqual(variable(path.read_text(), "PKG_VERSION"), "1.19.34")
        path.write_text(path.read_text().replace(NEW_TIME, "PKG_BUILD_TIME:=unknown"))
        before = self.snapshot()
        result = subprocess.run(["sh", "-eu", "-c", section], cwd=project,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mihomo timestamp recipe changed", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_symlinked_recipe_is_rejected(self):
        path = self.recipes()["nikki"]
        original = path.read_bytes()
        outside = self.work / "outside"
        outside.write_bytes(original)
        path.unlink()
        path.symlink_to(outside)
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe recipe path", result.stderr)
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(self.recipes()["packages"].read_text(), self.originals["packages"])


class IntegrationContractTests(unittest.TestCase):
    def test_pins_and_recorded_source_provenance(self):
        pins = (ROOT / "config/rc01/feeds.conf").read_text()
        for item in MANIFEST:
            self.assertIn("^" + item["feed_revision"] + "\n", pins)
        by_feed = {item["feed"]: item for item in MANIFEST}
        self.assertEqual(by_feed["packages"]["source_commit"], "c54807352ae196919497b3f8fddc3cedf88368b9")
        self.assertEqual(by_feed["packages"]["source_sha256"], "a6ea0771776ab9d3b93147cc221ad02f1b75073ce34380bb29d58b7df9863f44")
        self.assertEqual(by_feed["nikki"]["source_commit"], "0455d0d28c4526937380505153e65a843d8612e8")
        self.assertEqual(by_feed["nikki"]["source_sha256"], "5ef52ba261e872e99658422165d0c0a9fbebcf3366c9544ac8da813933e3d245")
        # These exact flags/call sites also preserve the existing source build.
        self.assertNotIn("GOSUMDB", SCRIPT.read_text())

    def test_prepare_calls_package_hook_after_pin_and_timestamp_guards(self):
        prepare = (ROOT / "scripts/prepare-rc01.sh").read_text()
        hook = "python3 -B scripts/patch-rc01-packages.py feeds/packages feeds/nikki"
        self.assertEqual(prepare.count(hook), 1)
        self.assertLess(prepare.index("Feed revision mismatch"), prepare.index(hook))
        self.assertLess(prepare.index("Mihomo timestamp recipe changed"), prepare.index(hook))
        self.assertLess(prepare.index(hook), prepare.index("make defconfig"))


if __name__ == "__main__":
    unittest.main()
