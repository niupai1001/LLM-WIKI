import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from station import distillation, documents, model_context
from station.workspaces import Workspace


SID = "1234567890abcdef"
PARAGRAPH = "Under the same test conditions, storing failure diagnoses improved repeated attempts; it did not improve every task."


def packet():
    return {"schema_version": distillation.SCHEMA_VERSION, "title": "从失败诊断中利用经验", "purpose": "在不更新参数时改善重复尝试。",
            "motivation": {"bottleneck": "重试缺少可执行的修正方向。"},
            "central_answer": {"idea": "保留失败诊断供下一轮决策使用。"},
            "results": [{"id": "r1", "observation": "相同测试条件下重复尝试得到改善，并有无收益任务。",
                         "conditions": "论文报告的同任务重复尝试。", "interpretation": "收益取决于任务。",
                         "evidence_type": "reported_experiment", "claim_ids": ["a1"]}],
            "knowledge_points": [{"id": "k1", "proposition": "失败诊断能为部分任务的下一轮决策提供修正方向。",
                                  "explanation": "将经历变成指导后续行为的经验。", "basis": "empirical",
                                  "type": "机制", "transferability": "桥接", "uncertainty": "作者提出机制解释；未证明跨领域迁移。",
                                  "result_ids": ["r1"], "claim_ids": ["a1"], "scope": "本研究的同任务重试。",
                                  "value": "区分重试次数和可执行的经验。"}],
            "claims": [{"id": "a1", "text": "在部分任务中，保存失败诊断改善了重复尝试。", "scope": "相同测试条件。",
                        "paragraph_ids": ["p0001-0001"], "quote": "Fabricated model quote is discarded"}],
            "body_markdown": "# 从失败诊断中利用经验\n\n研究希望在固定参数下改善重试。原有重试缺少修正方向，"
                             "作者将失败诊断交给下一轮。相同条件下部分任务改善，也有任务无收益。"
                             "由此可以知道，经验提供修正方向的价值依赖任务条件。[S:" + SID + "]"}


def review(value=None, **body_changes):
    body = {"unsupported_facts": [], "omitted_knowledge_ids": [], "omitted_key_results": [],
            "material_issues": [], "editorial_suggestions": []}
    body.update(body_changes)
    return {"claim_reviews": [{"claim_id": "a1", "status": value or "supported", "reason": "对照原文条件逐项核查。"}],
            "body_review": body, "argument_review": {"argument_chain_issues": [], "design_rationale_issues": [],
                                                     "overgeneralization_issues": [], "reading_coherence_issues": []},
            "conclusion": "ready", "source_coverage": {"status": "adequate", "reason": "", "affected_paragraph_ids": []}}


class PaperDistillationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace("candidate-v2", Path(self.tmp.name))
        self.ws.raw.mkdir()
        self.ws.originals.mkdir()
        self.path = self.ws.raw / (SID + ".json")
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with (self.ws.originals / (SID + ".pdf")).open("wb") as stream:
            writer.write(stream)
        self.archive()
        self.calls = []
        self.model = Mock(side_effect=self.respond)
        self.model.model_id = "test-paper-model-v1"
        self.parser = patch.object(distillation, "parse_source", side_effect=lambda *_: self.parsed())
        self.parser.start()
        self.addCleanup(self.parser.stop)

    def archive(self, **changes):
        record = {"id": SID, "title": "诊断经验研究", "kind": "paper", "pdf_path": SID + ".pdf",
                  "content_status": "full-text", "content_format": "pdf", "text": "Original source text",
                  "bibliography": {"publication_status": "preprint"}}
        record.update(changes)
        self.path.write_text(json.dumps(record), encoding="utf-8")

    def parsed(self):
        return {"source_id": SID, "source_version": documents.source_version(self.ws, SID), "cache_key": "parsed-v1",
                "parser_version": "test-parser-v1", "status": "full", "coverage": {"total_pages": 1, "missing_pages": []},
                "warnings": [], "paragraphs": [{"id": "p0001-0001", "page": 1, "page_label": "1", "start": 0,
                                                 "end": len(PARAGRAPH), "text": PARAGRAPH}]}

    def respond(self, messages):
        context = json.loads(messages[-1]["content"])
        self.calls.append(context)
        if context["stage"] == "review":
            return review()
        if context["stage"] == "read":
            return {"notes": [{"kind": "reported_result", "text": "同条件有收益和负结果，解释须保留任务条件。",
                               "paragraph_ids": [p["id"] for p in context["paragraphs"]]}]}
        if context["stage"] in {"select_evidence", "select_evidence_repair"}:
            return self.select_evidence(context)
        return packet()

    def select_evidence(self, context):
        catalogue = context["paragraph_catalogue"]
        notes = context["whole_paper_reading_notes"]
        selected = [catalogue[0]["id"], catalogue[-1]["id"]]
        first_note = next(n for n in notes if selected[0] in n["paragraph_ids"])
        negative = context["negative_note_ids"]
        coverage = {field: {"paragraph_ids": [selected[0]], "note_ids": [first_note["id"]], "reason": "原文解释了研究目标、方法及主结果。"}
                    for field in ("purpose", "motivation", "central_answer", "main_results")}
        coverage["negative_results"] = {"status": "included" if negative else "not_reported", "paragraph_ids": [selected[-1]] if negative else [],
                                        "note_ids": negative, "reason": "保留影响结论的负结果依据。" if negative else "阅读记录没有报告额外负结果。"}
        return {"selected_paragraph_ids": selected, "selection_reason": "保留主论证和关键负结果，其余细节继续保存在全篇阅读记录。",
                "argument_coverage": coverage,
                "accounted_paragraphs": [{"paragraph_id": p["id"], "role": "补充设置与辅助案例", "reason": "完整阅读记录保留这些细节，本次正文不重复展开。"}
                                         for p in catalogue if p["id"] not in selected]}

    def long_context_failure(self, default_model=False, paragraph_count=80, paragraph_text=None):
        parsed = self.parsed()
        parsed["paragraphs"] = [{"id": f"p{i+1:04d}-0001", "page": i+1, "start": 0, "end": 2300,
                                 "text": paragraph_text or PARAGRAPH + " empirical context" * 125} for i in range(paragraph_count)]
        parsed["coverage"] = {"total_pages": paragraph_count, "missing_pages": []}
        self.long_parsed = parsed
        parser = patch.object(distillation, "parse_source", return_value=parsed)
        parser.start()
        self.addCleanup(parser.stop)
        def notes(messages):
            context = json.loads(messages[-1]["content"])
            value = self.respond(messages)
            if context["stage"] == "read" and context["paragraphs"][-1]["id"] == parsed["paragraphs"][-1]["id"]:
                value["notes"].append({"kind": "negative_result", "text": "在一个受测任务中没有改善，限制结论范围。",
                                       "paragraph_ids": [parsed["paragraphs"][-1]["id"]]})
            return value
        self.model.side_effect = notes
        result = distillation.distill_source(self.ws, SID) if default_model else self.run_paper()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("synthesis exceeds", result["reasons"][0])
        self.assertGreater(sum(len(p["text"]) for p in parsed["paragraphs"]), distillation.MAX_SYNTHESIS_CHARACTERS)
        self.calls.clear()
        self.model.side_effect = self.respond
        return result

    def provider_fixture(self, **kwargs):
        environment = patch.dict("os.environ", {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-flash"})
        environment.start()
        self.addCleanup(environment.stop)
        self.provider_default = Mock(side_effect=lambda messages, max_tokens=None: self.model(messages))
        adapter = patch.object(distillation, "_default_model", self.provider_default)
        adapter.start()
        self.addCleanup(adapter.stop)
        return self.long_context_failure(default_model=True, **kwargs)

    def test_provider_recovery_retains_all_originals_notes_and_checks_complete_requests(self):
        original = self.provider_fixture()
        result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["synthesize", "review"])
        author, verifier = self.calls
        self.assertEqual(author["paragraphs"], distillation._paragraph_input(self.long_parsed))
        self.assertEqual(len(author["source"]["read_paragraph_ids"]), 80)
        self.assertEqual(author["whole_paper_reading_notes"], verifier["whole_paper_reading_notes"])
        audit = result["provider_context_recovery"]
        self.assertEqual(audit["original_paragraphs"], 80)
        self.assertFalse(audit["originals_and_notes_truncated"])
        self.assertEqual(audit["provider_profile"]["documentation_checked_at"], "2026-10-16")
        self.assertEqual(audit["output_token_budget"], 32768)
        self.assertEqual({b["stage"] for b in audit["request_budgets"]}, {"synthesize", "review"})
        self.assertTrue(all(b["utf8_bytes"] <= 800000 for b in audit["request_budgets"]))
        self.assertEqual([call.kwargs for call in self.provider_default.call_args_list[-2:]], [{"max_tokens": 32768}] * 2)
        cache = self.ws.root / "distillations" / SID / original["cache_key"]
        self.assertFalse(list(cache.glob("draft-*.json")))
        self.assertTrue((cache / "provider-context-v1" / "draft-0.json").exists())
        self.assertEqual(json.loads((cache / "provider-context-v1" / "origin.json").read_text())["blocked_result"], original)
        count = self.model.call_count
        self.assertEqual(distillation.resume_provider_context(self.ws, SID), result)
        self.assertEqual(self.model.call_count, count)

    def test_provider_profile_refuses_other_host_model_and_custom_adapter(self):
        self.provider_fixture()
        for config in ({"LLM_BASE_URL": "https://api.deepseek.com.example.org"}, {"LLM_MODEL": "deepseek-chat"}):
            with patch.dict("os.environ", config):
                result = distillation.resume_provider_context(self.ws, SID)
                self.assertFalse(result["provider_context_eligible"])
                self.assertTrue(result["fallback_allowed"])
        result = distillation.resume_provider_context(self.ws, SID, model=self.model)
        self.assertFalse(result["provider_context_eligible"])
        self.assertEqual(self.calls, [])

    def test_provider_full_message_byte_budget_blocks_before_author_request(self):
        self.provider_fixture(paragraph_count=120, paragraph_text="汉" * 2300)
        result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["provider_context_recovery"]["status"], "budget_blocked")
        self.assertLess(result["provider_context_recovery"]["context_characters"], 500000)
        self.assertIn("800000-byte", result["reasons"][0])
        self.assertEqual(self.calls, [])
        self.assertFalse(result["fallback_allowed"])

    def test_provider_initial_character_budget_blocks_without_truncation(self):
        self.provider_fixture(paragraph_count=230, paragraph_text="x" * 2300)
        result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("500000-character", result["reasons"][0])
        self.assertEqual(result["provider_context_recovery"]["original_paragraphs"], 230)
        self.assertEqual(self.calls, [])

    def test_provider_review_request_budget_does_not_trigger_author_repairs(self):
        self.provider_fixture()
        actual_check = model_context.check_messages
        def check(messages, model=None):
            if json.loads(messages[-1]["content"])["stage"] == "review":
                raise model_context.ContextBudgetExceeded("Review complete request exceeds its byte budget")
            return actual_check(messages, model=model)
        with patch.object(model_context, "check_messages", side_effect=check):
            result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([c["stage"] for c in self.calls], ["synthesize"])
        self.assertEqual(result["provider_context_recovery"]["status"], "budget_blocked")
        count = self.model.call_count
        self.assertEqual(distillation.resume_provider_context(self.ws, SID), result)
        self.assertEqual(self.model.call_count, count)

    def test_provider_transport_resume_reuses_author_draft_and_original_request_audit(self):
        self.provider_fixture()
        def interrupted(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "review":
                raise TimeoutError("Provider review interrupted")
            return self.respond(messages)
        self.model.side_effect = interrupted
        with self.assertRaises(TimeoutError):
            distillation.resume_provider_context(self.ws, SID)
        self.calls.clear()
        self.model.side_effect = self.respond
        result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["review"])
        self.assertEqual(result["repair_rounds"], 0)
        self.assertEqual({b["stage"] for b in result["provider_context_recovery"]["request_budgets"]}, {"synthesize", "review"})

    def test_provider_semantic_rejection_keeps_two_repairs_and_cannot_be_reopened(self):
        self.provider_fixture()
        def rejected(messages):
            value = self.respond(messages)
            return review("contradicted") if json.loads(messages[-1]["content"])["stage"] == "review" else value
        self.model.side_effect = rejected
        result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["repair_rounds"], 2)
        self.assertEqual(sum(c["stage"] == "repair" for c in self.calls), 2)
        self.assertEqual({b["stage"] for b in result["provider_context_recovery"]["request_budgets"]}, {"synthesize", "review", "repair"})
        self.assertEqual(sum(b["request_attempts"] for b in result["provider_context_recovery"]["request_budgets"]), 6)
        count = self.model.call_count
        self.assertEqual(distillation.resume_provider_context(self.ws, SID), result)
        self.assertEqual(self.model.call_count, count)

    def test_provider_cannot_reopen_preexisting_nonprovider_semantic_rejection(self):
        self.provider_fixture()
        def rejected(messages):
            value = self.respond(messages)
            return review("contradicted") if json.loads(messages[-1]["content"])["stage"] == "review" else value
        self.model.side_effect = rejected
        with patch.object(distillation, "MAX_SYNTHESIS_CHARACTERS", 500000):
            existing = distillation.distill_source(self.ws, SID, force=True)
        self.assertEqual(existing["status"], "blocked")
        self.assertEqual(existing["repair_rounds"], 2)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "cannot reopen existing semantic"):
            distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(self.calls, [])

    def test_provider_cannot_switch_away_from_started_selected_authoring(self):
        self.provider_fixture()
        def interrupted(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "review":
                raise TimeoutError("Selected-author review interrupted")
            return self.respond(messages)
        self.model.side_effect = interrupted
        with self.assertRaises(TimeoutError):
            distillation.resume_long_source(self.ws, SID)
        self.calls.clear()
        self.model.side_effect = self.respond
        refused = distillation.resume_provider_context(self.ws, SID)
        self.assertTrue(refused["continue_selected_context"])
        self.assertEqual(self.calls, [])
        self.assertEqual(distillation.resume_long_source(self.ws, SID)["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["review"])

    def test_provider_selection_only_origin_is_recoverable_without_changing_selection_history(self):
        original = self.provider_fixture()
        def wrong(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] in {"select_evidence", "select_evidence_repair"}:
                value["selected_paragraph_ids"].append("p9999-0001")
            return value
        self.model.side_effect = wrong
        self.assertEqual(distillation.resume_long_source(self.ws, SID)["status"], "blocked")
        cache = self.ws.root / "distillations" / SID / original["cache_key"]
        history = {p.name: p.read_bytes() for p in cache.glob("long-source-selection*.json")}
        self.calls.clear()
        self.model.side_effect = self.respond
        result = distillation.resume_provider_context(self.ws, SID)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["synthesize", "review"])
        self.assertEqual({p.name: p.read_bytes() for p in cache.glob("long-source-selection*.json")}, history)

    def test_long_recovery_reuses_all_reading_selects_evidence_and_verifies_body(self):
        original = self.long_context_failure()
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["select_evidence", "synthesize", "review"])
        selector, author, verifier = self.calls
        self.assertTrue(all("text" not in p for p in selector["paragraph_catalogue"]))
        self.assertEqual(len(selector["paragraph_catalogue"]), 80)
        self.assertEqual(len(author["source"]["read_paragraph_ids"]), 80)
        self.assertEqual(len(author["paragraphs"]), 2)
        self.assertEqual(len(verifier["packet"]["source"]["read_paragraph_ids"]), 80)
        self.assertEqual(author["whole_paper_reading_notes"], verifier["whole_paper_reading_notes"])
        audit = result["long_source_recovery"]
        self.assertEqual(audit["all_paragraphs_read"], 80)
        self.assertEqual(audit["accounted_paragraphs"], 78)
        self.assertLessEqual(audit["context_characters"], distillation.MAX_SYNTHESIS_CHARACTERS)
        cache = self.ws.root / "distillations" / SID / original["cache_key"]
        self.assertEqual(json.loads((cache / "long-source-origin.json").read_text())["blocked_result"], original)
        self.assertEqual(result, distillation.resume_long_source(self.ws, SID, model=self.model))
        self.assertEqual(len(self.calls), 3)

    def test_long_recovery_accounts_read_unselected_material_without_changing_evidence(self):
        self.long_context_failure()
        def omitted(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] in {"select_evidence", "select_evidence_repair"}:
                value["accounted_paragraphs"].pop()
            return value
        self.model.side_effect = omitted
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["select_evidence", "select_evidence_repair", "select_evidence_repair", "synthesize", "review"])
        self.assertEqual(result["long_source_recovery"]["remaining_selection_repairs"], 0)
        audit = result["long_source_recovery"]["selection_normalization"]
        self.assertEqual(audit["selection_model_requests"], 0)
        self.assertEqual(audit["selected_paragraph_ids_unchanged"], ["p0001-0001", "p0080-0001"])
        added = next(c["new_record"] for c in audit["changes"] if c["kind"] == "account-read-unselected-paragraph")
        self.assertEqual(added["paragraph_id"], "p0079-0001")
        self.assertIn("已完整阅读", added["reason"])

    def test_long_recovery_cannot_drop_known_negative_result_or_invent_paragraph(self):
        for fault in ("negative", "unknown"):
            with self.subTest(fault=fault):
                # A fresh input identity prevents one rejected selection from being retried.
                self.archive(text="Fresh long source " + fault)
                self.long_context_failure()
                def wrong(messages):
                    value = self.respond(messages)
                    if fault == "negative":
                        value["argument_coverage"]["negative_results"] = {"status": "not_reported", "paragraph_ids": [], "note_ids": [], "reason": "略去负面结果。"}
                    else:
                        value["selected_paragraph_ids"].append("p9999-9999")
                    return value
                self.model.side_effect = wrong
                result = distillation.resume_long_source(self.ws, SID, model=self.model)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual([c["stage"] for c in self.calls], ["select_evidence", "select_evidence_repair", "select_evidence_repair"])
                self.assertTrue("negative" in result["reasons"][0] if fault == "negative" else "unknown" in result["reasons"][0])

    def test_long_recovery_transport_retry_reuses_selection_and_author_draft(self):
        self.long_context_failure()
        def interrupted(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "review":
                raise TimeoutError("Independent review interrupted")
            return self.respond(messages)
        self.model.side_effect = interrupted
        with self.assertRaises(TimeoutError):
            distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual([c["stage"] for c in self.calls], ["select_evidence", "synthesize"])
        self.calls.clear()
        self.model.side_effect = self.respond
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["review"])

    def test_selection_structure_repair_receives_exact_unmatched_note_and_all_input(self):
        self.long_context_failure()
        def fix(messages):
            context = json.loads(messages[-1]["content"])
            value = self.respond(messages)
            if context["stage"] == "select_evidence":
                value["argument_coverage"]["purpose"]["note_ids"] = context["negative_note_ids"]
            elif context["stage"] == "select_evidence_repair":
                self.assertEqual(context["selection_structure_repair_round"], 1)
                issue = next(v for v in context["validation_issues"] if "unmatched_note_ids" in v)
                self.assertEqual(issue["unmatched_note_ids"], context["negative_note_ids"])
                self.assertEqual(issue["coverage_paragraph_ids"], ["p0001-0001"])
                self.assertEqual(issue["note_paragraph_ids"][context["negative_note_ids"][0]], ["p0080-0001"])
                self.assertEqual(context["whole_paper_reading_notes"], self.calls[0]["whole_paper_reading_notes"])
                self.assertEqual(context["paragraph_catalogue"], self.calls[0]["paragraph_catalogue"])
            return value
        self.model.side_effect = fix
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["repair_rounds"], 0)
        self.assertEqual(result["long_source_recovery"]["selection_structure_repairs"], 1)
        self.assertEqual([c["stage"] for c in self.calls], ["select_evidence", "select_evidence_repair", "synthesize", "review"])

    def test_selection_accounting_repair_reports_missing_duplicate_unknown_and_overlap_ids(self):
        self.long_context_failure()
        def fix(messages):
            context = json.loads(messages[-1]["content"])
            value = self.respond(messages)
            if context["stage"] == "select_evidence":
                value["accounted_paragraphs"].pop()
                value["accounted_paragraphs"].append(dict(value["accounted_paragraphs"][0]))
                for identifier in ("p9999-0001", "p0001-0001"):
                    value["accounted_paragraphs"].append({"paragraph_id": identifier, "role": "背景", "reason": "保留在阅读记录。"})
            elif context["stage"] == "select_evidence_repair":
                duplicate = next(v for v in context["validation_issues"] if "duplicate_ids" in v)
                account = next(v for v in context["validation_issues"] if "missing_paragraph_ids" in v)
                self.assertEqual(duplicate["duplicate_ids"], ["p0002-0001"])
                self.assertEqual(account["missing_paragraph_ids"], ["p0079-0001"])
                self.assertEqual(account["unknown_paragraph_ids"], ["p9999-0001"])
                self.assertEqual(account["overlap_paragraph_ids"], ["p0001-0001"])
            return value
        self.model.side_effect = fix
        self.assertEqual(distillation.resume_long_source(self.ws, SID, model=self.model)["status"], "ready")

    def test_legacy_selection_only_blocked_can_use_remaining_structure_budget(self):
        original = self.long_context_failure()
        def fail(messages):
            value = self.respond(messages)
            value["accounted_paragraphs"].pop()
            return value
        self.model.side_effect = fail
        with patch.object(distillation, "MAX_SELECTION_REPAIRS", 0), patch.object(distillation, "_normalize_long_selection", side_effect=ValueError("Old version has no bookkeeping recovery")):
            blocked = distillation.resume_long_source(self.ws, SID, model=self.model)
        cache = self.ws.root / "distillations" / SID / original["cache_key"]
        legacy_selection = (cache / "long-source-selection.json").read_bytes()
        # Reconstruct the earlier one-request cache shape, with no consumed
        # structural-correction budget and no authoring stages.
        final = json.loads((cache / "result.json").read_text())
        final["long_source_recovery"]["status"] = "blocked"
        final["long_source_recovery"].pop("remaining_selection_repairs")
        final["long_source_recovery"].pop("selection_structure_repairs")
        final["long_source_recovery"].pop("selection_normalization")
        (cache / "result.json").write_text(json.dumps(final))
        state = json.loads((cache / "long-source-recovery.json").read_text())
        state.update(status="blocked")
        state.pop("remaining_selection_repairs")
        state.pop("selection_structure_repairs")
        (cache / "long-source-recovery.json").write_text(json.dumps(state))
        (cache / "long-source-selection-0.json").unlink()
        (cache / (distillation.SELECTION_NORMALIZATION_VERSION + ".json")).unlink()
        self.calls.clear()
        self.model.side_effect = self.respond
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["repair_rounds"], 0)
        self.assertEqual(result["long_source_recovery"]["selection_structure_repairs"], 1)
        self.assertEqual(result["long_source_recovery"]["remaining_selection_repairs"], 1)
        self.assertEqual([c["stage"] for c in self.calls], ["select_evidence_repair", "synthesize", "review"])
        self.assertEqual((cache / "long-source-selection.json").read_bytes(), legacy_selection)
        first = json.loads((cache / "long-source-selection-0.json").read_text())
        self.assertTrue(first["validation_issues"])

    def test_selection_repair_exhaustion_does_not_reset_or_author_on_later_retry(self):
        self.long_context_failure()
        def fail(messages):
            value = self.respond(messages)
            value["selected_paragraph_ids"].append("p9999-0001")
            return value
        self.model.side_effect = fail
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["long_source_recovery"]["selection_structure_repairs"], 2)
        self.assertEqual(result["long_source_recovery"]["remaining_selection_repairs"], 0)
        self.assertEqual(result["long_source_recovery"]["new_author_requests"], 0)
        self.calls.clear()
        self.model.side_effect = self.respond
        self.assertEqual(distillation.resume_long_source(self.ws, SID, model=self.model), result)
        self.assertEqual(self.calls, [])

    def test_deterministic_bookkeeping_normalization_changes_no_selected_evidence(self):
        self.long_context_failure()
        def navigation_only(messages):
            context = json.loads(messages[-1]["content"])
            value = self.respond(messages)
            if context["stage"] in {"select_evidence", "select_evidence_repair"}:
                value["argument_coverage"]["purpose"]["note_ids"] = context["negative_note_ids"]
                value["accounted_paragraphs"].append({"paragraph_id": "p0001-0001", "role": "重复导航", "reason": "已读记录。"})
            return value
        self.model.side_effect = navigation_only
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["repair_rounds"], 0)
        audit = result["long_source_recovery"]["selection_normalization"]
        self.assertEqual(audit["selection_model_requests"], 0)
        self.assertEqual(audit["selected_paragraph_ids_unchanged"], ["p0001-0001", "p0080-0001"])
        self.assertEqual({c["kind"] for c in audit["changes"]}, {"coverage-note-index", "remove-selected-account-overlap"})
        self.assertEqual(sum(c["stage"].startswith("select_evidence") for c in self.calls), 3)

    def test_bookkeeping_normalization_does_not_hide_an_over_budget_selected_set(self):
        self.long_context_failure()
        def too_much(messages):
            context = json.loads(messages[-1]["content"])
            value = self.respond(messages)
            value["selected_paragraph_ids"] = [p["id"] for p in context["paragraph_catalogue"]]
            value["accounted_paragraphs"] = []
            value["argument_coverage"]["purpose"]["note_ids"] = context["negative_note_ids"]
            return value
        self.model.side_effect = too_much
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("still exceeds", result["reasons"][0])
        self.assertEqual(result["long_source_recovery"]["new_author_requests"], 0)
        self.assertEqual(result["long_source_recovery"]["selection_normalization"]["status"], "structure_valid")
        self.assertEqual(len(result["long_source_recovery"]["selection_normalization"]["selected_paragraph_ids_unchanged"]), 80)
        self.assertTrue(all(c["stage"].startswith("select_evidence") for c in self.calls))

    def test_long_recovery_semantic_failure_keeps_two_repair_limit_on_reentry(self):
        self.long_context_failure()
        def rejected(messages):
            value = self.respond(messages)
            return review("contradicted") if json.loads(messages[-1]["content"])["stage"] == "review" else value
        self.model.side_effect = rejected
        result = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["repair_rounds"], 2)
        self.assertEqual(sum(c["stage"] == "repair" for c in self.calls), 2)
        count = len(self.calls)
        again = distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(again["status"], "blocked")
        self.assertEqual(again["repair_rounds"], 2)
        self.assertEqual(len(self.calls), count)

    def test_long_recovery_refuses_preexisting_authoring_or_changed_source_identity(self):
        original = self.long_context_failure()
        cache = self.ws.root / "distillations" / SID / original["cache_key"]
        path = cache / "draft-0.json"
        path.write_text(json.dumps({"draft": packet(), "cache_key": original["cache_key"], "library_id": self.ws.library_id}))
        with self.assertRaisesRegex(ValueError, "reset existing"):
            distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(self.calls, [])
        path.unlink()
        self.archive(text="Changed after long-source reading")
        with self.assertRaisesRegex(ValueError, "current long-source input identity"):
            distillation.resume_long_source(self.ws, SID, model=self.model)
        self.assertEqual(self.calls, [])

    def run_paper(self, **kwargs):
        return distillation.distill_source(self.ws, SID, model=self.model, **kwargs)

    def test_pdf_paper_ready_only_after_separate_review_and_exact_archived_evidence(self):
        before = self.path.read_bytes()
        result = self.run_paper()
        self.assertEqual(result["status"], "ready")
        self.assertEqual([v["stage"] for v in self.calls], ["synthesize", "review"])
        claim = result["packet"]["claims"][0]
        self.assertEqual(claim["evidence_passages"][0]["quote"], PARAGRAPH)
        self.assertEqual(claim["evidence_passages"][0]["locator"]["paragraph_id"], "p0001-0001")
        self.assertNotIn("Fabricated", json.dumps(result["packet"]))
        self.assertEqual(claim["status"], "supported")
        self.assertFalse(result["reviews"][0]["independent_research_validation"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.ws.wiki.exists())

    def test_current_skill_packet_uses_frozen_archive_attribution_when_no_marker_is_generated(self):
        value = packet()
        value['body_markdown'] = value['body_markdown'].replace('[S:' + SID + ']', '')
        parsed = self.parsed()
        source = {'id': SID, 'title': 'Archived paper'}
        validated = distillation.validate_packet(value, source, parsed, require_current_schema=True)
        self.assertTrue(validated['body_markdown'].endswith('[S:' + SID + ']'))
        self.assertEqual(validated['source_notes']['attribution_normalization']['source_id'], SID)
        value['body_markdown'] += '[S:aaaaaaaaaaaaaaaa]'
        with self.assertRaisesRegex(ValueError, 'only its own archived source'):
            distillation.validate_packet(value, source, parsed, require_current_schema=True)

    def test_current_skill_packet_needs_no_wiki_planning_or_legacy_point_fields(self):
        def native(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] != "review":
                point = value["knowledge_points"][0]
                point["statement"] = point.pop("proposition")
                point.pop("basis")
                point.pop("value")
            return value
        self.model.side_effect = native
        result = self.run_paper()
        self.assertEqual(result["status"], "ready")
        value = result["packet"]
        self.assertNotIn("concept_proposals", value)
        self.assertNotIn("topic_proposals", value)
        point = value["knowledge_points"][0]
        self.assertEqual(point["statement"], point["proposition"])
        reference = point["evidence"]["passage_refs"][0]
        claim = next(c for c in value["claims"] if c["id"] == reference["claim_id"])
        evidence = claim["evidence_passages"][reference["passage_index"]]
        self.assertEqual(evidence["quote"], PARAGRAPH)
        self.assertEqual(evidence["locator"], reference["locator"])
        self.assertEqual(reference["source_version"], value["source"]["source_version"])

    def test_current_classifications_and_uncertainty_cannot_be_empty_or_invented(self):
        validated = self.run_paper()["packet"]
        for field, invalid in (("type", None), ("type", "主题"), ("transferability", "全能"),
                               ("uncertainty", ""), ("uncertainty", {"unexplained": None}),
                               ("uncertainty", [False, {}, " "])):
            with self.subTest(field=field, invalid=invalid):
                value = packet()
                value["knowledge_points"][0][field] = invalid
                with self.assertRaises(ValueError):
                    distillation.validate_packet(value, validated["source"], self.parsed(), require_current_schema=True)
        value = packet()
        value["concept_proposals"] = [{"name": "经验机制", "question": "诊断如何帮助重试？",
                                       "explanation": "保留修正方向。", "knowledge_point_ids": ["k1"]}]
        with self.assertRaisesRegex(ValueError, "must not generate concept planning"):
            distillation.validate_packet(value, validated["source"], self.parsed())

    def test_legacy_packet_still_validates_without_claiming_new_classification_compliance(self):
        source = self.run_paper()["packet"]["source"]
        value = packet()
        value.pop("schema_version")
        for field in ("type", "transferability", "uncertainty"):
            value["knowledge_points"][0].pop(field)
        value["concept_proposals"] = [{"name": "经验机制", "question": "诊断如何帮助重试？",
                                       "explanation": "保留修正方向。", "knowledge_point_ids": ["k1"]}]
        value["topic_proposals"] = [{"name": "智能体学习", "concept_names": ["经验机制"],
                                    "organization_reason": "相同的大问题。"}]
        legacy = distillation.validate_packet(value, source, self.parsed())
        self.assertEqual(legacy["schema_version"], distillation.LEGACY_SCHEMA_VERSION)
        self.assertNotIn("type", legacy["knowledge_points"][0])
        self.assertEqual(legacy["concept_proposals"][0]["name"], "经验机制")
        with self.assertRaisesRegex(ValueError, "knowledge point type"):
            distillation.validate_packet(value, source, self.parsed(), require_current_schema=True)

    def test_author_unknowns_survive_separately_from_parser_facts_and_visual_limits(self):
        author_notes = {"status": "full", "unexplained": ["作者未说明为何跨任务迁移会有效。"],
                        "gaps": ["图形曲线没有可读数值，无法判断每个任务的趋势。"]}
        def note_writer(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] != "review":
                value["source_notes"] = author_notes
            return value
        self.model.side_effect = note_writer
        parsed = self.parsed()
        parsed.update(status="partial", warnings=["Uncertain OCR"], coverage={"total_pages": 2, "missing_pages": [2]})
        with patch.object(distillation, "parse_source", return_value=parsed):
            result = self.run_paper()
        notes = result["packet"]["source_notes"]
        self.assertEqual(notes["research_notes"], author_notes)
        self.assertEqual(notes["status"], "partial")
        self.assertEqual(notes["warnings"], ["Uncertain OCR"])
        self.assertFalse(notes["visual_pdf_pages_read"])
        scope = result["packet"]["source"]["readable_scope"]
        self.assertFalse(scope["visual_pdf_pages_read"])
        self.assertEqual(scope["coverage"]["missing_pages"], [2])

    def test_full_provenance_reaches_author_reviewer_and_invalidates_cache(self):
        skill_directory = Path(self.tmp.name) / "test-skill"
        for name in distillation.SKILL_FILES:
            target = skill_directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((distillation.SKILL_ROOT / name).read_text(), encoding="utf-8")
        provenance = skill_directory / "references/provenance.md"
        original = provenance.read_text()
        with patch.object(distillation, "SKILL_ROOT", skill_directory):
            first = self.run_paper()
            for call in self.model.call_args_list:
                self.assertIn(original, call.args[0][0]["content"])
            provenance.write_text(original + "\n独立出处测试标记：拒绝用标题替代证据。\n")
            second = self.run_paper()
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        self.assertEqual(self.model.call_count, 4)

    def test_independent_argument_issues_need_repair_but_editorial_issue_does_not(self):
        value = self.run_paper()["packet"]
        for field in ("argument_chain_issues", "design_rationale_issues", "overgeneralization_issues", "reading_coherence_issues"):
            for severity in ("blocked", "needs-revision", "editorial"):
                with self.subTest(field=field, severity=severity):
                    judgment = review()
                    judgment["argument_review"][field] = [{"location": "正文第2段", "statement": "设计直接保证跨领域改善。",
                        "source_basis": "归档段落仅报告同条件、部分任务收益。", "severity": severity,
                        "suggestion": "区分作者设计解释和已验证结论。", "rechecked": True}]
                    audit = distillation.verify_packet(self.ws, self.parsed(), value, model=lambda _: judgment)
                    self.assertEqual(bool(audit["blocking_issues"]), severity != "editorial")
                    self.assertEqual(audit["conclusion"], "ready" if severity == "editorial" else severity)
        missing = review()
        missing.pop("argument_review")
        with self.assertRaisesRegex(ValueError, "research argument"):
            distillation.verify_packet(self.ws, self.parsed(), value, model=lambda _: missing)

    def test_unread_key_visual_cannot_be_certified_by_supported_text_claim(self):
        def unread(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] == "review":
                self.assertIn("未视觉读取", messages[0]["content"])
                value["source_coverage"] = {"status": "blocked", "reason": "核心结论依赖不可读的曲线，文字没有报告对应趋势。",
                                            "affected_paragraph_ids": ["p0001-0001"]}
            return value
        self.model.side_effect = unread
        result = self.run_paper()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["repair_rounds"], 2)
        self.assertTrue(all(r["conclusion"] == "blocked" for r in result["reviews"]))

    def test_large_review_includes_archived_negative_evidence_beyond_author_claims(self):
        value = self.run_paper()["packet"]
        parsed = self.parsed()
        parsed["paragraphs"] += [{"id": "p0002-0001", "page": 2, "start": 0, "end": 48000,
                                   "text": "Background bibliography. " * 2000},
                                  {"id": "p0003-0001", "page": 3, "start": 0, "end": 70,
                                   "text": "A second task did not improve; the apparent benefit was domain-specific."}]
        notes = [{"batch": 2, "notes": [{"kind": "negative_result", "text": "跨任务没有收益。", "paragraph_ids": ["p0003-0001"]}]}]
        context = distillation._review_input(parsed, value, notes)
        self.assertEqual([p["id"] for p in context["original_paragraphs"]], ["p0001-0001", "p0003-0001"])
        self.assertEqual(context["original_paragraphs"][-1]["text"], parsed["paragraphs"][-1]["text"])
        with patch.dict("os.environ", {"LLM_MODEL": "deepseek-flash", "LLM_BASE_URL": "https://api.deepseek.com"}):
            custom = Mock(return_value=review())
            distillation.verify_packet(self.ws, parsed, value, model=custom, notes=notes)
            self.assertEqual(json.loads(custom.call_args.args[0][-1]["content"])["original_context"], "claims_and_reading_note_evidence")
            default = Mock(return_value=review())
            with patch.object(distillation, "_default_model", default):
                distillation.verify_packet(self.ws, parsed, value, notes=notes)
            full = json.loads(default.call_args.args[0][-1]["content"])
            self.assertEqual(full["original_context"], "complete_parsed_text")
            self.assertEqual(len(full["original_paragraphs"]), 3)

    def test_txt_news_missing_and_fake_pdf_are_filtered_before_model_or_parser(self):
        cases = [{"kind": "news"}, {"pdf_path": "missing.pdf"}, {"pdf_path": "original.txt", "content_format": "txt"},
                 {"pdf_path": "fake.pdf"}]
        (self.ws.originals / "original.txt").write_text(PARAGRAPH)
        (self.ws.originals / "fake.pdf").write_text("This is not a PDF")
        for changes in cases:
            with self.subTest(changes=changes):
                self.archive(**changes)
                self.assertEqual(self.run_paper()["status"], "blocked")
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(distillation.parse_source.call_count, 0)

    def test_valid_result_reuses_cache_but_source_skill_parser_prompt_and_model_invalidate(self):
        first = self.run_paper()
        self.assertEqual(first, self.run_paper())
        self.assertEqual(self.model.call_count, 2)
        keys = {first["cache_key"]}
        self.archive(text="Source content changed")
        keys.add(self.run_paper()["cache_key"])
        with patch.object(distillation, "_skill", return_value=("changed skill", "changed-skill-version")):
            keys.add(self.run_paper()["cache_key"])
        original_parsed = self.parsed
        with patch.object(self, "parsed", side_effect=lambda: {**original_parsed(), "cache_key": "parsed-v2"}):
            keys.add(self.run_paper()["cache_key"])
        with patch.object(distillation, "PROMPT_VERSION", "new-prompt"):
            keys.add(self.run_paper()["cache_key"])
        self.model.model_id = "test-paper-model-v2"
        keys.add(self.run_paper()["cache_key"])
        self.assertEqual(len(keys), 6)
        self.assertEqual(self.model.call_count, 12)

    def test_independent_support_failure_blocks_after_only_two_repairs(self):
        def reject(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] == "review":
                return review("partially_supported")
            return value
        self.model.side_effect = reject
        result = self.run_paper()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["repair_rounds"], 2)
        self.assertEqual([v["stage"] for v in self.calls], ["synthesize", "review", "repair", "review", "repair", "review"])
        self.assertEqual(len(result["reviews"]), 3)
        self.assertEqual(result["packet"]["claims"][0]["status"], "pending_verification")
        calls = self.model.call_count
        self.assertEqual(result, self.run_paper())
        self.assertEqual(self.model.call_count, calls)

    def test_body_new_facts_and_omitted_knowledge_block_despite_supported_claims(self):
        for body in ({"unsupported_facts": ["正文添加跨任务泛化效果。"]}, {"omitted_knowledge_ids": ["k1"]},
                     {"omitted_key_results": ["漏掉未改善任务。"]}):
            with self.subTest(body=body):
                self.model.side_effect = lambda messages: review(**body) if json.loads(messages[-1]["content"])["stage"] == "review" else packet()
                result = self.run_paper(force=True)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["repair_rounds"], 2)

    def test_advisory_expression_suggestion_does_not_block(self):
        self.model.side_effect = lambda messages: review(editorial_suggestions=["可压缩重复句。"]) if json.loads(messages[-1]["content"])["stage"] == "review" else packet()
        self.assertEqual(self.run_paper()["status"], "ready")

    def test_archived_paragraph_citation_is_normalized_without_a_repair(self):
        def anchored(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "review":
                body = context["packet"]["body_markdown"]
                self.assertIn("[S:" + SID + "]（第1页，p0001-0001）", body)
                self.assertNotIn("[S:p0001-0001]", body)
                return self.respond(messages)
            value = self.respond(messages)
            value["body_markdown"] = value["body_markdown"].replace("[S:" + SID + "]", "[S:p0001-0001]（第1页，p0001-0001）")
            return value
        self.model.side_effect = anchored
        result = self.run_paper()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["repair_rounds"], 0)
        self.assertEqual([v["stage"] for v in self.calls], ["synthesize", "review"])

    def test_unknown_paragraph_and_cross_source_citations_remain_rejected(self):
        parsed = self.parsed()
        source = {"id": SID, "title": "原论文"}
        for marker in ("p9999-0001", "fedcba0987654321"):
            with self.subTest(marker=marker):
                value = packet()
                value["body_markdown"] = value["body_markdown"].replace("[S:" + SID + "]", "[S:" + marker + "]")
                with self.assertRaisesRegex(ValueError, "only its own archived source"):
                    distillation.validate_packet(value, source, parsed)

    def old_citation_failure(self, marker="p0001-0001"):
        """Build the exact terminal cache produced before citation normalization."""
        def anchored(messages):
            value = self.respond(messages)
            value["body_markdown"] = value["body_markdown"].replace("[S:" + SID + "]", "[S:" + marker + "]（第1页，p0001-0001）")
            return value
        self.model.side_effect = anchored
        with patch.object(distillation, "validate_packet", side_effect=ValueError("Paper body must cite only its own archived source")):
            result = self.run_paper()
        self.calls.clear()
        self.model.side_effect = self.respond
        return result

    def test_structure_only_cached_failure_revalidates_without_author_or_new_repair(self):
        original = self.old_citation_failure()
        result = distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["stage"] for c in self.calls], ["review"])

        self.assertEqual(result["repair_rounds"], original["repair_rounds"])
        self.assertEqual(result["repair_rounds"], 2)
        self.assertEqual(result["attempts"], original["attempts"])
        self.assertEqual(result["identity"], original["identity"])
        self.assertIn("[S:" + SID + "]（第1页，p0001-0001）", result["packet"]["body_markdown"])
        audit = result["structure_revalidations"][0]
        self.assertEqual(audit["author_requests"], 0)
        self.assertEqual(audit["additional_repair_rounds"], 0)
        self.assertEqual(audit["paragraph_ids"], ["p0001-0001"])
        self.assertEqual(result, self.run_paper())
        self.assertEqual([c["stage"] for c in self.calls], ["review"])

    def test_validation_preserves_the_original_author_draft(self):
        value = packet()
        before = json.loads(json.dumps(value))
        distillation.validate_packet(value, {'id': SID, 'title': 'Paper'}, self.parsed())
        self.assertEqual(value, before)

    def test_evidence_type_does_not_require_an_empirical_result_for_theoretical_claims(self):
        value = packet()
        point = value['knowledge_points'][0]
        point.update(type='证据', basis='theoretical', result_ids=[])
        validated = distillation.validate_packet(value, {'id': SID, 'title': 'Theory'}, self.parsed(), require_current_schema=True)
        self.assertEqual(validated['knowledge_points'][0]['result_ids'], [])
        self.assertTrue(validated['knowledge_points'][0]['evidence']['claim_ids'])
        point['basis'] = 'empirical'
        with self.assertRaisesRegex(ValueError, 'supporting result'):
            distillation.validate_packet(value, {'id': SID, 'title': 'Theory'}, self.parsed(), require_current_schema=True)

    def test_theoretical_adapter_recovery_still_blocks_a_failed_semantic_review(self):
        original = self.old_citation_failure()
        directory = self.ws.root / 'distillations' / SID / original['cache_key']
        error = 'Empirical knowledge point requires a supporting result'
        for index in range(3):
            draft = packet()
            draft['knowledge_points'][0].update(type='证据', basis='theoretical', result_ids=[])
            for name in ('draft-' + str(index) + '.json', 'round-' + str(index) + '.json'):
                path = directory / name
                value = json.loads(path.read_text())
                value['draft'] = draft
                if 'problems' in value:
                    value['problems'] = [error]
                path.write_text(json.dumps(value))
        path = directory / 'result.json'
        saved = json.loads(path.read_text())
        saved['reasons'] = [error]
        for attempt in saved['attempts']:
            attempt['problems'] = [error]
        path.write_text(json.dumps(saved))
        def partial(messages):
            self.respond(messages)
            return review('partially_supported')
        self.model.side_effect = partial
        result = distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['repair_rounds'], 2)
        self.assertTrue(result['structure_revalidations'][0]['theoretical_evidence_requires_semantic_review'])
        self.assertEqual([call['stage'] for call in self.calls], ['review'])

    def test_missing_attribution_and_derived_cache_fields_recover_without_new_authoring(self):
        original = self.old_citation_failure()
        directory = self.ws.root / 'distillations' / SID / original['cache_key']
        for index in range(3):
            raw = packet()
            raw['body_markdown'] = raw['body_markdown'].replace('[S:' + SID + ']', '')
            raw['schema_version'] = '1'
            raw['knowledge_points'][0].pop('basis', None)
            raw['knowledge_points'][0].pop('value', None)
            draft_path = directory / ('draft-' + str(index) + '.json')
            draft_value = json.loads(draft_path.read_text())
            draft_value['draft'] = raw
            draft_path.write_text(json.dumps(draft_value))
            round_path = directory / ('round-' + str(index) + '.json')
            round_value = json.loads(round_path.read_text())
            round_value['draft'] = json.loads(json.dumps(raw))
            canonical = distillation.validate_packet(raw, {'id': SID, 'title': 'Paper'}, self.parsed(), require_current_schema=True)
            round_value['draft']['knowledge_points'] = canonical['knowledge_points']
            round_path.write_text(json.dumps(round_value))
        result = distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['repair_rounds'], 2)
        self.assertTrue(result['structure_revalidations'][0]['validation_normalized_draft'])
        self.assertEqual(result['structure_revalidations'][0]['author_requests'], 0)
        self.assertEqual([c['stage'] for c in self.calls], ['review'])



    def test_current_source_prefixed_paragraph_marker_normalizes_and_is_audited(self):
        self.old_citation_failure(SID + ":p0001-0001")
        result = distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["packet"]["source_notes"]["citation_paragraph_ids"], ["p0001-0001"])
        self.assertEqual(result["structure_revalidations"][0]["paragraph_ids"], ["p0001-0001"])
        self.assertEqual([c["stage"] for c in self.calls], ["review"])
        for marker in (SID + ":p9999-0001", "fedcba0987654321:p0001-0001"):
            value = packet()
            value["body_markdown"] = value["body_markdown"].replace("[S:" + SID + "]", "[S:" + marker + "]")
            with self.assertRaisesRegex(ValueError, "only its own archived source"):
                distillation.validate_packet(value, {"id": SID, "title": "研究"}, self.parsed())

    def reading_fixture(self):
        parsed = self.parsed()
        parsed["paragraphs"].append({**parsed["paragraphs"][0], "id": "p0002-0001", "page": 2})
        parser = patch.object(distillation, "parse_source", return_value=parsed)
        direct = patch.object(distillation, "DIRECT_CHARACTERS", 1)
        batch = patch.object(distillation, "BATCH_CHARACTERS", 180)
        for fixture in (parser, direct, batch):
            fixture.start()
            self.addCleanup(fixture.stop)
        return parsed

    def test_read_note_duplicate_anchors_are_normalized_without_repair(self):
        self.reading_fixture()
        def duplicate(messages):
            value = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] == "read":
                value["notes"][0]["paragraph_ids"] *= 2
            return value
        self.model.side_effect = duplicate
        result = self.run_paper()
        self.assertEqual(result["status"], "ready")
        self.assertNotIn("read_repair", [c["stage"] for c in self.calls])
        for path in (self.ws.root / "distillations" / SID / result["cache_key"]).glob("read-*.json"):
            record = json.loads(path.read_text())
            self.assertEqual(len(record["notes"][0]["paragraph_ids"]), 1)

    def test_read_structure_correction_caches_raw_and_rejects_cross_batch_ids(self):
        self.reading_fixture()
        def correct(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "read_repair":
                self.calls.append(context)
                self.assertEqual(context["allowed_paragraph_ids"], ["p0001-0001"])
                self.assertIn("validation_error", context)
                identifier = "p9999-0001" if context["read_structure_repair_round"] == 1 else "p0001-0001"
                return {"notes": [{"kind": "result", "text": "保留结果条件。", "paragraph_ids": [identifier]}]}
            value = self.respond(messages)
            if context["stage"] == "read" and context["batch"] == 1:
                value["notes"][0]["paragraph_ids"] = ["p0002-0001"]
            return value
        self.model.side_effect = correct
        result = self.run_paper()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["repair_rounds"], 0)
        self.assertEqual([c["stage"] for c in self.calls], ["read", "read_repair", "read_repair", "read", "synthesize", "review"])
        cache = self.ws.root / "distillations" / SID / result["cache_key"]
        raw = json.loads((cache / "reading-0-attempt-0.json").read_text())
        self.assertEqual(raw["response"]["notes"][0]["paragraph_ids"], ["p0002-0001"])
        self.assertIn("outside its batch", raw["validation_error"])

    def test_read_structure_budget_is_separate_bounded_and_reuses_valid_batches(self):
        self.reading_fixture()
        def fail(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "read_repair":
                self.calls.append(context)
                return {"notes": [{"kind": "result", "text": "定位仍错误。", "paragraph_ids": ["p9999-0001"]}]}
            value = self.respond(messages)
            if context["stage"] == "read" and context["batch"] == 2:
                value["notes"][0]["paragraph_ids"] = ["p9999-0001"]
            return value
        self.model.side_effect = fail
        with self.assertRaisesRegex(ValueError, "after two corrections"):
            self.run_paper()
        self.assertEqual([c["stage"] for c in self.calls], ["read", "read", "read_repair", "read_repair"])
        self.assertEqual(len(list((self.ws.root / "distillations").glob("**/read-0.json"))), 1)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "after two corrections"):
            self.run_paper()
        self.assertEqual(self.calls, [])
        self.assertEqual(list((self.ws.root / "distillations").glob("**/draft-*.json")), [])

    def test_read_invalid_json_stops_this_run_and_records_type_for_later_retry(self):
        self.reading_fixture()
        self.model.side_effect = json.JSONDecodeError("Provider returned invalid JSON", "", 0)
        with self.assertRaises(json.JSONDecodeError):
            self.run_paper()
        self.assertEqual(self.model.call_count, 1)
        paths = list((self.ws.root / "distillations").glob("**/reading-0-attempt-0.json"))
        self.assertEqual(len(paths), 1)
        self.assertEqual(json.loads(paths[0].read_text())["request_error_type"], "JSONDecodeError")
        self.model.side_effect = self.respond
        self.assertEqual(self.run_paper()["status"], "ready")

    def test_structure_recovery_semantic_failure_stays_blocked_and_cannot_repeat(self):
        original = self.old_citation_failure()
        def partial(messages):
            self.respond(messages)
            return review("partially_supported")
        self.model.side_effect = partial
        result = distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["repair_rounds"], 2)
        self.assertEqual(result["attempts"], original["attempts"])
        self.assertEqual([c["stage"] for c in self.calls], ["review"])
        self.assertIn("partially_supported", result["reasons"][0])
        with self.assertRaisesRegex(ValueError, "semantic review|prior recovery"):
            distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual([c["stage"] for c in self.calls], ["review"])

    def test_structure_recovery_refuses_prior_semantic_rejection_without_calling_model(self):
        def partial(messages):
            value = self.respond(messages)
            return review("contradicted") if json.loads(messages[-1]["content"])["stage"] == "review" else value
        self.model.side_effect = partial
        self.assertEqual(self.run_paper()["status"], "blocked")
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "semantic review"):
            distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(self.calls, [])

    def test_structure_recovery_refuses_changed_source_or_model_identity(self):
        original = self.old_citation_failure()
        cached = self.ws.root / "distillations" / SID / original["cache_key"] / "result.json"
        previous = cached.read_bytes()
        self.model.model_id = "different-review-model"
        with self.assertRaisesRegex(ValueError, "current distillation identity"):
            distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.model.model_id = "test-paper-model-v1"
        self.archive(text="Original changed after the old structure failure")
        with self.assertRaisesRegex(ValueError, "current distillation identity"):
            distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(self.calls, [])
        self.assertEqual(cached.read_bytes(), previous)

    def test_structure_recovery_checks_each_cached_round_for_hidden_semantic_review(self):
        original = self.old_citation_failure()
        path = self.ws.root / "distillations" / SID / original["cache_key"] / "round-0.json"
        value = json.loads(path.read_text())
        value["review"] = review("contradicted")
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "semantic review"):
            distillation.revalidate_cached_structure(self.ws, SID, model=self.model)
        self.assertEqual(self.calls, [])

    def test_evidence_and_knowledge_relationships_are_validated(self):
        mutations = [lambda p: p["claims"][0].update(paragraph_ids=["foreign-paragraph"]),
                     lambda p: p["results"][0].update(claim_ids=["foreign-claim"]),
                     lambda p: p["knowledge_points"][0].update(result_ids=["foreign-result"]),
                     lambda p: p["knowledge_points"][0].update(claim_ids=[]),
                     lambda p: p.update(concept_proposals=[{"name": "候选概念", "question": "何种机制？", "explanation": "未知知识点", "knowledge_point_ids": ["foreign-point"]}]),
                     lambda p: p.update(topic_proposals=[{"name": "候选主题", "concept_names": ["foreign-concept"], "organization_reason": "未知概念"}]),
                     lambda p: p.update(body_markdown=p["body_markdown"] + "\n## 怎样成为 Wiki 概念和主题的输入\n编写说明。"),
                     lambda p: p.update(body_markdown=p["body_markdown"] + "[S:fedcba0987654321]")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                def bad(messages):
                    value = packet()
                    mutate(value)
                    return value
                self.model.side_effect = bad
                result = self.run_paper(force=True)
                self.assertEqual(result["status"], "blocked")
                self.assertIsNone(result["packet"])

    def test_long_paper_reads_every_paragraph_before_synthesis_and_reuses_batch_cache(self):
        parsed = self.parsed()
        paragraphs = []
        for i in range(4):
            paragraphs.append({"id": f"p0001-{i+1:04d}", "page": 1, "start": i * 400,
                               "end": (i+1)*400, "text": PARAGRAPH + str(i) + " context" * 40})
        parsed["paragraphs"] = paragraphs
        with patch.object(distillation, "parse_source", return_value=parsed), patch.object(distillation, "DIRECT_CHARACTERS", 300), patch.object(distillation, "BATCH_CHARACTERS", 600):
            first = self.run_paper()
            self.assertEqual(first["status"], "ready")
            stages = [v["stage"] for v in self.calls]
            self.assertEqual(stages, ["read"] * 4 + ["synthesize", "review"])
            actual = [p["id"] for call in self.calls if call["stage"] == "read" for p in call["paragraphs"]]
            self.assertEqual(actual, [p["id"] for p in paragraphs])
            cached = self.ws.root / "distillations" / SID / first["cache_key"]
            (cached / "result.json").unlink()
            (cached / "round-0.json").unlink()
            (cached / "draft-0.json").unlink()
            self.calls.clear()
            self.assertEqual(self.run_paper()["status"], "ready")
            self.assertEqual([v["stage"] for v in self.calls], ["synthesize", "review"])

    def test_review_transport_interruption_reuses_completed_draft(self):
        def interrupted(messages):
            if json.loads(messages[-1]["content"])["stage"] == "review":
                raise TimeoutError("Review connection timed out")
            return self.respond(messages)
        self.model.side_effect = interrupted
        with self.assertRaises(TimeoutError):
            self.run_paper()
        self.calls.clear()
        self.model.side_effect = self.respond
        result = self.run_paper()
        self.assertEqual(result["status"], "ready")
        self.assertEqual([v["stage"] for v in self.calls], ["review"])

    def test_preferred_txt_cannot_be_certified_using_an_unselected_pdf(self):
        (self.ws.originals / "original.txt").write_text(PARAGRAPH)
        self.archive(pdf_path="original.txt", asset_path=SID + ".pdf")
        self.assertEqual(self.run_paper()["status"], "blocked")
        self.assertEqual(distillation.parse_source.call_count, 0)

    def test_missing_pages_can_never_be_silently_certified_complete(self):
        parsed = self.parsed()
        parsed.update(status="partial", coverage={"total_pages": 2, "missing_pages": [2]})
        def missing(messages):
            context = json.loads(messages[-1]["content"])
            if context["stage"] == "review":
                self.assertEqual(context["source"]["coverage"]["missing_pages"], [2])
                value = review()
                value["source_coverage"] = {"status": "blocked", "reason": "主要结果在缺失页。", "affected_paragraph_ids": []}
                return value
            return packet()
        self.model.side_effect = missing
        with patch.object(distillation, "parse_source", return_value=parsed):
            result = self.run_paper()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["packet"]["source"]["parsing_status"], "partial")

    def test_changed_source_during_remote_call_is_not_cached_as_ready(self):
        def changed(messages):
            result = self.respond(messages)
            if json.loads(messages[-1]["content"])["stage"] == "review":
                self.archive(text="Changed while distilling")
            return result
        self.model.side_effect = changed
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.run_paper()
        self.assertEqual(list((self.ws.root / "distillations").glob("**/result.json")), [])

    def test_cancellation_checked_before_call_and_publication(self):
        with patch.object(distillation, "check_cancelled", side_effect=RuntimeError("Cancelled")):
            with self.assertRaisesRegex(RuntimeError, "Cancelled"):
                self.run_paper()
        self.assertEqual(self.model.call_count, 0)


if __name__ == "__main__":
    unittest.main()
