import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from station import documents, extraction
from station.workspaces import Workspace


SID = "1234567890abcdef"


def response(messages):
    context = json.loads(messages[-1]["content"])
    return {"concepts": [{"id": "c1", "name": "Evidence synthesis", "aliases": ["证据综合"],
                          "mechanism": "Combine independent evidence with retained conditions", "scope": "research notes"}],
            "questions": [{"id": "q1", "text": "How are conditions retained?", "concept_id": "c1"}],
            "claims": [{"id": "a1", "text": "Conditions remain explicit during synthesis.",
                        "scope": "authors' proposed method", "concept_id": "c1", "question_ids": ["q1"],
                        "paragraph_ids": [context["paragraphs"][0]["id"]],
                        "quote": "Fabricated model quote must not reach saved evidence"}]}


class CachedExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ws = Workspace("candidate-v2", Path(self.temporary.name))
        self.ws.raw.mkdir()
        self.path = self.ws.raw / (SID + ".json")
        self.archive("# Original research\n\nExplicit conditions must be retained.\n\nLimitations remain unresolved.\n")

    def archive(self, text):
        self.path.write_text(json.dumps({"id": SID, "title": "研究资料", "kind": "note", "category": "research",
                                         "text": text, "content_status": "full-text", "stage": "approved"}))

    def test_evidence_is_copied_from_archive_not_model_and_stage_is_preserved(self):
        before = self.path.read_bytes()
        result = extraction.extract_source(self.ws, SID, model=response, model_id="mock-v1")
        evidence = result["claims"][0]["evidence"][0]
        parsed = documents.parse_source(self.ws, SID)
        self.assertEqual(evidence["excerpt"], parsed["paragraphs"][0]["text"])
        self.assertNotIn("Fabricated", json.dumps(result))
        self.assertEqual(evidence["source_id"], SID)
        self.assertEqual(result["category"], "research")
        self.assertEqual(result["kind"], "note")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result["coverage"]["questions"][0]["status"], "pending")
        self.assertEqual(result["concepts"][0]["questions"], result["coverage"]["question_ids"])

    def test_successful_cache_reuses_calls_but_versions_model_and_prompt_invalidate(self):
        model = Mock(side_effect=response)
        first = extraction.extract_source(self.ws, SID, model=model, model_id="mock-v1")
        cached = extraction.extract_source(self.ws, SID, model=model, model_id="mock-v1")
        self.assertEqual(first, cached)
        self.assertEqual(model.call_count, 1)
        new_model = extraction.extract_source(self.ws, SID, model=model, model_id="mock-v2")
        self.assertNotEqual(first["cache_key"], new_model["cache_key"])
        with patch.object(extraction, "PROMPT_VERSION", "different-prompt"):
            new_prompt = extraction.extract_source(self.ws, SID, model=model, model_id="mock-v2")
        self.assertNotEqual(new_model["cache_key"], new_prompt["cache_key"])
        self.archive("Changed archived original evidence.")
        new_source = extraction.extract_source(self.ws, SID, model=model, model_id="mock-v1")
        self.assertNotEqual(first["source_version"], new_source["source_version"])
        self.assertEqual(model.call_count, 4)

    def test_multiple_questions_claims_and_concepts_are_addressable(self):
        def several(messages):
            value = response(messages)
            value["concepts"].append({"id": "c2", "name": "Conflict preservation", "aliases": [], "mechanism": "Expose disagreements", "scope": "research notes"})
            value["questions"].append({"id": "q2", "text": "How are disagreements exposed?", "concept_id": "c2"})
            value["claims"].append({**value["claims"][0], "id": "a2", "text": "Unresolved disagreements remain visible.", "concept_id": "c2", "question_ids": ["q2"]})
            return value
        result = extraction.extract_source(self.ws, SID, model=several, model_id="mock")
        self.assertEqual(len(result["concepts"]), 2)
        self.assertEqual(len(result["questions"]), 2)
        self.assertEqual(len(result["claims"]), 2)
        self.assertTrue(result["coverage"]["complete"])

    def test_long_original_batches_merge_same_concept_but_preserve_all_paragraph_coverage(self):
        self.archive("\n\n".join("Evidence paragraph " + str(i) + " " + "x" * 700 for i in range(6)))
        model = Mock(side_effect=response)
        with patch.object(extraction, "BATCH_CHARACTERS", 1500):
            result = extraction.extract_source(self.ws, SID, model=model, model_id="mock")
        self.assertGreater(model.call_count, 1)
        self.assertEqual(len(result["concepts"]), 1)
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(result["coverage"]["total_paragraphs"], 6)
        self.assertEqual(len(result["coverage"]["processed_paragraph_ids"]), 6)
        self.assertTrue(result["coverage"]["complete"])

    def test_batch_limit_explicitly_marks_unprocessed_paragraphs(self):
        self.archive("\n\n".join("Evidence " + str(i) + " " + "x" * 700 for i in range(6)))
        with patch.object(extraction, "BATCH_CHARACTERS", 1500), patch.object(extraction, "MAX_BATCHES", 1):
            result = extraction.extract_source(self.ws, SID, model=response, model_id="mock")
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(len(result["coverage"]["unprocessed"]), 5)
        self.assertTrue(all("safety limit" in p["reason"] for p in result["coverage"]["unprocessed"]))

    def test_invalid_evidence_duplicate_ids_and_cross_concept_question_fail_without_success_cache(self):
        invalid = [
            lambda value: value["claims"][0].update(paragraph_ids=["p9999-0001"]),
            lambda value: value["claims"][0].update(question_ids=["not-a-question"]),
            lambda value: value["concepts"].append(copy.deepcopy(value["concepts"][0])),
            lambda value: value["claims"][0].update(scope=12),
            lambda value: value.update(claims="not an array"),
        ]
        for index, mutate in enumerate(invalid):
            with self.subTest(case=index):
                def bad(messages):
                    value = response(messages)
                    mutate(value)
                    return value
                with self.assertRaises(ValueError):
                    extraction.extract_source(self.ws, SID, model=bad, model_id="bad-" + str(index))
        directory = self.ws.root / "extractions" / SID
        self.assertFalse(directory.exists() and list(directory.glob("*.json")))

    def test_json_parse_failures_are_not_successfully_cached(self):
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            extraction.extract_source(self.ws, SID, model=lambda messages: "{unfinished", model_id="bad-json")
        self.assertFalse((self.ws.root / "extractions" / SID).exists())

    def test_omission_reasons_and_missing_pages_are_visible(self):
        def omit(messages):
            value = response(messages)
            context = json.loads(messages[-1]["content"])
            omitted = context["paragraphs"][-1]["id"]
            value["omissions"] = [{"paragraph_id": omitted, "reason": "This paragraph describes an unresolved limitation"}]
            return value
        result = extraction.extract_source(self.ws, SID, model=omit, model_id="mock")
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(len(result["coverage"]["unprocessed"]), 1)
        self.assertIn("limitation", result["coverage"]["unprocessed"][0]["reason"])

    def test_later_batch_failure_resumes_already_validated_batches(self):
        self.archive("\n\n".join("Evidence " + str(i) + " " + "x" * 700 for i in range(3)))
        calls = []
        def interrupted(messages):
            context = json.loads(messages[-1]["content"])
            calls.append(context["batch_number"])
            if context["batch_number"] == 2:
                return {"concepts": []}
            return response(messages)
        with patch.object(extraction, "BATCH_CHARACTERS", 1500):
            with self.assertRaises(ValueError):
                extraction.extract_source(self.ws, SID, model=interrupted, model_id="same-model")
            model = Mock(side_effect=response)
            result = extraction.extract_source(self.ws, SID, model=model, model_id="same-model")
        self.assertEqual(calls, [1, 2])
        self.assertEqual(model.call_count, 2)
        self.assertTrue(result["coverage"]["complete"])

    def test_original_changes_during_model_call_are_rejected(self):
        def mutate_source(messages):
            self.archive("Concurrent replacement of archived evidence.")
            return response(messages)
        with self.assertRaisesRegex(ValueError, "changed during extraction"):
            extraction.extract_source(self.ws, SID, model=mutate_source, model_id="mock")
        directory = self.ws.root / "extractions" / SID
        self.assertFalse(directory.exists() and list(directory.glob("*.json")))

    def test_missing_original_never_calls_model(self):
        self.archive("")
        model = Mock()
        with self.assertRaisesRegex(ValueError, "no readable original"):
            extraction.extract_source(self.ws, SID, model=model, model_id="mock")
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
