"""Reject false ancestry and incomplete review claims using an actual Git DAG."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("provenance", ROOT / "scripts/rc01-source-metadata.py")
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


class IntegrationProvenance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rc01-ancestry-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.git("init", "-q")
        tree = self.git("mktree", input="")
        base = self.git("commit-tree", tree, input="base\n")
        self.local = self.git("commit-tree", tree, "-p", base, input="local\n")
        self.upstream = self.git("commit-tree", tree, "-p", base, input="upstream\n")
        self.merge = self.git("commit-tree", tree, "-p", self.local, "-p", self.upstream,
                              input="integrate\n")
        self.git("update-ref", "HEAD", self.merge)
        legacy = b'{"schema":1,"integration":"selected-commits"}\n'
        (self.root / "legacy.json").write_bytes(legacy)
        self.document = {"schema": 2,
                         "integration": {"mode": "upstream-merges-with-local-overrides",
                                         "imported_head": self.upstream},
                         "review": {"local_base": self.local, "inventory": "audit.json",
                                    "completed_unique_commits": 1},
                         "legacy_selection": {"path": "legacy.json",
                                              "sha256": hashlib.sha256(legacy).hexdigest()}}
        self.inventory = {"local": self.local, "upstream": self.upstream,
                          "records": [{"sha": self.upstream, "review_status": "complete",
                                       "review_depth": "actual diff and local comparison"}]}
        self.write_inventory()

    def git(self, *args, input=None):
        return subprocess.check_output(["git", *args], input=input, text=True,
                                       cwd=self.root, env=self.env).strip()

    def write_inventory(self):
        data = json.dumps(self.inventory).encode()
        (self.root / "audit.json").write_bytes(data)
        self.document["review"]["inventory_sha256"] = hashlib.sha256(data).hexdigest()

    def verify(self):
        return provenance.integration_metadata(self.document, self.root)

    def test_actual_merge_proves_both_parents_but_not_hardware(self):
        result = self.verify()
        self.assertEqual(result["source_head"], self.merge)
        self.assertTrue(result["ancestry_verified"])
        self.assertEqual(result["physical_acceptance"], "not-performed")

    def test_unmerged_upstream_cannot_be_claimed_by_metadata(self):
        self.git("update-ref", "HEAD", self.local)
        with self.assertRaisesRegex(ValueError, "ancestry"):
            self.verify()

    def test_duplicate_missing_extra_and_unreviewed_commits_rejected(self):
        good = copy.deepcopy(self.inventory)
        for records in (good["records"] * 2, [],
                        good["records"] + [{"sha": self.local}],
                        [{"sha": self.upstream, "review_status": "pending"}]):
            with self.subTest(records=records):
                self.inventory = {**good, "records": records}
                self.write_inventory()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_inventory_and_legacy_tampering_rejected(self):
        (self.root / "audit.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "inventory digest"):
            self.verify()
        self.write_inventory()
        (self.root / "legacy.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "legacy provenance digest"):
            self.verify()

    def test_wrong_endpoints_count_and_schema_rejected(self):
        self.inventory["local"] = self.upstream
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, "endpoints"):
            self.verify()
        self.inventory["local"] = self.local
        self.write_inventory()
        self.document["review"]["completed_unique_commits"] = 2
        with self.assertRaisesRegex(ValueError, "count"):
            self.verify()
        self.document["schema"] = 99
        with self.assertRaisesRegex(ValueError, "schema"):
            self.verify()

    def test_legacy_selection_stays_readable_without_merge_claim(self):
        result = provenance.integration_metadata({"schema": 1}, self.root)
        self.assertFalse(result["ancestry_claim"])


class MetadataSummary(unittest.TestCase):
    def test_dnsmasq_summary_tracks_the_structured_build_record(self):
        with tempfile.TemporaryDirectory(prefix="rc01-summary-") as temporary:
            path = Path(temporary) / "metadata.json"
            for release in (12, 13):
                record = {
                    "integration_proof": {"mode": "upstream-merges-with-local-overrides",
                                          "ancestry_verified": True},
                    "kernel": {"version": "6.18.60", "sha256": "a" * 64},
                    "wireless": {"version": "7.2", "release": 51},
                    "software": {"ipq-wifi": {"release": 7, "source_commit": "b" * 40},
                                 "dnsmasq": {"version": "2.93", "release": release,
                                             "zero_length_fix": "c" * 40}}}
                path.write_text(json.dumps(record))
                output = subprocess.check_output(
                    [sys.executable, "-B", str(ROOT / "scripts/rc01-source-metadata.py"),
                     "--summary", str(path)], text=True)
                values = dict(line.split("=", 1) for line in output.splitlines())
                self.assertEqual(values["dnsmasq"], "2.93-r" + str(release) + "@" + "c" * 40)
                self.assertNotIn("dnsmasq=", (ROOT / ".github/workflows/build-rc01.yml").read_text())
