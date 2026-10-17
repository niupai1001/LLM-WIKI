"""Paper/point acceptance adaptation, using only disposable synthetic originals."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from station import documents, evaluation, knowledge_synthesis, paper_rebuild, search
from station.workspaces import Workspace


SID = '1234567890abcdef'
TEXT = 'Renewable execution leases retain ownership and prevent stale workers from committing updates. '


class PaperEvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ws = Workspace('candidate-v2', self.root / 'candidate')
        self.old = Workspace('legacy', self.root / 'legacy')
        self.point_id = paper_rebuild.global_point_id(SID, 'ownership')
        for ws in (self.ws, self.old):
            for folder in (ws.raw, ws.originals, ws.wiki, ws.meta, ws.root / 'config'):
                folder.mkdir(parents=True)
            self.write(ws.config / 'embedding.json', {'base_url': '', 'model': '', 'api_key': ''})
            self.make_pdf(ws.originals / (SID + '.pdf'))
            self.write(ws.raw / (SID + '.json'), {
                'id': SID, 'title': 'Synthetic lease paper', 'kind': 'paper', 'category': 'research',
                'pdf_path': SID + '.pdf', 'asset_path': SID + '.pdf', 'content_format': 'pdf',
                'content_status': 'full-text', 'text': TEXT * 3})
        self.write(self.ws.root / 'paper-workflow.json', {'enabled': True, 'required_original': 'pdf'})
        parsed = documents.parse_source(self.ws, SID, ocr=False)
        self.version = parsed['source_version']
        paragraph = parsed['paragraphs'][0]
        self.claim = {'id': 'ownership', 'text': 'Leases prevent stale commits.',
                      'scope': 'The synthetic worker protocol.', 'source_ids': [SID],
                      'evidence_passages': [{'source_id': SID, 'quote': paragraph['text'],
                                             'locator': 'PDF page 1', 'paragraph_id': paragraph['id']}]}
        self.point = {'id': 'ownership', 'proposition': 'Leases fence stale workers.',
                      'explanation': 'An expired worker loses the right to commit.', 'basis': 'mechanism',
                      'scope': 'Synthetic renewable worker leases.', 'value': 'Separate execution from commit authority.',
                      'claim_ids': ['ownership'], 'result_ids': []}
        self.paper_body = '# Paper reading\n\nLeases prevent stale commits. [S:' + SID + ']'
        self.packet = {'source': {'source_id': SID, 'source_version': self.version},
                       'title': 'Synthetic lease paper', 'body_markdown': self.paper_body,
                       'claims': [self.claim], 'knowledge_points': [self.point], 'results': []}
        (self.ws.wiki / 'paper-reading.md').write_text(self.paper_body)
        self.write(self.ws.meta / 'paper-reading.json', {
            'slug': 'paper-reading', 'title': 'Paper reading', 'page_kind': 'concept', 'page_role': 'paper',
            'source_ids': [SID], 'claims': [self.claim], 'knowledge_point_ids': [self.point_id]})
        self.concept_quote = 'Renewable leases separate execution from commit authority.'
        (self.ws.wiki / 'lease-authority.md').write_text('# Lease authority\n\n' + self.concept_quote + ' [S:' + SID + ']')
        self.write(self.ws.meta / 'lease-authority.json', {
            'slug': 'lease-authority', 'title': 'Lease authority', 'page_kind': 'concept', 'page_role': 'knowledge',
            'source_ids': [SID], 'claims': [self.claim], 'knowledge_point_ids': [self.point_id],
            'claim_bindings': [{'claim_id': 'ownership', 'body_quote': self.concept_quote}],
            'point_bindings': [{'point_id': self.point_id, 'claim_ids': ['ownership'], 'body_quote': self.concept_quote}]})
        with sqlite3.connect(self.ws.db) as db:
            paper_rebuild._schema(db)
            db.execute('CREATE TABLE sources(id TEXT PRIMARY KEY,stage TEXT)')
            db.execute('INSERT INTO sources VALUES (?,?)', (SID, 'approved'))
            db.execute('INSERT INTO paper_distillations VALUES (?,?,?,?,?,?)',
                       (SID, self.version, 'paper-reading', json.dumps(self.packet), '[]', '2026-10-15'))
            db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)',
                       (self.point_id, SID, 'ownership', self.version, json.dumps(self.point)))
            db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason) VALUES (?,?,?)',
                       ('lease-authority', self.point_id, 'The point explains commit authority.'))
        self.fixture = {
            'suite_id': 'synthetic-paper-adaptation-test-only', 'coverage_mode': 'paper_knowledge_points',
            'knowledge_cases': [{'id': 'point-test', 'question': 'What prevents stale commits?',
                                 'expected_target_slug': 'lease-authority', 'expected_source_ids': [SID],
                                 'required_human_dimensions': ['source_mapping', 'target_mapping', 'claim_support', 'scope_preserved']}],
            'query_cases': [{'id': 'query-test', 'question': 'What prevents stale commits?',
                             'expected_target_slug': 'lease-authority', 'expected_source_ids': [SID]}],
            'thresholds': {'human_case_pass_rate': .95, 'core_claim_support_precision': .95,
                           'recall_at_8': .9, 'citation_location_rate': 1.0}}
        self.fixture_path = self.root / 'fixture.json'
        self.write(self.fixture_path, self.fixture)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))

    def make_pdf(self, path):
        writer = PdfWriter()
        page = writer.add_blank_page(width=400, height=400)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 20 300 Td (' + TEXT * 3 + ') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
        writer.write(path)

    def reasons(self, result):
        return {row['reason'] for row in result['unresolved']}

    def evaluate(self, fixture_path=None):
        return evaluation.evaluate_library(self.ws, write=False, legacy_ws=self.old,
                                            fixture_path=fixture_path or self.fixture_path)

    def test_completed_paper_and_routed_point_are_covered_without_inventing_questions(self):
        before = {str(path.relative_to(self.ws.root)): path.read_bytes()
                  for path in self.ws.root.rglob('*') if path.is_file()}
        result = evaluation._integrity(self.ws)
        self.assertEqual(result['coverage_mode'], 'paper_knowledge_points')
        self.assertEqual(result['papers'], {'required': 1, 'current_published': 1})
        self.assertEqual(result['knowledge_points'], {'total': 1, 'current': 1, 'routed': 1, 'unrouted': 0, 'processed': 1, 'deferred': 0, 'unprocessed': 0, 'active_memberships': 1, 'valid_memberships': 1, 'pending_review_memberships': 0})
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['questions'], 0)
        self.assertEqual(result['accounted_questions'], 0)
        self.assertEqual(result['evidence_coverage'], 1.0)
        self.assertEqual({str(path.relative_to(self.ws.root)): path.read_bytes()
                          for path in self.ws.root.rglob('*') if path.is_file()}, before)

    def test_approved_source_is_not_equivalent_to_a_current_distillation(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM paper_distillations')
        result = evaluation._integrity(self.ws)
        self.assertEqual(result['papers']['current_published'], 0)
        self.assertIn('no_current_paper_distillation', self.reasons(result))
        self.assertEqual(result['knowledge_points']['routed'], 0)

    def test_missing_pdf_or_changed_original_cannot_reuse_old_points(self):
        path = self.ws.originals / (SID + '.pdf')
        original = path.read_bytes()
        path.unlink()
        missing = evaluation._integrity(self.ws)
        self.assertIn('ineligible_paper_original', self.reasons(missing))
        self.assertEqual(missing['knowledge_points']['routed'], 0)
        path.write_bytes(original + b'\n% changed archived version\n')
        changed = evaluation._integrity(self.ws)
        self.assertIn('no_current_paper_distillation', self.reasons(changed))
        self.assertTrue(any('version changed' in row['reason'].lower() for row in changed['unresolved']))

    def test_published_paper_page_must_exist_and_match_its_packet_body(self):
        path = self.ws.wiki / 'paper-reading.md'
        original = path.read_text()
        path.unlink()
        missing = evaluation._integrity(self.ws)
        self.assertEqual(missing['papers']['current_published'], 0)
        path.write_text(original + '\n\nAn unreviewed claim was added.')
        changed = evaluation._integrity(self.ws)
        self.assertIn('paper_body_changed_after_distillation', self.reasons(changed))

    def test_point_data_must_match_its_version_bound_packet(self):
        point = {**self.point, 'scope': 'An unsupported wider scope.'}
        with sqlite3.connect(self.ws.db) as db:
            db.execute('UPDATE knowledge_points SET data_json=?', (json.dumps(point),))
        result = evaluation._integrity(self.ws)
        self.assertTrue(any('does not match' in row['reason'] for row in result['unresolved']))
        self.assertEqual(result['knowledge_points']['total'], 1)
        self.assertEqual(result['knowledge_points']['current'], 0)
        self.assertEqual(result['knowledge_points']['routed'], 0)

    def test_unrouted_point_and_broken_body_or_claim_binding_block_coverage(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM concept_memberships')
        result = evaluation._integrity(self.ws)
        self.assertIn('knowledge_point_not_routed', self.reasons(result))
        with sqlite3.connect(self.ws.db) as db:
            db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason) VALUES (?,?,?)', ('lease-authority', self.point_id, 'Known assignment'))
        path = self.ws.meta / 'lease-authority.json'
        original = json.loads(path.read_text())
        for bad_binding in (
            {'point_id': self.point_id, 'body_quote': 'Text absent from body.', 'claim_ids': ['ownership']},
            {'point_id': self.point_id, 'body_quote': self.concept_quote, 'claim_ids': ['unknown-claim']},
            {'point_id': self.point_id, 'body_quote': self.concept_quote, 'claim_ids': []},
        ):
            with self.subTest(binding=bad_binding):
                self.write(path, {**original, 'point_bindings': [bad_binding]})
                invalid = evaluation._integrity(self.ws)
                self.assertIn('knowledge_point_body_or_claim_binding_invalid', self.reasons(invalid))
        self.write(path, {**original, 'claim_bindings': []})
        self.assertIn('missing_claim_body_binding', {row['code'] for row in evaluation._integrity(self.ws)['issues']})

    def test_concept_metadata_cannot_claim_a_point_absent_from_durable_memberships(self):
        path = self.ws.meta / 'lease-authority.json'
        meta = json.loads(path.read_text())
        meta['knowledge_point_ids'].append('kp_' + '0' * 20)
        self.write(path, meta)
        self.assertIn('concept_membership_metadata_mismatch', self.reasons(evaluation._integrity(self.ws)))

    def test_every_paper_point_or_membership_table_change_invalidates_input_bindings(self):
        original = evaluation._input_digest(self.ws, [])
        changes = [('paper_distillations', 'published_at', 'a new publication receipt'),
                   ('knowledge_points', 'data_json', json.dumps({**self.point, 'value': 'Changed knowledge value.'})),
                   ('concept_memberships', 'reason', 'A changed concept assignment.')]
        for table, column, value in changes:
            with self.subTest(table=table):
                with sqlite3.connect(self.ws.db) as db:
                    previous = db.execute('SELECT ' + column + ' FROM ' + table).fetchone()[0]
                    db.execute('UPDATE ' + table + ' SET ' + column + '=?', (value,))
                self.assertNotEqual(evaluation._input_digest(self.ws, []), original)
                with sqlite3.connect(self.ws.db) as db:
                    db.execute('UPDATE ' + table + ' SET ' + column + '=?', (previous,))
                self.assertEqual(evaluation._input_digest(self.ws, []), original)

    def test_original_24_12_fixture_requires_adaptation_without_measuring_fake_recall(self):
        original_fixture = evaluation.FIXTURE_PATH.read_bytes()
        with patch.object(evaluation, '_retrieval') as retrieval, patch.object(search, 'embed_texts') as embeddings:
            report = self.evaluate(evaluation.FIXTURE_PATH)
            retrieval.assert_not_called()
            embeddings.assert_not_called()
        compatibility = report['fixture_compatibility']
        self.assertEqual(compatibility['knowledge_cases'], 24)
        self.assertEqual(compatibility['query_cases'], 12)
        self.assertEqual(compatibility['status'], 'requires_adaptation')
        self.assertIn('evaluation_fixture_requires_adaptation', report['blocked_reasons'])
        self.assertEqual(report['retrieval']['mode'], 'not-applicable')
        self.assertIsNone(report['retrieval']['recall_at_8'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['human_evaluation']['reviewed'], 0)
        self.assertIsNone(report['human_evaluation']['claim_support']['precision'])
        self.assertEqual(evaluation.FIXTURE_PATH.read_bytes(), original_fixture)

    def test_changed_synthesis_request_rules_invalidate_input_binding(self):
        original = evaluation._input_digest(self.ws, [])
        with patch.object(knowledge_synthesis, 'REQUEST_CONTEXT_VERSION', 'synthetic-new-context-test-only'):
            self.assertNotEqual(evaluation._input_digest(self.ws, []), original)

    def test_explicit_new_fixture_uses_point_gate_but_still_requires_real_human_and_hybrid_scores(self):
        report = self.evaluate()
        self.assertTrue(report['fixture_compatibility']['ready'])
        self.assertNotIn('extraction_or_question_coverage_incomplete', report['blocked_reasons'])
        self.assertNotIn('paper_knowledge_point_coverage_incomplete', report['blocked_reasons'])
        self.assertIn('human_evaluation_missing_stale_or_below_threshold', report['blocked_reasons'])
        self.assertIn('hybrid_retrieval_not_measured', report['blocked_reasons'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['human_evaluation']['threshold'], .95)
        self.assertEqual(report['human_evaluation']['claim_support']['threshold'], .95)
        self.assertEqual(report['retrieval']['threshold'], .9)
        self.assertIsNone(report['human_evaluation']['pass_rate'])
        self.assertIsNone(report['retrieval']['recall_at_8'])

    def test_workflow_mode_alone_does_not_make_missing_targets_applicable(self):
        fixture = copy.deepcopy(self.fixture)
        fixture['knowledge_cases'][0]['expected_target_slug'] = 'old-unmapped-slug'
        result = evaluation._fixture_compatibility(self.ws, fixture)
        self.assertFalse(result['ready'])
        self.assertEqual(result['missing_knowledge_targets'][0]['target_slug'], 'old-unmapped-slug')


if __name__ == '__main__':
    unittest.main()
