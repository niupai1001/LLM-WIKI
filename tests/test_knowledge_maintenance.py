import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from station import knowledge_maintenance as km
from station.workspaces import Workspace
from station.jobs import JobConflict


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace('test', Path(self.tmp.name))
        self.points = {'p1': {'id': 'p1', 'source_id': 's1', 'source_version': 'v1', 'data': {'proposition': 'shared mechanism evidence', 'scope': 'conditional', 'uncertainty': 'limited'}}}
        self.existing = [{'slug': 'mechanism', 'title': 'mechanism', 'question': 'shared mechanism?', 'version': 'v1', 'point_ids': [], 'body': 'old knowledge', 'meta': {}}]
        self.value = {'decision_version': km.VERSION, 'point_decisions': [{'point_id': 'p1', 'disposition': 'linked', 'memberships': [{'concept_slug': 'mechanism', 'role': 'support', 'reason': 'provides mechanism evidence'}]}], 'concept_actions': [{'concept_slug': 'mechanism', 'action': 'update', 'reason': 'add conditional evidence', 'affected_point_ids': ['p1']}], 'relation_actions': [], 'unresolved': []}

    def validate(self, value=None):
        return km.validate_decision(value or self.value, self.points, self.existing, {'mechanism'}, 'digest')

    def plan(self, invoke):
        return km.plan_maintenance(self.ws, {}, self.points, self.existing, {'mechanism'}, invoke, 'offline')

    def test_multiple_memberships_are_independent_and_valid(self):
        self.existing.append({'slug': 'comparison', 'title': 'comparison', 'question': 'Compare?', 'version': 'v2', 'point_ids': []})
        self.value['point_decisions'][0]['memberships'].append({'concept_slug': 'comparison', 'role': 'contrast', 'reason': 'shows different conditions'})
        self.value['concept_actions'].append({'concept_slug': 'comparison', 'action': 'update', 'reason': 'contrast', 'affected_point_ids': ['p1']})
        self.assertEqual(len(self.validate()['point_decisions'][0]['memberships']), 2)
        self.value['point_decisions'][0]['memberships'].append(copy.deepcopy(self.value['point_decisions'][0]['memberships'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate membership'):
            self.validate()

    def test_unknown_ids_missing_coverage_and_conflicting_actions_fail(self):
        for mutate in (lambda v: v['point_decisions'][0].update(point_id='unknown'), lambda v: v.update(point_decisions=[]), lambda v: v['concept_actions'].append(copy.deepcopy(v['concept_actions'][0]))):
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.assertRaises(ValueError):
                self.validate(value)

    def test_digest_and_concept_version_are_validated(self):
        self.value['input_digest'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'version conflict'):
            self.validate()
        del self.value['input_digest']
        self.value['base_versions'] = {'mechanism': 'old'}
        with self.assertRaisesRegex(ValueError, 'base version conflict'):
            self.validate()

    def test_high_risk_actions_remain_pending_review(self):
        self.value['concept_actions'][0]['action'] = 'merge'
        self.assertTrue(self.validate()['concept_actions'][0]['requires_review'])
        result = self.plan(Mock(return_value=self.value))
        self.assertEqual(result['status'], 'blocked')
        with sqlite3.connect(self.ws.db) as db:
            with self.assertRaisesRegex(ValueError, 'Pending review'):
                km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'])

    def test_deferring_existing_published_membership_requires_review(self):
        points = {'p1': {'source_id': 's1', 'source_version': 'v1', 'data': {'claim_ids': ['c1']}}}
        existing = [{'slug': 'existing', 'point_ids': ['p1'], 'version': 'v1'}]
        value = {'decision_version': km.VERSION,
                 'point_decisions': [{'point_id': 'p1', 'disposition': 'deferred', 'memberships': [],
                                      'reason': 'New conditions no longer fit the old explanation.', 'reevaluate_when': ['Identify whether the source now describes the same object']}],
                 'concept_actions': [], 'relation_actions': [], 'unresolved': []}
        validated = km.validate_decision(value, points, existing, {'existing'})
        self.assertTrue(validated['unresolved'])
        self.assertEqual(validated['unresolved'][0]['concept_slug'], 'existing')

    def test_compatible_migration_preserves_old_membership(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute('CREATE TABLE concept_memberships (concept_slug TEXT,point_id TEXT,reason TEXT,PRIMARY KEY(concept_slug,point_id))')
            db.execute('INSERT INTO concept_memberships VALUES (?,?,?)', ('old', 'p0', 'historical'))
            km.schema(db)
            km.schema(db)
            self.assertEqual(db.execute('SELECT concept_slug,point_id,reason,status FROM concept_memberships').fetchone(), ('old', 'p0', 'historical', 'active'))

    def test_cache_and_persisted_retry_budget_survive_restart(self):
        invalid = Mock(return_value={})
        result = self.plan(invalid)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(invalid.call_count, 3)
        self.plan(invalid)
        self.assertEqual(invalid.call_count, 3)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT attempts FROM maintenance_decisions').fetchone()[0], 3)

    def test_transport_errors_propagate_and_preserve_attempt(self):
        with self.assertRaises(TimeoutError):
            self.plan(Mock(side_effect=TimeoutError('connection timed out')))
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT attempts FROM maintenance_decisions').fetchone()[0], 1)

    def test_job_conflict_propagates_without_consuming_retry_budget(self):
        with self.assertRaises(JobConflict):
            self.plan(Mock(side_effect=JobConflict('lease lost')))
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT attempts FROM maintenance_decisions').fetchone()[0], 0)

    def test_apply_replay_is_idempotent_and_does_not_dirty_index(self):
        invoke = Mock(return_value=self.value)
        result = self.plan(invoke)
        self.assertEqual(result['status'], 'ready')
        again = self.plan(invoke)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(again['decision_id'], result['decision_id'])
        with sqlite3.connect(self.ws.db) as db:
            for _ in range(2):
                km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'])
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT status FROM maintenance_decisions').fetchone()[0], 'applied')
        self.assertEqual(self.plan(invoke)['status'], 'no_change')
        self.assertEqual(invoke.call_count, 1)

    def test_deferred_persists_then_reenters_after_related_new_knowledge(self):
        self.value.update(point_decisions=[{'point_id': 'p1', 'disposition': 'deferred', 'memberships': [], 'reason': 'not enough evidence', 'reevaluate_when': ['Obtain original evidence for the conditional mechanism']}], concept_actions=[])
        result = self.plan(Mock(return_value=self.value))
        with sqlite3.connect(self.ws.db) as db:
            km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'], [])
        self.assertEqual(self.plan(Mock())['pending_point_ids'], [])
        self.points['p2'] = {**self.points['p1'], 'id': 'p2'}
        def answer(stage, system, context):
            return {**self.value, 'point_decisions': [{'point_id': pid, 'disposition': 'deferred', 'memberships': [], 'reason': 'needs stable question', 'reevaluate_when': ['Clarify whether the two mechanism objects are equivalent']} for pid in context['pending_point_ids']]}
        self.assertEqual(self.plan(answer)['pending_point_ids'], ['p1', 'p2'])

    def test_no_change_updates_membership_without_page_plan(self):
        self.value['concept_actions'][0]['action'] = 'no_change'
        result = self.plan(Mock(return_value=self.value))
        self.assertEqual(result['status'], 'no_change')
        with sqlite3.connect(self.ws.db) as db:
            km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'], ['mechanism'])
            self.assertEqual(db.execute('SELECT disposition FROM point_dispositions').fetchone()[0], 'linked')

    def test_existing_points_preserved_and_internal_semantics_reach_model(self):
        self.points['p0'] = {**self.points['p1'], 'id': 'p0'}
        self.existing[0]['point_ids'] = ['p0']
        self.value['point_decisions'].append({'point_id': 'p0', 'disposition': 'linked', 'memberships': [{'concept_slug': 'mechanism', 'role': 'supplementary', 'reason': 'preserves explanation'}]})
        invoke = Mock(return_value=self.value)
        result = self.plan(invoke)
        self.assertEqual(set(result['plan']['concepts'][0]['point_ids']), {'p0', 'p1'})
        point = invoke.call_args.args[2]['knowledge_points'][0]
        self.assertEqual(point['uncertainty'], 'limited')
        self.assertNotIn('data_json', point)

    def test_unrelated_source_does_not_reprocess_accepted_point(self):
        result = self.plan(Mock(return_value=self.value))
        with sqlite3.connect(self.ws.db) as db:
            km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'])
        self.points['p2'] = {'id': 'p2', 'source_id': 's2', 'source_version': 'v1', 'data': {'proposition': 'totally independent astronomy'}}
        invoke = Mock(return_value={**self.value, 'point_decisions': [{'point_id': 'p2', 'disposition': 'deferred', 'memberships': [], 'reason': 'The object being measured is not specified', 'reevaluate_when': ['Identify the measured object in the source evidence']}], 'concept_actions': []})
        result = self.plan(invoke)
        self.assertEqual(result['pending_point_ids'], ['p2'])
        self.assertEqual(result['candidate_slugs'], [])

    def test_transaction_rollback_keeps_dispositions_and_memberships_together(self):
        result = self.plan(Mock(return_value=self.value))
        with self.assertRaises(RuntimeError):
            with sqlite3.connect(self.ws.db) as db:
                km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'])
                raise RuntimeError('publication failed')
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM concept_memberships').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM point_dispositions').fetchone()[0], 0)

    def test_relation_requires_known_origin_target_and_evidence(self):
        self.points['p1']['data']['claim_ids'] = ['c1']
        self.existing.append({'slug': 'comparison', 'title': 'mechanism comparison', 'question': 'comparison mechanism?', 'version': 'v2', 'point_ids': []})
        relation = {'source_slug': 'mechanism', 'target_slug': 'comparison', 'kind': 'implementation', 'basis': 'factual', 'reason': 'mechanism implementation supported by evidence', 'claim_ids': ['c1'], 'source_ids': ['s1']}
        self.value['relation_actions'] = [relation]
        self.assertEqual(self.validate()['base_versions'], {'mechanism': 'v1', 'comparison': 'v2'})
        for change in ({'source_slug': 'unknown'}, {'target_slug': 'mechanism'}, {'claim_ids': ['invented']}, {'source_ids': ['invented']}, {'basis': 'unknown'}):
            value = copy.deepcopy(self.value)
            value['relation_actions'][0].update(change)
            with self.assertRaises(ValueError):
                self.validate(value)
        result = self.plan(Mock(return_value=self.value))
        self.assertEqual(result['plan']['concepts'][0]['relation_actions'][0]['source_slug'], 'mechanism')

    def test_relation_rejects_single_source_cross_study_and_unbacked_source(self):
        self.points['p1']['data']['claim_ids'] = ['c1']
        self.existing.append({'slug': 'comparison', 'version': 'v2', 'point_ids': [],
                              'meta': {'source_ids': ['s2'], 'claims': [{'id': 'c2', 'source_ids': ['s2']}]}})
        relation = {'source_slug': 'mechanism', 'target_slug': 'comparison', 'kind': 'shared_mechanism',
                    'basis': 'editorial', 'reason': 'Same operative mechanism', 'claim_ids': ['c1'], 'source_ids': ['s1']}
        self.value['relation_actions'] = [relation]
        with self.assertRaisesRegex(ValueError, 'at least two'):
            self.validate()
        relation['source_ids'] = ['s1', 's2']
        with self.assertRaisesRegex(ValueError, 'Every relation source'):
            self.validate()
        relation['claim_ids'].append('c2')
        refs = self.validate()['relation_actions'][0]['evidence_refs']
        self.assertEqual(refs, [{'source_id': 's1', 'claim_id': 'c1'}, {'source_id': 's2', 'claim_id': 'c2'}])
        relation['evidence_refs'] = [{'source_id': 's2', 'claim_id': 'c1'}]
        with self.assertRaisesRegex(ValueError, 'actual source claim'):
            self.validate()

    def test_relation_local_claim_collisions_require_explicit_qualified_references(self):
        self.points['p1']['data']['claim_ids'] = ['c1']
        self.existing.append({'slug': 'comparison', 'version': 'v2', 'point_ids': [], 'meta': {}})
        relation = {'source_slug': 'mechanism', 'target_slug': 'comparison', 'kind': 'contrast',
                    'basis': 'editorial', 'reason': 'Different mechanisms', 'claim_ids': ['c1'], 'source_ids': ['s1', 's2']}
        self.value['relation_actions'] = [relation]
        papers = {'s2': {'packet': {'claims': [{'id': 'c1'}]}}}
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            km.validate_decision(self.value, self.points, self.existing, {'mechanism'}, papers=papers)
        relation['evidence_refs'] = [{'source_id': sid, 'claim_id': 'c1'} for sid in ['s1', 's2']]
        result = km.validate_decision(self.value, self.points, self.existing, {'mechanism'}, papers=papers)
        self.assertEqual(result['relation_actions'][0]['evidence_refs'], relation['evidence_refs'])

    def test_relation_canonical_alias_and_existing_claim_resolve_to_actual_source_claims(self):
        import hashlib
        alias = 'sc_' + hashlib.sha256(b's2:c2').hexdigest()[:20]
        self.points['p1']['data']['claim_ids'] = ['c1']
        self.existing.append({'slug': 'comparison', 'version': 'v2', 'point_ids': [], 'meta': {
            'claims': [{'id': 'old-summary', 'source_ids': ['s1'],
                        'evidence_refs': [{'source_id': 's1', 'claim_id': 'c1'}]}]}})
        relation = {'source_slug': 'mechanism', 'target_slug': 'comparison', 'kind': 'contrast',
                    'basis': 'editorial', 'reason': 'Different mechanisms',
                    'claim_ids': ['old-summary', alias], 'source_ids': ['s1', 's2']}
        self.value['relation_actions'] = [relation]
        papers = {'s2': {'packet': {'claims': [{'id': 'c2'}]}}}
        result = km.validate_decision(self.value, self.points, self.existing, {'mechanism'}, papers=papers)
        self.assertEqual(result['relation_actions'][0]['evidence_refs'],
                         [{'source_id': 's1', 'claim_id': 'c1'}, {'source_id': 's2', 'claim_id': 'c2'}])

    def test_selected_source_and_source_version_change_are_incremental(self):
        result = self.plan(Mock(return_value=self.value))
        with sqlite3.connect(self.ws.db) as db:
            km.apply_decision(db, result['decision_id'], result['decision'], result['point_versions'])
        self.points['p1']['source_version'] = 'v2'
        invoke = Mock(return_value=self.value)
        changed = self.plan(invoke)
        self.assertEqual(changed['pending_point_ids'], ['p1'])
        excluded = km.plan_maintenance(self.ws, {}, self.points, self.existing, {'mechanism'}, invoke, 'offline', source_ids={'other'})
        self.assertEqual(excluded['pending_point_ids'], [])

    def test_prompt_upgrade_cannot_refresh_exhausted_semantic_budget(self):
        self.assertEqual(self.plan(Mock(return_value={}))['status'], 'blocked')
        invoke = Mock(return_value=self.value)
        result = km.plan_maintenance(self.ws, {}, self.points, self.existing, {'mechanism'}, invoke, 'upgraded-model')
        self.assertEqual(result['status'], 'blocked')
        invoke.assert_not_called()

    def test_maintenance_context_includes_original_evidence(self):
        self.points['p1']['data'].update(claim_ids=['c1'], result_ids=['r1'])
        papers = {'s1': {'source_version': 'v1', 'packet': {'claims': [{'id': 'c1', 'text': 'Conditional mechanism', 'evidence_passages': [{'quote': 'Original quoted evidence', 'source_id': 's1'}]}], 'results': [{'id': 'r1', 'claim_ids': ['c1'], 'text': 'Observed result'}]}}}
        invoke = Mock(return_value=self.value)
        result = km.plan_maintenance(self.ws, papers, self.points, self.existing, {'mechanism'}, invoke, 'offline')
        self.assertEqual(result['status'], 'ready')
        materials = invoke.call_args.args[2]['source_materials'][0]
        self.assertEqual(materials['originals'][0]['text'], 'Original quoted evidence')
        self.assertEqual(materials['claims'][0]['evidence_locations'][0]['original_id'], materials['originals'][0]['id'])
        self.assertEqual(materials['results'][0]['id'], 'r1')

    def test_shared_original_quotes_are_deduplicated(self):
        self.points['p1']['data']['claim_ids'] = ['c1', 'c2']
        shared = {'quote': 'A long shared original paragraph', 'paragraph_id': 'paragraph-1', 'source_id': 's1'}
        papers = {'s1': {'source_version': 'v1', 'packet': {'claims': [{'id': cid, 'text': cid, 'evidence_passages': [shared]} for cid in ['c1', 'c2']]}}}
        invoke = Mock(return_value=self.value)
        km.plan_maintenance(self.ws, papers, self.points, self.existing, {'mechanism'}, invoke, 'offline')
        materials = invoke.call_args.args[2]['source_materials'][0]
        self.assertEqual(len(materials['originals']), 1)
        self.assertEqual(json.dumps(materials).count(shared['quote']), 1)
        self.assertEqual([c['evidence_locations'][0]['quote_end'] for c in materials['claims']], [len(shared['quote'])] * 2)

    def test_legacy_membership_version_is_read_only_and_stable_after_migration(self):
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(km.membership_version(db, 'mechanism'), km.canonical_digest([]))
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
            db.execute('CREATE TABLE concept_memberships (concept_slug TEXT,point_id TEXT,reason TEXT,PRIMARY KEY(concept_slug,point_id))')
            db.execute('INSERT INTO concept_memberships VALUES (?,?,?)', ('mechanism', 'p1', 'legacy'))
            before = km.membership_version(db, 'mechanism')
            self.assertEqual(len(list(db.execute('PRAGMA table_info(concept_memberships)'))), 3)
            km.schema(db)
            self.assertEqual(km.membership_version(db, 'mechanism'), before)


    def test_legacy_roles_have_canonical_meaning_and_unknown_new_roles_fail(self):
        self.assertEqual(self.validate()['point_decisions'][0]['memberships'][0]['role'], 'supplementary')
        for old, expected in [('required', 'core'), ('background', 'related')]:
            self.value['point_decisions'][0]['memberships'][0]['role'] = old
            self.assertEqual(self.validate()['point_decisions'][0]['memberships'][0]['role'], expected)
        self.value['point_decisions'][0]['memberships'][0]['role'] = 'explain everything'
        with self.assertRaisesRegex(ValueError, 'Membership role must'):
            self.validate()

    def test_core_only_default_and_preserved_legacy_obligations(self):
        for pid, role in [('p0', 'background'), ('p2', 'required'), ('p3', 'preserved')]:
            self.points[pid] = {**self.points['p1'], 'id': pid}
            self.existing[0]['point_ids'].append(pid)
            self.existing[0].setdefault('assignment_reasons', []).append({'point_id': pid, 'role': role, 'reason': 'historical role'})
        self.points['p4'] = {**self.points['p1'], 'id': 'p4'}
        self.existing[0]['point_ids'].append('p4')
        self.existing[0]['meta']['required_point_ids'] = ['p4']
        self.value['point_decisions'][0]['memberships'][0]['role'] = 'core'
        validated = km.validate_decision(self.value, {'p1': self.points['p1']}, self.existing, {'mechanism'}, 'digest')
        plan = km._plan(validated, self.points, self.existing)['concepts'][0]
        self.assertEqual(set(plan['required_point_ids']), {'p1', 'p2', 'p4'})
        roles = {r['point_id']: r['role'] for r in plan['assignment_reasons']}
        self.assertEqual(roles['p0'], 'related')
        self.assertEqual(roles['p3'], 'preserved')
        self.assertEqual(roles['p4'], 'core')
        self.value['concept_actions'][0]['required_point_ids'] = ['p1']
        with self.assertRaisesRegex(ValueError, 'Core knowledge cannot'):
            km._plan(km.validate_decision(self.value, {'p1': self.points['p1']}, self.existing, {'mechanism'}, 'digest'), self.points, self.existing)

    def test_related_cannot_be_forced_into_required_body_coverage(self):
        self.value['point_decisions'][0]['memberships'][0]['role'] = 'related'
        self.value['concept_actions'][0]['required_point_ids'] = ['p1']
        with self.assertRaisesRegex(ValueError, 'Related knowledge'):
            km._plan(self.validate(), self.points, self.existing)

    def test_lowering_old_required_role_needs_review(self):
        self.existing[0]['point_ids'] = ['p1']
        self.existing[0]['meta']['required_point_ids'] = ['p1']
        self.assertIn('Downgrading', self.validate()['unresolved'][0]['reason'])

    def test_deferred_requires_explicit_nonempty_reassessment_condition(self):
        self.value.update(point_decisions=[{'point_id': 'p1', 'disposition': 'deferred', 'memberships': [],
                                           'reason': 'The source does not specify which measured object this mechanism acts on'}], concept_actions=[])
        for condition in [None, [], ['']]:
            self.value['point_decisions'][0]['reevaluate_when'] = condition
            with self.assertRaises(ValueError):
                self.validate()
        self.value['point_decisions'][0]['reevaluate_when'] = ['Recover the measured object from the original source']
        self.assertEqual(self.validate()['point_decisions'][0]['disposition'], 'deferred')

    def test_empty_candidates_allow_new_stable_concept_without_forcing_creation(self):
        self.value['concept_actions'][0].update(concept_slug='conditional-mechanism', action='create',
                                               title='Conditional mechanism', question='How does this mechanism act?')
        self.value['point_decisions'][0]['memberships'][0].update(concept_slug='conditional-mechanism', role='core')
        invoke = Mock(return_value=self.value)
        result = km.plan_maintenance(self.ws, {}, self.points, [], set(), invoke, 'offline')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['plan']['concepts'][0]['required_point_ids'], ['p1'])
        prompt = invoke.call_args.args[1]
        self.assertIn('not a deferral reason', prompt)
        self.assertIn('不要为覆盖率硬建页面', prompt)

    def test_raw_invalid_responses_are_saved_for_audit(self):
        self.plan(Mock(return_value={'invalid': 'raw response'}))
        with sqlite3.connect(self.ws.db) as db:
            attempts = db.execute('SELECT request_json,response_json,error FROM maintenance_attempts').fetchall()
            self.assertEqual(len(attempts), 3)
            self.assertTrue(all(json.loads(row[1]) == {'invalid': 'raw response'} for row in attempts))
            self.assertTrue(all(row[0] and row[2] for row in attempts))


if __name__ == '__main__':
    unittest.main()
