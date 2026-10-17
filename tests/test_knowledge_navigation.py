import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from station import documents, knowledge_navigation as navigation, knowledge_synthesis
from station.compiler import digest
from station.workspaces import Workspace


class KnowledgeNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace("preview", Path(self.temp.name))
        for path in (self.ws.raw, self.ws.originals, self.ws.wiki, self.ws.meta, self.ws.root / "parsed"):
            path.mkdir()
        self.sids = ["1234567890abcdef", "fedcba0987654321"]
        self.pids = []
        with sqlite3.connect(self.ws.db) as db:
            db.execute("CREATE TABLE paper_distillations(source_id TEXT PRIMARY KEY,source_version TEXT,page_slug TEXT,packet_json TEXT,reviews_json TEXT,published_at TEXT)")
            db.execute("CREATE TABLE knowledge_points(id TEXT PRIMARY KEY,source_id TEXT,local_id TEXT,source_version TEXT,data_json TEXT)")
            db.execute("CREATE TABLE concept_memberships(point_id TEXT,concept_slug TEXT)")
            db.execute("INSERT INTO concept_memberships VALUES ('existing-point','existing-reviewed-concept')")
            for index, sid in enumerate(self.sids):
                writer = PdfWriter()
                writer.add_blank_page(width=100, height=100)
                with (self.ws.originals / (sid + ".pdf")).open("wb") as stream:
                    writer.write(stream)
                record = {"id": sid, "kind": "paper", "title": "论文" + str(index), "pdf_path": sid + ".pdf",
                          "content_status": "full-text", "content_format": "pdf", "text": "Original paragraph."}
                (self.ws.raw / (sid + ".json")).write_text(json.dumps(record))
                version = documents.source_version(self.ws, sid)
                point = {"id": "k1", "proposition": "条件不同，结论保留各自的适用范围。" + str(index),
                         "explanation": "原样解释\n\n包含分段。", "scope": "仅本论文任务。",
                         "type": "机制", "transferability": "领域", "uncertainty": "并未验证跨域效果。",
                         "claim_ids": ["c1"], "evidence": {"passage_refs": [{"source_id": sid,
                             "source_version": version, "claim_id": "c1", "locator": {
                                 "page": 1, "paragraph_id": "p0001-0001", "start": 0, "end": 19}}]}}
                packet = {"source": {"source_id": sid, "source_version": version}, "title": record["title"],
                          "knowledge_points": [point], "claims": [{"id": "c1"}]}
                pid = "kp_" + hashlib.sha256((sid + ":k1").encode()).hexdigest()[:20]
                self.pids.append(pid)
                reviews = [{"conclusion": "ready", "request_independent": True, "blocking_issues": [],
                            "claim_reviews": [{"claim_id": "c1", "status": "supported", "reason": "Matches original."}]}]
                db.execute("INSERT INTO paper_distillations VALUES (?,?,?,?,?,?)", (sid, version, "paper-" + str(index), json.dumps(packet), json.dumps(reviews), "now"))
                db.execute("INSERT INTO knowledge_points VALUES (?,?,?,?,?)", (pid, sid, "k1", version, json.dumps(point)))
                (self.ws.wiki / ("paper-" + str(index) + ".md")).write_text("# 原解读\n\n内容和审核记录不能被改写。")
                (self.ws.meta / ("paper-" + str(index) + ".json")).write_text(json.dumps({"page_role": "paper", "page_kind": "concept"}))
                parsed = {"library_id": self.ws.library_id, "source_id": sid, "source_version": version,
                          "paragraphs": [{"id": "p0001-0001", "page": 1, "start": 0, "end": 19, "text": "Original paragraph."}]}
                (self.ws.root / "parsed" / (sid + ".json")).write_text(json.dumps(parsed))
        self.plan = {"concepts": [{"slug": "feedback-control", "title": "反馈控制", "question": "反馈如何影响行动？",
                    "point_ids": list(self.pids), "assignment_reasons": [{"point_id": pid, "reason": "同问题的不同研究。"} for pid in self.pids]}], "excluded": []}
        self.groups = [{"slug": "agent-learning", "title": "智能体学习", "overview": "按问题浏览研究。", "concept_slugs": ["feedback-control"]}]
        self.plan_path = self.ws.root / "plan" / "plan.json"
        self.plan_path.parent.mkdir()
        self.write_plan()

    def write_plan(self):
        papers, points, stale = knowledge_synthesis._inputs(self.ws)
        self.assertFalse(stale)
        identity = {"papers": {sid: {k: p[k] for k in ("source_version", "page_slug", "packet_json")} for sid, p in papers.items()},
                    "points": {pid: {k: p[k] for k in ("source_id", "local_id", "source_version", "data_json")} for pid, p in points.items()}}
        value = {"library_id": self.ws.library_id, "input_digest": knowledge_synthesis._digest(identity)}
        self.plan_path.write_text(json.dumps({**value, "plan": self.plan}))
        self.plan_path.with_name("input-identity.json").write_text(json.dumps({**value, "identity": identity}))

    def build(self):
        return navigation.build_navigation(self.ws, self.plan_path, self.groups)

    def test_publishes_only_navigation_with_exact_original_points_and_idempotent_rebuild(self):
        original = {p: p.read_bytes() for folder in (self.ws.raw, self.ws.originals, self.ws.root / "parsed", self.ws.wiki, self.ws.meta) for p in folder.glob("*")}
        with patch("station.wiki.call_llm", side_effect=AssertionError("No model may be used")):
            report = self.build()
            self.assertEqual(self.build()["status"], "unchanged")
        self.assertEqual(report["knowledge_points"], 2)
        self.assertEqual(report["synthesized_concepts_published"], 0)
        for p, value in original.items():
            self.assertEqual(p.read_bytes(), value)
        body = (self.ws.wiki / "idx-feedback-control.md").read_text()
        meta = json.loads((self.ws.meta / "idx-feedback-control.json").read_text())
        self.assertEqual(meta["page_role"], "knowledge_index")
        self.assertEqual(meta["synthesis_status"], "navigation_only")
        for index, sid in enumerate(self.sids):
            self.assertIn("条件不同，结论保留各自的适用范围。" + str(index), body)
            self.assertIn("原样解释\n\n包含分段。", body)
            self.assertIn("/page/paper-" + str(index) + "?library_id=preview", body)
            self.assertIn("/source/" + sid + "?page=1&paragraph_id=p0001-0001&parsed_version=", body)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM navigation_memberships").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT * FROM concept_memberships").fetchall(), [("existing-point", "existing-reviewed-concept")])
            self.assertEqual(db.execute("SELECT count(*) FROM publication_journal").fetchone()[0], 1)

    def test_changed_pdf_blocks_before_any_publication(self):
        with (self.ws.originals / (self.sids[0] + ".pdf")).open("ab") as stream:
            stream.write(b" changed original")
        with self.assertRaisesRegex(ValueError, "current reviewed"):
            self.build()
        self.assertFalse((self.ws.wiki / "idx-feedback-control.md").exists())

    def test_cross_library_plan_and_parser_snapshots_are_rejected(self):
        value = json.loads(self.plan_path.read_text())
        value["library_id"] = "other"
        self.plan_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "another library"):
            self.build()
        self.write_plan()
        parsed_path = self.ws.root / "parsed" / (self.sids[0] + ".json")
        value = json.loads(parsed_path.read_text()); value["library_id"] = "other"
        parsed_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "another library"):
            self.build()

    def test_plan_missing_or_duplicate_points_cannot_silently_drop_knowledge(self):
        self.plan["concepts"][0]["point_ids"].pop()
        self.plan["concepts"][0]["assignment_reasons"].pop()
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "omitted"):
            self.build()
        self.plan["concepts"][0]["point_ids"] = self.pids + [self.pids[0]]
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "multiply assigned"):
            self.build()

    def test_rejects_stale_same_id_point_payload(self):
        with sqlite3.connect(self.ws.db) as db:
            row = db.execute("SELECT packet_json FROM paper_distillations WHERE source_id=?", (self.sids[0],)).fetchone()
            packet = json.loads(row[0]); packet["knowledge_points"][0]["scope"] = "新的范围"
            db.execute("UPDATE paper_distillations SET packet_json=? WHERE source_id=?", (json.dumps(packet), self.sids[0]))
            db.execute("UPDATE knowledge_points SET data_json=? WHERE id=?", (json.dumps(packet["knowledge_points"][0]), self.pids[0]))
        with self.assertRaisesRegex(ValueError, "plan is stale"):
            self.build()

    def test_rejects_existing_unmanaged_body_and_does_not_publish_partial_batch(self):
        protected = self.ws.wiki / "idx-feedback-control.md"
        protected.write_text("# Existing independently reviewed concept")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            self.build()
        self.assertEqual(protected.read_text(), "# Existing independently reviewed concept")
        self.assertFalse((self.ws.wiki / "agent-learning.md").exists())

    def test_topics_require_exact_unique_assignment(self):
        self.groups[0]["concept_slugs"] = []
        with self.assertRaisesRegex(ValueError, "multiply assigned"):
            self.build()
        self.groups[0]["concept_slugs"] = ["feedback-control"]
        self.groups.append({**self.groups[0], "slug": "another-topic"})
        with self.assertRaisesRegex(ValueError, "multiply assigned"):
            self.build()

    def test_real_paragraph_missing_or_wrong_claim_blocks_without_writes(self):
        parsed_path = self.ws.root / "parsed" / (self.sids[0] + ".json")
        parsed = json.loads(parsed_path.read_text()); parsed["paragraphs"][0]["id"] = "another-paragraph"
        parsed_path.write_text(json.dumps(parsed))
        with self.assertRaisesRegex(ValueError, "paragraph is missing"):
            self.build()
        self.assertFalse((self.ws.wiki / "idx-feedback-control.md").exists())

    def test_database_failure_rolls_back_entire_navigation_batch(self):
        real_publish = navigation.publish
        def fail(ws, changes, proposal_id, database_callback):
            def broken(db):
                database_callback(db)
                raise RuntimeError("simulated publication interruption")
            return real_publish(ws, changes, proposal_id, database_callback=broken)
        with patch.object(navigation, "publish", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                self.build()
        self.assertFalse((self.ws.wiki / "idx-feedback-control.md").exists())
        self.assertFalse((self.ws.wiki / "agent-learning.md").exists())

    def test_rebuild_restores_missing_membership_index_without_rewriting_papers(self):
        self.build()
        with sqlite3.connect(self.ws.db) as db:
            db.execute("DELETE FROM navigation_memberships")
        self.assertEqual(self.build()["status"], "published")
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM navigation_memberships").fetchone()[0], 2)

    def test_unreviewed_packet_is_not_promoted_by_navigation(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute("UPDATE paper_distillations SET reviews_json='[]' WHERE source_id=?", (self.sids[0],))
        with self.assertRaisesRegex(ValueError, "independent content review"):
            self.build()
        self.assertFalse((self.ws.wiki / "idx-feedback-control.md").exists())

    def test_conditions_directory_label_does_not_rewrite_alignment_plan(self):
        self.plan["concepts"][0]["slug"] = "agent-conclusion-boundaries"
        self.groups[0]["concept_slugs"] = ["agent-conclusion-boundaries"]
        self.write_plan()
        before = self.plan_path.read_bytes()
        self.build()
        self.assertTrue((self.ws.wiki / "idx-agent-conclusion-boundaries.md").read_text().startswith("# 各研究的适用条件索引\n"))
        self.assertIn("[各研究的适用条件索引]", (self.ws.wiki / "agent-learning.md").read_text())
        self.assertEqual(self.plan_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
