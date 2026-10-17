"""Review fixture integrity, independent of private/local library data."""
import hashlib
import json
from pathlib import Path
import unittest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "knowledge-maintenance-reference.json"


class MaintenanceReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_complete_review_catalog_has_stable_identities(self):
        ref = self.reference
        self.assertEqual(ref["schema_version"], "knowledge-maintenance-reference/v1")
        self.assertFalse(ref["human_validated"])
        sources = {s["source_id"]: s for s in ref["sources"]}
        self.assertEqual(len(sources), 4)
        self.assertEqual(sorted(s["knowledge_point_count"] for s in sources.values()), [8, 9, 13, 15])
        points = ref["point_decisions"]
        self.assertEqual(ref["point_count"], 45)
        self.assertEqual(len(points), 45)
        self.assertEqual(len({p["point_id"] for p in points}), 45)
        for source_id, source in sources.items():
            self.assertEqual(source["acceptance_stage"], "approved")
            self.assertEqual(sum(p["source_id"] == source_id for p in points), source["knowledge_point_count"])
        for point in points:
            self.assertFalse(point["human_validated"])
            self.assertEqual(point["source_version"], sources[point["source_id"]]["source_version"])
            self.assertEqual(point["knowledge"]["id"], point["local_id"])
            for field in ("statement", "explanation", "scope", "uncertainty", "claim_ids", "result_ids"):
                self.assertIn(field, point["knowledge"])
            decision = point["candidate_decision"]
            self.assertIn(decision["status"], ("linked", "deferred"))
            if decision["status"] == "linked":
                self.assertTrue(decision["memberships"])
                self.assertEqual(len({m["concept_slug"] for m in decision["memberships"]}), len(decision["memberships"]))
                for membership in decision["memberships"]:
                    self.assertIn(membership["role"], ("required", "background"))
                    self.assertTrue(membership["reason"])
            else:
                self.assertTrue(decision["reason"])
                self.assertTrue(decision["reassess_when"])

    def test_cases_reference_review_points_and_isolate_synthetic_conflict(self):
        ref = self.reference
        points = {p["point_id"]: p for p in ref["point_decisions"]}
        self.assertTrue(ref["coverage_gaps"])
        synthetic = []
        real_point_ids = set()
        for case in ref["cases"]:
            self.assertFalse(case["human_validated"])
            for field in ("old", "new", "decision", "wiki_change", "acceptance"):
                self.assertTrue(case[field])
            if case["scenario"].startswith("synthetic"):
                synthetic.append(case)
                self.assertEqual(case["point_ids"], [])
                self.assertIn("合成", case["old"])
                self.assertIn("合成", case["new"])
            else:
                self.assertTrue(case["point_ids"])
                for point_id in case["point_ids"]:
                    self.assertIn(point_id, points)
                    self.assertTrue(points[point_id]["anchors"])
                    real_point_ids.add(point_id)
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(synthetic[0]["id"], "synthetic-conflict")
        self.assertTrue(any(p["candidate_decision"]["status"] == "deferred" for p in points.values()))
        self.assertTrue(any(len(p["candidate_decision"].get("memberships", [])) > 1 for p in points.values()))
        for point_id, point in points.items():
            if point_id not in real_point_ids:
                self.assertEqual(point["anchors"], [])

    def test_excerpt_offsets_hashes_and_legacy_snapshots_are_compact(self):
        self.assertLess(FIXTURE_PATH.stat().st_size, 200_000)
        for point in self.reference["point_decisions"]:
            for anchor in point["anchors"]:
                self.assertEqual(anchor["source_id"], point["source_id"])
                self.assertEqual(anchor["source_version"], point["source_version"])
                locator = anchor["locator"]
                self.assertRegex(locator["paragraph_id"], r"^p\d{4}-\d{4}$")
                self.assertGreater(locator["page"], 0)
                self.assertGreaterEqual(locator["quote_start"], 0)
                self.assertEqual(locator["quote_end"] - locator["quote_start"], len(anchor["quote"]))
                self.assertTrue(anchor["quote"])
                self.assertEqual(hashlib.sha256(anchor["quote"].encode()).hexdigest(), anchor["quote_sha256"])
        valid_ids = {p["point_id"] for p in self.reference["point_decisions"]}
        for snapshot in self.reference["concept_snapshots"]:
            self.assertNotIn("body", snapshot)
            self.assertEqual(snapshot["kind"], "legacy_index_context_only")
            self.assertTrue(snapshot["title"])
            self.assertTrue(snapshot["slug"])
            self.assertRegex(snapshot["version"], r"^[a-f0-9]{64}$")
            for assertion in snapshot["relevant_old_assertions"]:
                self.assertIn(assertion["point_id"], valid_ids)
                self.assertTrue(assertion["statement"])


if __name__ == "__main__":
    unittest.main()
