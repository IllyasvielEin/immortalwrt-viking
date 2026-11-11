import base64
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rc01_public_artifacts", ROOT / "scripts/validate-rc01-public-artifacts.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PublicArtifacts(unittest.TestCase):
    def inspect(self, files, identities=()):
        with tempfile.TemporaryDirectory(prefix="rc01-public-artifacts-") as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            return guard.check([root], [re.compile(re.escape(value)) for value in identities])

    def test_decoded_source_lock_payload_is_checked(self):
        marker = "fixture-private-builder"
        payload = base64.b64encode(("From: " + marker + "\n").encode()).decode()
        result = self.inspect({"lock.json": json.dumps({"outputs": {"Makefile": {"content": payload}}})}, [marker])
        self.assertEqual(result["findings"], [{"file": "lock.json", "category": "builder-identity"}])
        self.assertNotIn(marker, json.dumps(result))

    def test_pem_and_credential_are_rejected_without_disclosing_values(self):
        value = "ghp_" + "x" * 36
        key_marker = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
        result = self.inspect({"build.log": value + "\n" + key_marker + "\n"})
        self.assertEqual({item["category"] for item in result["findings"]}, {"private-key", "credential"})
        self.assertNotIn(value, json.dumps(result))

    def test_actual_home_paths_fail_but_placeholders_and_ci_runner_pass(self):
        result = self.inspect({"bad.md": "/home/fixture-user/work/source\nC:\\Users\\fixture-user\\Documents\\file"})
        self.assertEqual(result["findings"], [{"file": "bad.md", "category": "personal-home"}])
        result = self.inspect({"good.md": "%USERPROFILE%\\Documents\\file\n<workspace>/source\n/home/runner/work/repo"})
        self.assertEqual(result["findings"], [])

    def test_upstream_attribution_public_key_and_source_hash_are_preserved(self):
        text = "From: Upstream Maintainer\nhttps://github.com/openwrt/openwrt\n" + "a" * 64
        result = self.inspect({"source.patch": text, "key.pub": "-----BEGIN PUBLIC KEY-----\n"})
        self.assertEqual(result["findings"], [])

    def test_invalid_json_is_not_silently_skipped(self):
        result = self.inspect({"lock.json": "{invalid"})
        self.assertEqual(result["findings"], [{"file": "lock.json", "category": "invalid-json-artifact"}])

    def test_symlink_cannot_hide_an_uploaded_text_artifact(self):
        with tempfile.TemporaryDirectory(prefix="rc01-public-artifacts-") as directory:
            root = Path(directory)
            outside = root / "unpublished"
            outside.write_text("private fixture data")
            (root / "lock.json").symlink_to(outside)
            result = guard.check([root], [])
            self.assertEqual(result["findings"], [{"file": "lock.json", "category": "symlink-text-artifact"}])

    def test_workflow_checks_each_upload_category_before_publishing(self):
        workflow = (ROOT / ".github/workflows/build-rc01.yml").read_text()
        for category in ("source_privacy", "metrics_privacy", "image_privacy", "verified_privacy", "logs_privacy"):
            self.assertIn(f"id: {category}", workflow)
            self.assertIn(f"steps.{category}.outcome == 'success'", workflow)

    def test_build_guide_has_no_abbreviated_site_endpoint(self):
        guide = (ROOT / "docs/rc01-build.md").read_text()
        if re.search(r"(?<![0-9A-Za-z])(?:\.\d{1,3}){1,3}:\d+", guide):
            self.fail("build guide contains an abbreviated site address")
