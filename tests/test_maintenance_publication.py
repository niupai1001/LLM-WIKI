"""Publication boundary tests use isolated libraries and no model requests."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from station import paper_rebuild, publication, wiki
from station.workspaces import Workspace, use_workspace


class MaintenancePublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ws = Workspace('publication-test', Path(temporary.name))
        self.ws.wiki.mkdir()
        self.ws.meta.mkdir()
        paper_rebuild.initialize(self.ws)
        with sqlite3.connect(self.ws.db) as db:
            db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)', ('kp-one', 'source', 'one', 'v1', '{"text":"one"}'))
        self.patch(paper_rebuild, 'source_version', return_value='v1')
        self.patch(paper_rebuild, '_records', side_effect=lambda ws, claims, ids: (claims, {}))
        self.patch(paper_rebuild, '_review_records', return_value=[])
        self.patch(wiki, '_require_quoted_changes', return_value=None)

    def patch(self, *args, **kwargs):
        context = patch.object(*args, **kwargs)
        self.addCleanup(context.stop)
        return context.start()

    def proposal(self, slug='alpha'):
        return {'slug': slug, 'title': slug.title(), 'body_markdown': 'A concrete answer.',
                'source_ids': ['source'], 'input_versions': {'source': 'v1'},
                'claims': [], 'semantic_reviews': [], 'point_ids': ['kp-one'],
                'point_bindings': [{'point_id': 'kp-one', 'body_quote': 'concrete answer', 'claim_ids': []}],
                'claim_bindings': [], 'base_version': None,
                'topic_routes': [{'slug': 'learning', 'title': 'Learning topics',
                                  'overview': 'Explore durable learning questions.', 'question': 'How does this answer work?'}]}

    def test_metadata_edit_blocks_publication_before_replacement(self):
        proposal = self.proposal()
        paper_rebuild.publish_concept(self.ws, proposal, proposal['point_ids'])
        with use_workspace(self.ws):
            proposal['base_version'] = wiki.page_version(self.ws.wiki / 'alpha.md')
        meta = self.ws.meta / 'alpha.json'
        value = json.loads(meta.read_text())
        value['human_note'] = 'A later editorial decision'
        meta.write_text(json.dumps(value))
        before = (self.ws.wiki / 'alpha.md').read_bytes()
        with self.assertRaisesRegex(ValueError, 'body or metadata changed'):
            paper_rebuild.publish_concept(self.ws, proposal, proposal['point_ids'])
        self.assertEqual((self.ws.wiki / 'alpha.md').read_bytes(), before)
        self.assertEqual(json.loads(meta.read_text())['human_note'], value['human_note'])

    def test_changed_point_content_rolls_back_whole_group(self):
        proposals = [self.proposal('alpha'), self.proposal('beta')]
        snapshot = {'kp-one': {'source_id': 'source', 'source_version': 'v1', 'data_digest': 'old'}}
        for proposal in proposals:
            proposal['point_versions'] = snapshot
        with self.assertRaisesRegex(ValueError, 'point content'):
            paper_rebuild.publish_concepts(self.ws, proposals)
        self.assertFalse((self.ws.wiki / 'alpha.md').exists())
        self.assertFalse((self.ws.wiki / 'beta.md').exists())
        self.assertFalse((self.ws.wiki / 'learning.md').exists())
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships').fetchone()[0], 0)

    def test_group_merges_shared_topic_and_index_and_permits_group_relation(self):
        first, second = self.proposal('alpha'), self.proposal('beta')
        claim = {'id': 'fact', 'source_ids': ['source'], 'evidence_passages': [{'source_id': 'source'}]}
        first['claims'] = [claim]
        first['claim_bindings'] = [{'claim_id': 'fact', 'body_quote': 'concrete answer'}]
        first['relations'] = [{'id': 'implementation', 'target_slug': 'beta', 'kind': 'implementation',
            'summary': 'This implementation provides a concrete mechanism.', 'scope': 'Within the stated setting.',
            'claim_ids': ['fact'], 'source_ids': ['source']}]
        results = paper_rebuild.publish_concepts(self.ws, [first, second])
        self.assertEqual(len(results), 2)
        topic = (self.ws.wiki / 'learning.md').read_text()
        index = (self.ws.wiki / 'index.md').read_text()
        for slug in ('alpha', 'beta'):
            self.assertIn('[[' + slug + '|', topic)
            self.assertIn('[[' + slug + '|', index)
        entries = json.loads((self.ws.meta / 'learning.json').read_text())['topic_navigation']['entries']
        self.assertEqual({entry['slug'] for entry in entries}, {'alpha', 'beta'})

    def test_failure_after_first_file_replacement_restores_files_and_memberships(self):
        original_replace = publication.os.replace
        failed = []

        def fail_once(source, target):
            if Path(target).name == 'beta.md' and not failed:
                failed.append(True)
                raise OSError('Injected group replacement failure')
            return original_replace(source, target)

        with patch.object(publication.os, 'replace', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'group replacement failure'):
                paper_rebuild.publish_concepts(self.ws, [self.proposal('alpha'), self.proposal('beta')])
        self.assertTrue(failed)
        self.assertFalse((self.ws.wiki / 'alpha.md').exists())
        self.assertFalse((self.ws.wiki / 'beta.md').exists())
        self.assertFalse((self.ws.wiki / 'index.md').exists())
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT state FROM publication_journal').fetchone()[0], 'rolled_back')

    def test_verified_decision_controls_roles_and_background_memberships(self):
        from station.knowledge_maintenance import point_version
        snapshot = {'kp-one': point_version({'source_id': 'source', 'source_version': 'v1', 'data': {'text': 'one'}})}
        decision = {'point_decisions': [{'point_id': 'kp-one', 'disposition': 'linked', 'memberships': [
            {'concept_slug': 'alpha', 'role': 'core', 'reason': 'Explains the central question'},
            {'concept_slug': 'beta', 'role': 'background', 'reason': 'Provides necessary context'}]}],
            'concept_actions': [{'concept_slug': slug, 'action': 'create'} for slug in ('alpha', 'beta')]}
        first, second = self.proposal('alpha'), self.proposal('beta')
        first['point_bindings'][0]['reason'] = 'This binding reason must not become membership evidence'
        second.update(required_point_ids=[], point_bindings=[])
        first.update(presentation_policy='wiki-synthesis-reader', synthesis_reviews=[{'knowledge': {'passed': True}}])
        paper_rebuild.publish_concepts(self.ws, [first, second], decision_id='decision-one',
                                       decision=decision, point_versions=snapshot)
        with sqlite3.connect(self.ws.db) as db:
            rows = db.execute('SELECT concept_slug,role,reason FROM concept_memberships ORDER BY concept_slug').fetchall()
            self.assertEqual(rows, [('alpha', 'core', 'Explains the central question'),
                                    ('beta', 'background', 'Provides necessary context')])
            self.assertEqual(db.execute('SELECT count(*) FROM maintenance_executions').fetchone()[0], 2)
        metadata = json.loads((self.ws.meta / 'alpha.json').read_text())
        self.assertEqual(metadata['maintenance_decision_id'], 'decision-one')
        self.assertEqual(metadata['presentation_policy'], 'wiki-synthesis-reader')
        self.assertEqual(metadata['synthesis_reviews'], first['synthesis_reviews'])

    def test_redistillation_preserves_removed_point_history_and_marks_existing_memberships(self):
        from station.knowledge_maintenance import schema
        sid = '1111111111111111'
        removed, changed = (paper_rebuild.global_point_id(sid, local) for local in ('removed', 'changed'))
        self.ws.raw.mkdir()
        (self.ws.raw / (sid + '.json')).write_text(json.dumps({'title': 'Archived source'}))
        with sqlite3.connect(self.ws.db) as db:
            schema(db)
            db.execute('CREATE TABLE sources (id TEXT PRIMARY KEY, stage TEXT, claim_at TEXT)')
            db.execute('INSERT INTO sources(id) VALUES (?)', (sid,))
            for identifier, local in ((removed, 'removed'), (changed, 'changed')):
                db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)', (identifier, sid, local, 'v0', '{}'))
                db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason) VALUES (?,?,?)', ('existing', identifier, 'Accepted knowledge'))
        packet = {'source': {'source_id': sid, 'source_version': 'v1'}, 'body_markdown': 'Reader prose [S:' + sid + ']',
                  'claims': [], 'knowledge_points': [{'id': 'changed', 'proposition': 'Updated understanding'}]}
        with patch.object(paper_rebuild, 'exclusion_reason', return_value=''):
            paper_rebuild.publish_packet(self.ws, packet, [])
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT status FROM concept_memberships').fetchall(), [('pending_review',), ('pending_review',)])
            row = db.execute('SELECT old_json,new_json FROM knowledge_point_changes WHERE point_id=?', (removed,)).fetchone()
            self.assertEqual(row, ('{}', None))
            self.assertEqual(db.execute('SELECT count(*) FROM knowledge_point_changes WHERE point_id=?', (changed,)).fetchone()[0], 1)

    def test_membership_edit_blocks_publication_even_when_page_is_unchanged(self):
        from station.knowledge_maintenance import membership_version
        proposal = self.proposal()
        with sqlite3.connect(self.ws.db) as db:
            proposal['base_membership_version'] = membership_version(db, proposal['slug'])
            db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason,role,status) VALUES (?,?,?,?,?)',
                       (proposal['slug'], 'kp-one', 'An independent accepted decision', 'background', 'active'))
        with self.assertRaisesRegex(ValueError, 'memberships changed'):
            paper_rebuild.publish_concept(self.ws, proposal, proposal['point_ids'])
        self.assertFalse((self.ws.wiki / 'alpha.md').exists())

    def test_existing_database_only_background_membership_is_preserved_in_metadata(self):
        from station.knowledge_maintenance import point_version
        with sqlite3.connect(self.ws.db) as db:
            db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason) VALUES (?,?,?)',
                       ('alpha', 'kp-one', 'Background accepted by an earlier no-change decision'))
        proposal = self.proposal()
        proposal.update(required_point_ids=[], point_bindings=[], maintenance_decision_id='later-decision',
                        maintenance_decision={'point_decisions': [], 'concept_actions': [{'concept_slug': 'alpha', 'action': 'update'}]},
                        point_versions={'kp-one': point_version({'source_id': 'source', 'source_version': 'v1', 'data': {'text': 'one'}})})
        paper_rebuild.publish_concept(self.ws, proposal, proposal['point_ids'])
        self.assertEqual(json.loads((self.ws.meta / 'alpha.json').read_text())['knowledge_point_ids'], ['kp-one'])

    def test_invalid_relation_target_is_rejected(self):
        proposal = self.proposal()
        proposal['relations'] = [{'id': 'missing', 'target_slug': 'absent', 'kind': 'implementation'}]
        with self.assertRaisesRegex(ValueError, '目标无效'):
            paper_rebuild.publish_concept(self.ws, proposal, proposal['point_ids'])
        self.assertFalse((self.ws.wiki / 'alpha.md').exists())

    def test_new_target_requires_absent_body_and_metadata(self):
        (self.ws.meta / 'alpha.json').write_text('{}')
        proposal = self.proposal()
        # An orphan metadata record must not be mistaken for an empty new target.
        with self.assertRaisesRegex(ValueError, 'target'):
            paper_rebuild.publish_concepts(self.ws, [proposal])


if __name__ == '__main__':
    unittest.main()
