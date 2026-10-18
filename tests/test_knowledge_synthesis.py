import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from station import compiler, documents, knowledge_synthesis as synthesis
from station.jobs import JobConflict
from station.workspaces import Workspace


SIDS = ["1234567890abcdef", "fedcba0987654321"]


def point_id(sid, local):
    return "kp_" + hashlib.sha256((sid + ":" + local).encode()).hexdigest()[:20]


class AlignmentTransportTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = 'kp_' + 'a' * 20, 'kp_' + 'b' * 20
        self.aliases = {'P0001': self.a, 'P0002': self.b}
        self.reply = {'concepts': [{'slug': 'evidence-selection', 'title': '证据选择',
            'question': '如何选择能回答问题的证据？', 'point_ids': ['P0001'],
            'assignment_reasons': {'P0001': '解释证据与问题的对应关系。'}}],
            'excluded': [{'point_id': 'P0002', 'reason': '保留不同问题，暂不合并。'}]}

    def test_compact_identities_expand_exactly_and_preserve_exclusions_and_raw_reply(self):
        decoded = synthesis._decode_alignment(self.reply, self.aliases)
        plan = synthesis._validate_plan(decoded, {self.a: {}, self.b: {}}, [], set())
        self.assertEqual(plan['concepts'][0]['point_ids'], [self.a])
        self.assertEqual(plan['concepts'][0]['assignment_reasons'][0]['point_id'], self.a)
        self.assertEqual(plan['excluded'][0]['point_id'], self.b)
        self.assertEqual(self.reply['concepts'][0]['point_ids'], ['P0001'])

    def test_maintenance_transport_retains_multiple_memberships_and_deferral(self):
        value = {'point_decisions': [
            {'point_id': 'P0001', 'disposition': 'linked', 'memberships': [{'concept_slug': 'first'}, {'concept_slug': 'second'}]},
            {'point_id': 'P0002', 'disposition': 'deferred', 'memberships': [], 'reason': '等待相关机制材料。'}],
            'concept_actions': [{'concept_slug': 'first', 'action': 'create', 'affected_point_ids': ['P0001']} ]}
        decoded = synthesis._decode_alignment(value, self.aliases)
        self.assertEqual(decoded['point_decisions'][0]['point_id'], self.a)
        self.assertEqual(len(decoded['point_decisions'][0]['memberships']), 2)
        self.assertEqual(decoded['point_decisions'][1]['point_id'], self.b)
        self.assertEqual(decoded['concept_actions'][0]['affected_point_ids'], [self.a])
        self.assertEqual(value['point_decisions'][0]['point_id'], 'P0001')

    def test_unknown_transport_identity_is_rejected_without_guessing(self):
        self.reply['concepts'][0]['point_ids'] = ['P9999']
        with self.assertRaisesRegex(ValueError, 'Unknown request-local knowledge point'):
            synthesis._decode_alignment(self.reply, self.aliases)

    def test_equivalent_id_field_is_expanded_without_leaking_aliases_into_page_inputs(self):
        self.reply['concepts'][0]['assignment_reasons'] = [{'id': 'P0001', 'reason': '支持问题。'}]
        self.reply['excluded'] = [{'id': 'P0002', 'reason': '暂时保留独立问题。'}]
        decoded = synthesis._decode_alignment(self.reply, self.aliases)
        self.assertEqual(decoded['concepts'][0]['assignment_reasons'], [{'point_id': self.a, 'reason': '支持问题。'}])
        self.assertEqual(decoded['excluded'][0]['point_id'], self.b)
        self.assertNotIn('id', decoded['excluded'][0])
        self.reply['concepts'][0]['assignment_reasons'][0]['point_id'] = 'P0002'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            synthesis._decode_alignment(self.reply, self.aliases)

    def test_completion_keeps_all_preserved_points_and_validates_the_complete_result(self):
        base = synthesis._decode_alignment(self.reply, self.aliases)
        base['excluded'] = []
        addition = {'concepts': [{'slug': 'evidence-selection', 'title': '候选标题', 'question': '候选问题',
            'point_ids': [self.b], 'assignment_reasons': [{'point_id': self.b, 'reason': '补齐不同条件。'}]}], 'excluded': []}
        merged = synthesis._merge_alignment_completion(base, addition)
        plan = synthesis._validate_plan(merged, {self.a: {}, self.b: {}}, [], set())
        self.assertEqual(plan['concepts'][0]['point_ids'], [self.a, self.b])
        self.assertEqual(plan['concepts'][0]['question'], self.reply['concepts'][0]['question'])
        self.assertEqual(base['concepts'][0]['point_ids'], [self.a])

    def test_completion_cannot_cross_libraries_or_point_versions(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ws = SimpleNamespace(root=Path(temp.name), library_id='preview')
        directory = ws.root / 'knowledge_synthesis'
        directory.mkdir()
        points = {self.a: {'data_json': '{}'}}
        papers = {'source': {'source_version': 'v1'}}
        value = {'library_id': 'another', 'point_digest': synthesis._completion_digest(points),
                 'source_versions': {'source': 'v1'}, 'pending_point_ids': [self.a]}
        path = directory / 'alignment-completion.json'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'different library inputs'):
            synthesis._load_alignment_completion(ws, papers, points)
        value.update(library_id='preview', point_digest='old-version')
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'different library inputs'):
            synthesis._load_alignment_completion(ws, papers, points)

    def test_short_ids_do_not_allow_an_omitted_or_multiply_assigned_point(self):
        self.reply['excluded'] = []
        decoded = synthesis._decode_alignment(self.reply, self.aliases)
        with self.assertRaisesRegex(ValueError, 'silently omitted'):
            synthesis._validate_plan(decoded, {self.a: {}, self.b: {}}, [], set())
        self.reply['concepts'][0]['point_ids'] = ['P0001', 'P0001', 'P0002']
        with self.assertRaisesRegex(ValueError, 'multiply assigned'):
            synthesis._validate_plan(synthesis._decode_alignment(self.reply, self.aliases), {self.a: {}, self.b: {}}, [], set())


class KnowledgeSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ws = Workspace("candidate-v2", Path(self.temporary.name))
        for folder in (self.ws.raw, self.ws.originals, self.ws.wiki, self.ws.meta):
            folder.mkdir()
        self.originals = {}
        self.points = []
        self.calls = []
        self.publications = []
        with sqlite3.connect(self.ws.db) as db:
            db.execute("CREATE TABLE paper_distillations(source_id TEXT PRIMARY KEY,source_version TEXT,page_slug TEXT,packet_json TEXT,reviews_json TEXT,published_at TEXT)")
            db.execute("CREATE TABLE knowledge_points(id TEXT PRIMARY KEY,source_id TEXT,local_id TEXT,source_version TEXT,data_json TEXT)")
            for index, sid in enumerate(SIDS):
                self.add_paper(db, sid, index)
        self.model = Mock(side_effect=self.respond)
        self.model.model_id = "test-synthesis-v1"
        self.original_patch = patch.object(compiler, "_originals", side_effect=lambda ws, ids: [self.originals[s] for s in sorted(ids)])
        self.original_patch.start()
        self.addCleanup(self.original_patch.stop)
        self.publisher = Mock(side_effect=self.publish)
        self.module_patch = patch.dict(sys.modules, {"station.paper_rebuild": SimpleNamespace(publish_concept=self.publisher)})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def add_paper(self, db, sid, index):
        method = "During an ongoing task, observations guide the next action." if index == 0 else "After a failed attempt, reflection guides the next trial."
        text = method + " The reported improvement depends on the quality of feedback; some evaluated tasks did not improve."
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with (self.ws.originals / (sid + ".pdf")).open("wb") as stream:
            writer.write(stream)
        record = {"id": sid, "kind": "paper", "title": "反馈研究" + str(index), "pdf_path": sid + ".pdf",
                  "content_status": "full-text", "content_format": "pdf", "text": text}
        (self.ws.raw / (sid + ".json")).write_text(json.dumps(record))
        version = documents.source_version(self.ws, sid)
        local_points = [{"id": "k-mechanism", "proposition": "当前执行中根据新观测调整下一步行动。" if index == 0 else "失败后的反思经验指导下一次尝试。",
                         "explanation": "区分执行内反馈和跨尝试经验。", "basis": "mechanism", "claim_ids": ["mechanism"],
                         "result_ids": [], "scope": "本文的方法定义。", "value": "解释反馈作用的时间位置。"},
                        {"id": "k-reliability", "proposition": "反馈的质量限制行为修正的效果。", "explanation": "有反馈不自动带来成功。",
                         "basis": "empirical", "claim_ids": ["reliability"], "result_ids": ["r1"], "scope": "本文测试任务。",
                         "value": "将反馈判定和决策改变联系起来。"}]
        claims = [{"id": "mechanism", "text": local_points[0]["proposition"], "scope": "本文方法定义。", "source_ids": [sid], "status": "supported",
                   "evidence_passages": [{"source_id": sid, "quote": text, "locator": {"paragraph_id": "p0001-0001", "page": 1}}]},
                  {"id": "reliability", "text": local_points[1]["proposition"], "scope": "本文测试任务。", "source_ids": [sid], "status": "supported",
                   "evidence_passages": [{"source_id": sid, "quote": text, "locator": {"paragraph_id": "p0001-0001", "page": 1}}]}]
        packet = {"title": record["title"], "source": {"source_version": version}, "knowledge_points": local_points,
                  "claims": claims, "results": [{"id": "r1", "observation": "部分测试有改善，另一些没有。", "claim_ids": ["reliability"]}]}
        slug = "paper-" + str(index)
        (self.ws.wiki / (slug + ".md")).write_text("# 论文解读\n\n已提炼的完整解读。")
        (self.ws.meta / (slug + ".json")).write_text(json.dumps({"page_kind": "concept", "page_role": "paper_reading", "title": record["title"]}))
        db.execute("INSERT INTO paper_distillations VALUES(?,?,?,?,?,?)", (sid, version, slug, json.dumps(packet), "[]", "now"))
        for point in local_points:
            identifier = point_id(sid, point["id"])
            self.points.append(identifier)
            db.execute("INSERT INTO knowledge_points VALUES(?,?,?,?,?)", (identifier, sid, point["id"], version, json.dumps(point)))
        self.originals[sid] = {"source_id": sid, "title": record["title"], "source_version": version, "parsed_version": "parsed-" + sid,
                               "kind": "paper", "paragraphs": [{"id": "p0001-0001", "page": 1, "start": 0, "end": len(text),
                                                                   "text": text, "method": "pypdf"}]}

    def plan(self):
        assignments = [("task-feedback-control", "任务内反馈控制", [self.points[0]]),
                       ("reflection-across-attempts", "跨尝试反思经验", [self.points[2]]),
                       ("feedback-reliability", "反馈驱动修正的可靠性", [self.points[1], self.points[3]])]
        return {"concepts": [{"slug": slug, "title": title, "question": "反馈怎样影响后续决定？", "point_ids": ids,
                              "assignment_reasons": [{"point_id": i, "reason": "本知识解释该稳定问题的机制或条件。"} for i in ids]}
                             for slug, title, ids in assignments], "excluded": []}

    def test_legacy_completion_does_not_authorize_new_maintenance(self):
        documents._save(self.ws.root / 'knowledge_synthesis' / 'alignment-completion.json', {
            'library_id': self.ws.library_id, 'base_plan': self.plan(), 'pending_point_ids': []})
        result = self.run_synthesis()
        self.assertEqual(result['status'], 'completed')
        request = next(c for c in self.calls if c.get('stage') == 'maintain')
        self.assertEqual(set(request['pending_point_ids']), set(self.points))
        self.assertEqual(self.publisher.call_count, 3)

    def decision(self, context):
        pending = set(context['pending_point_ids'])
        decisions, actions = [], []
        for concept in self.plan()['concepts']:
            ids = pending & set(concept['point_ids'])
            if not ids:
                continue
            slug = concept['slug']
            for pid in sorted(ids):
                decisions.append({'point_id': pid, 'disposition': 'linked', 'memberships': [
                    {'concept_slug': slug, 'role': 'core', 'reason': '本知识解释该稳定问题的机制或条件。'}],
                    'change_summary': '补充反馈的作用位置及其条件。'})
            actions.append({'concept_slug': slug, 'action': 'update' if (self.ws.wiki / (slug + '.md')).exists() else 'create',
                'title': concept['title'], 'question': concept['question'], 'affected_point_ids': sorted(ids),
                'reason': '补充与此概念问题相关的知识。', 'requires_review': False})
        return {'decision_version': 'knowledge-maintenance/v1', 'point_decisions': decisions,
                'concept_actions': actions, 'relation_actions': [], 'unresolved': []}

    def respond(self, messages):
        context = json.loads(messages[-1]["content"])
        if "阶段：verify" in messages[0]["content"]:
            self.calls.append({"stage": "verify", **context})
            return {"reviews": [{"claim_id": c["id"], "verdict": "supported", "reason": "逐项核对原文及条件。", "source_ids": c["source_ids"]}
                                for c in context["proposal"]["claims"]], "body_issues": [], "old_knowledge_issues": [], "style_suggestions": []}
        self.calls.append(context)
        stage = context["stage"]
        if stage == "maintain":
            return self.decision(context)
        if stage == "bind":
            claims = context['claims']
            bindings = []
            for point in context['knowledge_points']:
                claim = next(c for c in claims if point['source_id'] in c['source_ids'] and point['proposition'] == c['text'])
                bindings.append({'point_id': point['id'], 'claim_ids': [claim['id']], 'body_paragraph_ids': [next(p['id'] for p in context['body_paragraphs'] if claim['text'] in p['text'])]})
            return {'claim_bindings': [{'claim_id': c['id'], 'body_paragraph_ids': [next(p['id'] for p in context['body_paragraphs'] if c['text'] in p['text'])]} for c in claims],
                    'point_bindings': bindings, 'unmapped_claims': [], 'unmapped_points': []}
        if stage == "point-review":
            return {"reviews": [{"point_id": p["id"], "verdict": "covered", "reason": "正文保留机制与适用条件。"}
                                for p in context["knowledge_points"]], "merge_issues": [], "unsupported_relations": [],
                    "important_condition_issues": [], "style_suggestions": []}
        target, source_points = context["target"], context["knowledge_points"]
        claims, point_bindings, paragraphs, claim_bindings = [], [], [], []
        for index, point in enumerate(source_points):
            sid = point["source_id"]
            original_claim = "reliability" if point["local_id"] == "k-reliability" else "mechanism"
            text = point["proposition"] + "这项认识保留了原文的任务条件。"
            quote = text + "[S:" + sid + "]"
            paragraphs.append(quote)
            cid = "c" + str(index)
            claims.append({"id": cid, "text": point["proposition"], "scope": "原论文的任务与反馈条件。", "source_ids": [sid],
                           "evidence_refs": [{"source_id": sid, "claim_id": original_claim}]})
            claim_bindings.append({"claim_id": cid, "body_quote": text})
            point_bindings.append({"point_id": point["id"], "claim_ids": [cid], "body_quote": text})
        body = "# " + target["title"] + "\n\n" + "\n\n".join(paragraphs)
        return {"body_markdown": body, "claims": claims, "claim_bindings": claim_bindings, "point_bindings": point_bindings,
                "topic_routes": [{"slug": "agents-and-feedback", "title": "智能体如何利用反馈改善行为", "overview": "先理解执行内的反馈控制，再理解跨尝试经验及它们共同依赖的反馈条件。",
                                  "question": "反馈怎样作用于行为并受到哪些条件限制？"}], "aliases": [], "claim_changes": []}

    def publish(self, ws, proposal, ids, job_id=None):
        self.publications.append(proposal)
        from station.knowledge_maintenance import apply_decision
        with sqlite3.connect(ws.db) as db:
            apply_decision(db, proposal['maintenance_decision_id'], proposal['maintenance_decision'],
                           proposal['point_versions'], concept_slugs=[proposal['slug']])
        self.assertEqual(set(ids), {b["point_id"] for b in proposal["point_bindings"]})
        self.assertTrue(proposal["semantic_reviews"][-1]["passed"])
        self.assertTrue(proposal["semantic_reviews"][-1]["knowledge_point_review"]["passed"])
        (ws.wiki / (proposal["slug"] + ".md")).write_text(proposal["body_markdown"])
        (ws.meta / (proposal["slug"] + ".json")).write_text(json.dumps(proposal))
        for topic in proposal["topic_routes"]:
            (ws.wiki / (topic["slug"] + ".md")).write_text("# " + topic["title"])
            (ws.meta / (topic["slug"] + ".json")).write_text(json.dumps({"page_kind": "topic", "title": topic["title"]}))
        return {"slug": proposal["slug"], "published": True}

    def run_synthesis(self):
        return synthesis.synthesize_library(self.ws, model=self.model, job_id="test-job")

    def large_repeated_evidence(self):
        """Model real packets: many claims reuse long, exact PDF paragraphs."""
        with sqlite3.connect(self.ws.db) as db:
            for sid in SIDS:
                paragraph = self.originals[sid]["paragraphs"][0]
                text = paragraph["text"] + " Archived findings for " + sid + ": " + "measured conditions; " * 100
                paragraph.update(text=text, end=len(text))
                self.originals[sid]["paragraphs"].append(dict(paragraph))
                packet = json.loads(db.execute("SELECT packet_json FROM paper_distillations WHERE source_id=?", (sid,)).fetchone()[0])
                extra = []
                for claim in packet["claims"]:
                    claim["evidence_passages"][0]["quote"] = text
                    for number in range(40):
                        clone = json.loads(json.dumps(claim))
                        clone["id"] += "-" + str(number)
                        extra.append(clone)
                packet["claims"].extend(extra)
                for point in packet["knowledge_points"]:
                    local = point["claim_ids"][0]
                    point["claim_ids"].extend(c["id"] for c in extra if c["id"].startswith(local + "-"))
                    db.execute("UPDATE knowledge_points SET data_json=? WHERE id=?",
                               (json.dumps(point), point_id(sid, point["id"])))
                db.execute("UPDATE paper_distillations SET packet_json=? WHERE source_id=?", (json.dumps(packet), sid))

    def test_cross_paper_points_form_shared_concept_and_one_topic_with_mechanisms_distinct(self):
        result = self.run_synthesis()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.publisher.call_count, 3)
        published = {p["slug"]: p for p in self.publications}
        shared = published["feedback-reliability"]
        self.assertEqual(set(shared["source_ids"]), set(SIDS))
        self.assertEqual(len(shared["point_bindings"]), 2)
        self.assertIn("task-feedback-control", published)
        self.assertIn("reflection-across-attempts", published)
        self.assertEqual({t["slug"] for p in published.values() for t in p["topic_routes"]}, {"agents-and-feedback"})
        stages = [c.get("stage") for c in self.calls]
        self.assertEqual(stages, ["maintain", "write", "verify", "point-review", "write", "verify", "point-review", "write", "verify", "point-review"])
        for claim in shared["claims"]:
            passage = claim["evidence_passages"][0]
            self.assertEqual(passage["quote"], self.originals[passage["source_id"]]["paragraphs"][0]["text"])
            self.assertIsInstance(passage["locator"], str)

    def test_repeating_completed_input_reuses_alignment_and_publication_receipts(self):
        first = self.run_synthesis()
        calls, publications = self.model.call_count, self.publisher.call_count
        second = self.run_synthesis()
        self.assertEqual(second['results'], [])
        self.assertEqual(second["status"], "completed")
        self.assertEqual(self.model.call_count, calls)
        self.assertEqual(self.publisher.call_count, publications)

    def test_selected_concept_only_writes_and_reviews_that_target_with_complete_plan(self):
        selected = "reflection-across-attempts"
        result = synthesis.synthesize_library(self.ws, model=self.model, target_slug=selected)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["target_slug"], selected)
        self.assertEqual([r["slug"] for r in result["results"]], [selected])
        self.assertEqual(len(result["plan"]["concepts"]), 3)
        self.assertEqual(self.publisher.call_count, 1)
        self.assertEqual(self.publications[0]["slug"], selected)
        for call in self.calls:
            if call.get("stage") != "maintain":
                self.assertEqual(call.get("target", call.get("proposal", {})).get("slug"), selected)
        self.assertFalse((self.ws.wiki / "task-feedback-control.md").exists())
        self.assertFalse((self.ws.wiki / "feedback-reliability.md").exists())
        again = self.run_synthesis()
        self.assertEqual(again["status"], "completed")
        self.assertEqual(self.publisher.call_count, 3)
        self.assertEqual(sum(c.get("stage") == "write" for c in self.calls), 3)

    def test_selected_target_still_checks_all_points_and_unknown_target_is_blocked(self):
        def missing(messages):
            result = self.respond(messages)
            if json.loads(messages[-1]["content"]).get("stage") == "maintain":
                result["point_decisions"].pop()
            return result
        self.model.side_effect = missing
        result = synthesis.synthesize_library(self.ws, model=self.model, target_slug="task-feedback-control")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("exactly one", result["reasons"][0])
        self.assertEqual(self.publisher.call_count, 0)
        self.model.side_effect = self.respond
        self.model.model_id = "test-synthesis-changed-model"
        exhausted = synthesis.synthesize_library(self.ws, model=self.model)
        self.assertEqual(exhausted['status'], 'blocked')
        self.assertIn('budget exhausted', exhausted['reasons'][0])

    def test_unknown_target_is_rejected_without_author_requests(self):
        unknown = synthesis.synthesize_library(self.ws, model=self.model, target_slug='unknown-concept')
        self.assertEqual(unknown['status'], 'blocked')
        self.assertIn('not in the current complete alignment plan', unknown['reasons'][0])
        self.assertEqual(unknown['results'], [])
        self.assertEqual(self.publisher.call_count, 0)

    def test_completed_replay_does_not_overwrite_original_receipt(self):
        first = self.run_synthesis()
        directory = self.ws.root / "knowledge_synthesis" / first["input_digest"]
        full = (directory / "result.json").read_bytes()
        calls = self.model.call_count
        result = self.run_synthesis()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['results'], [])
        self.assertEqual(self.model.call_count, calls)
        self.assertEqual((directory / 'result.json').read_bytes(), full)

    def test_real_rebuild_publisher_persists_points_concepts_topic_and_reviewed_body(self):
        self.module_patch.stop()
        rebuild = importlib.import_module("station.paper_rebuild")
        parsed = {sid: {"source_version": original["source_version"], "paragraphs": original["paragraphs"]}
                  for sid, original in self.originals.items()}
        with patch.object(rebuild, "parse_source", side_effect=lambda ws, sid: parsed[sid]):
            result = self.run_synthesis()
        self.assertEqual(result["status"], "completed")
        with sqlite3.connect(self.ws.db) as db:
            members = db.execute("SELECT concept_slug,point_id FROM concept_memberships").fetchall()
        self.assertEqual(len(members), 4)
        self.assertEqual({p for _, p in members}, set(self.points))
        topic = json.loads((self.ws.meta / "agents-and-feedback.json").read_text())
        self.assertEqual(len(topic["topic_navigation"]["entries"]), 3)
        meta = json.loads((self.ws.meta / "feedback-reliability.json").read_text())
        self.assertEqual(set(meta["source_ids"]), set(SIDS))
        self.assertEqual(set(meta["knowledge_point_ids"]), {self.points[1], self.points[3]})
        self.assertEqual(meta["knowledge_topic"], "反馈怎样影响后续决定？")
        self.assertEqual(meta["pipeline_version"], "paper-concepts-v1")
        self.assertTrue(all(c["verdict"] == "supported" for c in meta["semantic_reviews"][-1]["reviews"]))
        calls = self.model.call_count
        with patch.object(rebuild, "parse_source", side_effect=lambda ws, sid: parsed[sid]):
            again = self.run_synthesis()
        self.assertEqual(again["status"], "completed")
        self.assertEqual(self.model.call_count, calls)

    def test_long_shared_evidence_is_sent_once_and_persisted_quotes_remain_complete(self):
        self.large_repeated_evidence()
        with sqlite3.connect(self.ws.db) as db:
            before = list(db.execute('SELECT * FROM knowledge_points ORDER BY id'))
        papers, points, _ = synthesis._inputs(self.ws)
        full_claims, _ = synthesis._paper_materials(papers, points)
        self.assertGreater(len(synthesis._json(full_claims)), synthesis.MAX_CONTEXT_CHARACTERS)
        result = self.run_synthesis()
        self.assertEqual(result["status"], "completed")
        for context in self.calls:
            if context.get("stage") == "maintain":
                continue
            originals = context["originals"]
            seen = set()
            encoded = synthesis._json(context)
            self.assertLess(len(encoded), synthesis.MAX_CONTEXT_CHARACTERS)
            for original in originals:
                for paragraph in original["paragraphs"]:
                    key = (original["source_id"], paragraph["id"])
                    self.assertNotIn(key, seen)
                    seen.add(key)
                    self.assertEqual(encoded.count(paragraph["text"]), 1)
                    self.assertEqual(paragraph["text"], self.originals[key[0]]["paragraphs"][0]["text"])
            request_claims = context.get("supplied_claims", context.get("proposal", {}).get("claims", []))
            for claim in request_claims:
                self.assertNotIn("evidence_passages", claim)
                for location in claim["evidence_locations"]:
                    self.assertIn((location["source_id"], location["paragraph_id"]), seen)
                    self.assertEqual(location.get("quote_start", 0), 0)
                    self.assertEqual(location.get("quote_end", len(self.originals[location["source_id"]]["paragraphs"][0]["text"])),
                                     len(self.originals[location["source_id"]]["paragraphs"][0]["text"]))
        shared = next(p for p in self.publications if p["slug"] == "feedback-reliability")
        self.assertEqual(set(shared["source_ids"]), set(SIDS))
        for claim in shared["claims"]:
            evidence = claim["evidence_passages"][0]
            self.assertEqual(evidence["quote"], self.originals[evidence["source_id"]]["paragraphs"][0]["text"])
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(list(db.execute('SELECT * FROM knowledge_points ORDER BY id')), before)
        calls = self.model.call_count
        again = self.run_synthesis()
        self.assertEqual(again["status"], "completed")
        self.assertEqual(self.model.call_count, calls)

    def test_supporting_result_keeps_its_additional_claim_and_original_location(self):
        sid = SIDS[0]
        with sqlite3.connect(self.ws.db) as db:
            packet = json.loads(db.execute("SELECT packet_json FROM paper_distillations WHERE source_id=?", (sid,)).fetchone()[0])
            extra = json.loads(json.dumps(packet["claims"][0]))
            extra.update(id="result-condition", text="这一结果仅在记录的反馈条件下报告。")
            packet["claims"].append(extra)
            packet["results"][0]["claim_ids"].append(extra["id"])
            db.execute("UPDATE paper_distillations SET packet_json=? WHERE source_id=?", (json.dumps(packet), sid))
        result = self.run_synthesis()
        self.assertEqual(result["status"], "completed")
        request = next(c for c in self.calls if c.get("stage") == "write" and c["target"]["slug"] == "feedback-reliability")
        claim = next(c for c in request["supplied_claims"] if c["source_id"] == sid and c["local_id"] == "result-condition")
        self.assertEqual(claim["text"], extra["text"])
        self.assertEqual(claim["evidence_locations"][0]["paragraph_id"], "p0001-0001")

    def test_partial_quote_preserves_exact_range_and_full_original_locator(self):
        sid = SIDS[0]
        paragraph = self.originals[sid]["paragraphs"][0]
        claim = {"id": "substring", "text": "限定范围的原文判断。", "scope": "测试条件。", "source_ids": [sid],
                 "evidence_passages": [{"source_id": sid, "quote": paragraph["text"][7:35],
                                        "locator": {"paragraph_id": paragraph["id"], "page": 1, "start": 7, "end": 35}}]}
        before = json.dumps(claim)
        view = synthesis._claim_context(claim, self.originals)
        location = view["evidence_locations"][0]
        self.assertEqual(location, {"source_id": sid, "paragraph_id": paragraph["id"], "quote_start": 7, "quote_end": 35})
        request_original = synthesis._request_originals([self.originals[sid]], [claim])[0]["paragraphs"][0]
        self.assertEqual(request_original, paragraph)
        self.assertEqual(request_original["text"][location["quote_start"]:location["quote_end"]], claim["evidence_passages"][0]["quote"])
        self.assertEqual(json.dumps(claim), before)

    def test_over_budget_author_blocks_only_target_and_reuses_other_publications(self):
        slug = "task-feedback-control"
        (self.ws.wiki / (slug + ".md")).write_text("# 保留既有知识\n\n" + "Existing knowledge must not be silently omitted. " * 3600)
        (self.ws.meta / (slug + ".json")).write_text(json.dumps({"title": "任务内反馈控制", "page_kind": "concept", "claims": []}))
        result = self.run_synthesis()
        self.assertEqual(result["status"], "blocked")
        blocked = next(r for r in result["results"] if r["slug"] == slug)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("write context exceeds", blocked["reasons"][0])
        self.assertEqual(self.publisher.call_count, 2)
        self.assertEqual({p["slug"] for p in self.publications}, {"reflection-across-attempts", "feedback-reliability"})
        self.assertFalse(any(c.get("stage") in {"write", "repair"} and c["target"]["slug"] == slug for c in self.calls))
        calls = self.model.call_count
        again = self.run_synthesis()
        self.assertEqual(again["status"], "blocked")
        self.assertEqual(sum(c.get('stage') in {'write', 'repair'} for c in self.calls), 2)
        self.assertLessEqual(self.model.call_count, calls + 1)

    def test_over_budget_context_keeps_failure_reason_and_continues_other_targets(self):
        def oversized_draft(messages):
            result = self.respond(messages)
            context = json.loads(messages[-1]["content"])
            if context.get("stage") == "write" and context["target"]["slug"] == "task-feedback-control":
                result["body_markdown"] += "\n\n" + "Do not truncate an earlier factual draft. " * 4300
            if context.get('stage') == 'point-review' and context['target']['slug'] == 'task-feedback-control':
                result['merge_issues'] = ['正文包含需修正的解释。']
            return result
        self.model.side_effect = oversized_draft
        result = self.run_synthesis()
        blocked = next(r for r in result["results"] if r["status"] == "blocked")
        self.assertEqual(blocked["slug"], "task-feedback-control")
        self.assertIn("context exceeds", blocked["reasons"][0])
        self.assertEqual(self.publisher.call_count, 2)
        self.assertEqual(sum(c.get("stage") == "repair" for c in self.calls), 0)

    def test_over_budget_concept_allows_compile_job_to_finish_final_index(self):
        self.module_patch.stop()
        rebuild = importlib.import_module("station.paper_rebuild")
        slug = "task-feedback-control"
        (self.ws.wiki / (slug + ".md")).write_text("# 保留既有知识\n\n" + "Existing knowledge must not be silently omitted. " * 3600)
        (self.ws.meta / (slug + ".json")).write_text(json.dumps({"title": "任务内反馈控制", "page_kind": "concept", "claims": []}))
        parsed = {sid: {"source_version": original["source_version"], "paragraphs": original["paragraphs"]}
                  for sid, original in self.originals.items()}
        with patch.object(rebuild, "parse_source", side_effect=lambda ws, sid: parsed[sid]), \
                patch("station.search.rebuild_index", return_value={"indexed": True}) as index:
            result = rebuild.compile_job(self.ws, {"source_ids": SIDS}, "test-job", model=self.model)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["synthesis"]["status"], "blocked")
        self.assertEqual(sum(r["status"] == "published" for r in result["synthesis"]["results"]), 2)
        index.assert_called_once_with(self.ws, embeddings=True)
        self.assertEqual(result["index"], {"indexed": True})

    def test_author_value_errors_are_bounded_and_do_not_abort_library(self):
        def invalid(messages):
            context = json.loads(messages[-1]["content"])
            if context.get("stage") in {"write", "repair"} and context["target"]["slug"] == "task-feedback-control":
                self.calls.append(context)
                raise ValueError("Provider returned an invalid JSON object")
            return self.respond(messages)
        self.model.side_effect = invalid
        result = self.run_synthesis()
        blocked = next(r for r in result["results"] if r["status"] == "blocked")
        self.assertIn("repair request: Provider returned", blocked["reasons"][0])
        self.assertEqual(sum(c.get("stage") in {"write", "repair"} and c["target"]["slug"] == "task-feedback-control" for c in self.calls), 3)
        self.assertEqual(self.publisher.call_count, 2)

    def test_request_context_version_invalidates_only_synthesis_cache(self):
        marker = self.ws.root / "distillations" / "successful-source-cache.json"
        marker.parent.mkdir()
        marker.write_text("source cache unchanged")
        before = {p: p.read_bytes() for p in self.ws.raw.glob("*.json")}
        first = self.run_synthesis()
        calls = self.model.call_count
        with patch.object(synthesis, "REQUEST_CONTEXT_VERSION", "changed-request-protocol"):
            second = self.run_synthesis()
        self.assertNotEqual(first["input_digest"], second["input_digest"])
        self.assertEqual(self.model.call_count, calls)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(marker.read_text(), "source cache unchanged")
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))

    def test_compact_fact_request_keeps_legacy_checks_and_full_proposal_hashes(self):
        self.run_synthesis()
        proposal = self.publications[-1]
        originals = [self.originals[sid] for sid in proposal["source_ids"]]
        default = compiler._review(proposal, {}, originals, self.model)
        default_request = self.calls[-1]
        compact = compiler._review(proposal, {}, originals, self.model,
                                   request_context=synthesis._review_context(proposal, {}, originals))
        compact_request = self.calls[-1]
        self.assertIn("evidence_passages", default_request["proposal"]["claims"][0])
        self.assertNotIn("evidence_passages", compact_request["proposal"]["claims"][0])
        self.assertTrue(default["passed"] and compact["passed"])
        for field in ("proposal_digest", "body_digest", "original_versions"):
            self.assertEqual(default[field], compact[field])
        def omitted(messages):
            value = self.respond(messages)
            value["reviews"].pop()
            return value
        self.model.side_effect = omitted
        with self.assertRaisesRegex(ValueError, "omitted or duplicated claims"):
            compiler._review(proposal, {}, originals, self.model,
                             request_context=synthesis._review_context(proposal, {}, originals))

    def test_author_cancellation_is_not_converted_to_a_blocked_target(self):
        def cancelled(messages):
            context = json.loads(messages[-1]["content"])
            if context.get("stage") == "write":
                raise JobConflict("Job cancellation requested")
            return self.respond(messages)
        self.model.side_effect = cancelled
        with self.assertRaisesRegex(JobConflict, "cancellation requested"):
            self.run_synthesis()
        self.assertEqual(self.publisher.call_count, 0)
        self.assertEqual(list((self.ws.root / "knowledge_synthesis").glob("*/*/blocked.json")), [])

    def test_interrupted_point_review_reuses_written_body_and_completed_fact_review(self):
        def interrupt(messages):
            context = json.loads(messages[-1]["content"])
            if context.get("stage") == "point-review":
                raise TimeoutError("review interrupted")
            return self.respond(messages)
        self.model.side_effect = interrupt
        with self.assertRaises(TimeoutError):
            self.run_synthesis()
        self.assertEqual(self.publisher.call_count, 0)
        self.calls.clear()
        self.model.side_effect = self.respond
        result = self.run_synthesis()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0]["stage"], "point-review")
        self.assertEqual(sum(c.get("stage") == "maintain" for c in self.calls), 0)
        self.assertEqual(sum(c.get("stage") == "write" for c in self.calls), 2)
        self.assertEqual(sum(c.get("stage") == "verify" for c in self.calls), 2)

    def test_alignment_cannot_silently_omit_points_and_has_only_two_repairs(self):
        def missing(messages):
            result = self.respond(messages)
            if json.loads(messages[-1]["content"]).get("stage") == "maintain":
                result["point_decisions"].pop()
            return result
        self.model.side_effect = missing
        result = self.run_synthesis()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("exactly one", result["reasons"][0])
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(self.publisher.call_count, 0)

    def test_deferred_reason_persists_without_forcing_a_page(self):
        def deferred(messages):
            result = self.respond(messages)
            if json.loads(messages[-1]['content']).get('stage') == 'maintain':
                slug = 'feedback-reliability'
                result['concept_actions'] = [a for a in result['concept_actions'] if a['concept_slug'] != slug]
                for point in result['point_decisions']:
                    if point['memberships'][0]['concept_slug'] == slug:
                        point.update(disposition='deferred', memberships=[], reason='原文尚不足以形成稳定概念。', reevaluate_when=['取得能界定反馈可靠性的原文机制材料后重评。'])
            return result
        self.model.side_effect = deferred
        result = self.run_synthesis()
        self.assertEqual(len(result['deferred_points']), 2)
        self.assertEqual(self.publisher.call_count, 2)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM point_dispositions WHERE disposition='deferred'").fetchone()[0], 2)
        self.assertFalse((self.ws.wiki / 'feedback-reliability.md').exists())

    def test_related_membership_does_not_force_body_expansion_and_publishes_atomically(self):
        self.module_patch.stop()
        rebuild = importlib.import_module("station.paper_rebuild")
        extra = self.points[2]
        def role_aware(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') in {'write', 'repair'} and context['target']['slug'] == 'task-feedback-control':
                author_context = {**context, 'knowledge_points': [p for p in context['knowledge_points']
                    if p['id'] in context['required_point_ids']]}
                messages = [messages[0], {**messages[-1], 'content': json.dumps(author_context)}]
            value = self.respond(messages)
            if context.get('stage') == 'maintain':
                point = next(p for p in value['point_decisions'] if p['point_id'] == extra)
                point['memberships'].append({'concept_slug': 'task-feedback-control', 'role': 'related',
                    'reason': 'Contrasts correction during execution with reflection across attempts.'})
                next(a for a in value['concept_actions'] if a['concept_slug'] == 'task-feedback-control')['affected_point_ids'].append(extra)
            if context.get('stage') == 'point-review' and context['target']['slug'] == 'task-feedback-control':
                next(v for v in value['reviews'] if v['point_id'] == extra).update(verdict='not_expanded',
                    reason='This is a reading connection, not a second full mechanism exposition.')
            return value
        self.model.side_effect = role_aware
        parsed = {sid: {'source_version': o['source_version'], 'paragraphs': o['paragraphs']}
                  for sid, o in self.originals.items()}
        with patch.object(rebuild, 'parse_source', side_effect=lambda ws, sid: parsed[sid]):
            result = self.run_synthesis()
        self.assertEqual(result['status'], 'completed', result)
        meta = json.loads((self.ws.meta / 'task-feedback-control.json').read_text())
        self.assertIn(extra, meta['knowledge_point_ids'])
        self.assertNotIn(extra, meta['required_point_ids'])
        self.assertNotIn(extra, {b['point_id'] for b in meta['point_bindings']})
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships WHERE point_id=?', (extra,)).fetchone()[0], 2)

    def test_unclassified_review_error_does_not_trigger_prose_rewrite(self):
        def malformed_review(messages):
            value = self.respond(messages)
            context = json.loads(messages[-1]['content'])
            if '阶段：verify' in messages[0]['content'] and context['proposal']['slug'] == 'feedback-reliability':
                value['body_issues'] = [{'body_quote': '反馈', 'reason': '没有分类的审查意见。'}]
            return value
        self.model.side_effect = malformed_review
        result = self.run_synthesis()
        bad = next(r for r in result['results'] if r['slug'] == 'feedback-reliability')
        self.assertEqual(bad['status'], 'blocked')
        self.assertEqual(sum(c.get('stage') == 'repair' for c in self.calls), 0)
        self.assertEqual(sum(c.get('stage') == 'write' for c in self.calls), 3)

    def test_extra_unexpressed_registration_repairs_metadata_without_rewriting_body(self):
        def metadata_model(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') == 'metadata-repair':
                self.calls.append(context)
                return {'remove_new_claim_ids': ['unused'], 'claim_evidence_updates': [], 'relation_claim_updates': []}
            if context.get('stage') == 'bind' and any(c['id'] == 'unused' for c in context['claims']):
                clean = {**context, 'claims': [c for c in context['claims'] if c['id'] != 'unused']}
                result = self.respond([messages[0], {**messages[-1], 'content': json.dumps(clean)}])
                result['unmapped_claims'] = [{'claim_id': 'unused', 'reason': 'This extra registration is not expressed in the body.'}]
                return result
            result = self.respond(messages)
            if context.get('stage') == 'write' and context['target']['slug'] == 'task-feedback-control':
                result['claims'].append({'id': 'unused', 'text': '原文中的另一个未在本页展开的结果。',
                    'scope': '原实验条件。', 'source_ids': [SIDS[0]],
                    'evidence_refs': [{'source_id': SIDS[0], 'claim_id': 'mechanism'}]})
            return result
        self.model.side_effect = metadata_model
        result = self.run_synthesis()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(sum(c.get('stage') == 'metadata-repair' for c in self.calls), 1)
        self.assertEqual(sum(c.get('stage') == 'repair' for c in self.calls), 0)
        self.assertEqual(sum(c.get('stage') == 'write' for c in self.calls), 3)
        body = next(c for c in self.calls if c.get('stage') == 'metadata-repair')['previous_proposal']['body_markdown']
        actual = next(p for p in self.publications if p['slug'] == 'task-feedback-control')
        self.assertEqual(actual['body_markdown'], body)
        self.assertNotIn('unused', {c['id'] for c in actual['claims']})

    def test_binding_contract_exception_does_not_request_another_whole_body(self):
        from station import body_bindings
        original = body_bindings.ground_bindings
        def reject_mapping(draft, *args, **kwargs):
            if '当前执行中根据新观测' in draft['body_markdown']:
                raise ValueError('Ambiguous stable claim identity requires metadata review')
            return original(draft, *args, **kwargs)
        with patch.object(body_bindings, 'ground_bindings', side_effect=reject_mapping):
            result = self.run_synthesis()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(sum(c.get('stage') == 'repair' for c in self.calls), 0)
        self.assertEqual(sum(c.get('stage') == 'write' for c in self.calls), 3)

    def test_contract_label_alone_cannot_route_a_factual_error_to_metadata_repair(self):
        prior = {'semantic_review': {'evidence': {'issues': [
            {'kind': 'contract_error', 'severity': 'blocking', 'reason': 'The numerical comparison is false.'},
            {'kind': 'contract_error', 'severity': 'blocking', 'registration_action': 'remove_unexpressed'}]},
            'knowledge': {'passed': True}}}
        self.assertFalse(synthesis._metadata_repair_kind(prior))
        prior['semantic_review']['evidence']['issues'].pop(0)
        self.assertTrue(synthesis._metadata_repair_kind(prior))

    def test_metadata_correction_cannot_remove_old_knowledge_or_author_new_fact_text(self):
        claim = {'id': 'old', 'text': '旧的正确知识。', 'scope': '原条件。', 'source_ids': [SIDS[0]]}
        previous = {'body_markdown': '旧的正确知识需要保留。', 'claims': [claim]}
        with self.assertRaisesRegex(ValueError, 'remove old claims'):
            synthesis._repair_metadata_only(previous, {'remove_new_claim_ids': ['old']}, {'claims': [claim]})
        new = {**claim, 'id': 'new'}
        with self.assertRaisesRegex(ValueError, 'explicitly unexpressed'):
            synthesis._repair_metadata_only({**previous, 'claims': [claim, new]}, {'remove_new_claim_ids': ['new']}, {})
        with self.assertRaisesRegex(ValueError, 'fact text'):
            synthesis._repair_metadata_only(previous, {'claim_evidence_updates': [{'claim_id': 'old', 'text': '改写。'}]}, {})
        with self.assertRaisesRegex(ValueError, 'only registration edits'):
            synthesis._repair_metadata_only(previous, {'body_markdown': '补写正文。'}, {})

    def test_review_context_preserves_appendix_evidence_outside_registered_claim_quotes(self):
        original = dict(self.originals[SIDS[0]])
        original['paragraphs'] = [*original['paragraphs'], {'id': 'appendix',
            'text': 'The appendix documents a better search method and a corrected misunderstanding.', 'page': 17}]
        result = synthesis._request_originals([original], [])
        self.assertIn('appendix', {p['id'] for p in result[0]['paragraphs']})
        self.assertFalse(result[0]['is_excerpt'])

    def test_approved_editorial_basis_is_program_owned_when_author_omits_it(self):
        relation = {'target_slug': 'other', 'kind': 'contrast', 'claim_ids': ['c']}
        draft = {'body_markdown': '机制对照说明不同的反馈作用。', 'claims': [], 'relations': [relation]}
        target = {'relation_actions': [{**relation, 'basis': 'editorial'}]}
        adapted = synthesis._prepare_author_draft(draft, target, {})
        self.assertEqual(adapted['relations'][0]['basis'], 'editorial')
        self.assertNotIn('basis', relation)
        explicit = {**draft, 'relations': [{**relation, 'basis': 'factual'}]}
        self.assertEqual(synthesis._prepare_author_draft(explicit, target, {})['relations'][0]['basis'], 'factual')

    def test_program_normalizes_page_envelope_routes_and_preserves_old_claims(self):
        old_claim = {'id': 'stable', 'text': '原有条件仍然成立。', 'scope': '原条件。', 'source_ids': [SIDS[0]]}
        draft = {'body_markdown': '# 标题\n\n相关机制见[另一概念](/wiki/other#mechanism)。', 'claims': []}
        adapted = synthesis._prepare_author_draft(draft, {}, {'claims': [old_claim]})
        self.assertEqual(adapted['body_markdown'], '相关机制见[另一概念](/page/other#mechanism)。')
        self.assertEqual(adapted['claims'], [old_claim])
        self.assertEqual(synthesis._body_targets(adapted['body_markdown']), {'other'})
        self.assertEqual(draft['claims'], [])

    def test_supported_quotes_do_not_bypass_body_fact_review(self):
        def reject(messages):
            result = self.respond(messages)
            context = json.loads(messages[-1]["content"])
            if "阶段：verify" in messages[0]["content"] and context["proposal"]["slug"] == "feedback-reliability":
                result.pop("body_issues", None)
                claim = context["proposal"]["claims"][0]
                sid = claim["source_ids"][0]
                result["issues"] = [{"kind": "fact_error", "severity": "blocking", "body_quote": "反馈",
                    "reason": "正文增加了未经实验的普遍成功结论。", "claim_ids": [claim["id"]],
                    "source_ids": [sid], "evidence": [{"source_id": sid, "quote": self.originals[sid]["paragraphs"][0]["text"]}]}]
            return result
        self.model.side_effect = reject
        result = self.run_synthesis()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.publisher.call_count, 2)
        blocked = next(r for r in result["results"] if r["status"] == "blocked")
        self.assertEqual(blocked["slug"], "feedback-reliability")
        self.assertEqual(len(blocked["semantic_reviews"]), 3)
        self.assertFalse((self.ws.wiki / "feedback-reliability.md").exists())

    def test_point_omission_and_harmful_merge_independently_block_publication(self):
        def mismerge(messages):
            result = self.respond(messages)
            context = json.loads(messages[-1]["content"])
            if context.get("stage") == "point-review" and context["target"]["slug"] == "feedback-reliability":
                result["reviews"][0].update(verdict="mismerged", reason="将不同反馈判定条件混为统一保证。")
                result["merge_issues"] = ["没有保留执行内与跨尝试反馈的不同作用。"]
            return result
        self.model.side_effect = mismerge
        result = self.run_synthesis()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.publisher.call_count, 2)
        self.assertEqual(sum(c.get("stage") == "repair" for c in self.calls), 2)

    def test_invalid_binding_is_repaired_without_rewriting_the_body(self):
        def missing_body(messages):
            result = self.respond(messages)
            context = json.loads(messages[-1]['content'])
            if context.get('stage') == 'write' and context['target']['slug'] == 'feedback-reliability':
                result['point_bindings'].pop()
            return result
        self.model.side_effect = missing_body
        result = self.run_synthesis()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.publisher.call_count, 3)
        self.assertEqual(sum(c.get('stage') == 'repair' for c in self.calls), 0)
        self.assertEqual(sum(c.get('stage') == 'bind' for c in self.calls), 1)
        self.assertEqual(sum(c.get('stage') == 'verify' for c in self.calls), 3)

    def test_plan_cannot_overwrite_paper_reading_page(self):
        def overwrite(messages):
            result = self.respond(messages)
            if json.loads(messages[-1]["content"]).get("stage") == "maintain":
                result['concept_actions'][0]['concept_slug'] = 'paper-0'
                result['point_decisions'][0]['memberships'][0]['concept_slug'] = 'paper-0'
            return result
        self.model.side_effect = overwrite
        result = self.run_synthesis()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.publisher.call_count, 0)
        self.assertIn("论文解读", (self.ws.wiki / "paper-0.md").read_text())

    def test_txt_and_changed_source_records_are_not_synthesis_inputs(self):
        sid = SIDS[0]
        raw = self.ws.raw / (sid + ".json")
        record = json.loads(raw.read_text())
        record.update(pdf_path="original.txt", content_format="txt")
        (self.ws.originals / "original.txt").write_text(record["text"])
        raw.write_text(json.dumps(record))
        papers, points, stale = synthesis._inputs(self.ws)
        self.assertNotIn(sid, papers)
        self.assertTrue(all(p["source_id"] != sid for p in points.values()))
        self.assertTrue(stale)
        self.assertEqual(self.model.call_count, 0)

    def test_current_source_changed_during_model_call_cannot_publish(self):
        def changed(messages):
            result = self.respond(messages)
            if json.loads(messages[-1]["content"]).get("stage") == "write":
                raw = self.ws.raw / (SIDS[0] + ".json")
                value = json.loads(raw.read_text())
                value["text"] += " Changed after planning."
                raw.write_text(json.dumps(value))
            return result
        self.model.side_effect = changed
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.run_synthesis()
        self.assertEqual(self.publisher.call_count, 0)

    def test_cancellation_prevents_model_calls_and_publication(self):
        with patch.object(synthesis, "check_cancelled", side_effect=RuntimeError("Cancelled")):
            with self.assertRaisesRegex(RuntimeError, "Cancelled"):
                self.run_synthesis()
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(self.publisher.call_count, 0)


if __name__ == "__main__":
    unittest.main()

class MetadataTerminalRecoveryTests(unittest.TestCase):
    def test_only_exhausted_format_rounds_without_any_review_can_reopen(self):
        from copy import deepcopy
        row = {'draft': {'body_markdown': 'unchanged prose'},
               'problems': ['Concept claim binding does not occur in the body'],
               'semantic_review': {'evidence': None, 'knowledge': None}}
        rounds = [deepcopy(row) for _ in range(3)]
        self.assertTrue(synthesis._metadata_only_terminal(rounds))
        for field, value in [('problems', ['Concept omitted or duplicated an assigned knowledge point']),
                             ('semantic_review', {'evidence': {'passed': False}, 'knowledge': None}),
                             ('metadata_revalidation', True)]:
            changed = deepcopy(rounds)
            changed[-1][field] = value
            self.assertFalse(synthesis._metadata_only_terminal(changed))
        self.assertFalse(synthesis._metadata_only_terminal(rounds[:2]))

    def test_revalidation_never_needs_a_new_author_when_cache_is_missing(self):
        original = {"body_markdown": "Existing immutable prose", "claims": []}
        origin = {"rounds": [{"draft": original}]}
        self.assertEqual(synthesis._revalidation_draft(origin, None), {"draft": original})
        self.assertEqual(synthesis._revalidation_draft(origin, {"draft": original}), {"draft": original})
        with self.assertRaisesRegex(ValueError, "draft changed"):
            synthesis._revalidation_draft(origin, {"draft": {"body_markdown": "different"}})

class SynthesisSnapshotJobTests(unittest.TestCase):
    def test_downstream_job_rejects_stale_or_changed_points_without_authoring(self):
        with patch.object(synthesis,'_inputs',return_value=({'s1':{}},{'p':{'data_json':'x'}},[])), patch.object(synthesis,'synthesize_library') as run:
            with self.assertRaisesRegex(ValueError,'paper snapshot'):
                synthesis.synthesis_job(None,{'source_ids':['other'],'points_digest':'x'},'j')
            with self.assertRaisesRegex(ValueError,'knowledge-point snapshot'):
                synthesis.synthesis_job(None,{'source_ids':['s1'],'points_digest':'wrong'},'j')
            run.assert_not_called()
            payload={'source_ids':['s1'],'points_digest':synthesis._completion_digest({'p':{'data_json':'x'}})}
            synthesis.synthesis_job(None,payload,'j')
            run.assert_called_once_with(None,job_id='j',terminal_origin_digest=None)
