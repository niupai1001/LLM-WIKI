import copy
import json
import unittest
from unittest.mock import Mock, patch

from station import body_bindings as bindings


BODY = ("# 经验与反馈\n\n失败诊断能给下一轮尝试提供可执行的方向，但收益依赖任务与反馈条件。"
        "[S:paper-a]\n\n过程内反馈能修正当前动作，跨尝试经验则影响下一次尝试的决策。[S:paper-b]")
FIRST = "失败诊断能给下一轮尝试提供可执行的方向，但收益依赖任务与反馈条件。"
SECOND = "过程内反馈能修正当前动作，跨尝试经验则影响下一次尝试的决策。"


def draft(duplicate=False):
    return {"body_markdown": BODY, "aliases": ["经验反馈"], "topic_routes": [{"slug": "agents"}],
            "claims": [{"id": "C1", "text": "失败诊断为重试提供方向。", "scope": "原论文部分任务与可靠反馈。",
                        "source_ids": ["paper-a"], "evidence_refs": [{"source_id": "paper-a", "claim_id": "c7"}],
                        "evidence_passages": [{"source_id": "paper-a", "quote": "Archived original A.",
                                                "locator": {"page": 3, "paragraph_id": "p3"}}]},
                       {"id": "C1" if duplicate else "C2", "text": "过程反馈和跨尝试经验作用于不同决策时刻。",
                        "scope": "反馈可供动作修正时。", "source_ids": ["paper-b"],
                        "evidence_refs": [{"source_id": "paper-b", "claim_id": "c7"}]}],
            "claim_bindings": [{"claim_id": "C1", "body_quote": "诊断使下一轮具备改进方向，这取决于任务。"}],
            "point_bindings": []}


def points():
    return {"kp-a": {"id": "kp-a", "source_id": "paper-a", "data": {"id": "local-k1", "claim_ids": ["c7"],
                       "scope": "原论文部分任务与可靠反馈。", "proposition": "失败诊断为重试提供方向。"}},
            "kp-b": {"id": "kp-b", "source_id": "paper-b", "data": {"id": "local-k2", "claim_ids": ["c7"],
                       "scope": "反馈可供动作修正时。", "proposition": "过程反馈和跨尝试经验作用于不同决策时刻。"}}}


def mapping(context):
    by_source = {c["source_ids"][0]: c["id"] for c in context["claims"]}
    return {"claim_bindings": [{"claim_id": by_source["paper-a"], "body_paragraph_ids": ["b0002"]},
                               {"claim_id": by_source["paper-b"], "body_paragraph_ids": ["b0003"]}],
            "point_bindings": [{"point_id": "kp-a", "claim_ids": [by_source["paper-a"]], "body_paragraph_ids": ["b0002"]},
                               {"point_id": "kp-b", "claim_ids": [by_source["paper-b"]], "body_paragraph_ids": ["b0003"]}],
            "unmapped_claims": [], "unmapped_points": []}


class BodyBindingsTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.calls = []
        def invoke(system, context):
            self.calls.append(copy.deepcopy(context))
            return mapping(context)
        self.invoke = Mock(side_effect=invoke)

    def load(self, key):
        return copy.deepcopy(self.cache.get(key))

    def save(self, key, value):
        self.assertNotIn("/", key)
        self.cache[key] = copy.deepcopy(value)

    def ground(self, value=None, source_points=None, old_meta=None):
        return bindings.ground_bindings(value or draft(), source_points or points(), old_meta or {},
                                       self.invoke, self.load, self.save)

    def test_duplicate_new_claims_get_stable_distinct_ids_and_no_fact_is_dropped(self):
        value = draft(True)
        value["claims"].append({**copy.deepcopy(value["claims"][0]), "id": "unique"})
        value["claims"].append(copy.deepcopy(value["claims"][0]))
        before = copy.deepcopy(value)
        first = bindings.prepare_draft(value)
        again = bindings.namespace_claim_ids(value)
        self.assertEqual(first, again)
        claims = first["draft"]["claims"]
        self.assertEqual(len(claims), 4)
        self.assertEqual(len({c["id"] for c in claims}), 4)
        self.assertEqual(claims[2]["id"], "unique")
        self.assertEqual(bindings._facts(claims), bindings._facts(value["claims"]))
        self.assertEqual(first["draft"]["body_markdown"], BODY)
        self.assertEqual(len(first["audit"]["claim_id_changes"]), 3)
        self.assertEqual(before, value)
        for change in first["audit"]["claim_id_changes"]:
            self.assertEqual(change["original_id"], "C1")

    def test_existing_page_cannot_rename_colliding_ids(self):
        with self.assertRaisesRegex(ValueError, "cannot be automatically namespaced"):
            bindings.prepare_draft(draft(True), {"claims": [{"id": "C1", "text": "already published"}]})
        self.assertEqual(self.invoke.call_count, 0)
        self.assertEqual(bindings.prepare_draft(draft(), {"claims": [{"id": "C1"}]})["draft"], draft())

    def test_grounding_changes_only_ids_and_bindings_not_source_scope_or_evidence(self):
        value, original_points = draft(True), points()
        before, before_points = copy.deepcopy(value), copy.deepcopy(original_points)
        result = self.ground(value, original_points)
        self.assertEqual(result["audit"]["status"], "ready")
        self.assertEqual(value, before)
        self.assertEqual(original_points, before_points)
        self.assertEqual(result["draft"]["body_markdown"], BODY)
        self.assertEqual(bindings._facts(result["draft"]["claims"]), bindings._facts(value["claims"]))
        audit = result["audit"]
        for key in ("body_unchanged", "claims_unchanged_except_ids", "unrelated_metadata_unchanged", "requires_independent_review"):
            self.assertTrue(audit[key])
        self.assertFalse(audit["semantic_support_verified"])
        self.assertEqual(audit["author_requests"], 0)
        context = self.calls[0]
        self.assertEqual(context["claims"][0]["scope"], value["claims"][0]["scope"])
        self.assertEqual(context["claims"][0]["evidence_refs"], value["claims"][0]["evidence_refs"])
        self.assertEqual(context["knowledge_points"][0]["claim_ids"], ["c7"])
        self.assertEqual(context["knowledge_points"][0]["source_id"], "paper-a")
        self.assertEqual(context["knowledge_points"][1]["source_id"], "paper-b")

    def test_valid_existing_bindings_need_no_mapping_request(self):
        value = draft()
        old = mapping({"claims": value["claims"]})
        for field in ("claim_bindings", "point_bindings"):
            for item in old[field]:
                paragraph_id = item.pop("body_paragraph_ids")[0]
                item["body_quote"] = FIRST if paragraph_id == "b0002" else SECOND
            value[field] = old[field]
        result = self.ground(value)
        self.assertEqual(result["draft"], value)
        self.assertEqual(result["audit"]["status"], "ready")
        self.assertEqual(self.invoke.call_count, 0)

    def test_authored_quote_gets_only_metadata_correction_with_complete_immutable_context(self):
        def correct(system, context):
            self.calls.append(copy.deepcopy(context))
            result = mapping(context)
            if len(self.calls) == 1:
                result["claim_bindings"][0]["body_quote"] = "失败诊断帮助下一轮重试，但效果取决于任务。"
            return result
        self.invoke.side_effect = correct
        result = self.ground()
        self.assertEqual(result["audit"]["status"], "ready")
        self.assertEqual(result["audit"]["metadata_correction_rounds"], 1)
        self.assertEqual([c["stage"] for c in self.calls], ["body-bindings", "body-bindings-correction"])
        self.assertIn("never authored body_quote", self.calls[1]["validation_error"])
        for key in ("body_markdown", "body_paragraphs", "claims", "knowledge_points"):
            self.assertEqual(self.calls[0][key], self.calls[1][key])
        invalid = next(v for k, v in self.cache.items() if k.endswith("attempt-0"))
        self.assertIn("never authored body_quote", invalid["validation_error"])
        self.assertIn("response", invalid)

    def test_incomplete_duplicate_foreign_and_source_losing_bindings_are_rejected(self):
        mutations = [lambda r: r["point_bindings"].pop(),
                     lambda r: r["claim_bindings"].append(copy.deepcopy(r["claim_bindings"][0])),
                     lambda r: r["claim_bindings"][0].update(claim_id="foreign"),
                     lambda r: r["point_bindings"][0].update(claim_ids=[]),
                     lambda r: r["point_bindings"][0].update(claim_ids=["C2"]),
                     lambda r: r["point_bindings"][0].update(body_quote="[S:paper-a]"),
                     lambda r: r["point_bindings"][0].update(body_quote="经验反馈")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.cache.clear()
                self.invoke.reset_mock()
                def invalid(system, context):
                    result = mapping(context)
                    mutate(result)
                    return result
                self.invoke.side_effect = invalid
                result = self.ground()
                self.assertEqual(result["audit"]["status"], "blocked")
                self.assertEqual(self.invoke.call_count, 3)
                self.assertEqual(result["audit"]["metadata_correction_rounds"], 2)
                self.assertEqual(result["draft"], draft())
                self.ground()
                self.assertEqual(self.invoke.call_count, 3)

    def test_true_unmapped_blocks_immediately_without_authoring_or_format_retry(self):
        def unmapped(system, context):
            return {"unmapped_points": [{"point_id": "kp-b", "reason": "正文没有解释该点所需的任务条件。"}]}
        self.invoke.side_effect = unmapped
        result = self.ground()
        self.assertEqual(result["audit"]["status"], "blocked")
        self.assertEqual(result["audit"]["unmapped"][0]["id"], "kp-b")
        self.assertEqual(self.invoke.call_count, 1)
        self.assertEqual(result["draft"], draft())
        self.ground()
        self.assertEqual(self.invoke.call_count, 1)

    def test_mapper_cannot_change_body_claims_scope_or_quote_even_when_bindings_are_valid(self):
        for field in ("body_markdown", "claims"):
            with self.subTest(field=field):
                self.cache.clear()
                self.invoke.reset_mock()
                def modify(system, context):
                    result = mapping(context)
                    result[field] = "new content" if field == "body_markdown" else [{"id": "C1", "text": "a more general fact"}]
                    return result
                self.invoke.side_effect = modify
                result = self.ground()
                self.assertEqual(result["audit"]["status"], "blocked")
                self.assertIn("forbidden content changes", result["audit"]["reasons"][0])
                self.assertEqual(self.invoke.call_count, 1)
                self.assertEqual(result["draft"], draft())

    def test_cache_tracks_body_fact_evidence_point_scope_and_prompt_but_keeps_current_unrelated_metadata(self):
        first = self.ground()
        self.assertEqual(self.invoke.call_count, 1)
        unrelated = draft()
        unrelated["aliases"].append("新增别名")
        again = self.ground(unrelated)
        self.assertEqual(self.invoke.call_count, 1)
        self.assertTrue(again["audit"]["cache_reused"])
        self.assertEqual(again["draft"]["aliases"], unrelated["aliases"])
        identities = {first["audit"]["binding_identity"]}
        changed = draft()
        changed["body_markdown"] += "\n此句不改变原有定位。"
        identities.add(self.ground(changed)["audit"]["binding_identity"])
        changed = draft()
        changed["claims"][0]["scope"] += "仅限明确条件。"
        identities.add(self.ground(changed)["audit"]["binding_identity"])
        changed = draft()
        changed["claims"][0]["evidence_passages"][0]["quote"] = "Different archived original"
        identities.add(self.ground(changed)["audit"]["binding_identity"])
        changed_points = points()
        changed_points["kp-a"]["data"]["scope"] += "另一个范围。"
        identities.add(self.ground(source_points=changed_points)["audit"]["binding_identity"])
        with patch.object(bindings, "SYSTEM", bindings.SYSTEM + "\nchanged mapping instructions"):
            identities.add(self.ground()["audit"]["binding_identity"])
        self.assertEqual(len(identities), 6)
        self.assertEqual(self.invoke.call_count, 6)

    def test_transport_resume_reuses_invalid_raw_reply_and_remaining_metadata_budget(self):
        def interrupted(system, context):
            self.calls.append(context)
            if len(self.calls) == 1:
                return {"claim_bindings": [], "point_bindings": []}
            raise TimeoutError("mapper transport failure")
        self.invoke.side_effect = interrupted
        with self.assertRaises(TimeoutError):
            self.ground()
        self.assertEqual(len(self.cache), 1)
        self.calls.clear()
        self.invoke.side_effect = lambda system, context: (self.calls.append(context) or mapping(context))
        result = self.ground()
        self.assertEqual(result["audit"]["status"], "ready")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["metadata_correction_round"], 1)
        self.assertEqual(result["audit"]["metadata_correction_rounds"], 1)

    def test_invalid_json_is_cached_and_can_only_use_two_format_corrections(self):
        self.invoke.side_effect = lambda system, context: "not-json"
        result = self.ground()
        self.assertEqual(result["audit"]["status"], "blocked")
        self.assertEqual(self.invoke.call_count, 3)
        self.assertEqual(sum(key.rsplit("-", 2)[-2] == "attempt" for key in self.cache), 3)
        self.assertTrue(all(v.get("response") == "not-json" for k, v in self.cache.items() if "-attempt-" in k))

    def test_callback_cannot_mutate_the_input_and_cancellation_stops_requests(self):
        value = draft()
        before = copy.deepcopy(value)
        def mutate_context(system, context):
            reply = mapping(context)
            context["body_markdown"] = "discard the original"
            context["claims"][0]["text"] = "fabricated universal success"
            return json.dumps(reply, ensure_ascii=False)
        self.invoke.side_effect = mutate_context
        result = self.ground(value)
        self.assertEqual(result["audit"]["status"], "ready")
        self.assertEqual(before, value)
        self.assertEqual(result["draft"]["claims"], before["claims"])
        with patch.object(bindings, "check_cancelled", side_effect=RuntimeError("cancelled")):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.ground()

    def test_body_paragraph_catalogue_preserves_newline_markdown_and_utf8_offsets(self):
        body = "\r\n# 原标题\r\n\r\n  **机制解释**第一行\r\n第二行仍属同一段。\r\n \t\r\n第三段保留条件。[S:paper-a]\r\n\r\n"
        catalogue = bindings.body_paragraphs(body)
        self.assertEqual([p["id"] for p in catalogue], ["b0001", "b0002", "b0003"])
        self.assertIn("第一行\r\n第二行", catalogue[1]["text"])
        original_bytes = body.encode("utf-8")
        for paragraph in catalogue:
            self.assertEqual(body[paragraph["start"]:paragraph["end"]], paragraph["text"])
            self.assertEqual(original_bytes[paragraph["byte_start"]:paragraph["byte_end"]], paragraph["text"].encode("utf-8"))
        span = bindings._paragraph_quote(["b0002", "b0003"], body, catalogue, "test span")
        self.assertIn("\r\n \t\r\n", span)
        self.assertEqual(span.encode("utf-8"), original_bytes[catalogue[1]["byte_start"]:catalogue[2]["byte_end"]])

    def test_multiple_adjacent_paragraphs_become_one_exact_original_span_locally(self):
        def span_mapper(system, context):
            self.calls.append(copy.deepcopy(context))
            reply = mapping(context)
            reply["claim_bindings"][0]["body_paragraph_ids"] = ["b0002", "b0003"]
            reply["point_bindings"][0]["body_paragraph_ids"] = ["b0002", "b0003"]
            return reply
        self.invoke.side_effect = span_mapper
        value = draft()
        before = copy.deepcopy(value)
        result = self.ground(value)
        self.assertEqual(result["audit"]["status"], "ready")
        quote = result["draft"]["claim_bindings"][0]["body_quote"]
        self.assertEqual(quote, BODY[BODY.index(FIRST):])
        self.assertIn("\n\n", quote)
        self.assertEqual(result["draft"]["point_bindings"][0]["body_quote"], quote)
        self.assertEqual(before, value)
        self.assertEqual(before["claims"], result["draft"]["claims"])
        self.assertEqual(BODY, self.calls[0]["body_markdown"])
        self.assertEqual(bindings.body_paragraphs(BODY), self.calls[0]["body_paragraphs"])
        raw = next(v["response"] for k, v in self.cache.items() if k.endswith("attempt-0"))
        self.assertNotIn("body_quote", raw["claim_bindings"][0])

    def test_unknown_unordered_and_repeated_paragraph_ids_block(self):
        cases = [(["b9999"], "unknown"), (["b0003", "b0002"], "ordered"),
                 (["b0002", "b0002"], "unique"), ([], "nonempty")]
        for ids, reason in cases:
            with self.subTest(ids=ids):
                self.cache.clear()
                self.invoke.reset_mock()
                def invalid(system, context):
                    reply = mapping(context)
                    reply["point_bindings"][0]["body_paragraph_ids"] = ids
                    return reply
                self.invoke.side_effect = invalid
                result = self.ground()
                self.assertEqual(result["audit"]["status"], "blocked")
                self.assertEqual(self.invoke.call_count, 3)
                self.assertIn(reason, result["audit"]["reasons"][0])
                self.assertEqual(result["draft"], draft())
                self.ground()
                self.assertEqual(self.invoke.call_count, 3)

    def test_distributed_knowledge_uses_exact_spans_without_intervening_prose(self):
        value = draft()
        value["body_markdown"] = BODY.replace("\n\n过程内反馈", "\n\n这是完全不相干的中间内容，不应成为该知识的证据。\n\n过程内反馈")
        def distributed(system, context):
            reply = mapping(context)
            reply["claim_bindings"][1]["body_paragraph_ids"] = ["b0004"]
            reply["point_bindings"][1]["body_paragraph_ids"] = ["b0004"]
            reply["point_bindings"][0]["body_paragraph_ids"] = ["b0002", "b0004"]
            return reply
        self.invoke.side_effect = distributed
        result = self.ground(value)
        self.assertEqual(result["audit"]["status"], "ready")
        located = result["draft"]["point_bindings"][0]
        self.assertEqual(len(located["body_quotes"]), 2)
        self.assertEqual(located["body_quote"], located["body_quotes"][0])
        self.assertNotIn("不相干", "".join(located["body_quotes"]))
        self.assertEqual(result["draft"]["body_markdown"], value["body_markdown"])
        self.assertEqual(self.invoke.call_count, 1)
        for span, quote in zip(located["body_spans"], located["body_quotes"]):
            self.assertEqual(value["body_markdown"][span["start"]:span["end"]], quote)
        tampered = copy.deepcopy(result["draft"])
        tampered["point_bindings"][0]["body_quotes"][1] = "虚构位置与原文不一致"
        with self.assertRaisesRegex(ValueError, "locations disagree"):
            bindings._validate_bindings({k: tampered[k] for k in ("claim_bindings", "point_bindings")}, tampered,
                                        bindings._point_context(points()))

    def test_distributed_locations_cannot_be_injected_without_paragraph_identity(self):
        value = draft()
        old = mapping({"claims": value["claims"]})
        for field in ("claim_bindings", "point_bindings"):
            for item in old[field]:
                paragraph_id = item.pop("body_paragraph_ids")[0]
                item["body_quote"] = FIRST if paragraph_id == "b0002" else SECOND
        old["point_bindings"][0]["body_quotes"] = ["伪造不在正文中的另一条知识"]
        with self.assertRaisesRegex(ValueError, "require body_paragraph_ids"):
            bindings._validate_bindings(old, value, bindings._point_context(points()))

    def test_public_location_validator_rejects_tampered_secondary_span(self):
        body = BODY + "\n\n仅用于保留适用条件的额外实质段落。"
        catalogue = bindings.body_paragraphs(body)
        reply = {"point_bindings": [{"point_id": "kp-a", "body_paragraph_ids": ["b0002", "b0004"]}]}
        located = bindings._resolve_paragraph_bindings(reply, {"body_markdown": body})["point_bindings"][0]
        self.assertEqual(bindings.validate_body_locations(located, body), located)
        located["body_spans"][1]["end"] -= 1
        with self.assertRaisesRegex(ValueError, "locations disagree"):
            bindings.validate_body_locations(located, body)
        self.assertEqual(bindings.validate_body_locations({"body_quote": FIRST}, BODY), {"body_quote": FIRST})

    def test_relation_references_follow_only_unambiguous_claim_provenance(self):
        value = draft(True)
        value["relations"] = [{"claim_ids": ["C1"], "source_ids": ["paper-b"]}]
        result = bindings.prepare_draft(value)
        self.assertEqual(result["draft"]["relations"][0]["claim_ids"], [result["draft"]["claims"][1]["id"]])
        self.assertEqual(value["relations"][0]["claim_ids"], ["C1"])
        value["relations"][0]["source_ids"] = ["paper-a", "paper-b"]
        with self.assertRaisesRegex(ValueError, "Ambiguous relation"):
            bindings.prepare_draft(value)

    def test_exact_quote_from_model_is_still_forbidden_in_new_location_protocol(self):
        def quote_mapper(system, context):
            reply = mapping(context)
            reply["claim_bindings"][0]["body_quote"] = FIRST
            return reply
        self.invoke.side_effect = quote_mapper
        result = self.ground()
        self.assertEqual(result["audit"]["status"], "blocked")
        self.assertIn("never authored body_quote", result["audit"]["reasons"][0])
        self.assertEqual(self.invoke.call_count, 3)

    def test_protocol_upgrade_preserves_old_terminal_cache_without_new_requests(self):
        original = draft()
        prepared = bindings.prepare_draft(original)["draft"]
        old_identity = bindings._digest({"version": bindings.LEGACY_VERSION, "prompt": bindings._digest(bindings.LEGACY_SYSTEM),
            "body": prepared["body_markdown"], "claims": prepared["claims"],
            "points": bindings._point_context(points()), "old_claims": []})
        old_key = "body-bindings-" + old_identity + "-result"
        terminal = {"binding_identity": old_identity, "bindings": {}, "status": "blocked", "corrections": 2,
                    "attempts": [{"correction": i, "validation_error": "Old verbatim copy failed"} for i in range(3)],
                    "reasons": ["Old verbatim copy failed"], "unmapped": []}
        self.cache[old_key] = copy.deepcopy(terminal)
        self.cache[old_key.replace("-result", "-attempt-0")] = {"response": {"body_quote": "Unusable old text"}}
        old_records = copy.deepcopy(self.cache)
        result = self.ground(original)
        self.assertEqual(result["audit"]["status"], "blocked")
        self.assertTrue(result["audit"]["blocked_cache_preserved"])
        self.assertEqual(result["audit"]["previous_protocol_terminal"]["binding_identity"], old_identity)
        self.assertNotEqual(result["audit"]["binding_identity"], old_identity)
        self.assertFalse(result["audit"]["semantic_support_verified"])
        self.assertEqual(self.invoke.call_count, 0)
        for key, record in old_records.items():
            self.assertEqual(self.cache[key], record)
        again = self.ground(original)
        self.assertTrue(again["audit"]["cache_reused"])
        self.assertTrue(again["audit"]["blocked_cache_preserved"])
        self.assertEqual(self.invoke.call_count, 0)


if __name__ == "__main__":
    unittest.main()
