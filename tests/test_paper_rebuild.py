import tempfile
import unittest
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from station import distillation, jobs, knowledge_synthesis, paper_rebuild, search
from station.workspaces import Workspace


class PaperRebuildRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ws = Workspace('candidate-v2', Path(temporary.name))
        paper_rebuild.initialize(self.ws)
        self.ids = ['1111111111111111', '2222222222222222']

    def test_changed_preview_skill_stops_before_source_processing_or_model_requests(self):
        with patch.object(distillation, '_skill', return_value=('new skill', 'new-hash')), \
                patch.object(paper_rebuild, 'selected_sources') as select, \
                patch.object(distillation, 'distill_source') as author:
            with self.assertRaisesRegex(ValueError, 'SKILL changed'):
                paper_rebuild.compile_job(self.ws, {'skill_hash': 'frozen-hash'}, 'preview-job')
        select.assert_not_called()
        author.assert_not_called()

    def test_preview_page_mapping_cannot_add_sources_outside_the_selected_scope(self):
        with patch.object(paper_rebuild, 'selected_sources', return_value=(self.ids, [], {})), \
                patch.object(distillation, 'distill_source') as author:
            with self.assertRaisesRegex(ValueError, 'selected source scope'):
                paper_rebuild.compile_job(self.ws, {'paper_slugs': {'3333333333333333': 'extra-paper'}}, 'preview-job')
        author.assert_not_called()

    def test_published_paper_from_a_different_skill_is_not_silently_reused(self):
        sid = self.ids[0]
        self.ws.wiki.mkdir()
        self.ws.meta.mkdir()
        (self.ws.wiki / 'existing.md').write_text('Previous reading')
        (self.ws.meta / 'existing.json').write_text(json.dumps({'distillation_skill_hash': 'old'}))
        with sqlite3.connect(self.ws.db) as db:
            db.execute('INSERT INTO paper_distillations(source_id,source_version,page_slug) VALUES (?,?,?)',
                       (sid, 'same-source', 'existing'))
        with patch.object(distillation, '_skill', return_value=('new skill', 'new')), \
                patch.object(paper_rebuild, 'selected_sources', return_value=([sid], [], {})), \
                patch.object(paper_rebuild, 'source_version', return_value='same-source'), \
                patch.object(distillation, 'distill_source', return_value={'status': 'blocked', 'reasons': ['Needs review']}) as author, \
                patch.object(search, 'rebuild_index', return_value={}):
            result = paper_rebuild.compile_job(self.ws, {'skill_hash': 'new', 'synthesize': False}, 'preview-job')
        author.assert_called_once()
        self.assertEqual(result['published'], 0)

    def run_rebuild(self, recovery_error=None):
        def distilled(ws, sid, **kwargs):
            reason = ('Paper body must cite only its own archived source' if sid == self.ids[0] else
                      'Whole-source reading completed, but synthesis exceeds its explicit context limit; no material was silently truncated')
            return {'status': 'blocked', 'reasons': [reason]}

        def recovered(ws, sid, **kwargs):
            return {'status': 'ready', 'packet': {'source': {'source_id': sid}}, 'reviews': ['review']}

        def published(ws, packet, reviews, job_id):
            self.assertEqual(reviews, ['review'])
            return {'source_id': packet['source']['source_id'], 'status': 'published'}

        with patch.object(paper_rebuild, 'selected_sources', return_value=(self.ids, [], {})), \
                patch.object(distillation, 'distill_source', side_effect=distilled), \
                patch.object(distillation, 'revalidate_cached_structure', side_effect=recovery_error or recovered) as structural, \
                patch.object(distillation, 'resume_provider_context', return_value={'fallback_allowed': True}), \
                patch.object(distillation, 'resume_long_source', side_effect=recovered) as long_source, \
                patch.object(paper_rebuild, 'publish_packet', side_effect=published) as publish, \
                patch.object(search, 'rebuild_index', return_value={'ready': True}) as index:
            result = paper_rebuild.compile_job(self.ws, {'synthesize': False}, 'durable-job')
        self.assertEqual(structural.call_count, 1)
        self.assertEqual(long_source.call_count, 1)
        index.assert_called_once_with(self.ws, embeddings=True)
        return result, publish

    def test_source_recoveries_remain_inside_compile_and_publish_after_review(self):
        result, publish = self.run_rebuild()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['published'], 2)
        self.assertEqual(publish.call_count, 2)

    def test_refused_recovery_blocks_one_source_without_skipping_other_source_or_index(self):
        result, publish = self.run_rebuild(ValueError('Existing semantic rejection cannot be bypassed'))
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['published'], 1)
        self.assertEqual(publish.call_count, 1)
        blocked = next(row for row in result['results'] if row['status'] == 'blocked')
        self.assertEqual(blocked['source_id'], self.ids[0])
        self.assertIn('semantic rejection', blocked['reasons'][0])

    def save_previous(self):
        previous = {'results': [
            {'source_id': self.ids[0], 'slug': 'paper-' + self.ids[0], 'status': 'blocked', 'reasons': ['Evidence issue']},
            {'source_id': self.ids[1], 'slug': 'paper-' + self.ids[1], 'status': 'published'}],
            'synthesis': {'input_digest': 'same', 'status': 'blocked', 'results': [
                {'slug': 'concept-a', 'status': 'blocked', 'reasons': ['A issue']},
                {'slug': 'concept-b', 'status': 'blocked', 'reasons': ['B issue']}]}}
        path = self.ws.root / 'runs' / 'durable-job' / 'paper-rebuild.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(previous))
        return previous

    def test_single_concept_retry_preserves_other_failed_outcomes_and_never_reauthors_sources(self):
        self.save_previous()
        selected = {'input_digest': 'same', 'status': 'completed', 'results': [{'slug': 'concept-a', 'status': 'published'}]}
        with patch.object(paper_rebuild, 'selected_sources', return_value=(self.ids, [], {})), \
                patch.object(distillation, 'distill_source') as author, \
                patch.object(knowledge_synthesis, 'synthesize_library', return_value=selected) as synthesize, \
                patch.object(search, 'rebuild_index', return_value={}):
            result = paper_rebuild.compile_job(self.ws, {'retry_target': 'concept-a'}, 'durable-job')
        author.assert_not_called()
        synthesize.assert_called_once_with(self.ws, model=None, job_id='durable-job', target_slug='concept-a')
        self.assertEqual(result['status'], 'blocked')
        outcomes = {row['slug']: row['status'] for row in result['synthesis']['results']}
        self.assertEqual(outcomes, {'concept-a': 'published', 'concept-b': 'blocked'})
        self.assertFalse(result['results'][0]['attempted'])

    def test_single_paper_retry_processes_only_selected_source(self):
        self.save_previous()
        with patch.object(paper_rebuild, 'selected_sources', return_value=(self.ids, [], {})), \
                patch.object(distillation, 'distill_source', return_value={'status': 'blocked', 'reasons': ['Still unresolved']}) as author, \
                patch.object(search, 'rebuild_index', return_value={}):
            result = paper_rebuild.compile_job(self.ws, {'retry_target': 'paper-' + self.ids[0], 'synthesize': False}, 'durable-job')
        author.assert_called_once_with(self.ws, self.ids[0], model=None, force=False)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['published'], 1)

    def test_unknown_retry_target_cannot_start_any_source_work(self):
        with patch.object(paper_rebuild, 'selected_sources', return_value=(self.ids, [], {})), \
                patch.object(distillation, 'distill_source') as author:
            with self.assertRaisesRegex(ValueError, 'persisted blocked'):
                paper_rebuild.compile_job(self.ws, {'retry_target': 'unknown'}, 'durable-job')
        author.assert_not_called()

    def test_jobs_accept_nested_blocked_concept_without_changing_input_versions(self):
        job = jobs.enqueue(self.ws, 'compile', {}, input_versions={self.ids[0]: 'v1'})
        claimed = jobs.claim(self.ws)
        jobs.complete(self.ws, job['id'], claimed['lease_token'], {
            'status': 'blocked', 'results': [], 'synthesis': {'results': [{'slug': 'concept-a', 'status': 'blocked'}]}})
        queued = jobs.retry(self.ws, job['id'], target_slug='concept-a')
        self.assertEqual(queued['payload']['retry_target'], 'concept-a')
        self.assertEqual(queued['input_versions'], {self.ids[0]: 'v1'})

    def test_explicit_concept_target_is_forwarded_and_other_concepts_are_documented(self):
        selected = {'status': 'completed', 'results': [{'slug': 'concept-a', 'status': 'published'}],
                    'plan': {'concepts': [{'slug': 'concept-a'}, {'slug': 'concept-b'}]}}
        with patch.object(paper_rebuild, 'selected_sources', return_value=(self.ids, [], {})), \
                patch.object(distillation, 'distill_source', return_value={'status': 'blocked', 'reasons': ['Source unresolved']}), \
                patch.object(knowledge_synthesis, 'synthesize_library', return_value=selected) as synthesize, \
                patch.object(search, 'rebuild_index', return_value={}):
            result = paper_rebuild.compile_job(self.ws, {'target_slug': 'concept-a'}, 'durable-job')
        synthesize.assert_called_once_with(self.ws, model=None, job_id='durable-job', target_slug='concept-a')
        self.assertEqual([r['slug'] for r in result['synthesis']['deferred_concepts']], ['concept-b'])

    def test_verified_provider_recovery_is_preferred_without_reselecting_evidence(self):
        blocked = {'status': 'blocked', 'reasons': [
            'Whole-source reading completed, but synthesis exceeds its explicit context limit; no material was silently truncated']}
        recovered = {'status': 'ready', 'packet': {'source': {'source_id': self.ids[0]}}, 'reviews': ['review']}
        with patch.object(paper_rebuild, 'selected_sources', return_value=([self.ids[0]], [], {})), \
                patch.object(distillation, 'distill_source', return_value=blocked), \
                patch.object(distillation, 'resume_provider_context', return_value=recovered) as provider, \
                patch.object(distillation, 'resume_long_source') as selection, \
                patch.object(paper_rebuild, 'publish_packet', return_value={'source_id': self.ids[0], 'status': 'published'}), \
                patch.object(search, 'rebuild_index', return_value={}):
            result = paper_rebuild.compile_job(self.ws, {'synthesize': False}, 'durable-job')
        self.assertEqual(result['status'], 'completed')
        provider.assert_called_once_with(self.ws, self.ids[0], model=None)
        selection.assert_not_called()
