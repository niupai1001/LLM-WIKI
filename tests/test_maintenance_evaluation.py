"""Coverage semantics for maintenance, using disposable paper fixtures by composition."""
import json
import sqlite3
import unittest
from unittest.mock import Mock

from station import evaluation, knowledge_maintenance as maintenance, knowledge_synthesis
from tests import test_paper_evaluation as paper_fixtures


class MaintenanceEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = paper_fixtures.PaperEvaluationTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ws = self.fixture.ws
        self.pid = self.fixture.point_id

    def result(self):
        return evaluation._integrity(self.ws)

    def reasons(self, result):
        return {row['reason'] for row in result['unresolved']}

    def alter_meta(self, **changes):
        path = self.ws.meta / 'lease-authority.json'
        self.fixture.write(path, {**json.loads(path.read_text()), **changes})

    def apply(self, disposition='linked', action='no_change', required=None):
        papers, points, stale = knowledge_synthesis._inputs(self.ws)
        self.assertEqual(stale, [])
        existing, _ = knowledge_synthesis._existing(self.ws)
        point_decision = {'point_id': self.pid, 'disposition': disposition,
                          'memberships': [], 'reason': 'Retain valid knowledge until a stable question emerges.'}
        actions = []
        if disposition == 'linked':
            point_decision['memberships'] = [{'concept_slug': 'lease-authority', 'role': 'Explains how retained evidence supports commit authority.', 'reason': 'The existing explanation already conveys the conditional mechanism.'}]
            actions = [{'concept_slug': 'lease-authority', 'action': action, 'reason': 'Maintain the accepted explanation.', 'affected_point_ids': [self.pid]}]
            if required is not None:
                actions[0]['required_point_ids'] = required
        decision = {'decision_version': maintenance.VERSION, 'point_decisions': [point_decision],
                    'concept_actions': actions, 'relation_actions': [], 'unresolved': []}
        run = maintenance.plan_maintenance(self.ws, papers, points, existing, {'lease-authority', 'paper-reading'}, Mock(return_value=decision), 'synthetic')
        self.assertIn(run['status'], {'ready', 'no_change'})
        with sqlite3.connect(self.ws.db) as db:
            maintenance.apply_decision(db, run['decision_id'], run['decision'], run['point_versions'])
        return run

    def duplicate_membership(self):
        path = self.ws.wiki / 'lease-authority.md'
        (self.ws.wiki / 'second-authority.md').write_text(path.read_text())
        meta = json.loads((self.ws.meta / 'lease-authority.json').read_text())
        self.fixture.write(self.ws.meta / 'second-authority.json', {**meta, 'slug': 'second-authority'})
        with sqlite3.connect(self.ws.db) as db:
            db.execute('INSERT INTO concept_memberships (concept_slug,point_id,reason) VALUES (?,?,?)',
                       ('second-authority', self.pid, 'A distinct explanation of the same evidence.'))

    def test_two_valid_memberships_are_covered_independently(self):
        self.duplicate_membership()
        result = self.result()
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['knowledge_points']['routed'], 1)
        self.assertEqual(result['knowledge_points']['valid_memberships'], 2)
        self.assertEqual(result['knowledge_points']['active_memberships'], 2)
        self.assertNotIn('knowledge_point_multiply_routed', self.reasons(result))

    def test_second_membership_missing_page_or_binding_still_blocks(self):
        self.duplicate_membership()
        path = self.ws.wiki / 'second-authority.md'
        body = path.read_text()
        path.unlink()
        result = self.result()
        self.assertEqual(result['knowledge_points']['routed'], 0)
        self.assertIn('knowledge_point_target_not_current', self.reasons(result))
        path.write_text(body)
        meta_path = self.ws.meta / 'second-authority.json'
        meta = json.loads(meta_path.read_text())
        self.fixture.write(meta_path, {**meta, 'point_bindings': []})
        result = self.result()
        self.assertEqual(result['knowledge_points']['routed'], 0)
        self.assertIn('knowledge_point_body_or_claim_binding_invalid', self.reasons(result))

    def test_applied_deferred_is_processed_without_being_routed(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM concept_memberships')
        self.alter_meta(knowledge_point_ids=[], point_bindings=[])
        self.apply(disposition='deferred')
        result = self.result()
        self.assertEqual(result['unresolved'], [])
        points = result['knowledge_points']
        self.assertEqual((points['processed'], points['deferred'], points['unprocessed']), (1, 1, 0))
        self.assertEqual((points['routed'], points['unrouted']), (0, 1))
        report = self.fixture.evaluate()
        self.assertFalse(report['passed'])
        self.assertEqual(report['human_evaluation']['reviewed'], 0)
        self.assertIn('human_evaluation_missing_stale_or_below_threshold', report['blocked_reasons'])

    def test_deferred_requires_current_point_version_and_applied_decision(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM concept_memberships')
        self.alter_meta(knowledge_point_ids=[], point_bindings=[])
        run = self.apply(disposition='deferred')
        with sqlite3.connect(self.ws.db) as db:
            saved = db.execute('SELECT point_version_json FROM point_dispositions').fetchone()[0]
            stale = {**json.loads(saved), 'source_version': 'old'}
            db.execute('UPDATE point_dispositions SET point_version_json=?', (json.dumps(stale),))
        result = self.result()
        self.assertEqual(result['knowledge_points']['deferred'], 0)
        self.assertIn('deferred_decision_not_current_or_applied', self.reasons(result))
        with sqlite3.connect(self.ws.db) as db:
            db.execute('UPDATE point_dispositions SET point_version_json=?', (saved,))
            db.execute('UPDATE maintenance_decisions SET status=? WHERE id=?', ('ready', run['decision_id']))
        self.assertEqual(self.result()['knowledge_points']['processed'], 0)

    def test_no_change_membership_may_exceed_metadata_without_body_binding(self):
        self.apply()
        self.alter_meta(knowledge_point_ids=[], point_bindings=[])
        result = self.result()
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['knowledge_points']['routed'], 1)
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM maintenance_executions')
        result = self.result()
        self.assertEqual(result['knowledge_points']['routed'], 0)
        self.assertIn('concept_membership_metadata_mismatch', self.reasons(result))

    def test_explicit_background_selection_uses_action_not_role_keyword(self):
        self.apply(action='update', required=[])
        self.alter_meta(point_bindings=[], required_point_ids=[])
        result = self.result()
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['knowledge_points']['valid_memberships'], 1)

    def test_partially_ready_decision_requires_this_pair_execution(self):
        run = self.apply()
        self.alter_meta(knowledge_point_ids=[], point_bindings=[])
        with sqlite3.connect(self.ws.db) as db:
            db.execute('UPDATE maintenance_decisions SET status=? WHERE id=?', ('ready', run['decision_id']))
        self.assertEqual(self.result()['knowledge_points']['routed'], 1)
        with sqlite3.connect(self.ws.db) as db:
            db.execute("UPDATE concept_memberships SET role='A forged role absent from the decision'")
        self.assertEqual(self.result()['knowledge_points']['routed'], 0)

    def test_role_text_alone_cannot_waive_legacy_binding(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute("UPDATE concept_memberships SET role='background'")
        self.alter_meta(point_bindings=[])
        result = self.result()
        self.assertEqual(result['knowledge_points']['processed'], 0)
        self.assertIn('knowledge_point_body_or_claim_binding_invalid', self.reasons(result))

    def test_pending_review_membership_is_not_current_routing(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute("UPDATE concept_memberships SET status='pending_review'")
        result = self.result()
        self.assertEqual(result['knowledge_points']['routed'], 0)
        self.assertEqual(result['knowledge_points']['pending_review_memberships'], 1)
        self.assertIn('membership_pending_review_or_inactive', self.reasons(result))

    def test_agent_disposition_without_durable_decision_cannot_count(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute('DELETE FROM concept_memberships')
            db.execute('INSERT INTO point_dispositions (point_id,disposition,reason,point_version_json,decision_id) VALUES (?,?,?,?,?)',
                       (self.pid, 'deferred', 'Agent says handled', '{}', 'nonexistent'))
        self.alter_meta(knowledge_point_ids=[], point_bindings=[])
        result = self.result()
        self.assertEqual(result['knowledge_points']['processed'], 0)
        self.assertIn('deferred_decision_not_current_or_applied', self.reasons(result))


if __name__ == '__main__':
    unittest.main()
