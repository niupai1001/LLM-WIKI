"""Library-local compilation through real parsing, validation and publication.

Model responses are controlled; no remote provider is called. These regressions
assert persisted page/coverage behavior rather than merely inspecting prompts.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from station import common, compiler, documents, extraction
from station.workspaces import Workspace, use_workspace


SID = "1234567890abcdef"
UNKNOWN = "ffffffffffffffff"
ORIGINAL = ("The authors propose retaining assumptions while combining archived evidence. "
            "The proposal has not been validated as universal consensus.\n\n"
            "A second mechanism preserves unresolved conflicts together with the original conditions. "
            "It records uncertainty instead of replacing prior knowledge.\n")
CLAIM_TEXT = {"concept-one": "The proposed synthesis retains explicit assumptions from archived evidence.",
              "concept-two": "The proposed conflict mechanism preserves unresolved conditions and uncertainty."}
SCOPE = "authors' proposed method in a research note"


class Model:
    def __init__(self, owner, faults=None):
        self.owner, self.faults, self.calls = owner, faults or {}, []
        self.model_id = "compiler-mock-v1"

    def __call__(self, messages):
        system = messages[0]["content"]
        context = json.loads(messages[-1]["content"])
        if system.startswith("You extract"):
            phase = "extract"
            paragraphs = context["paragraphs"]
            output = {"concepts": [{"id": "c1", "name": "Assumption retention", "aliases": ["保留条件"], "mechanism": "Retain assumptions", "scope": SCOPE},
                                   {"id": "c2", "name": "Conflict preservation", "aliases": ["保留分歧"], "mechanism": "Expose conflicts", "scope": SCOPE}],
                      "questions": [{"id": "q1", "text": "How are assumptions retained?", "concept_id": "c1"},
                                    {"id": "q2", "text": "How are conflicts preserved?", "concept_id": "c2"}],
                      "claims": [{"id": "a1", "text": CLAIM_TEXT["concept-one"], "scope": SCOPE, "concept_id": "c1", "question_ids": ["q1"], "paragraph_ids": [paragraphs[0]["id"]]},
                                 {"id": "a2", "text": CLAIM_TEXT["concept-two"], "scope": SCOPE, "concept_id": "c2", "question_ids": ["q2"], "paragraph_ids": [paragraphs[-1]["id"]]}]}
        elif "阶段：align" in system:
            phase = "align"
            targets = []
            for question in context["questions"]:
                slug = "concept-one" if "assumptions" in question["text"] else "concept-two"
                targets.append({"slug": slug, "title": "保留原始条件" if slug.endswith("one") else "保留未决分歧",
                                "category": "research", "page_role": "knowledge", "aliases": [],
                                "assignments": [{"source_id": question["source_id"], "question_ids": [question["id"]]}],
                                "legacy_slugs": []})
            output = {"targets": targets, "deferred": []}
            active=context.get('active_categories',[])
            category=next((c['id'] for c in active if c['id']=='research'),active[0]['id'] if active else 'research')
            for target in targets: target['category']=category
        elif "阶段：write" in system or "阶段：repair" in system:
            phase = "repair" if "阶段：repair" in system else "write"
            target = context["target"]
            slug = target["slug"]
            sid = target["assignments"][0]["source_id"]
            originals = next(s for s in context["originals"] if s["source_id"] == sid)
            paragraph = originals["paragraphs"][0 if slug == "concept-one" else -1]
            body_quote = CLAIM_TEXT[slug] + " Its evidence is limited to the authors' proposed research-note method."
            body = body_quote + f" [S:{sid}]\n\nThe original uncertainty and conditions remain visible for further verification."
            claim_id = "stable-one" if slug == "concept-one" else "stable-two"
            claim = {"id": claim_id, "text": CLAIM_TEXT[slug], "scope": SCOPE, "source_ids": [sid], "status": "单源支持",
                     "evidence_passages": [{"source_id": sid, "locator": "Archived paragraph " + paragraph["id"], "quote": paragraph["text"]}]}
            output = {"body_markdown": body, "claims": [claim], "conflicts": [], "relations": [], "related_slugs": [],
                      "claim_bindings": [{"claim_id": claim_id, "body_quote": body_quote}], "claim_changes": [],
                      "legacy_mappings": [{"old_slug": p["slug"], "old_claim_id": old["id"], "new_claim_ids": [claim_id],
                                           "disposition": "revised", "reason": "Recompiled from the same archived original"}
                                          for p in context["legacy_baseline"] for old in p.get("claims", [])],
                      "topic_routes": [{"slug": "research-methods", "title": "研究证据方法", "overview": "这里按条件保留与分歧处理的具体问题组织阅读。", "question": "如何理解" + target["title"] + "的机制和适用条件？"}]}
            fault = self.faults.get(slug)
            if fault == "body-omission":
                output["claim_bindings"] = []
            elif fault == "unknown-citation":
                output["body_markdown"] += f" An unsupported factual extension [S:{UNKNOWN}]"
            elif fault == "baseline-omission":
                output["legacy_mappings"] = []
            elif fault == "missing-evidence":
                output["claims"][0]["evidence_passages"] = []
        elif "阶段：verify" in system:
            phase = "verify"
            proposal = context["proposal"]
            slug = proposal["slug"]
            verdict = "partial" if self.faults.get(slug) == "partial-review" else "supported"
            output = {"reviews": [{"claim_id": c["id"], "verdict": verdict, "reason": "The archived paragraph retains the required condition" if verdict == "supported" else "The condition only partly supports the claimed generality", "source_ids": c["source_ids"]} for c in proposal["claims"]],
                      "body_issues": [], "old_knowledge_issues": [], "style_suggestions": ["An optional wording improvement"]}
        else:
            raise AssertionError("Unexpected model phase")
        self.calls.append((phase, context))
        return output


class CompilerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ws = Workspace("candidate-v2", Path(self.temporary.name))
        self.ws.config.mkdir()
        project_config = Path(__file__).resolve().parent.parent / "config"
        for name in ("categories.json", "wiki-writing.md"):
            shutil.copyfile(project_config / name, self.ws.config / name)
        from fixture_config import CATEGORIES
        shutil.copyfile(CATEGORIES,self.ws.config/'categories.json')
        (self.ws.config / "embedding.json").write_text(json.dumps({"base_url": "", "model": "", "api_key": ""}))
        with use_workspace(self.ws):
            with common.connect() as db:
                record = {"id": SID, "title": "Explicit research note", "url": "https://example.org/research-note", "kind": "note",
                          "category": "research", "content_status": "full-text", "text": ORIGINAL, "stage": "new"}
                self.source = self.ws.raw / (SID + ".json")
                self.source.write_text(json.dumps(record), encoding="utf-8")
                db.execute("INSERT INTO sources(id,title,url,feed,kind,summary,raw_path,captured_at,category,content_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (SID, record["title"], record["url"], "manual", "note", "", str(self.source), common.utcnow(), "research", "full-text"))
        self.payload = {"source_ids": [SID], "embeddings": False}
        self.fixed = extraction.extract_source(self.ws, SID, model=Model(self))

    def run_job(self, model, job="run-one", actual_extraction=False):
        if actual_extraction:
            return compiler.compile_job(self.ws, self.payload, job, model=model)
        with patch.object(extraction, "extract_source", return_value=copy.deepcopy(self.fixed)):
            return compiler.compile_job(self.ws, self.payload, job, model=model)

    def seed_page(self, slug="concept-one", baseline=False):
        base = self.ws.root / "baseline" if baseline else self.ws.root
        wiki, meta = base / "wiki", base / "wiki_meta"
        wiki.mkdir(parents=True, exist_ok=True)
        meta.mkdir(parents=True, exist_ok=True)
        (wiki / (slug + ".md")).write_text("# Original retained knowledge\n\nOriginal conditions must survive migration.\n")
        item = {"slug": slug, "title": "Original retained knowledge", "category": "research", "page_kind": "concept", "page_role": "knowledge",
                "source_ids": [SID], "claims": [{"id": "baseline-one" if baseline else "stable-one", "text": "An earlier condition must remain explicit.",
                                                "scope": "earlier conditional mechanism", "source_ids": [SID], "status": "单源支持",
                                                "evidence_passages": [{"source_id": SID, "locator": "Original archived text", "quote": ORIGINAL.split("\n\n")[0]}]}],
                "relations": [], "conflicts": []}
        (meta / (slug + ".json")).write_text(json.dumps(item))
        return item

    def test_end_to_end_extraction_multiple_targets_review_and_atomic_navigation(self):
        # Different model identity ensures this case also executes real extraction.
        model = Model(self)
        model.model_id = "fresh-end-to-end"
        result = self.run_job(model, actual_extraction=True)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["published"], 2)
        self.assertEqual({r["status"] for r in result["results"]}, {"published"})
        self.assertEqual([phase for phase, _ in model.calls].count("extract"), 1)
        self.assertEqual([phase for phase, _ in model.calls].count("verify"), 2)
        for phase,context in model.calls:
            if phase=='write':
                expected='assumptions' if context['target']['slug']=='concept-one' else 'conflicts'
                questions=context['extractions'][0]['questions']
                self.assertEqual(len(questions),1)
                self.assertIn(expected,questions[0]['text'])
                self.assertEqual(len(context['extractions'][0]['claims']),1)
        self.assertEqual({r["status"] for r in compiler.coverage(self.ws)}, {"published"})
        topic = (self.ws.wiki / "research-methods.md").read_text()
        self.assertIn("[[concept-one|", topic)
        self.assertIn("[[concept-two|", topic)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute("SELECT stage FROM sources WHERE id=?", (SID,)).fetchone()[0], "approved")

    def test_v2_expression_rewrite_keeps_knowledge_and_marks_body_bindings_stale(self):
        self.assertEqual(self.run_job(Model(self))['published'],2)
        from station import wiki
        previous=json.loads((self.ws.meta/'concept-one.json').read_text())
        body=wiki.page_body((self.ws.wiki/'concept-one.md').read_text()).replace('proposed','described')
        with use_workspace(self.ws),patch.object(wiki,'call_llm',return_value={'body_markdown':body,'needs_fact_review':False}):
            proposal=wiki.prepare_rewrite('concept-one')
            wiki.approve(proposal)
        current=json.loads((self.ws.meta/'concept-one.json').read_text())
        self.assertEqual(current['claims'],previous['claims'])
        self.assertEqual(current['semantic_reviews'],previous['semantic_reviews'])
        self.assertTrue(current['claim_bindings_stale'])
        for slug in ("concept-one", "concept-two"):
            meta = json.loads((self.ws.meta / (slug + ".json")).read_text())
            self.assertTrue(meta["semantic_reviews"][-1]["passed"])
            self.assertEqual(meta["source_ids"], [SID])

    def test_second_target_failure_preserves_first_publication_and_coverage(self):
        result = self.run_job(Model(self, {"concept-two": "missing-evidence"}))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["published"], 1)
        self.assertTrue((self.ws.wiki / "concept-one.md").exists())
        self.assertFalse((self.ws.wiki / "concept-two.md").exists())
        self.assertEqual({r["target_slug"]: r["status"] for r in compiler.coverage(self.ws)}, {"concept-one": "published", "concept-two": "failed"})
        self.assertEqual(len(list(self.ws.pending.glob("*.json"))), 1)

    def test_same_id_meaning_change_without_before_after_record_is_blocked(self):
        self.seed_page()
        before = (self.ws.wiki / "concept-one.md").read_bytes()
        result = self.run_job(Model(self))
        failed = next(r for r in result["results"] if r["slug"] == "concept-one")
        self.assertEqual(failed["status"], "blocked")
        self.assertIn("Same-ID semantic change", str(failed["issues"]))
        self.assertEqual((self.ws.wiki / "concept-one.md").read_bytes(), before)

    def test_incremental_update_preserves_aliases_and_question_coverage(self):
        self.run_job(Model(self))
        path=self.ws.meta/'concept-one.json'
        metadata=json.loads(path.read_text())
        metadata['aliases']=['Existing canonical alias']
        metadata['knowledge_questions'].append({'source_id':SID,'question_ids':['older-question']})
        path.write_text(json.dumps(metadata))
        self.payload['revisit']=True
        result=self.run_job(Model(self),job='update-two')
        self.assertEqual(result['status'],'completed',result)
        current=json.loads(path.read_text())
        self.assertIn('Existing canonical alias',current['aliases'])
        self.assertIn('older-question',current['knowledge_questions'][0]['question_ids'])

    def test_metadata_edit_during_review_blocks_publication(self):
        self.run_job(Model(self))
        self.payload['revisit']=True
        base=Model(self)
        def concurrent_model(messages):
            output=base(messages)
            if '阶段：verify' in messages[0]['content']:
                context=json.loads(messages[-1]['content'])
                path=self.ws.meta/(context['proposal']['slug']+'.json')
                metadata=json.loads(path.read_text());metadata['aliases']=['Concurrent edit']
                path.write_text(json.dumps(metadata))
            return output
        result=self.run_job(concurrent_model,job='concurrent-edit')
        self.assertEqual(result['published'],0,result)
        self.assertTrue(all('changed during compilation' in str(r['issues']) for r in result['results']))

    def test_recorded_claim_missing_from_body_is_blocked(self):
        result = self.run_job(Model(self, {"concept-one": "body-omission"}))
        failed = next(r for r in result["results"] if r["slug"] == "concept-one")
        self.assertEqual(failed["status"], "blocked")
        self.assertIn("Body omits", str(failed["issues"]))
        self.assertFalse((self.ws.wiki / "concept-one.md").exists())

    def test_unknown_body_source_is_blocked_even_when_claim_evidence_is_valid(self):
        result = self.run_job(Model(self, {"concept-one": "unknown-citation"}))
        failed = next(r for r in result["results"] if r["slug"] == "concept-one")
        self.assertEqual(failed["status"], "blocked")
        self.assertIn("Unknown body citation", str(failed["issues"]))

    def test_legacy_baseline_claim_must_be_explicitly_mapped(self):
        self.seed_page(baseline=True)
        result = self.run_job(Model(self, {"concept-one": "baseline-omission"}))
        failed = next(r for r in result["results"] if r["slug"] == "concept-one")
        self.assertEqual(failed["status"], "blocked")
        self.assertIn("baseline claims", str(failed["issues"]))

    def test_retry_completed_run_does_not_republish(self):
        first = self.run_job(Model(self))
        self.assertEqual(first["published"], 2)
        history = list((self.ws.root / "proposal_history").glob("*.json"))
        model = Model(self)
        second = self.run_job(model)
        self.assertEqual(second["published"], 0)
        self.assertEqual(len(list((self.ws.root / "proposal_history").glob("*.json"))), len(history))
        self.assertEqual(model.calls, [])

    def test_retry_partial_run_does_not_repeat_published_target(self):
        first = self.run_job(Model(self, {"concept-two": "missing-evidence"}))
        self.assertEqual(first["published"], 1)
        before = (self.ws.meta / "concept-one.json").read_bytes()
        # User resolves the blocked proposal; retry should compile only the failure.
        for pending in self.ws.pending.glob("*.json"):
            pending.unlink()
        model = Model(self)
        second = self.run_job(model)
        self.assertEqual(second["published"], 1)
        self.assertEqual((self.ws.meta / "concept-one.json").read_bytes(), before)
        writes = [context["target"]["slug"] for phase, context in model.calls if phase == "write"]
        self.assertEqual(writes, ["concept-two"])

    def test_one_blocked_target_can_be_retried_without_calling_other_target(self):
        first=self.run_job(Model(self,{'concept-one':'missing-evidence','concept-two':'missing-evidence'}))
        self.assertEqual(first['published'],0)
        self.payload['retry_target']='concept-two'
        model=Model(self)
        second=self.run_job(model)
        self.assertEqual(second['published'],1,second)
        self.assertEqual(second['status'],'blocked')
        self.assertEqual([context['target']['slug'] for phase,context in model.calls if phase=='write'],['concept-two'])
        self.assertFalse((self.ws.wiki/'concept-one.md').exists())
        self.assertEqual({r['target_slug']:r['status'] for r in compiler.coverage(self.ws)}, {'concept-one':'failed','concept-two':'published'})

    def test_queued_input_version_replacement_fails_before_extraction(self):
        self.payload["input_versions"] = {SID: documents.source_version(self.ws, SID)}
        record = json.loads(self.source.read_text())
        record["text"] += " Concurrent original replacement."
        self.source.write_text(json.dumps(record))
        model = Model(self)
        with self.assertRaisesRegex(ValueError, "Queued input source changed"):
            self.run_job(model)
        self.assertEqual(model.calls, [])

    def test_bulk_compile_without_embedding_stops_before_model_calls(self):
        self.payload={'source_ids':[SID,UNKNOWN]}
        model=Model(self)
        with self.assertRaisesRegex(ValueError,'EMBEDDING_BASE_URL'):
            self.run_job(model,actual_extraction=True)
        self.assertEqual(model.calls,[])
        self.assertFalse((self.ws.root/'runs'/'run-one').exists())

    def test_short_original_retains_unquoted_conditions_at_end(self):
        paragraphs=[{'id':str(i),'text':f'Original paragraph {i} with scope conditions.'} for i in range(8)]
        result=compiler._focused_originals([{'source_id':SID,'paragraphs':paragraphs}],[])
        self.assertEqual(result[0]['paragraphs'],paragraphs)
        self.assertFalse(result[0]['is_excerpt'])

    def test_agent_only_intake_still_generates_knowledge_topics(self):
        config=json.loads((self.ws.config/'categories.json').read_text())
        for entry in config: entry['enabled']=entry['id']=='agents'
        (self.ws.config/'categories.json').write_text(json.dumps(config))
        model=Model(self)
        result=self.run_job(model)
        self.assertEqual(result['status'],'completed')
        alignment=next(context for phase,context in model.calls if phase=='align')
        self.assertEqual([c['id'] for c in alignment['active_categories']],['agents'])
        pages=[json.loads(p.read_text()) for p in self.ws.meta.glob('*.json')]
        self.assertTrue(any(p['page_kind']=='topic' for p in pages))
        self.assertEqual({p['category'] for p in pages},{'agents'})

    def test_job_inputs_skip_summaries_and_freeze_only_full_original_versions(self):
        record={'id':UNKNOWN,'title':'Abstract only','category':'research','content_status':'summary-only','text':'An abstract is not archived full evidence.'}
        (self.ws.raw/(UNKNOWN+'.json')).write_text(json.dumps(record))
        selected,skipped,versions=compiler.compilation_inputs(self.ws)
        self.assertEqual(selected,[SID])
        self.assertEqual(set(versions),{SID})
        self.assertEqual(skipped[0]['source_id'],UNKNOWN)
        self.assertTrue(skipped[0]['reason'])
        with self.assertRaisesRegex(ValueError,'尚未取得全文'):
            compiler.compilation_inputs(self.ws,[UNKNOWN])

    def test_scheduler_queue_omits_summary_only_sources(self):
        from station.cli import queue_auto_compile
        with use_workspace(self.ws),common.connect() as db:
            db.execute('INSERT INTO sources(id,title,url,feed,kind,raw_path,captured_at,category,content_status) VALUES(?,?,?,?,?,?,?,?,?)',
                       (UNKNOWN,'Abstract','https://example.org/abstract','test','paper',str(self.ws.raw/(UNKNOWN+'.json')),common.utcnow(),'agents','summary-only'))
        with use_workspace(self.ws): job=queue_auto_compile(self.ws)
        self.assertEqual(job['payload']['source_ids'],[SID])

    def test_repeated_blocked_target_keeps_one_pending_proposal_and_history(self):
        model=Model(self,{'concept-one':'body-omission'})
        first=self.run_job(model)
        old=next(r['proposal_id'] for r in first['results'] if r['slug']=='concept-one')
        second=self.run_job(Model(self,{'concept-one':'body-omission'}))
        new=next(r['proposal_id'] for r in second['results'] if r['slug']=='concept-one')
        pending=[p for p in self.ws.pending.glob('*.json') if json.loads(p.read_text()).get('slug')=='concept-one']
        self.assertEqual([p.stem for p in pending],[new])
        history=json.loads((self.ws.root/'proposal_history'/f'{old}.json').read_text())
        self.assertEqual(history['replacement'],new)
        self.assertEqual(history['outcome'],'superseded_by_retry')

    def test_alignment_repairs_empty_assignments_before_publication(self):
        base=Model(self)
        calls=[]
        def model(messages):
            reply=base(messages)
            if '阶段：align' in messages[0]['content']:
                calls.append(messages)
                if len(calls)==1: reply['targets'][0]['assignments'][0]['question_ids']=[]
            return reply
        result=self.run_job(model)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(calls),2)
        self.assertIn('Target requires question IDs',calls[1][-1]['content'])

    def test_alignment_repairs_are_bounded_and_do_not_publish_invalid_plan(self):
        base=Model(self)
        calls=[]
        def model(messages):
            reply=base(messages)
            if '阶段：align' in messages[0]['content']:
                calls.append(messages)
                reply['targets'][0]['assignments'][0]['question_ids']=[]
            return reply
        with self.assertRaisesRegex(ValueError,'Target requires question IDs'):
            self.run_job(model)
        self.assertEqual(len(calls),3)
        self.assertFalse(list(self.ws.meta.glob('*.json')))

    def test_unquoted_legacy_claim_retrieves_late_original_without_inventing_quote(self):
        paragraphs=[{'id':str(i),'text':'Unrelated introductory material. '*180} for i in range(6)]
        paragraphs.append({'id':'late','text':'Hierarchical planning retains task dependency constraints during execution.'})
        claim={'id':'hierarchical-planning','text':'层级规划保留依赖条件。','scope':'task dependency constraints','source_ids':[SID],'evidence_passages':[]}
        result=compiler._focused_originals([{'source_id':SID,'paragraphs':paragraphs}],[claim])
        self.assertIn(paragraphs[-1],result[0]['paragraphs'])
        self.assertTrue(result[0]['is_excerpt'])
        self.assertEqual(claim['evidence_passages'],[])

    def test_partial_independent_review_exhausts_two_repairs_and_blocks(self):
        model = Model(self, {"concept-one": "partial-review"})
        result = self.run_job(model)
        failed = next(r for r in result["results"] if r["slug"] == "concept-one")
        self.assertEqual(failed["status"], "blocked")
        self.assertFalse((self.ws.wiki / "concept-one.md").exists())
        self.assertEqual(len([context for phase, context in model.calls if phase == "repair" and context["target"]["slug"] == "concept-one"]), 2)
        pending = json.loads((self.ws.pending / (failed["proposal_id"] + ".json")).read_text())
        self.assertEqual(len(pending["semantic_reviews"]), 3)
        self.assertEqual({r["verdict"] for review in pending["semantic_reviews"] for r in review["reviews"]}, {"partial"})


if __name__ == "__main__":
    unittest.main()
