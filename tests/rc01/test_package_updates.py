"""Pinned feeds, controlled patches and their application by prepare-rc01.sh; offline."""
from pathlib import Path
import json
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_fixture as fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ROOT / "config/rc01/patches"
MANIFEST = json.loads((PATCHES / "packages/manifest.json").read_text())["packages"]
PREPARE = (ROOT / "scripts/prepare-rc01.sh").read_text()
FEEDS = dict(re.fullmatch(r"src-git (\S+) \S+\^([0-9a-f]{40})", line).groups()
             for line in (ROOT / "config/rc01/feeds.conf").read_text().splitlines() if line)


def variable(text, name):
    values = [line.split(":=", 1)[1] for line in text.splitlines() if line.startswith(name + ":=")]
    if len(values) != 1:
        raise AssertionError(f"expected one {name}, got {values}")
    return values[0]


def patch_list():
    block = re.search(r"rc01_patches='([^']*)'", PREPARE)[1]
    return [tuple(line.split(":", 1)) for line in block.splitlines()]


class PinnedFeeds(unittest.TestCase):
    def test_every_feed_line_is_a_full_commit_pin_and_only_the_daeuniverse_feed_remains_of_dae(self):
        lines = (ROOT / "config/rc01/feeds.conf").read_text().splitlines()
        for line in lines:
            self.assertRegex(line, r"^src-git [a-z0-9_-]+ https://\S+\.git\^[0-9a-f]{40}$")
        self.assertEqual(set(FEEDS), {"packages", "luci", "routing", "nikki", "daeuniverse"})
        self.assertEqual(FEEDS["daeuniverse"], "e104a4930c5fc4f625f3355257626495685424f5")
        self.assertEqual(len(lines), len(FEEDS))
        self.assertNotIn("daede", (ROOT / "config/rc01/feeds.conf").read_text())

    def test_manifest_and_patches_agree_with_feed_pins_and_prepare(self):
        listed = patch_list()
        patches = {p.relative_to(ROOT).as_posix() for p in PATCHES.glob("*/*.patch")}
        self.assertEqual({patch for _, patch in listed}, patches)
        self.assertEqual(len(listed), len(patches))
        for _, patch in listed:
            self.assertTrue((ROOT / patch).is_file())
        for item in MANIFEST:
            with self.subTest(patch=item["patch"]):
                self.assertEqual(item["feed_revision"], FEEDS[item["feed"]])
                self.assertIn((item["feed"], f"config/rc01/patches/packages/{item['patch']}"), listed)
                self.assertNotIn("baseline_sha256", item)
        for feed, _ in listed:
            self.assertIn(feed, FEEDS)

    def test_patches_declare_the_reviewed_version_and_source_hash(self):
        for item in MANIFEST:
            with self.subTest(patch=item["patch"]):
                patch = (PATCHES / "packages" / item["patch"]).read_text()
                self.assertIn(f"--- a/{item['path']}\n", patch)
                added = {line[1:].split(":=", 1)[0]: line[1:].split(":=", 1)[1]
                         for line in patch.splitlines() if line.startswith("+") and ":=" in line}
                self.assertEqual(added["PKG_VERSION"], item["version"])
                key = "PKG_HASH" if item["feed"] == "packages" else "PKG_MIRROR_HASH"
                self.assertEqual(added[key], item["source_sha256"])
                if item["feed"] == "nikki":
                    self.assertEqual(added["PKG_SOURCE_VERSION"], item["source_commit"])
                    self.assertEqual(added["PKG_BUILD_VERSION"], "v" + item["version"])
                    # Reproducible build time is part of the patch, not a hidden pre-step.
                    self.assertIn('PKG_BUILD_TIME:=$(shell date -u -d "@$(SOURCE_DATE_EPOCH)" -Iseconds)', patch)

    def test_prepare_has_no_resolver_replay_or_dae_inputs(self):
        for text in ("software-sources", ".rc01-sources", "RC01_WITH_DAE", "RC01_UPDATE_MODE",
                     "RC01_REPLAY", "dae.config", "daede"):
            self.assertNotIn(text, PREPARE)
        for text in ("scripts/feeds update -a", "feeds.conf"):
            self.assertIn(text, PREPARE)


class PrepareAppliesControlledPatches(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-prepare-patches-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.project = fixture.make_project(self.work)

    def prepare(self):
        return fixture.run_prepare(self.project, sys.executable)

    def recipe(self, feed, path):
        return (self.project / "feeds" / feed / path).read_text()

    def test_every_patch_is_checked_then_applied_before_the_package_index_is_built(self):
        result, events = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(events.index("feeds update -a"), events.index("feeds update -i"))
        self.assertIn("GOTOOLCHAIN=local", self.recipe("packages", "lang/golang/golang-compiler.mk"))
        self.assertIn("GOTOOLCHAIN=local", self.recipe("packages", "lang/golang/golang-host-build.mk"))
        for item in MANIFEST:
            text = self.recipe(item["feed"], item["path"])
            self.assertEqual(variable(text, "PKG_VERSION"), item["version"])
        nikki = next(item for item in MANIFEST if item["feed"] == "nikki")
        self.assertIn("SOURCE_DATE_EPOCH", self.recipe("nikki", nikki["path"]))
        # Applied patches stay uncommitted so the feed remains at its pinned HEAD.
        self.assertEqual(variable(self.recipe("daeuniverse", "honk/Makefile"), "PKG_RELEASE"), "3")
        for feed in ("packages", "nikki", "daeuniverse"):
            self.assertEqual(fixture.git(self.project / "feeds" / feed, "rev-parse", "HEAD"), FEEDS_FIXTURE(self.project)[feed])

    def test_second_run_recognises_applied_patches_and_changes_nothing(self):
        self.assertEqual(self.prepare()[0].returncode, 0)
        before = {p: p.read_bytes() for p in (self.project / "feeds").rglob("*") if p.is_file() and ".git" not in p.parts}
        result, _ = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Already applied"), len(patch_list()))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_feed_not_at_its_pinned_commit_is_rejected_before_any_patch(self):
        feed = self.project / "feeds/packages"
        (feed / "extra").write_text("moves HEAD\n")
        fixture.git(feed, "add", "-A")
        fixture.git(feed, "commit", "--quiet", "-m", "unpinned")
        result, events = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not at its pinned commit", result.stderr)
        self.assertNotIn("GOTOOLCHAIN=local", self.recipe("packages", "lang/golang/golang-compiler.mk"))
        self.assertNotIn("feeds update -i", events)

    def test_patch_that_does_not_apply_fails_check_without_touching_that_recipe(self):
        nikki = next(item for item in MANIFEST if item["feed"] == "nikki")
        path = self.project / "feeds/nikki" / nikki["path"]
        drifted = path.read_text().replace("PKG_BUILD_PARALLEL:=1", "PKG_BUILD_PARALLEL:=0")
        self.assertNotEqual(drifted, path.read_text())
        path.write_text(drifted)
        result, events = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("patch does not apply", result.stderr)
        self.assertEqual(path.read_text(), drifted)
        self.assertNotIn("feeds update -i", events)
        self.assertNotIn("defconfig", events)


def FEEDS_FIXTURE(project):
    conf = (project / "config/rc01/feeds.conf").read_text()
    return dict(re.fullmatch(r"src-git (\S+) \S+\^([0-9a-f]{40})", line).groups() for line in conf.splitlines())


if __name__ == "__main__":
    unittest.main()
