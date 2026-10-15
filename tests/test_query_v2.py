"""Answers must cite this library's current archived originals, not model URLs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from station import query
from station.documents import source_version
from station.workspaces import Workspace

SID = "a" * 16


def workspace(root, name="candidate-v2"):
    ws = Workspace(name, Path(root))
    for folder in (ws.raw, ws.wiki, ws.meta, ws.originals, ws.root / "config", ws.root / "parsed"):
        folder.mkdir(parents=True, exist_ok=True)
    return ws


def source(ws, text, status="full-text"):
    (ws.raw / (SID + ".json")).write_text(json.dumps(
        {"id": SID, "title": "Synthetic original", "text": text, "content_status": status},
        ensure_ascii=False))


def supported_model(messages):
    evidence = json.loads(messages[-1]["content"])["evidence"]
    return {"status": "answered", "statements": [
        {"text": "Feedback preserves the stated experimental conditions.", "citation_ids": [evidence[0]["id"]]}]}


class QueryV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = workspace(self.temp.name)
        self.env = patch.dict(os.environ, {"EMBEDDING_BASE_URL": "", "EMBEDDING_MODEL": "",
                                           "EMBEDDING_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_answer_has_verified_source_and_library_url_and_is_read_only(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        before = {p: p.read_bytes() for folder in (self.ws.raw, self.ws.wiki, self.ws.meta)
                  for p in folder.iterdir()}
        result = query.answer(self.ws, "Feedback", model=supported_model)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["citations"][0]["source_id"], SID)
        self.assertIn("library_id=candidate-v2", result["citations"][0]["url"])
        self.assertIn("[E1](/source/", result["answer_markdown"])
        self.assertEqual(before, {p: p.read_bytes() for folder in (self.ws.raw, self.ws.wiki, self.ws.meta)
                                 for p in folder.iterdir()})

    def test_no_evidence_never_calls_model(self):
        model = Mock(side_effect=AssertionError("No source, no model"))
        self.assertEqual(query.answer(self.ws, "missing concept", model)["status"], "insufficient_evidence")
        model.assert_not_called()

    def test_summary_is_not_original_evidence(self):
        source(self.ws, "Feedback summary containing no archived original evidence.", "summary-only")
        model = Mock()
        self.assertEqual(query.answer(self.ws, "Feedback", model)["citations"], [])
        model.assert_not_called()

    def test_unknown_and_empty_citations_are_rejected_without_showing_facts(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        for ids in ([], ["E999"], ["other-library"], [1]):
            with self.subTest(ids=ids):
                model = lambda _: {"status": "answered", "statements": [
                    {"text": "Unverified model fact.", "citation_ids": ids}]}
                result = query.answer(self.ws, "Feedback", model)
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertNotIn("Unverified model fact", result["answer_markdown"])

    def test_custom_links_unknown_source_markers_and_html_are_rejected(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        for text in ("Read [fabrication](https://external.invalid)", "False [S:unknown]",
                     "<script>alert(1)</script>", "False [E99]"):
            with self.subTest(text=text):
                model = lambda _: {"status": "answered", "statements": [
                    {"text": text, "citation_ids": ["E1"]}]}
                self.assertEqual(query.answer(self.ws, "Feedback", model)["status"], "insufficient_evidence")

    def test_reference_link_payload_is_rendered_as_plain_text(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        result = query.answer(self.ws, "Feedback", lambda _: {"status": "answered", "statements": [
            {"text": "[Click][payload]\n\n[payload]: javascript:alert(1)", "citation_ids": ["E1"]}]})
        self.assertEqual(result["status"], "answered")
        self.assertIn(r"\[Click\]\[payload\]", result["answer_markdown"])
        self.assertNotIn("\n\n", result["answer_markdown"])
        # The only generated link remains the archived source citation.
        import markdown
        rendered = markdown.markdown(result["answer_markdown"])
        self.assertNotIn('href="javascript:', rendered)
        self.assertEqual(rendered.count("<a "), 1)

    def test_page_citation_is_grounded_in_archived_quote_not_page_prose(self):
        quote = "Original archive retains task scope and reports a conditional result only."
        source(self.ws, "Original archive retains\n task scope and reports a conditional result only.")
        (self.ws.wiki / "alignment.md").write_text("# Alignment\n\nThe query keyword is canonical terminology.")
        (self.ws.meta / "alignment.json").write_text(json.dumps({"claims": [
            {"id": "claim-a", "source_ids": [SID], "evidence_passages": [
                {"source_id": SID, "quote": quote, "locator": "old vague locator"}]}]}))
        def model(messages):
            evidence = json.loads(messages[-1]["content"])["evidence"]
            self.assertIn("Original archive", evidence[0]["quote"])
            self.assertNotIn("query keyword", evidence[0]["quote"])
            return {"status": "answered", "statements": [
                {"text": "The original result is conditional.", "citation_ids": [evidence[0]["id"]]}]}
        result = query.answer(self.ws, "canonical terminology", model)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["citations"][0]["page_slug"], "alignment")
        self.assertIn("library_id=candidate-v2", result["citations"][0]["page_url"])

    def test_page_without_verifiable_original_quote_is_insufficient(self):
        source(self.ws, "Actual archived text with scoped original supporting conditions.")
        (self.ws.wiki / "keyword.md").write_text("# Canonical terminology\n\nPage prose has ungrounded claims.")
        (self.ws.meta / "keyword.json").write_text(json.dumps({"claims": [
            {"evidence_passages": [{"source_id": SID, "quote": "This fabricated passage does not appear anywhere in the original archive."}]}]}))
        model = Mock()
        self.assertEqual(query.answer(self.ws, "canonical terminology", model)["status"], "insufficient_evidence")
        model.assert_not_called()

    def test_source_version_changed_during_model_request_rejects_answer(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        def changes_source(messages):
            reply = supported_model(messages)
            source(self.ws, "Feedback has a different original version and distinct reported conditions.")
            return reply
        result = query.answer(self.ws, "Feedback", changes_source)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["citations"], [])
        self.assertTrue(result["warnings"])

    def test_ocr_original_locator_preserves_page_paragraph_and_parse_version(self):
        source(self.ws, "", "missing")
        parsed = {"source_version": source_version(self.ws, SID), "status": "partial", "pages": [
            {"number": 7, "method": "ocr", "paragraphs": [
                {"id": "p7-2", "start": 40, "end": 160,
                 "text": "Feedback preserves experimental task conditions in this OCR original paragraph."}]}]}
        (self.ws.root / "parsed" / (SID + ".json")).write_text(json.dumps(parsed))
        result = query.answer(self.ws, "Feedback", supported_model)
        self.assertEqual(result["status"], "answered")
        citation = result["citations"][0]
        self.assertEqual(citation["location"]["page"], 7)
        self.assertEqual(citation["location"]["paragraph_id"], "p7-2")
        self.assertIn("parsed_version=", citation["url"])

    def test_model_insufficient_reason_does_not_leak_uncited_assertions(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        result = query.answer(self.ws, "Feedback", lambda _: {
            "status": "insufficient_evidence", "reason": "The model claims unsafe uncited medical advice."})
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertNotIn("medical advice", result["answer_markdown"])

    def test_model_failure_never_exposes_credentials_or_exception_body(self):
        source(self.ws, "Feedback preserves the stated experimental conditions in the original evidence.")
        model = Mock(side_effect=RuntimeError("secret-api-key request body private text"))
        result = query.answer(self.ws, "Feedback", model)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertNotIn("secret-api-key", json.dumps(result))

    def test_same_id_in_other_library_cannot_supply_citation(self):
        other = workspace(self.ws.root / "legacy", "legacy")
        source(other, "Feedback preserves the stated experimental conditions in the legacy original evidence.")
        model = Mock()
        result = query.answer(self.ws, "Feedback", model)
        self.assertEqual(result["status"], "insufficient_evidence")
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
