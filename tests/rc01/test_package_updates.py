"""Offline checks against the real pinned recipe objects; no feed update/build."""
from pathlib import Path
import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("package_sources", ROOT / "scripts/rc01-software-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)
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
        (self.work / "feeds").mkdir()
        shutil.copytree(PATCHES, self.work / "config/rc01/patches/packages")
        for item in MANIFEST:
            name = item["feed"]
            feed = self.work / "feeds" / name
            subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(ROOT / "feeds" / name), str(feed)], check=True)
            # Detach only the disposable clone; never checkout the shared feed.
            (feed / ".git/HEAD").write_text(item["feed_revision"] + "\n")
            recipe = feed / item["path"]
            recipe.parent.mkdir(parents=True)
            recipe.write_text(self.originals[name])
            (recipe.parent / "unrelated.keep").write_bytes(b"preserve local files\n")
            self.feeds[name] = feed

    def apply(self):
        lock = {"with_dae": False, "prepared": True,
                "feeds": {item["feed"]: {"commit": item["feed_revision"]} for item in MANIFEST}}
        with mock.patch.object(sources, "ROOT", self.work), mock.patch.object(sources, "go_adapter"), \
             mock.patch.object(sources, "validate_lock", side_effect=lambda value: value):
            sources.verify_feeds(lock)
            sources.locked_sources(lock)
            sources.apply_outputs(lock)
            sources.verify(lock)

    def recipes(self):
        return {item["feed"]: self.feeds[item["feed"]] / item["path"] for item in MANIFEST}

    def snapshot(self):
        return {str(p.relative_to(self.work)): p.read_bytes()
                for feed in self.feeds.values() for p in feed.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(feed).parts}

    def assert_refused_unchanged(self, message):
        before = self.snapshot()
        with self.assertRaisesRegex(sources.SourceError, message):
            self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_locked_baselines_exact_versions_hashes_and_idempotence(self):
        for item in MANIFEST:
            self.assertEqual(hashlib.sha256(self.recipes()[item["feed"]].read_text().replace(OLD_TIME, NEW_TIME).encode()).hexdigest(),
                             item["baseline_sha256"])
        self.apply()
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
                self.assertEqual(variable(text, "PKG_BUILD_VERSION"), "v" + item["version"])
                self.assertIn(NEW_TIME, text)
            # The complete recipe must differ only in these reviewed fields.
            old = dict(line.split(":=", 1) for line in self.originals[item["feed"]].replace(OLD_TIME, NEW_TIME).splitlines() if ":=" in line)
            new = dict(line.split(":=", 1) for line in text.splitlines() if ":=" in line)
            changed = {key for key in old | new if old.get(key) != new.get(key)}
            self.assertEqual(changed, {"PKG_VERSION", "PKG_RELEASE", "PKG_HASH"} if item["feed"] == "packages"
                             else {"PKG_VERSION", "PKG_SOURCE_VERSION", "PKG_MIRROR_HASH", "PKG_BUILD_VERSION"})
            self.assertEqual(path.parent.joinpath("unrelated.keep").read_bytes(), b"preserve local files\n")
        before = self.snapshot()
        self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_each_wrong_feed_revision_refuses_before_any_recipe_write(self):
        for item in MANIFEST:
            with self.subTest(feed=item["feed"]):
                head = self.feeds[item["feed"]] / ".git/HEAD"
                correct = head.read_text()
                head.write_text("0" * 40 + "\n")
                self.assert_refused_unchanged("locked commit: " + item["feed"])
                head.write_text(correct)

    def test_drift_outside_patch_context_is_not_overwritten(self):
        for name, path in self.recipes().items():
            with self.subTest(feed=name):
                original = path.read_bytes()
                path.write_bytes(original + b"# local recipe customization\n")
                self.assert_refused_unchanged("pre/postimage drift")
                path.write_bytes(original)

    def test_partial_update_and_postimage_drift_refuse_without_other_writes(self):
        path = self.recipes()["nikki"]
        original = path.read_text()
        selected = next(item for item in MANIFEST if item["feed"] == "nikki")
        path.write_text(sources.replace_field(original, "PKG_VERSION", selected["version"]))
        self.assert_refused_unchanged("pre/postimage drift")
        path.write_text(original)
        self.apply()
        path.write_text(path.read_text().replace("with_gvisor", "without_gvisor"))
        self.assert_refused_unchanged("pre/postimage drift")

    def test_mixed_already_applied_and_baseline_can_resume(self):
        tailscale = self.recipes()["packages"]
        original = tailscale.read_bytes()
        self.apply()
        tailscale.write_bytes(original)
        self.apply()
        for item in MANIFEST:
            self.assertEqual(hashlib.sha256(self.recipes()[item["feed"]].read_bytes()).hexdigest(),
                             item["applied_sha256"])

    def test_timestamp_guard_accepts_raw_preimage_and_rejects_unknown_or_duplicate_lines(self):
        path = self.recipes()["nikki"]
        for applied in (False, True):
            if applied:
                self.apply()
            original = path.read_text()
            timestamp = NEW_TIME if applied else OLD_TIME
            for replacement in ("PKG_BUILD_TIME:=unknown", timestamp + "\n" + timestamp):
                with self.subTest(applied=applied, timestamp=replacement):
                    path.write_text(original.replace(timestamp, replacement))
                    self.assert_refused_unchanged("pre/postimage drift")
                    path.write_text(original)
            if applied:
                path.write_text(original.replace(NEW_TIME, OLD_TIME))
                self.assert_refused_unchanged("pre/postimage drift")
                path.write_text(original)

    def test_symlinked_recipe_is_rejected(self):
        path = self.recipes()["nikki"]
        original = path.read_bytes()
        outside = self.work / "outside"
        outside.write_bytes(original)
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(sources.SourceError, "unsafe application output path"):
            self.apply()
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(self.recipes()["packages"].read_text(), self.originals["packages"])


class IntegrationContractTests(unittest.TestCase):
    def test_pins_and_recorded_source_provenance(self):
        pins = (ROOT / "config/rc01/feeds.conf").read_text()
        for item in MANIFEST:
            self.assertIn("^" + item["feed_revision"] + "\n", pins)
        # The selected manifest owns version/source identity; test the real
        # patch products instead of freezing another copy of its values here.
        baselines = ROOT / "config/rc01/software-baselines"
        recipes = {("packages", "net/tailscale/Makefile"): (baselines/"tailscale.Makefile").read_bytes(),
                   ("nikki", "mihomo-meta/Makefile"): (baselines/"mihomo.Makefile").read_bytes()}
        lock = {"with_dae": False, "feeds": {item["feed"]: {"commit": item["feed_revision"]} for item in MANIFEST}}
        with mock.patch.object(sources, "go_adapter"), \
             mock.patch.object(sources, "pristine", side_effect=lambda feed, name: recipes[(feed, name)]):
            sources.locked_sources(lock)
        for item in MANIFEST:
            output = lock["outputs"][f"feeds/{item['feed']}/{item['path']}"]
            text = base64.b64decode(output["content"]).decode()
            self.assertEqual(variable(text, "PKG_VERSION"), item["version"])
            key = "PKG_HASH" if item["feed"] == "packages" else "PKG_MIRROR_HASH"
            self.assertEqual(variable(text, key), item["source_sha256"])
            self.assertEqual(output["after"], item["applied_sha256"])
            if item["feed"] == "nikki":
                self.assertEqual(variable(text, "PKG_SOURCE_VERSION"), item["source_commit"])
                self.assertEqual(variable(text, "PKG_BUILD_VERSION"), "v"+item["version"])



if __name__ == "__main__":
    unittest.main()
