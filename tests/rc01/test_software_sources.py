"""Offline source resolution/replay tests, with real recipe transforms and SHA locks."""
from pathlib import Path
import base64
import copy
import importlib.util
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rc01_software_sources", ROOT / "scripts/rc01-software-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


def archive(path, root, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as output:
        for name, value in files.items():
            member = tarfile.TarInfo(root + "/" + name)
            member.size = len(value)
            member.mode = 0o755 if name.endswith(".sh") else 0o644
            output.addfile(member, io.BytesIO(value))
    return path


class WorkflowConfiguration(unittest.TestCase):
    def test_acceptance_variants_have_independent_concurrency_and_publish_source_lock(self):
        workflow=(ROOT/".github/workflows/build-rc01.yml").read_text()
        group=re.search(r"^  group: (.+)$",workflow,re.M)[1]
        for field in ("inputs.include_dae","inputs.software_updates","inputs.go_bootstrap","inputs.replay_run_id"):
            self.assertIn(field,group)
        default=group.replace("${{ inputs.include_dae }}","false")
        daemon=group.replace("${{ inputs.include_dae }}","true")
        self.assertNotEqual(default,daemon)
        self.assertIn("  cancel-in-progress: false",workflow)
        self.assertIn("name: rc01-software-source-lock",workflow)
        self.assertIn(".rc01-sources/lock.json",workflow)
        self.assertIn("RC01_REPLAY_LOCK=",workflow)
        self.assertIn("software_updates:",workflow)
        self.assertIn("default: latest",workflow)


class ReleasePolicy(unittest.TestCase):
    def api(self, path):
        if path.endswith("/releases/latest"):
            return {"tag_name": "v1.2.3", "draft": False, "prerelease": False,
                    "published_at": "2026-01-01T00:00:00Z", "html_url": "https://github.com/example/app/releases/tag/v1.2.3"}
        return {"sha": "a"*40, "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}

    def test_stable_release_is_resolved_to_commit(self):
        release = sources.stable_release("example/app", self.api)
        self.assertEqual(release["version"], "1.2.3")
        self.assertEqual(release["commit"], "a"*40)
        self.assertIsInstance(release["epoch"], int)

    def test_prerelease_draft_unrecognized_tag_and_missing_commit_are_rejected(self):
        for change in ({"draft": True}, {"prerelease": True}, {"draft": None},
                       {"tag_name": "v2.0.0-rc1"}, {"tag_name": "nightly"}):
            def changed(path):
                data = self.api(path)
                if path.endswith("/releases/latest"):
                    data.update(change)
                return data
            with self.subTest(change=change), self.assertRaises(sources.SourceError):
                sources.stable_release("example/app", changed)
        def missing(path):
            data = self.api(path)
            if "/commits/" in path:
                data["sha"] = "main"
            return data
        with self.assertRaises(sources.SourceError):
            sources.stable_release("example/app", missing)

    def test_recipe_adapter_rejects_build_logic_changes(self):
        baseline = "PKG_VERSION:=1.0.0\nPKG_HASH:=" + "a"*64 + "\nGO_PKG_TAGS:=safe\n"
        current = baseline.replace("1.0.0", "1.0.1").replace("a"*64, "b"*64)
        sources.compatible_recipe(current, baseline, ["PKG_VERSION", "PKG_HASH"])
        with self.assertRaises(sources.SourceError):
            sources.compatible_recipe(current.replace("safe", "changed"), baseline, ["PKG_VERSION", "PKG_HASH"])
        with self.assertRaises(sources.SourceError):
            sources.compatible_recipe(current+"PKG_HASH:=skip\n", baseline, ["PKG_VERSION", "PKG_HASH"])

    def test_go_requirement_does_not_silently_select_an_old_compiler(self):
        self.assertEqual(sources.go_requirement({"go.mod": (b"module example.invalid/app\ngo 1.27.0\n", 0o644)}, "1.27.3"), "1.27.0")
        with self.assertRaises(sources.SourceError):
            sources.go_requirement({"go.mod": (b"go 1.28.0\n", 0o644)}, "1.27.3")


class BuildLock(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-software-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(sources, "ROOT", self.root).start()
        mock.patch.object(sources, "STATE", self.root / ".rc01-sources").start()
        for name in sources.PROTECTED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"reviewed platform input\n")
        config = self.root / "config/rc01/feeds.conf"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("".join(f"src-git {name} https://github.com/example/{name}.git^{'a'*40}\n"
                                  for name in ("packages", "luci", "routing", "nikki", "daede")))
        self.feeds = {name: {"url": f"https://github.com/example/{name}.git", "branch": "main", "commit": "b"*40}
                      for name in ("packages", "luci", "routing", "nikki", "daede")}
        self.lock = {"schema": 1, "mode": "latest", "platform_inputs": sources.platform_inputs(),
                     "feeds": self.feeds, "with_dae": False, "outputs": {}, "inputs": {},
                     "resolved_at": "2026-01-01T00:00:00Z", "source_date_epoch":1,"build_commit":"e"*40}

    def test_latest_feed_head_is_resolved_once_and_never_written_to_platform(self):
        calls = []
        def get(path):
            calls.append(path)
            return {"default_branch": "stable"} if "/commits/" not in path else {"sha": "b"*40}
        original = sources.platform_inputs()
        resolved = sources.feed_plan("latest", get)
        self.assertEqual({x["commit"] for x in resolved.values()}, {"b"*40})
        self.assertEqual({x["branch"] for x in resolved.values()}, {"stable"})
        self.assertEqual(len(calls), 10)
        self.assertEqual(sources.platform_inputs(), original)
        self.assertNotIn("kernel", sources.feeds_text({"feeds": resolved}))

    def test_locked_resolution_does_not_call_latest(self):
        resolved = sources.feed_plan("locked", lambda _: self.fail("network in locked mode"))
        self.assertEqual({x["commit"] for x in resolved.values()}, {"a"*40})

    def test_fresh_or_incomplete_plan_preserves_unowned_feed_changes(self):
        feed = self.root / "feeds/packages"
        feed.mkdir(parents=True)
        marker = feed / "manual-file"
        marker.write_text("preserve manual work")
        for incomplete in (False, True):
            with self.subTest(incomplete=incomplete):
                state = self.root / ".rc01-sources/lock.json"
                if incomplete:
                    sources.write_json(state, {**self.lock, "prepared": False})
                with mock.patch.object(sources, "feed_plan", return_value=self.feeds), \
                     mock.patch.object(sources, "run", return_value="e"*40), \
                     mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1"}), \
                     mock.patch.object(sources, "generated_feed_changes", side_effect=sources.SourceError("unowned changes")):
                    with self.assertRaises(sources.SourceError):
                        sources.plan("latest")
                self.assertEqual(marker.read_text(), "preserve manual work")
                if not incomplete:
                    self.assertFalse(state.exists())

    def test_serialized_sidecars_follow_the_current_optional_variant(self):
        lock = {"package_manifest": {"packages": []}, "optional_dae": {"version": "fixture"},
                "dae_patch": "From: RC01 build maintainers\n"}
        sources.write_source_artifacts(lock)
        state = self.root / ".rc01-sources"
        self.assertEqual(json.loads((state / "dae-sources.json").read_text()), lock["optional_dae"])
        sources.write_source_artifacts({"package_manifest": {"packages": []}})
        self.assertFalse((state / "dae-sources.json").exists())
        self.assertFalse((state / "dae.patch").exists())

    def test_replay_does_not_query_latest_and_rejects_platform_or_variant_drift(self):
        self.lock["prepared"] = True
        replay = self.root / "saved.json"
        sources.write_json(replay, self.lock)
        with mock.patch.dict(os.environ, {"RC01_WITH_DAE": "false"}), mock.patch.object(sources, "api", side_effect=AssertionError("latest queried")):
            sources.plan("latest", replay)
            self.assertTrue(sources.load_lock()["replay"])
            (self.root / sources.PROTECTED[0]).write_bytes(b"changed platform")
            with self.assertRaises(sources.SourceError):
                sources.plan("latest", replay)
        (self.root / sources.PROTECTED[0]).write_bytes(b"reviewed platform input\n")
        with mock.patch.dict(os.environ, {"RC01_WITH_DAE": "true"}), self.assertRaises(sources.SourceError):
            sources.plan("latest", replay)

    def test_exact_outputs_are_atomic_idempotent_and_drift_is_refused(self):
        first = self.root / "feeds/packages/net/tailscale/Makefile"
        second = self.root / "feeds/nikki/mihomo-meta/Makefile"
        for path in (first, second):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"before")
            sources.record_output(self.lock, str(path.relative_to(self.root)), b"before", b"after")
        second.write_bytes(b"local change")
        with self.assertRaises(sources.SourceError):
            sources.apply_outputs(self.lock)
        self.assertEqual(first.read_bytes(), b"before")
        second.write_bytes(b"before")
        sources.apply_outputs(self.lock)
        sources.apply_outputs(self.lock)
        self.assertEqual(first.read_bytes(), b"after")
        second.write_bytes(b"drift after preparation")
        self.lock["prepared"] = True
        with mock.patch.object(sources, "verify_feeds"), self.assertRaises(sources.SourceError):
            sources.verify(self.lock)

    def test_verify_deleted_outputs_require_missing_paths_and_preserve_all_peers(self):
        name = "feeds/nikki/nikki/stale-file"
        target = self.root / name
        target.parent.mkdir(parents=True)
        sources.record_output(self.lock, name, b"old upstream file", None)
        peer_name = "feeds/packages/net/tailscale/Makefile"
        peer = self.root / peer_name
        peer.parent.mkdir(parents=True)
        peer.write_bytes(b"reviewed recipe")
        peer.chmod(0o644)
        sources.record_output(self.lock, peer_name, None, peer.read_bytes())
        self.lock["prepared"] = True
        lock_path = self.root / ".rc01-sources/lock.json"
        sources.write_json(lock_path, self.lock)
        sources.write_preparation_phase(self.lock, "complete")
        phase_path = lock_path.with_name("preparation.json")
        with mock.patch.object(sources, "verify_feeds"):
            sources.verify(self.lock)
            for kind in ("directory", "fifo", "symlink", "regular"):
                with self.subTest(kind=kind):
                    if kind == "directory": target.mkdir()
                    elif kind == "fifo": os.mkfifo(target)
                    elif kind == "symlink": target.symlink_to(peer)
                    else: target.write_bytes(b"unexpected file")
                    paths = (target, peer, lock_path, phase_path)
                    before = [(p.lstat(), p.read_bytes() if p.is_file() and not p.is_symlink() else None) for p in paths]
                    with self.assertRaises(sources.SourceError):
                        sources.verify(self.lock)
                    after = [(p.lstat(), p.read_bytes() if p.is_file() and not p.is_symlink() else None) for p in paths]
                    for (old, old_bytes), (new, new_bytes) in zip(before, after):
                        for attr in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns"):
                            self.assertEqual(getattr(old, attr), getattr(new, attr))
                        self.assertEqual(old_bytes, new_bytes)
                    if kind == "directory": target.rmdir()
                    else: target.unlink()
            sources.verify(self.lock)

    def test_replay_cannot_write_platform_or_follow_symlink(self):
        for name in ("target/linux/kernel", "feeds/../target/linux/kernel", "feeds/packages/net/../../../../bootloader"):
            with self.subTest(name=name), self.assertRaises(sources.SourceError):
                sources.record_output(self.lock, name, None, b"unreviewed")
        outside = self.root / "outside"
        outside.write_bytes(b"keep")
        link = self.root / "feeds/packages/net/tailscale/Makefile"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        with self.assertRaises(sources.SourceError):
            sources.record_output(self.lock, str(link.relative_to(self.root)), b"keep", b"bad")
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_corrupt_postimage_and_unknown_feed_commit_are_rejected(self):
        name = "feeds/packages/net/tailscale/Makefile"
        sources.record_output(self.lock, name, None, b"reviewed")
        self.lock["outputs"][name]["content"] = base64.b64encode(b"altered").decode()
        with self.assertRaises(sources.SourceError):
            sources.apply_outputs(self.lock)
        with mock.patch.object(sources, "run", side_effect=[str(self.root / "feeds/packages"), "c"*40]), self.assertRaises(sources.SourceError):
            sources.verify_feeds(self.lock)

    def test_archive_reader_rejects_traversal_or_multiple_roots(self):
        path = archive(self.root / "bad.tar.gz", "root", {"../bad": b"bad"})
        with self.assertRaises(sources.SourceError):
            sources.archive_members(path)
        path = archive(self.root / "good.tar.gz", "root", {"go.mod": b"go 1.27.0\n"})
        self.assertEqual(sources.archive_members(path)["go.mod"][0], b"go 1.27.0\n")

    def test_download_sha_mismatch_does_not_replace_existing_file(self):
        path = self.root / "download.tar.gz"
        path.write_bytes(b"preserve previous file")
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        with mock.patch.object(sources.urllib.request, "urlopen", return_value=Response(b"wrong source")):
            with self.assertRaises(sources.SourceError):
                sources.download("https://github.com/example/app/source.tar.gz", path, "a"*64)
        self.assertEqual(path.read_bytes(), b"preserve previous file")
        self.assertFalse(path.with_suffix(path.suffix+".partial").exists())

    def test_replay_platform_binding_includes_dts_and_driver_patch_contents(self):
        dts=self.root/"target/linux/qualcommbe/dts/fixture.dts"
        patch=self.root/"package/kernel/mac80211/patches/ath12k/fixture.patch"
        for path in (dts,patch):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"reviewed implementation")
        self.lock["platform_inputs"]=sources.platform_inputs()
        sources.validate_lock(self.lock)
        for path in (dts,patch):
            path.write_bytes(b"changed without version/metadata update")
            with self.subTest(path=path.name),self.assertRaises(sources.SourceError):sources.validate_lock(self.lock)
            path.write_bytes(b"reviewed implementation")

    def test_next_build_restores_only_its_unchanged_generated_feed_outputs(self):
        previous=copy.deepcopy(self.lock)
        for name in previous["feeds"]:
            feed=self.root/"feeds"/name;feed.mkdir(parents=True,exist_ok=True)
            (feed/"README").write_bytes(b"source fixture")
            if name=="packages":
                path=feed/"net/tailscale/Makefile";path.parent.mkdir(parents=True);path.write_bytes(b"original feed recipe")
            subprocess.run(["git","init","--quiet",str(feed)],check=True)
            subprocess.run(["git","-C",str(feed),"add","."],check=True)
            subprocess.run(["git","-C",str(feed),"-c","user.name=Build fixture","-c","user.email=build@example.invalid",
                            "-c","core.hooksPath=/dev/null","commit","--quiet","-m","feed"],check=True)
            previous["feeds"][name]["commit"]=subprocess.check_output(["git","-C",str(feed),"rev-parse","HEAD"],text=True).strip()
        name="feeds/packages/net/tailscale/Makefile"
        sources.record_output(previous,name,b"original feed recipe",b"generated recipe")
        path=self.root/name;path.write_bytes(b"generated recipe");path.chmod(0o644)
        unrelated=self.root/"feeds/packages/private-local-work"
        unrelated.write_bytes(b"preserve")
        with self.assertRaises(sources.SourceError):sources.reset_generated(previous)
        self.assertEqual(path.read_bytes(),b"generated recipe")
        self.assertEqual(unrelated.read_bytes(),b"preserve")
        unrelated.unlink()
        path.write_bytes(b"changed generated recipe")
        with self.assertRaises(sources.SourceError):sources.reset_generated(previous)
        self.assertEqual(path.read_bytes(),b"changed generated recipe")
        path.write_bytes(b"generated recipe")
        sources.reset_generated(previous)
        self.assertEqual(path.read_bytes(),b"original feed recipe")
        self.assertFalse(subprocess.check_output(["git","-C",str(self.root/"feeds/packages"),"status","--porcelain"],text=True))

    def test_replay_application_origins_are_project_specific(self):
        lock=copy.deepcopy(self.lock)
        entries=[]
        for feed,path,repo,package in (("packages","net/tailscale/Makefile","tailscale/tailscale","tailscale"),
                                       ("nikki","mihomo-meta/Makefile","MetaCubeX/mihomo","mihomo-meta")):
            entries.append({"feed":feed,"path":path,"version":"1.2.3","source_url":"https://github.com/"+repo+".git",
                            "source_commit":"d"*40,"source_archive":package+"-1.2.3.tar.gz","source_epoch":1,"source_sha256":"a"*64,
                            "release":{"repository":repo,"version":"1.2.3","tag":"v1.2.3","commit":"d"*40,"epoch":1}})
        lock["package_manifest"]={"packages":entries}
        lock["nikki"]={"repository":"nikkinikki-org/OpenWrt-nikki","commit":"d"*40,"tag":"v1.2.3","version":"1.2.3",
                       "source_url":"https://codeload.github.com/nikkinikki-org/OpenWrt-nikki/tar.gz/"+"d"*40,
                       "source_archive":"rc01-nikki-"+"d"*40+".tar.gz","source_sha256":"a"*64}
        sources.validate_lock(lock)
        for index in range(2):
            changed=copy.deepcopy(lock);changed["package_manifest"]["packages"][index]["release"]["repository"]="example/other"
            with self.assertRaises(sources.SourceError):sources.validate_lock(changed)
        for key,value in (("source_url","https://codeload.github.com/example/other/tar.gz/"+"d"*40),
                          ("source_archive","../outside.tar.gz")):
            changed=copy.deepcopy(lock);changed["nikki"][key]=value
            with self.assertRaises(sources.SourceError):sources.validate_lock(changed)
        lock["with_dae"]=True
        lock["optional_dae"]={"feed_commit":lock["feeds"]["daede"]["commit"],"source_archives":{}}
        for name in ("dae","daed"):
            filename=name+"-src-2026.01.01-"+"a"*12+".tar.gz"
            lock["optional_dae"]["source_archives"][name]={"filename":filename,"version":"2026.01.01","sha256":"a"*64,
                "url":"https://github.com/kenzok8/openwrt-daede/releases/download/"+name+"-src/"+filename}
        sources.validate_lock(lock)
        lock["optional_dae"]["source_archives"]["dae"]["url"]="https://github.com/example/other/source.tar.gz"
        with self.assertRaises(sources.SourceError):sources.validate_lock(lock)

    def test_actual_recipe_adapters_lock_source_sha_and_preserve_guards(self):
        baseline = ROOT / "config/rc01/software-baselines"
        recipes = {("packages", "net/tailscale/Makefile"): (baseline/"tailscale.Makefile").read_bytes(),
                   ("nikki", "mihomo-meta/Makefile"): (baseline/"mihomo.Makefile").read_bytes()}
        self.lock["go"] = {"version": "1.27.3"}
        def source(release, package, expected=None):
            return archive(self.root/"dl"/(package+"-"+release["version"]+".tar.gz"), package+"-"+release["version"],
                           {"go.mod": b"module example.invalid/app\ngo 1.27.0\n"})
        def release(repo):
            return {"repository": repo, "version": "1.2.3", "tag": "v1.2.3", "commit": "d"*40, "epoch": 1}
        with mock.patch.object(sources, "pristine", side_effect=lambda feed,path: recipes[(feed,path)]), mock.patch.object(sources, "stable_release", side_effect=release), mock.patch.object(sources, "git_archive_source", side_effect=source):
            sources.apps_adapter(self.lock)
        self.lock["inputs"]={}
        self.lock["nikki"]={"version":"1.2.3"}
        def go(derived):
            derived["go"]={"version":"1.27.3"}
            derived["inputs"]={}
        with mock.patch.object(sources,"pristine",side_effect=lambda feed,path: recipes[(feed,path)]), mock.patch.object(sources,"git_archive_source",side_effect=source), mock.patch.object(sources,"stable_release",side_effect=AssertionError("replay queried latest")), mock.patch.object(sources,"go_adapter",side_effect=go), mock.patch.object(sources,"nikki_adapter",side_effect=lambda derived,release: derived.update(nikki=copy.deepcopy(release))), mock.patch.object(sources,"dae_adapter"):
            sources.replay_sources(self.lock)
            altered=copy.deepcopy(self.lock)
            name="feeds/packages/net/tailscale/Makefile"
            payload=base64.b64decode(altered["outputs"][name]["content"])+b"\n# unreviewed recipe payload\n"
            altered["outputs"][name]["content"]=base64.b64encode(payload).decode()
            altered["outputs"][name]["after"]=sources.digest(payload)
            with self.assertRaisesRegex(sources.SourceError,"replay outputs differ"):
                sources.replay_sources(altered)
        self.assertEqual(len(self.lock["package_manifest"]["packages"]), 2)
        for entry in self.lock["package_manifest"]["packages"]:
            path = self.root / "dl" / entry["source_archive"]
            self.assertEqual(entry["source_sha256"], sources.digest(path.read_bytes()))
            self.assertEqual(entry["source_commit"], "d"*40)
            output = self.lock["outputs"][f"feeds/{entry['feed']}/{entry['path']}"]
            text = base64.b64decode(output["content"]).decode()
            self.assertNotIn("HASH:=skip", text)
            self.assertIn("PKG_SOURCE_VERSION:="+"d"*40, text)
            if entry["feed"] == "nikki":
                self.assertIn(sources.NEW_TIME, text)
                self.assertIn("CONFLICTS:=mihomo-alpha", text)
            else:
                self.assertIn("ts_omit_clientupdate", text)

    def test_nikki_uses_stable_release_source_not_unreleased_feed_files(self):
        release = {"repository": "example/nikki", "version": "1.2.3", "tag": "v1.2.3", "commit": "d"*40, "epoch": 1}
        def dl(url,path,expected=None):
            return archive(path,"nikki-release",{"nikki/Makefile":b"PKG_VERSION:=2026.01.01\n", "luci-app-nikki/Makefile":b"PKG_VERSION:=1.2.3\n", "nikki/files/start.sh":b"#!/bin/sh\nexit 0\n", "other-board/unsafe":b"ignored"})
        with mock.patch.object(sources,"stable_release",return_value=release), mock.patch.object(sources,"download",side_effect=dl), mock.patch.object(sources,"run",return_value="nikki/stale\nnikki/Makefile"), mock.patch.object(sources,"pristine",return_value=b"feed head"):
            sources.nikki_adapter(self.lock)
        self.assertIsNone(self.lock["outputs"]["feeds/nikki/nikki/stale"]["after"])
        self.assertNotIn("feeds/nikki/other-board/unsafe",self.lock["outputs"])
        self.assertEqual(self.lock["nikki"]["commit"],"d"*40)
        self.assertEqual(self.lock["feeds"]["nikki"]["commit"],"b"*40)


class RecipeClosure(unittest.TestCase):
    """Exercise real build recipes and local patches with offline source assets."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-recipe-closure-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(mock.patch.stopall)
        self.baselines = self.root / "baselines"
        import shutil
        shutil.copytree(ROOT / "config/rc01/software-baselines", self.baselines)
        shutil.copytree(ROOT / "config/rc01/patches", self.root / "config/rc01/patches")
        mock.patch.object(sources, "ROOT", self.root).start()
        mock.patch.object(sources, "STATE", self.root / ".rc01-sources").start()
        mock.patch.object(sources, "BASELINES", self.baselines).start()
        self.manifest = json.loads((self.baselines / "recipe-closure.json").read_text())
        self.data = {}
        self.modes = {}
        for group, feed in (("go", "packages"), ("daede", "daede")):
            self.data[feed], self.modes[feed] = {}, {}
            for name, item in self.manifest[group]["files"].items():
                # The real Go inputs and all daemon patch preimages are kept
                # as public source snapshots. Other lifecycle files use small
                # synthetic bytes; their exact names/modes/hash closure is real.
                payload = ((self.baselines / item["baseline"]).read_bytes() if item.get("baseline")
                           else b"public auxiliary source fixture: " + name.encode() + b"\n")
                item["sha256"] = sources.digest(payload)
                self.data[feed][name] = payload
                self.modes[feed][name] = item["mode"]
        (self.baselines / "recipe-closure.json").write_text(json.dumps(self.manifest))
        mock.patch.object(sources, "pristine", side_effect=lambda feed, name: self.data[feed][name]).start()
        def tree(*args, **kwargs):
            if "ls-tree" not in args:
                raise AssertionError("unexpected command in offline recipe fixture")
            feed = Path(args[2]).name
            roots = args[args.index("--")+1:]
            return "\n".join(f"100{self.modes[feed][name]:03o} {name}" for name in self.data[feed]
                             if any(name == root or name.startswith(root+"/") for root in roots))
        mock.patch.object(sources, "run", side_effect=tree).start()
        self.lock = {"with_dae": True, "feeds": {"packages": {"commit": "a"*40}, "daede": {"commit": "b"*40}},
                     "go": {"version": "1.27.3"}, "inputs": {}, "outputs": {}}

    def change_field(self, feed, name, key, value):
        self.data[feed][name] = sources.replace_field(self.data[feed][name].decode(), key, value).encode()

    def daemon_archives(self, version="2026.12.01"):
        paths = {}
        for name, modpath in (("dae", "core/go.mod"), ("daed", "wing/go.mod")):
            path = archive(self.root / (name+".tar.gz"), name+"-fixture", {modpath: b"module example.invalid/core\ngo 1.27.0\n"})
            hashed = sources.digest(path.read_bytes())
            for key, value in (("PKG_VERSION", version), ("PKG_RELEASE", "5"), ("PKG_HASH", hashed),
                               ("PKG_SOURCE", name+"-src-"+version+"-"+hashed[:12]+".tar.gz")):
                self.change_field("daede", name+"/Makefile", key, value)
            paths[name] = path
        pins = self.data["daede"]["ci/pins.env"].decode().replace("2026.11.09", version)
        pins = re.sub(r"(?m)^([A-Z_]+_COMMIT)=.*$", lambda m: m[1]+"="+"c"*40, pins)
        self.data["daede"]["ci/pins.env"] = pins.encode()
        def download(url, path, expected):
            name = "daed" if "/daed-src/" in url else "dae"
            self.assertEqual(sources.digest(paths[name].read_bytes()), expected)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(paths[name].read_bytes())
            return path
        return download

    def test_go_stable_patch_update_replays_exact_local_toolchain_patch(self):
        compiler = "lang/golang/golang1.27/Makefile"
        self.change_field("packages", compiler, "GO_VERSION_PATCH", "9")
        self.change_field("packages", compiler, "PKG_HASH", "b"*64)
        self.change_field("packages", compiler, "PKG_RELEASE", "2")
        self.change_field("packages", "lang/golang/golang-bootstrap/Makefile", "PKG_RELEASE", "2")
        sources.go_adapter(self.lock)
        self.assertEqual(self.lock["go"]["version"], "1.27.9")
        self.assertEqual(self.lock["go"]["bootstrap"], "1.24.13")
        self.assertEqual(self.lock["go"]["recipe_sha256"], sources.digest(self.data["packages"][compiler]))
        self.assertEqual(len(self.lock["inputs"]), 12)
        self.assertIn("feeds/packages/lang/golang/golang-values.mk", self.lock["inputs"])
        for item in self.lock["outputs"].values():
            self.assertIn(b"GOTOOLCHAIN=local", base64.b64decode(item["content"]))

    def test_go_rejects_host_compile_bootstrap_install_and_secondary_helper_changes(self):
        compiler = "lang/golang/golang1.27/Makefile"
        bootstrap = "lang/golang/golang-bootstrap/Makefile"
        mutations = ((compiler, lambda b: b+b"\ndefine Host/Compile\n\tGOTOOLCHAIN=auto go version\nendef\n"),
                     (bootstrap, lambda b: b.replace(b"define Host/Compile", b"define Host/Compile\n\tGOTOOLCHAIN=auto go version")),
                     (bootstrap, lambda b: b.replace(b"$(INSTALL_BIN)", b"$(INSTALL_DATA)", 1)),
                     (compiler, lambda b: b.replace(b"GO_BOOTSTRAP_VERSION:=bootstrap", b"GO_BOOTSTRAP_VERSION:=1.25")),
                     ("lang/golang/golang-package.mk", lambda b: b+b"\nGO_PKG_TAGS+=unreviewed\n"),
                     ("lang/golang/golang-build.sh", lambda b: b+b"\nGOTOOLCHAIN=auto go version\n"))
        for name, mutate in mutations:
            original = self.data["packages"][name]
            self.data["packages"][name] = mutate(original)
            with self.subTest(path=name), self.assertRaises(sources.SourceError):
                sources.go_adapter(copy.deepcopy(self.lock))
            self.data["packages"][name] = original

    def test_selected_recipe_tree_addition_deletion_type_and_mode_drift_fail(self):
        for feed, group in (("packages", "go"), ("daede", "daede")):
            first = next(iter(self.data[feed]))
            data = self.data[feed].pop(first)
            with self.subTest(group=group, drift="deletion"), self.assertRaises(sources.SourceError):
                sources.recipe_closure(feed, group, {})
            self.data[feed][first] = data
            extra = "lang/golang/golang1.27/patches/unreviewed.patch" if feed == "packages" else "dae/patches/unreviewed.patch"
            self.data[feed][extra], self.modes[feed][extra] = b"new patch", 0o644
            with self.subTest(group=group, drift="addition"), self.assertRaises(sources.SourceError):
                sources.recipe_closure(feed, group, {})
            del self.data[feed][extra]
            original = self.modes[feed][first]
            self.modes[feed][first] = 0o755 if original != 0o755 else 0o644
            with self.subTest(group=group, drift="permission"), self.assertRaises(sources.SourceError):
                sources.recipe_closure(feed, group, {})
            self.modes[feed][first] = original

    def test_daemon_legal_version_hash_pin_and_luci_updates_keep_actual_patch_guards(self):
        download = self.daemon_archives()
        self.change_field("daede", "luci-app-daede/Makefile", "PKG_VERSION", "1.16")
        self.change_field("daede", "luci-app-daede/Makefile", "PKG_RELEASE", "2")
        with mock.patch.object(sources, "download", side_effect=download), mock.patch.object(sources, "api", side_effect=AssertionError("replay queried latest")):
            sources.dae_adapter(self.lock, replay=True)
        self.assertEqual(self.lock["optional_dae"]["source_archives"]["dae"]["version"], "2026.12.01")
        self.assertEqual(self.lock["optional_dae"]["source_archives"]["dae"]["local_release"], 6)
        self.assertEqual(self.lock["optional_dae"]["upstream_pins"]["CORE_COMMIT"], "c"*40)
        self.assertEqual(self.lock["optional_dae"]["kernel_btf"], {"source": "actual-built-kernel", "shadow_vmlinux_btf_allowed": False})
        self.assertNotIn("core_delta", self.lock["optional_dae"])
        guard = base64.b64decode(self.lock["outputs"]["feeds/daede/dae/files/dae-guard"]["content"])
        self.assertIn(b"wait", guard)
        self.assertIn(b"Never remove shared BPF pins", guard)
        ui = base64.b64decode(self.lock["outputs"]["feeds/daede/luci-app-daede/Makefile"]["content"])
        self.assertIn(b"PKG_VERSION:=1.16", ui)
        self.assertIn(b"PKG_RELEASE:=3", ui)
        self.assertIn(b"default PACKAGE_$(PKG_NAME)_dae", ui)
        updater = base64.b64decode(self.lock["outputs"]["feeds/daede/luci-app-daede/root/usr/share/luci-app-daede/update-pkg.sh"]["content"])
        self.assertIn(b"online core/UI upgrades are disabled", updater)

    def test_daemon_rejects_tags_btf_dependency_install_and_lifecycle_changes_before_download(self):
        mutations = (("dae/Makefile", lambda b: b.replace(b"GO_PKG_TAGS:=trace,timetzdata", b"GO_PKG_TAGS:=timetzdata")),
                     ("dae/Makefile", lambda b: b.replace(b"default DAE_USE_KERNEL_BTF", b"default DAE_USE_VMLINUX_BTF")),
                     ("dae/Makefile", lambda b: b.replace(b"+kmod-sched-bpf", b"+kmod-unreviewed")),
                     ("dae/Makefile", lambda b: b.replace(b"$(INSTALL_BIN)", b"$(INSTALL_DATA)", 1)),
                     ("daed/Makefile", lambda b: b+b"\ndefine Host/Compile\n\tGOTOOLCHAIN=auto go version\nendef\n"),
                     ("luci-app-daede/Makefile", lambda b: b.replace(b"+luci-base", b"+luci-base +unreviewed")),
                     ("dae/files/dae.init", lambda b: b+b"\nstart_service() { true; }\n"),
                     ("daed/patches/0005-bound-startup-network-wait.patch", lambda b: b+b"unreviewed source patch\n"),
                     ("vmlinux-btf/Makefile", lambda b: b+b"\nBTF_SOURCE:=shadow\n"),
                     ("ci/pins.env", lambda b: b+b"NEW_COMPONENT_COMMIT="+b"d"*40+b"\n"))
        for name, mutate in mutations:
            original = self.data["daede"][name]
            self.data["daede"][name] = mutate(original)
            with self.subTest(path=name), mock.patch.object(sources, "download", side_effect=AssertionError("download before recipe validation")), self.assertRaises(sources.SourceError):
                sources.dae_adapter(copy.deepcopy(self.lock), replay=True)
            self.data["daede"][name] = original

    def test_replay_compares_all_derived_provenance_and_drops_unverified_release_fields(self):
        download = self.daemon_archives()
        self.lock.update(package_manifest={"packages": []}, nikki={"version": "1.2.3"})
        with mock.patch.object(sources, "download", side_effect=download):
            sources.dae_adapter(self.lock, replay=True)
        expected = copy.deepcopy(self.lock)
        def go(lock):
            lock["go"] = copy.deepcopy(expected["go"])
            lock["inputs"] = {name: hashed for name, hashed in expected["inputs"].items() if not name.startswith("feeds/daede/")}
        def apps(lock, _):
            lock["package_manifest"] = copy.deepcopy(expected["package_manifest"])
        def nikki(lock, _):
            lock["nikki"] = copy.deepcopy(expected["nikki"])
        with mock.patch.object(sources, "go_adapter", side_effect=go), mock.patch.object(sources, "apps_adapter", side_effect=apps), mock.patch.object(sources, "nikki_adapter", side_effect=nikki), mock.patch.object(sources, "download", side_effect=download):
            sources.replay_sources(self.lock)
            for key in ("package_manifest", "go", "optional_dae", "nikki"):
                altered = copy.deepcopy(expected)
                altered[key]["unverified_fact"] = "not derived from source"
                with self.subTest(field=key), self.assertRaisesRegex(sources.SourceError, "replay "+key+" differ"):
                    sources.replay_sources(altered)
            altered = copy.deepcopy(expected)
            altered["dae_patch"] += "\n# unverified patch provenance\n"
            with self.assertRaisesRegex(sources.SourceError, "replay dae_patch differ"):
                sources.replay_sources(altered)
        release = {"repository": "example/app", "tag": "v1.2.3", "version": "1.2.3", "commit": "c"*40, "epoch": 1,
                   "unverified_fact": "external lock", "release_url": "https://example.invalid/unverified"}
        self.assertEqual(set(sources.canonical_release(release)), {"repository", "tag", "version", "commit", "epoch"})

    def locked_fixture(self, with_dae=True):
        manifest = json.loads((self.root / "config/rc01/patches/packages/manifest.json").read_text())
        for entry in manifest["packages"]:
            feed, name = entry["feed"], entry["path"]
            baseline = "tailscale.Makefile" if feed == "packages" else "mihomo.Makefile"
            self.data.setdefault(feed, {})[name] = (self.baselines / baseline).read_bytes()
            self.lock["feeds"][feed] = {"commit": entry["feed_revision"]}
        pinned = json.loads((self.root / "config/rc01/patches/daede/sources.json").read_text())
        self.lock["feeds"]["daede"]["commit"] = pinned["feed_commit"]
        self.lock["mode"] = "locked"
        self.lock["with_dae"] = with_dae
        self.lock.pop("go", None)
        for feed, files in self.data.items():
            for name, payload in files.items():
                path = self.root / "feeds" / feed / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                path.chmod(0o644)
        return pinned

    def test_locked_finish_derives_go_inputs_exact_patches_and_replay_provenance_offline(self):
        pinned = self.locked_fixture()
        expected = copy.deepcopy(self.lock)
        with mock.patch.object(sources, "api", side_effect=AssertionError("locked queried latest")), mock.patch.object(sources, "download", side_effect=AssertionError("locked fetched source")):
            sources.locked_sources(expected)
            sources.apply_outputs(expected)
            with mock.patch.object(sources, "load_lock", return_value=self.lock), mock.patch.object(sources, "validate_lock", side_effect=lambda lock: lock), mock.patch.object(sources, "verify_feeds"), mock.patch.object(sources, "generated_feed_changes"):
                sources.write_preparation_phase(self.lock, "locked-pending")
                sources.finish_locked()
            saved = json.loads((self.root / ".rc01-sources/lock.json").read_text())
            self.assertTrue(saved["prepared"])
            self.assertEqual(saved["go"]["version"], "1.27.3")
            self.assertEqual(saved["go"]["bootstrap"], "1.24.13")
            self.assertEqual(len(saved["go"]), 4)
            for name in ("golang1.27/Makefile", "golang-bootstrap/Makefile", "golang-values.mk"):
                self.assertIn("feeds/packages/lang/golang/"+name, saved["inputs"])
            self.assertEqual(saved["outputs"], expected["outputs"])
            self.assertEqual(saved["optional_dae"], pinned)
            saved["replay"] = True
            with mock.patch.object(sources, "load_lock", return_value=saved), mock.patch.object(sources, "validate_lock", side_effect=lambda lock: lock), mock.patch.object(sources, "verify_feeds"), mock.patch.object(sources, "generated_feed_changes"):
                sources.write_preparation_phase(saved, "locked-pending")
                sources.finish_locked()
                for key in ("go", "package_manifest", "optional_dae"):
                    altered = copy.deepcopy(saved)
                    altered[key]["unverified_fact"] = "external lock"
                    sources.write_preparation_phase(altered, "locked-pending")
                    with self.subTest(field=key), mock.patch.object(sources, "load_lock", return_value=altered), self.assertRaisesRegex(sources.SourceError, "locked replay "+key+" differ"):
                        sources.finish_locked()

    def test_completed_finish_and_prepare_preserve_user_chmod_and_all_peers(self):
        for with_dae in (False, True):
            with self.subTest(with_dae=with_dae):
                self.locked_fixture(with_dae)
                expected = copy.deepcopy(self.lock)
                sources.locked_sources(expected)
                sources.apply_outputs(expected)
                with mock.patch.object(sources, "validate_lock", side_effect=lambda lock: lock), \
                     mock.patch.object(sources, "verify_feeds"), mock.patch.object(sources, "generated_feed_changes"):
                    with mock.patch.object(sources, "load_lock", return_value=self.lock):
                        sources.write_preparation_phase(self.lock, "locked-pending")
                        sources.finish_locked()
                    saved = json.loads((self.root / ".rc01-sources/lock.json").read_text())
                    target = self.root / "feeds/packages/lang/golang/golang-compiler.mk"
                    lock_path = self.root / ".rc01-sources/lock.json"
                    phase_path = lock_path.with_name("preparation.json")
                    paths = [self.root / name for name in saved["outputs"]] + [lock_path, phase_path]
                    def snapshot():
                        return {str(path.relative_to(self.root)): sources.file_snapshot(path) for path in paths}
                    for phase in ("complete", "compatible-old-lock"):
                        if phase == "complete": sources.write_preparation_phase(saved, "complete")
                        else: phase_path.unlink()
                        for mode in (0o755, 0o444, 0o4644, 0o2644, 0o1644):
                            for action in (sources.verify, sources.finish_locked, sources.prepare):
                                with self.subTest(phase=phase, mode=oct(mode), action=action.__name__):
                                    target.chmod(mode)
                                    before = snapshot()
                                    with mock.patch.object(sources, "load_lock", return_value=saved), self.assertRaises(sources.SourceError):
                                        action()
                                    self.assertEqual(snapshot(), before)
                                    target.chmod(0o644)
                        before = snapshot()
                        with mock.patch.object(sources, "load_lock", return_value=saved):
                            sources.verify()
                            sources.finish_locked()
                            sources.prepare()
                        self.assertEqual(snapshot(), before)


class ResetGeneratedProtection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc01-reset-protection-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(sources, "ROOT", self.root).start()
        self.feed = self.root / "feeds/packages"
        self.names = ("feeds/packages/net/tailscale/Makefile", "feeds/packages/lang/golang/golang-compiler.mk")
        for name in self.names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original source "+name.encode())
            path.chmod(0o644)
        subprocess.run(["git", "init", "--quiet", str(self.feed)], check=True)
        self.git("add", ".")
        self.git("-c", "user.name=Reset fixture", "-c", "user.email=reset@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "public source fixture")
        self.lock = {"feeds": {"packages": {"commit": self.git("rev-parse", "HEAD")}}, "outputs": {}}
        for name in self.names:
            path = self.root / name
            sources.record_output(self.lock, name, path.read_bytes(), b"generated "+name.encode())
            path.write_bytes(b"generated "+name.encode())
        mock.patch.object(sources, "validate_lock", side_effect=lambda lock: lock).start()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.feed), *args], text=True).strip()

    def assert_peer_preserved(self):
        for name in self.names:
            path = self.root / name
            if path.is_file() and not path.is_symlink():
                self.assertEqual(path.read_bytes(), b"generated "+name.encode())

    def test_initial_chmod_content_symlink_directory_fifo_and_staged_drift_preserve_peers(self):
        target = self.root / self.names[0]
        for change in ("executable", "readonly", "setuid", "content", "symlink", "directory", "fifo", "staged"):
            original = target.read_bytes()
            original_mode = target.stat().st_mode & 0o7777
            linked = self.root / "public-link-target"
            linked.write_bytes(original)
            if change == "executable": target.chmod(0o755)
            elif change == "readonly": target.chmod(0o444)
            elif change == "setuid": target.chmod(0o4644)
            elif change == "content": target.write_bytes(b"local edit")
            elif change == "symlink": target.unlink(); target.symlink_to(linked)
            elif change == "directory": target.unlink(); target.mkdir()
            elif change == "fifo": target.unlink(); os.mkfifo(target)
            elif change == "staged": self.git("add", "net/tailscale/Makefile")
            before = sources.file_snapshot(self.root / self.names[1])
            with self.subTest(change=change), self.assertRaises(sources.SourceError):
                sources.reset_generated(self.lock)
            self.assertEqual(sources.file_snapshot(self.root / self.names[1]), before)
            if change in ("executable", "readonly", "setuid"):
                self.assertEqual(target.stat().st_mode & 0o7777, {"executable":0o755,"readonly":0o444,"setuid":0o4644}[change])
            elif change == "content": self.assertEqual(target.read_bytes(), b"local edit")
            elif change == "symlink": self.assertTrue(target.is_symlink()); self.assertEqual(linked.read_bytes(), original)
            elif change == "staged": self.assertEqual(self.git("diff", "--cached", "--name-only"), "net/tailscale/Makefile"); self.git("reset", "--quiet", "HEAD")
            if target.is_dir(): target.rmdir()
            elif target.is_symlink() or not target.is_file(): target.unlink()
            if target.exists(): target.chmod(original_mode)
            target.write_bytes(original); target.chmod(original_mode)

    def test_write_time_chmod_symlink_content_and_staged_drift_preserve_whole_batch(self):
        target = self.root / self.names[0]
        real_changes = sources.generated_feed_changes
        for change in ("mode", "symlink", "content", "staged"):
            original = target.read_bytes()
            calls = 0
            def change_before_second_check(previous, owned):
                nonlocal calls
                calls += 1
                if calls == 2:
                    if change == "mode": target.chmod(0o755)
                    elif change == "content": target.write_bytes(b"edit during preflight")
                    elif change == "symlink":
                        linktarget = self.root / "public-link-target"
                        linktarget.write_bytes(original)
                        target.unlink(); target.symlink_to(linktarget)
                    else: self.git("add", "net/tailscale/Makefile")
                return real_changes(previous, owned)
            before = sources.file_snapshot(self.root / self.names[1])
            with self.subTest(change=change), mock.patch.object(sources, "generated_feed_changes", side_effect=change_before_second_check), self.assertRaises(sources.SourceError):
                sources.reset_generated(self.lock)
            self.assertEqual(calls, 2)
            self.assertEqual(sources.file_snapshot(self.root / self.names[1]), before)
            if change == "mode": self.assertEqual(target.stat().st_mode & 0o7777, 0o755)
            elif change == "content": self.assertEqual(target.read_bytes(), b"edit during preflight")
            elif change == "symlink": self.assertTrue(target.is_symlink()); target.unlink()
            else: self.assertTrue(self.git("diff", "--cached", "--name-only")); self.git("reset", "--quiet", "HEAD")
            target.write_bytes(original); target.chmod(0o644)
        sources.reset_generated(self.lock)
        for name in self.names:
            self.assertEqual((self.root / name).read_bytes(), b"original source "+name.encode())

    def test_internal_symlink_parent_is_rejected_even_if_it_points_inside_feed(self):
        parent = self.root / self.names[0]
        parent = parent.parent
        destination = self.feed / "retained-public-source"
        parent.rename(destination)
        parent.symlink_to(destination, target_is_directory=True)
        before = sources.file_snapshot(self.root / self.names[1])
        with self.assertRaises(sources.SourceError): sources.reset_generated(self.lock)
        self.assertEqual(sources.file_snapshot(self.root / self.names[1]), before)
        self.assertEqual((destination / "Makefile").read_bytes(), b"generated "+self.names[0].encode())


class SourceMetadataIntegration(unittest.TestCase):
    def test_metadata_consumes_build_lock_and_rejects_later_recipe_drift(self):
        spec = importlib.util.spec_from_file_location("rc01_dynamic_metadata", ROOT / "scripts/rc01-source-metadata.py")
        metadata = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(metadata)
        with tempfile.TemporaryDirectory(prefix="rc01-metadata-fixture-") as temporary:
            root = Path(temporary)
            for name in [*sources.PROTECTED, "scripts/rc01-software-sources.py", "config/rc01/feeds.conf",
                         "config/rc01/patches/daede/sources.json", "package/network/services/dnsmasq/Makefile",
                         "package/network/services/dnsmasq/patches/010-Fix-zero-length-blockdata-allocation.patch",
                         "package/firmware/ipq-wifi/Makefile"]:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            (root / ".config").write_text("# CONFIG_PACKAGE_dae is not set\n")
            feeds = {}
            for line in (root / "config/rc01/feeds.conf").read_text().splitlines():
                _, name, pinned = line.split()
                feed = root / "feeds" / name
                feed.mkdir(parents=True)
                (feed / "README").write_bytes(b"public software source fixture\n")
                subprocess.run(["git", "init", "--quiet", str(feed)], check=True)
                subprocess.run(["git", "-C", str(feed), "add", "."], check=True)
                subprocess.run(["git", "-C", str(feed), "-c", "user.name=Source fixture", "-c", "user.email=source@example.invalid",
                                "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "fixture source"], check=True)
                commit = subprocess.check_output(["git", "-C", str(feed), "rev-parse", "HEAD"], text=True).strip()
                feeds[name] = {"url": pinned.rsplit("^",1)[0], "branch": "default", "commit": commit}
            manifest = []
            outputs = {}
            for feed, path, baseline in (("packages", "net/tailscale/Makefile", "tailscale.Makefile"),
                                         ("nikki", "mihomo-meta/Makefile", "mihomo.Makefile")):
                data = (ROOT / "config/rc01/software-baselines" / baseline).read_bytes()
                target = root / "feeds" / feed / path
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o644)
                hashed = sources.digest(data)
                outputs[f"feeds/{feed}/{path}"] = {"before":hashed,"after":hashed,"mode":0o644,
                                                    "content":base64.b64encode(data).decode()}
                manifest.append({"feed":feed,"path":path,"version":sources.field(data.decode(),"PKG_VERSION"),
                                 "applied_sha256":hashed,"source_sha256":"a"*64,
                                 "source_commit":"d"*40,"source_epoch":1,
                                 "source_archive":("tailscale" if feed=="packages" else "mihomo-meta")+"-"+sources.field(data.decode(),"PKG_VERSION")+".tar.gz",
                                 "source_url":"https://github.com/"+("tailscale/tailscale" if feed=="packages" else "MetaCubeX/mihomo")+".git",
                                 "release":{"repository":"tailscale/tailscale" if feed=="packages" else "MetaCubeX/mihomo",
                                            "version":sources.field(data.decode(),"PKG_VERSION"),"tag":"v"+sources.field(data.decode(),"PKG_VERSION"),"epoch":1,"commit":"d"*40}})
            for path, text in (("feeds/nikki/nikki/Makefile", "PKG_VERSION:=2026.01.01\nPKG_RELEASE:=1\n"),
                               ("feeds/packages/lang/golang/golang1.27/Makefile", "GO_VERSION_MAJOR_MINOR:=1.27\nGO_VERSION_PATCH:=3\n")):
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
            lock={"schema":1,"mode":"latest","prepared":True,"with_dae":False,"feeds":feeds,"outputs":outputs,
                  "platform_inputs":{name:sources.digest((root/name).read_bytes()) for name in sources.PROTECTED},
                  "resolved_at":"2026-01-01T00:00:00Z","source_date_epoch":1,"build_commit":"e"*40,"package_manifest":{"packages":manifest},
                  "nikki":{"repository":"nikkinikki-org/OpenWrt-nikki","tag":"v1.2.3","version":"1.2.3","commit":"d"*40,
                           "source_url":"https://codeload.github.com/nikkinikki-org/OpenWrt-nikki/tar.gz/"+"d"*40,
                           "source_archive":"rc01-nikki-"+"d"*40+".tar.gz","source_sha256":"a"*64}}
            with mock.patch.object(sources,"ROOT",root):lock["platform_inputs"]=sources.platform_inputs()
            state=root/".rc01-sources";state.mkdir()
            sources.write_json(state/"lock.json",lock)
            (state/"feeds.conf").write_text(sources.feeds_text(lock))
            with mock.patch.object(metadata,"ROOT",root),mock.patch.object(metadata,"local_patchset",return_value={"sha256":"a"*64}):
                result=metadata.metadata()
                self.assertEqual(result["software_resolution"]["mode"],"latest")
                self.assertEqual(result["software"]["tailscale"]["version"],manifest[0]["version"])
                self.assertEqual(result["feeds"],{name:item["commit"] for name,item in feeds.items()})
                self.assertFalse(result["optional_dae"]["enabled"])
                # The result has no fixture username, machine path or environment.
                self.assertNotIn(str(root),json.dumps(result))
                path=root/"feeds/packages/net/tailscale/Makefile"
                path.write_bytes(path.read_bytes()+b"# changed after prepare\n")
                with self.assertRaises(ValueError):metadata.metadata()


class RawGitArchiveCompatibility(unittest.TestCase):
    def test_archive_hash_is_independent_of_caller_umask(self):
        with tempfile.TemporaryDirectory(prefix="rc01-pack-fixture-") as temporary:
            root=Path(temporary)
            repo=root/"source";repo.mkdir()
            (repo/"nested").mkdir()
            (repo/"nested/go.mod").write_bytes(b"module example.invalid/app\ngo 1.27.0\n")
            (repo/"start.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
            (repo/"start.sh").chmod(0o755)
            subprocess.run(["git","init","--quiet",str(repo)],check=True)
            subprocess.run(["git","-C",str(repo),"add","."],check=True)
            subprocess.run(["git","-C",str(repo),"-c","user.name=Archive fixture","-c","user.email=archive@example.invalid",
                            "-c","core.hooksPath=/dev/null","commit","--quiet","-m","source"],check=True)
            commit=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
            epoch=int(subprocess.check_output(["git","-C",str(repo),"show","-s","--format=%ct","HEAD"],text=True))
            release={"repository":"example/app","version":"1.2.3","commit":commit,"epoch":epoch}
            real_run=subprocess.run
            def local_fetch(args,**kwargs):
                if args[0]=="git" and "fetch" in args:
                    args=list(args);args[-2]=str(repo)
                return real_run(args,**kwargs)
            with mock.patch.object(sources,"ROOT",root/"build"),mock.patch.object(sources.subprocess,"run",side_effect=local_fetch):
                (root/"build").mkdir()
                hashes=[]
                for mask in (0o022,0o002):
                    previous=os.umask(mask)
                    try:
                        path=sources.git_archive_source(release,"mihomo-meta")
                        hashes.append(sources.digest(path.read_bytes()))
                        with tarfile.open(path) as stream:
                            for item in stream.getmembers():
                                if item.isdir():self.assertEqual(item.mode,0o755)
                    finally:os.umask(previous)
                self.assertEqual(hashes[0],hashes[1])
                with self.assertRaises(sources.SourceError):
                    sources.git_archive_source(release,"mihomo-meta",expected="a"*64)


if __name__ == "__main__":
    unittest.main()
