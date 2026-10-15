import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from station import evaluation, search, query
from station.documents import source_version
from station.workspaces import Workspace
from station.publication import _LOCAL


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ws = Workspace('candidate', self.root / 'candidate')
        self.old = Workspace('legacy', self.root / 'legacy')
        self.sid = 'a' * 16
        self.text = 'Lease renewal makes background execution ownership explicit and prevents stale workers from committing updates.'
        for ws in (self.ws, self.old):
            for directory in (ws.raw, ws.wiki, ws.meta, ws.originals, ws.root / 'config'):
                directory.mkdir(parents=True)
            self.write(ws.config / 'embedding.json', {'base_url':'', 'model':'', 'api_key':''})
            self.write(ws.raw / (self.sid + '.json'), {'id': self.sid, 'title': 'Lease renewal', 'text': self.text,
                'content_status': 'full-text', 'content_format': 'text'})
            (ws.wiki / 'leases.md').write_text('# Lease renewal\n\n' + self.text + '\n\n[Evidence](/source/' + self.sid + ')')
            self.write(ws.meta / 'leases.json', {'slug': 'leases', 'title': 'Lease renewal', 'page_kind': 'concept',
                'source_ids': [self.sid], 'claims': [{'id': 'ownership', 'text': 'Renewable leases retain ownership.',
                'scope': 'Background workers', 'source_ids': [self.sid],
                'evidence_passages': [{'source_id': self.sid, 'quote': self.text, 'locator': 'characters 0–109'}]}]})
        extraction = {'library_id': self.ws.library_id, 'source_id': self.sid, 'source_version': source_version(self.ws, self.sid),
                      'cache_key': 'current', 'created_at': '2026-01-01', 'coverage': {'complete': True},
                      'questions': [{'id': 'q1', 'text': 'How do leases renew?'}]}
        self.write(self.ws.root / 'extractions' / self.sid / 'current.json', extraction)
        with sqlite3.connect(self.ws.db) as db:
            db.execute('CREATE TABLE knowledge_coverage(source_id TEXT,cache_key TEXT,question_id TEXT,target_slug TEXT,status TEXT,reason TEXT)')
            db.execute('INSERT INTO knowledge_coverage VALUES(?,?,?,?,?,?)', (self.sid,'current','q1','leases','published',''))
        self.fixture = {'suite_id': 'synthetic-test-only', 'knowledge_cases': [{'id': 'extract-test', 'question': 'Lease renewal ownership', 'expected_target_slug': 'leases',
            'expected_source_ids': [self.sid], 'required_human_dimensions': ['source_mapping','target_mapping','claim_support','scope_preserved']}],
            'query_cases': [{'id': 'query-test', 'question': 'Lease renewal ownership', 'expected_target_slug': 'leases',
            'expected_source_ids': [self.sid], 'expected_page_slugs': ['leases'], 'expected_status': 'answered'}],
            'thresholds': {'human_case_pass_rate': .95, 'core_claim_support_precision': .95, 'recall_at_8': .9, 'citation_location_rate': 1.0}}
        self.fixture_path = self.root / 'fixture.json'
        self.write(self.fixture_path, self.fixture)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))

    def evaluate(self, write=False):
        return evaluation.evaluate_library(self.ws, write=write, legacy_ws=self.old, fixture_path=self.fixture_path)

    def bindings(self, report):
        return {k: report[k] for k in ('library_id','manifest_digest','legacy_manifest_digest','evaluation_input_digest','fixture_digest')}

    def labels(self, report, reviewer_type='human'):
        claims = json.loads((self.ws.meta / 'leases.json').read_text())['claims']
        records = [{
            'case_id': c['id'], 'reviewer_type': reviewer_type, 'by': 'Synthetic fixture reviewer',
            'rationale': 'Synthetic test only: compared the local test quote and scoped claim.',
            'target_slug': 'leases', 'checked_source_ids': [self.sid], 'claim_ids': [claim['id'] for claim in claims],
            'dimensions': {k: True for k in c['required_human_dimensions']}} for c in self.fixture['knowledge_cases']]
        claim_records = [{'target_slug':'leases','claim_id':claim['id'],'reviewer_type':reviewer_type,
            'by':'Synthetic fixture reviewer','rationale':'Test-only scoped assertion checked against the test original.',
            'checked_source_ids':[self.sid],'support':'supported'} for claim in claims]
        self.write(self.ws.root / 'human_evaluation.json', {**self.bindings(report), 'records':records,'claim_records':claim_records})

    @contextmanager
    def hybrid(self):
        self.write(self.ws.config / 'embedding.json', {'base_url':'https://embedding.test/v1','model':'test','api_key':''})
        def embed(config, texts):
            self.assertNotIn(str(self.ws.root), getattr(_LOCAL, 'held', {}))
            self.assertNotIn(str(self.old.root), getattr(_LOCAL, 'held', {}))
            return [[1.0,0.0] for _ in texts]
        with patch.object(search, 'embed_texts', side_effect=embed) as client:
            search.rebuild_index(self.ws, embeddings=True)
            yield client

    def answers(self, report):
        segment = search.source_segments(self.ws, self.sid)[0]
        location = {**segment['location'], 'start': 0, 'end': len(segment['text'])}
        url = query._evidence_url(self.ws, self.sid, location)
        citation = {'id':'E1','source_id':self.sid,'quote':self.text,'locator':'characters 0–109',
                    'location':location,'source_version':segment['source_version'],'url':url}
        answer = {'library_id':self.ws.library_id,'question':'Lease renewal ownership','status':'answered',
                  'answer_markdown':'Synthetic answer. [E1]('+url+')','citations':[citation]}
        self.write(self.ws.root / 'query_evaluation.json', {**self.bindings(report),'records':[
            {'case_id':'query-test','reviewer_type':'agent','answer':answer}]})

    def test_no_human_labels_never_fabricates_95_percent(self):
        report = self.evaluate(write=True)
        self.assertFalse(report['passed'])
        self.assertIsNone(report['human_evaluation']['pass_rate'])
        self.assertEqual(report['human_evaluation']['reviewed'], 0)
        self.assertIn('semantic_index_not_ready', report['blocked_reasons'])
        self.assertIn('human_evaluation_missing_stale_or_below_threshold', report['blocked_reasons'])
        self.assertIsNone(report['retrieval']['recall_at_8'])
        self.assertEqual(report['retrieval']['diagnostic_recall_at_8'], 1.0)
        self.assertEqual(report['retrieval']['mode'], 'lexical-only-diagnostic')
        self.assertEqual(report['integrity']['evidence_coverage'], 1.0)
        self.assertEqual(report['comparison']['unresolved'], [])
        self.assertTrue((self.ws.root / 'acceptance.json').is_file())

    def test_agent_labels_cannot_count_as_human(self):
        report = self.evaluate()
        self.labels(report, reviewer_type='agent')
        again = self.evaluate()
        self.assertEqual(again['human_evaluation']['reviewed'], 0)
        self.assertIsNone(again['human_evaluation']['pass_rate'])

    def test_bound_human_labels_and_agent_citation_locations_are_separate(self):
        report = self.evaluate()
        self.labels(report)
        self.answers(report)
        again = self.evaluate()
        self.assertEqual(again['human_evaluation']['pass_rate'], 1.0)
        self.assertEqual(again['query_evaluation']['citation_location_rate'], 1.0)
        self.assertTrue(again['query_evaluation']['ready'])
        self.assertFalse(again['passed'])
        self.assertEqual(again['blocked_reasons'], ['semantic_index_not_ready','hybrid_retrieval_not_measured'])
        # A mock embedding client drives the actual hybrid index/search path.
        with self.hybrid():
            self.assertTrue(self.evaluate()['passed'])

    def test_acceptance_current_rejects_changed_review_and_extraction(self):
        report = self.evaluate()
        self.labels(report)
        self.answers(report)
        with self.hybrid():
            accepted = self.evaluate()
            self.assertTrue(evaluation.acceptance_current(self.ws, accepted, legacy_ws=self.old, fixture_path=self.fixture_path))
            path = self.ws.root / 'human_evaluation.json'
            previous = path.read_text()
            path.write_text('{}')
            self.assertFalse(evaluation.acceptance_current(self.ws, accepted, legacy_ws=self.old, fixture_path=self.fixture_path))
            path.write_text(previous)
            extraction = self.ws.root / 'extractions' / self.sid / 'current.json'
            data = json.loads(extraction.read_text())
            data['coverage']['complete'] = False
            self.write(extraction, data)
            self.assertFalse(evaluation.acceptance_current(self.ws, accepted, legacy_ws=self.old, fixture_path=self.fixture_path))
        again = self.evaluate()
        self.assertIsNone(again['human_evaluation']['pass_rate'])
        self.assertIn('extraction_or_question_coverage_incomplete', again['blocked_reasons'])

    def test_content_change_invalidates_labels_and_source_sync(self):
        report = self.evaluate()
        self.labels(report)
        self.answers(report)
        record = json.loads((self.ws.raw / (self.sid + '.json')).read_text())
        record['text'] += ' Changed source version.'
        self.write(self.ws.raw / (self.sid + '.json'), record)
        again = self.evaluate()
        self.assertFalse(again['human_evaluation']['record_bindings_current'])
        self.assertIn('sources_not_synchronized', again['blocked_reasons'])
        self.assertIn('extraction_or_question_coverage_incomplete', again['blocked_reasons'])

    def test_wrong_quote_and_deleted_claim_are_not_certified(self):
        meta = json.loads((self.ws.meta / 'leases.json').read_text())
        meta['claims'][0]['evidence_passages'][0]['quote'] = 'This quotation does not occur in the archived original at all.'
        meta['claims'][0]['id'] = 'replacement'
        self.write(self.ws.meta / 'leases.json', meta)
        report = self.evaluate()
        self.assertIn('structural_integrity_failed', report['blocked_reasons'])
        self.assertIn('legacy_claim_mappings_unresolved', report['blocked_reasons'])
        self.assertEqual(report['integrity']['evidence_coverage'], 0)

    def test_query_wrong_link_does_not_pass_location_check(self):
        report = self.evaluate()
        self.answers(report)
        path = self.ws.root / 'query_evaluation.json'
        saved = json.loads(path.read_text())
        saved['records'][0]['answer']['citations'][0]['url'] = '/source/wrong'
        self.write(path,saved)
        again = self.evaluate()
        self.assertEqual(again['query_evaluation']['citation_location_rate'], 0)
        self.assertFalse(again['query_evaluation']['ready'])

    def test_case_booleans_cannot_replace_per_claim_support_records(self):
        report = self.evaluate()
        self.labels(report)
        path = self.ws.root / 'human_evaluation.json'
        labels = json.loads(path.read_text())
        labels.pop('claim_records')
        self.write(path, labels)
        again = self.evaluate()
        self.assertEqual(again['human_evaluation']['pass_rate'], 1.0)
        self.assertIsNone(again['human_evaluation']['claim_support']['precision'])
        self.assertEqual(again['human_evaluation']['claim_support']['reviewed'], 0)
        self.assertFalse(again['human_evaluation']['ready'])

    def test_unique_core_claim_precision_95_percent_and_duplicates(self):
        meta = json.loads((self.ws.meta / 'leases.json').read_text())
        original = meta['claims'][0]
        meta['claims'] = [{**copy.deepcopy(original), 'id':'ownership' if i == 0 else 'claim-'+str(i)} for i in range(20)]
        self.write(self.ws.meta / 'leases.json', meta)
        repeated = copy.deepcopy(self.fixture['knowledge_cases'][0])
        repeated['id'] = 'update-test'
        self.fixture['knowledge_cases'].append(repeated)
        self.write(self.fixture_path, self.fixture)
        report = self.evaluate()
        self.labels(report)
        path = self.ws.root / 'human_evaluation.json'
        labels = json.loads(path.read_text())
        labels['claim_records'][0]['support'] = 'contradicted'
        self.write(path, labels)
        again = self.evaluate()['human_evaluation']
        self.assertEqual(again['reviewed'], 2)
        self.assertEqual(again['claim_support']['required'], 20)
        self.assertEqual(again['claim_support']['reviewed'], 20)
        self.assertEqual(again['claim_support']['precision'], .95)
        self.assertTrue(again['ready'])
        labels['claim_records'][1]['support'] = 'partially_supported'
        self.write(path, labels)
        below = self.evaluate()['human_evaluation']
        self.assertEqual(below['pass_rate'], 1.0)
        self.assertEqual(below['claim_support']['precision'], .9)
        self.assertFalse(below['ready'])
        labels['claim_records'].append(copy.deepcopy(labels['claim_records'][2]))
        self.write(path, labels)
        duplicate = self.evaluate()['human_evaluation']['claim_support']
        self.assertEqual(duplicate['reviewed'], 19)
        self.assertIsNone(duplicate['precision'])
        self.assertEqual(duplicate['missing_or_invalid_claims'][0]['reason'], 'duplicate_records')

    def test_substantive_case_failure_blocks_even_above_case_pass_rate(self):
        first = self.fixture['knowledge_cases'][0]
        self.fixture['knowledge_cases'] = [{**copy.deepcopy(first),'id':'case-'+str(i),
            'required_human_dimensions': first['required_human_dimensions']+['old_claims_preserved']} for i in range(24)]
        self.write(self.fixture_path, self.fixture)
        report = self.evaluate()
        self.labels(report)
        path = self.ws.root / 'human_evaluation.json'
        labels = json.loads(path.read_text())
        labels['records'][0]['dimensions']['old_claims_preserved'] = False
        self.write(path, labels)
        human = self.evaluate()['human_evaluation']
        self.assertGreater(human['pass_rate'], .95)
        self.assertEqual(human['claim_support']['precision'], 1.0)
        self.assertFalse(human['ready'])
        self.assertEqual(human['blocking_cases'], [{'case_id':'case-0','dimensions':['old_claims_preserved']}])

    def test_actual_hybrid_uses_knowledge_cases_and_page_candidates_without_lock(self):
        first = self.fixture['knowledge_cases'][0]
        self.fixture['knowledge_cases'] = [{**copy.deepcopy(first),'id':'case-'+str(i)} for i in range(24)]
        self.fixture['thresholds']['recall_at_8'] = .8
        self.write(self.fixture_path, self.fixture)
        with self.hybrid() as client, patch.object(search, 'search', wraps=search.search) as retrieval:
            report = self.evaluate()
            self.assertEqual(client.call_count, 25)  # One index batch, 24 query requests.
            self.assertEqual(retrieval.call_count, 24)
            for call in retrieval.call_args_list:
                self.assertEqual(call.kwargs, {'limit':8,'semantic':True,'kind':'page'})
        self.assertEqual(report['retrieval']['mode'], 'hybrid-online')
        self.assertEqual(report['retrieval']['recall_at_8'], 1.0)
        self.assertEqual(report['retrieval']['threshold'], .9)
        self.assertTrue(report['retrieval']['ready'])
        self.assertEqual(report['retrieval']['cases'][0]['retrieved'], ['leases'])

    def test_hybrid_network_failure_does_not_certify_lexical_fallback(self):
        with self.hybrid():
            with patch.object(search, 'embed_texts', side_effect=OSError('test embedding outage')):
                report = self.evaluate()
        self.assertEqual(report['retrieval']['mode'], 'hybrid-incomplete')
        self.assertEqual(report['retrieval']['diagnostic_recall_at_8'], 1.0)
        self.assertIsNone(report['retrieval']['recall_at_8'])
        self.assertIn('hybrid_retrieval_not_measured', report['blocked_reasons'])

    def test_source_change_during_query_embedding_invalidates_report(self):
        report = self.evaluate()
        self.labels(report)
        self.answers(report)
        with self.hybrid():
            def change(config, texts):
                self.assertNotIn(str(self.ws.root), getattr(_LOCAL, 'held', {}))
                path = self.ws.raw / (self.sid + '.json')
                record = json.loads(path.read_text())
                record['text'] += ' Source changed during retrieval.'
                self.write(path, record)
                return [[1.0,0.0]]
            with patch.object(search, 'embed_texts', side_effect=change):
                changed = self.evaluate(write=True)
        self.assertFalse(changed['passed'])
        self.assertIn('library_changed_during_evaluation', changed['blocked_reasons'])

    def test_deferred_question_requires_explicit_missing_reason(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute("UPDATE knowledge_coverage SET status='deferred',target_slug='',reason='Only a name is mentioned; original method paper is needed'")
        report = self.evaluate()
        self.assertEqual(report['integrity']['published_questions'], 0)
        self.assertEqual(report['integrity']['accounted_questions'], 1)
        self.assertEqual(len(report['integrity']['documented_missing_questions']), 1)
        self.assertNotIn('extraction_or_question_coverage_incomplete', report['blocked_reasons'])
        with sqlite3.connect(self.ws.db) as db:
            db.execute("UPDATE knowledge_coverage SET reason=' '")
        missing = self.evaluate()
        self.assertIn('extraction_or_question_coverage_incomplete', missing['blocked_reasons'])

    def test_unverifiable_legacy_mapping_never_counts_as_preserved(self):
        meta = json.loads((self.ws.meta / 'leases.json').read_text())
        meta['legacy_mappings'] = [{'old_slug':'leases','old_claim_id':'ownership','new_claim_ids':['ownership'],
            'disposition':'unverifiable','reason':'Original does not establish the old condition'}]
        self.write(self.ws.meta / 'leases.json', meta)
        self.assertIn('legacy_claim_mappings_unresolved', self.evaluate()['blocked_reasons'])

    def test_removed_legacy_original_is_not_synchronized_with_retained_candidate(self):
        for ws in (self.ws, self.old):
            (ws.originals / 'source.txt').write_text('Archived original payload')
            path = ws.raw / (self.sid + '.json')
            raw = json.loads(path.read_text())
            raw['asset_path'] = 'source.txt'
            self.write(path, raw)
        (self.old.originals / 'source.txt').unlink()
        sync = evaluation._source_sync(self.ws, self.old)
        self.assertFalse(sync['ready'])
        self.assertEqual(sync['source_version_changes'], [self.sid])

    def test_current_acceptance_rejects_vector_and_legacy_changes(self):
        report = self.evaluate()
        self.labels(report)
        self.answers(report)
        with self.hybrid():
            accepted = self.evaluate()
            self.assertTrue(accepted['passed'])
            self.assertTrue(evaluation.acceptance_current(self.ws, accepted, legacy_ws=self.old, fixture_path=self.fixture_path))
            with sqlite3.connect(self.ws.root / 'search.db') as db:
                db.execute("UPDATE chunks SET vector='[0.0,1.0]' WHERE kind='page'")
            self.assertFalse(evaluation.acceptance_current(self.ws, accepted, legacy_ws=self.old, fixture_path=self.fixture_path))
            refreshed = self.evaluate()
            self.assertTrue(refreshed['passed'])
            (self.old.wiki / 'leases.md').write_text('# Baseline knowledge changed')
            self.assertFalse(evaluation.acceptance_current(self.ws, refreshed, legacy_ws=self.old, fixture_path=self.fixture_path))
            after_legacy_change = self.evaluate()
            self.assertFalse(after_legacy_change['human_evaluation']['record_bindings_current'])
            self.assertFalse(after_legacy_change['human_evaluation']['ready'])

    def test_public_gold_has_24_knowledge_and_12_query_cases_with_source_refs(self):
        fixture = json.loads(evaluation.FIXTURE_PATH.read_text())
        self.assertEqual(len(fixture['knowledge_cases']), 24)
        self.assertEqual(len(fixture['query_cases']), 12)
        self.assertIn('insufficient_evidence',{c['expected_status'] for c in fixture['query_cases']})
        self.assertFalse(fixture['provenance']['human_validated'])
        self.assertGreaterEqual(fixture['thresholds']['recall_at_8'], .9)
        self.assertGreaterEqual(fixture['thresholds']['core_claim_support_precision'], .95)
        self.assertEqual({c['operation'] for c in fixture['knowledge_cases']}, {'extraction','update'})
        catalog_path = Path(__file__).resolve().parent.parent / 'data/source-catalog.json'
        catalog = {s['id']:s for s in json.loads(catalog_path.read_text())['sources']}
        for case in fixture['knowledge_cases'] + fixture['query_cases']:
            self.assertEqual(case['expected_page_slugs'], [case['expected_target_slug']])
            self.assertTrue(case['expected_source_ids'])
            self.assertEqual(set(case['expected_source_ids']), {r['source_id'] for r in case['source_references']})
            for ref in case['source_references']:
                self.assertEqual(ref['url'],catalog[ref['source_id']]['url'])

    def test_insufficient_evidence_case_must_refuse_without_fabricated_citations(self):
        case={'id':'query-insufficient','question':'Prove that every untested dataset has a perfect result.',
              'expected_status':'insufficient_evidence','expected_target_slug':'leases','expected_source_ids':[self.sid]}
        self.fixture['query_cases'].append(case)
        self.write(self.fixture_path,self.fixture)
        report=self.evaluate();self.answers(report)
        path=self.ws.root/'query_evaluation.json';record=json.loads(path.read_text())
        refusal={'library_id':self.ws.library_id,'question':case['question'],'status':'insufficient_evidence',
                 'answer_markdown':'现有归档证据不足以回答这个问题。','citations':[]}
        record['records'].append({'case_id':case['id'],'reviewer_type':'agent','answer':refusal})
        self.write(path,record)
        self.assertTrue(self.evaluate()['query_evaluation']['ready'])
        refusal['answer_markdown']='All untested datasets definitely achieve a perfect result.'
        self.write(path,record)
        self.assertFalse(self.evaluate()['query_evaluation']['ready'])
