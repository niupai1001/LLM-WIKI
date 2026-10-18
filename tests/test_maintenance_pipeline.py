"""End-to-end L3 tests with real transactional publication and offline evidence."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests import test_knowledge_synthesis as fixtures
from station import knowledge_synthesis as synthesis, paper_rebuild
from station.jobs import JobConflict
from station.model_attempts import synthesis_lock


class MaintenancePipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.KnowledgeSynthesisTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.module_patch.stop()
        self.ws = self.fixture.ws
        context = patch.object(paper_rebuild, 'parse_source', side_effect=lambda ws, sid: self.fixture.originals[sid])
        context.start()
        self.addCleanup(context.stop)

    def run_pipeline(self, responder=None, **options):
        model = Mock(side_effect=responder or self.fixture.respond)
        model.model_id = 'test-synthesis-v1'
        return synthesis.synthesize_library(self.ws, model=model, **options)

    def only_one_pending_point(self):
        keep = self.fixture.points[0]
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM knowledge_points WHERE id!=?', (keep,))
        return keep

    def test_real_publisher_commits_shared_point_to_two_concepts_atomically(self):
        pid = self.only_one_pending_point()
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') == 'maintain':
                return {'decision_version': 'knowledge-maintenance/v1', 'point_decisions': [
                    {'point_id': pid, 'disposition': 'linked', 'memberships': [
                        {'concept_slug': 'first-answer', 'role': 'core', 'reason': 'Explains the first question'},
                        {'concept_slug': 'second-answer', 'role': 'contrast', 'reason': 'Tests a different condition'}]}],
                    'concept_actions': [{'concept_slug': slug, 'action': 'create', 'title': slug,
                        'question': 'How does feedback guide decisions?', 'reason': 'A distinct durable question',
                        'affected_point_ids': [pid]} for slug in ('first-answer', 'second-answer')],
                    'relation_actions': [], 'unresolved': []}
            return self.fixture.respond(messages)
        result = self.run_pipeline(respond)
        self.assertEqual(result['status'], 'completed', result)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships WHERE point_id=?', (pid,)).fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM publication_journal WHERE proposal_id LIKE 'concept-group-%'").fetchone()[0], 1)
        topic = (self.ws.wiki / 'agents-and-feedback.md').read_text()
        for slug in ('first-answer', 'second-answer'):
            self.assertTrue((self.ws.wiki / (slug + '.md')).is_file())
            self.assertIn('[[' + slug + '|', topic)

    def test_concurrent_second_runner_is_rejected_before_model_request(self):
        model = Mock()
        with synthesis_lock(self.ws):
            with self.assertRaisesRegex(JobConflict, 'Another knowledge maintenance run'):
                synthesis.synthesize_library(self.ws, model=model)
        model.assert_not_called()

    def test_author_timeouts_keep_three_request_budget_across_restarts(self):
        self.only_one_pending_point()
        calls = []
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') in {'write', 'repair'}:
                calls.append(context['stage'])
                raise TimeoutError('offline simulated provider timeout')
            return self.fixture.respond(messages)
        for _ in range(5):
            try:
                self.run_pipeline(respond)
            except TimeoutError:
                pass
        self.assertEqual(len(calls), 3)

    def test_partial_success_does_not_refresh_independent_failed_author_budget(self):
        failed = 'reflection-across-attempts'
        calls = []
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') in {'write', 'repair'} and context['target']['slug'] == failed:
                calls.append(context['stage'])
                return {}
            return self.fixture.respond(messages)
        first = self.run_pipeline(respond)
        self.assertEqual(first['status'], 'blocked')
        self.assertTrue((self.ws.wiki / 'task-feedback-control.md').is_file(), first)
        self.assertEqual(len(calls), 3)
        second = self.run_pipeline(respond)
        self.assertEqual(second['status'], 'blocked')
        self.assertEqual(len(calls), 3, 'Other successful targets must not grant this failed author a new budget')

    def test_prepared_run_publication_never_calls_model(self):
        result = self.run_pipeline(publish_pages=False)
        self.assertEqual(result['status'], 'completed', result)
        with patch.object(synthesis, '_invoke', side_effect=AssertionError('Prepared publication requested a model')):
            published = synthesis.synthesize_library(self.ws, prepared_run=result['maintenance'])
        self.assertEqual(published['status'], 'completed', published)
        self.assertEqual(len(published['results']), 3)

    def test_prepared_run_rejects_changed_source_without_model_call(self):
        result = self.run_pipeline(publish_pages=False)
        sid = fixtures.SIDS[0]
        path = self.ws.raw / (sid + '.json')
        value = json.loads(path.read_text())
        value['text'] += ' Changed archived source.'
        path.write_text(json.dumps(value))
        with patch.object(synthesis, '_invoke', side_effect=AssertionError('Stale trial requested a model')):
            with self.assertRaisesRegex(ValueError, 'version changed|snapshot|missing|stale'):
                synthesis.synthesize_library(self.ws, prepared_run=result['maintenance'], source_ids=fixtures.SIDS)
        self.assertFalse((self.ws.wiki / 'task-feedback-control.md').exists())

    def test_prepared_run_rejects_newly_occupied_target(self):
        result = self.run_pipeline(publish_pages=False)
        (self.ws.wiki / 'task-feedback-control.md').write_text('# Human page\n\nPreserve this later edit.')
        with patch.object(synthesis, '_invoke', side_effect=AssertionError('Changed target requested a model')):
            with self.assertRaisesRegex(ValueError, 'base version changed|target|changed'):
                synthesis.synthesize_library(self.ws, prepared_run=result['maintenance'])
        self.assertIn('Preserve this later edit', (self.ws.wiki / 'task-feedback-control.md').read_text())

    def test_no_change_background_membership_survives_next_existing_snapshot_without_file_writes(self):
        self.only_one_pending_point()
        first = self.run_pipeline()
        self.assertEqual(first['status'], 'completed', first)
        slug = 'task-feedback-control'
        sid = fixtures.SIDS[0]
        background_id = self.fixture.points[1]
        with sqlite3.connect(self.ws.db) as db:
            packet, version = db.execute('SELECT packet_json,source_version FROM paper_distillations WHERE source_id=?', (sid,)).fetchone()
            point = json.loads(packet)['knowledge_points'][1]
            db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)',
                       (background_id, sid, point['id'], version, json.dumps(point)))
            publications_before = db.execute('SELECT count(*) FROM publication_journal').fetchone()[0]
            index_before = db.execute('SELECT index_dirty FROM publication_state').fetchone()[0]
        before = {str(path): path.read_bytes() for folder in (self.ws.wiki, self.ws.meta) for path in folder.iterdir() if path.is_file()}
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') == 'maintain':
                return {'decision_version': 'knowledge-maintenance/v1', 'point_decisions': [
                    {'point_id': background_id, 'disposition': 'linked', 'memberships': [
                        {'concept_slug': slug, 'role': 'background', 'reason': 'Adds context already adequately expressed by the page'}]}],
                    'concept_actions': [{'concept_slug': slug, 'action': 'no_change', 'reason': 'Existing prose needs no change',
                                         'affected_point_ids': [background_id]}], 'relation_actions': [], 'unresolved': []}
            raise AssertionError('No-change decision requested downstream writing or review')
        result = self.run_pipeline(respond)
        self.assertEqual(result['status'], 'completed', result)
        after = {str(path): path.read_bytes() for folder in (self.ws.wiki, self.ws.meta) for path in folder.iterdir() if path.is_file()}
        self.assertEqual(before, after)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT role FROM concept_memberships WHERE concept_slug=? AND point_id=?',
                                        (slug, background_id)).fetchone()[0], 'related')
            self.assertEqual(db.execute('SELECT count(*) FROM publication_journal').fetchone()[0], publications_before)
            self.assertEqual(db.execute('SELECT index_dirty FROM publication_state').fetchone()[0], index_before)
        existing, _ = synthesis._existing(self.ws)
        concept = next(c for c in existing if c['slug'] == slug)
        self.assertIn(background_id, concept['point_ids'])

    def test_unapproved_but_structurally_valid_relation_cannot_publish(self):
        self.only_one_pending_point()
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            reply = self.fixture.respond(messages)
            if context.get('stage') in {'write', 'repair'}:
                reply['relations'] = [{'id': 'unapproved', 'target_slug': 'paper-0', 'kind': 'implementation',
                    'summary': 'This method is an implementation of the stated feedback mechanism.',
                    'scope': 'Only within the original task setting.', 'claim_ids': [reply['claims'][0]['id']],
                    'source_ids': [fixtures.SIDS[0]]}]
            return reply
        result = self.run_pipeline(respond)
        self.assertEqual(result['status'], 'blocked', result)
        self.assertFalse((self.ws.wiki / 'task-feedback-control.md').exists())
        self.assertTrue(any('relation' in reason.lower() or '关系' in reason
                            for row in result['results'] for reason in row.get('reasons', [])), result)

    def test_shared_point_group_replacement_failure_rolls_back_every_concept_and_membership(self):
        from pathlib import Path
        from station import publication
        self.only_one_pending_point()
        original_decision = self.fixture.decision
        def decision(context):
            value = original_decision(context)
            action = copy.deepcopy(value['concept_actions'][0])
            action.update(concept_slug='second-answer', title='A second explanation')
            value['concept_actions'].append(action)
            value['point_decisions'][0]['memberships'].append(
                {'concept_slug': 'second-answer', 'role': 'contrast', 'reason': 'Answers a distinct related question'})
            return value
        self.fixture.decision = decision
        replace = publication.os.replace
        failures = []
        def fail_once(source, target):
            if Path(target).name == 'second-answer.md' and not failures:
                failures.append(True)
                raise OSError('Injected pipeline group replacement failure')
            return replace(source, target)
        with patch.object(publication.os, 'replace', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'pipeline group replacement failure'):
                self.run_pipeline()
        self.assertTrue(failures)
        for slug in ('task-feedback-control', 'second-answer', 'agents-and-feedback', 'index'):
            self.assertFalse((self.ws.wiki / (slug + '.md')).exists())
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM maintenance_executions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT state FROM publication_journal').fetchone()[0], 'rolled_back')

    def existing_feedback_concept(self):
        slug = 'existing-feedback'
        body = '# Existing feedback\n\nAn accepted explanation that needs no text change.\n'
        metadata = {'slug': slug, 'title': '当前执行中根据新观测调整下一步行动。',
                    'knowledge_topic': '当前执行中根据新观测调整下一步行动。', 'page_kind': 'concept',
                    'page_role': 'knowledge', 'source_ids': [], 'claims': [], 'knowledge_point_ids': []}
        (self.ws.wiki / (slug + '.md')).write_text(body)
        (self.ws.meta / (slug + '.json')).write_text(json.dumps(metadata))
        return slug, body, metadata

    def test_changed_no_change_dependency_rolls_back_connected_create_and_all_memberships(self):
        pid = self.only_one_pending_point()
        existing_slug, body, metadata = self.existing_feedback_concept()
        edits = []
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            stage = context.get('stage')
            if stage == 'maintain':
                return {'decision_version': 'knowledge-maintenance/v1', 'point_decisions': [
                    {'point_id': pid, 'disposition': 'linked', 'memberships': [
                        {'concept_slug': 'new-feedback-answer', 'role': 'core', 'reason': 'Explains a new durable question'},
                        {'concept_slug': existing_slug, 'role': 'background', 'reason': 'Supports the existing explanation without changing prose'}]}],
                    'concept_actions': [
                        {'concept_slug': 'new-feedback-answer', 'action': 'create', 'title': 'New feedback answer',
                         'question': 'How does feedback guide actions?', 'reason': 'A distinct useful explanation', 'affected_point_ids': [pid]},
                        {'concept_slug': existing_slug, 'action': 'no_change', 'reason': 'Existing text already expresses the background',
                         'affected_point_ids': [pid]}], 'relation_actions': [], 'unresolved': []}
            if stage in {'write', 'repair'}:
                edited = {**metadata, 'human_note': 'A later human editorial decision'}
                (self.ws.meta / (existing_slug + '.json')).write_text(json.dumps(edited))
                edits.append(edited)
            return self.fixture.respond(messages)
        try:
            result = self.run_pipeline(respond)
        except ValueError as error:
            self.assertIn('changed', str(error).lower())
            result = None
        if result is not None:
            self.assertEqual(result['status'], 'blocked', result)
        self.assertTrue(edits, 'The version change must occur after maintenance and during authoring')
        self.assertFalse((self.ws.wiki / 'new-feedback-answer.md').exists())
        self.assertEqual((self.ws.wiki / (existing_slug + '.md')).read_text(), body)
        self.assertEqual(json.loads((self.ws.meta / (existing_slug + '.json')).read_text()), edits[-1])
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships WHERE point_id=?', (pid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM point_dispositions WHERE point_id=?', (pid,)).fetchone()[0], 0)

    def test_pure_no_change_membership_edit_is_rejected_even_when_page_is_unchanged(self):
        from station.knowledge_maintenance import schema
        pid = self.only_one_pending_point()
        slug, body, metadata = self.existing_feedback_concept()
        changes = []
        with sqlite3.connect(self.ws.db) as db:
            schema(db)
        def respond(messages):
            context = json.loads(messages[-1]['content'])
            if context.get('stage') != 'maintain':
                raise AssertionError('Pure no-change must not request an author')
            with sqlite3.connect(self.ws.db) as db:
                db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason,role,status) VALUES (?,?,?,?,?)',
                           (slug, 'an-independent-point', 'A concurrently accepted maintenance decision', 'context', 'active'))
            changes.append(True)
            return {'decision_version': 'knowledge-maintenance/v1', 'point_decisions': [
                {'point_id': pid, 'disposition': 'linked', 'memberships': [
                    {'concept_slug': slug, 'role': 'background', 'reason': 'Adds supporting context without editing prose'}]}],
                'concept_actions': [{'concept_slug': slug, 'action': 'no_change', 'reason': 'Existing explanation is sufficient',
                                     'affected_point_ids': [pid]}], 'relation_actions': [], 'unresolved': []}
        try:
            result = self.run_pipeline(respond)
        except ValueError as error:
            self.assertIn('changed', str(error).lower())
            result = None
        if result is not None:
            self.assertEqual(result['status'], 'blocked', result)
        self.assertTrue(changes)
        self.assertEqual((self.ws.wiki / (slug + '.md')).read_text(), body)
        self.assertEqual(json.loads((self.ws.meta / (slug + '.json')).read_text()), metadata)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships WHERE point_id=?', (pid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT point_id FROM concept_memberships WHERE concept_slug=?', (slug,)).fetchall(), [('an-independent-point',)])
