import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station import wiki_experiment as trial
from station.compiler import digest
from station.common import save_json
from station.workspaces import Workspace


class WikiExperimentTests(unittest.TestCase):
    def test_context_preserves_internal_semantics_without_review_commentary(self):
        secret = 'UNWANTED-REVIEW-CONTENT'
        semantics = 'REQUIRED-INTERNAL-SEMANTICS'
        point = {'source_id': '0123456789abcdef', 'data': {'proposition': '有意义的知识命题', 'claim_ids': ['c'],
            **{k: semantics for k in ['scope', 'type', 'explanation', 'uncertainty', 'transferability']}}}
        papers = {'0123456789abcdef': {'source_version': 'v1', 'record': {'title': '论文'}, 'reviews_json': secret,
            'packet': {'body_markdown': '完整论文论证', 'claims': [{'id': 'c', 'text': '支持事实',
            'scope': semantics, 'status': secret, 'evidence_passages': [{'source_id': '0123456789abcdef',
            'quote': '原文实际语句', 'locator': {'page': 1}, 'confidence': secret}]}]}}}
        with patch.object(trial, '_inputs', return_value=(papers, {'p': point}, [])), patch('station.knowledge_navigation._reviewed'):
            ctx, _, _ = trial.context_for(None, {'sources': ['0123456789abcdef'], 'title': '主题', 'question': '问题', 'pages': []})
        self.assertNotIn(secret, str(ctx))
        for key in ['scope', 'type', 'explanation', 'uncertainty', 'transferability']:
            self.assertEqual(ctx['sources'][0]['points'][0][key], semantics)
        self.assertIn('完整论文论证', str(ctx))
        self.assertIn('有意义的知识命题', str(ctx))
        self.assertIn('原文实际语句', str(ctx))

    def fixture(self):
        scope = {'sources': ['0123456789abcdef'], 'pages': [{'slug': 'concept', 'title': '概念'}]}
        draft = {'pages': [{'slug': 'concept', 'title': '概念',
            'body_markdown': '# 概念\n\n' + '这是一段解释概念如何起作用的完整文字。' * 40 + '\n## 机制\n支持事实。[S:0123456789abcdef]',
            'claim_bindings': [{'claim_ref': '0123456789abcdef:c', 'body_quote': '支持事实。'}],
            'point_bindings': [{'point_id': 'p', 'body_quote': '支持事实。', 'claim_refs': ['0123456789abcdef:c']}]}],
            'topic_body': '阅读路径解释。' * 20 + '[[concept|概念]]'}
        points = {'p': {'source_id': '0123456789abcdef', 'data': {'claim_ids': ['c']}}}
        return scope, draft, {'0123456789abcdef:c': {}}, points

    def test_unrelated_source_and_body_binding_are_rejected(self):
        scope, draft, claims, points = self.fixture()
        trial.validate_draft(draft, scope, claims, points)
        changed = copy.deepcopy(draft)
        changed['pages'][0]['point_bindings'][0]['body_quote'] = '正文里并没有这句话'
        with self.assertRaises(ValueError):
            trial.validate_draft(changed, scope, claims, points)
        changed = copy.deepcopy(draft)
        changed['pages'][0]['body_markdown'] += '[S:other-library]'
        with self.assertRaises(ValueError):
            trial.validate_draft(changed, scope, claims, points)

    def test_incomplete_independent_check_never_counts_as_ready(self):
        _, draft, _, _ = self.fixture()
        with self.assertRaises(ValueError):
            trial.validate_review(draft, {'pages': [{'slug': 'concept', 'claim_reviews': [], 'issues': []}]})

    def trial_fixture(self, folder):
        ws = Workspace('test', Path(folder))
        scope = {'title': '主题实验', 'topic': 'topic', 'question': '本主题解释什么',
                 'sources': ['0123456789abcdef'], 'pages': []}
        context = {'sources': [{'source_id': '0123456789abcdef', 'source_version': 'v1'}]}
        return ws, scope, context

    def test_run_trial_uses_shared_pipeline_without_publishing(self):
        with tempfile.TemporaryDirectory() as folder:
            ws, scope, context = self.trial_fixture(folder)
            model = object()
            synthesis = {'status': 'completed', 'maintenance': {'decision_id': 'durable-decision'}}
            with patch.dict(trial.SCOPES, {'test': scope}), patch.object(trial, 'context_for', return_value=(context, {}, {})), \
                 patch('station.knowledge_synthesis.synthesize_library', return_value=synthesis) as shared, \
                 patch('station.publication.publish') as publish:
                root, result = trial.run_trial(ws, 'test', model=model)
                shared.assert_called_once_with(ws, model=model, source_ids=scope['sources'], publish_pages=False)
                publish.assert_not_called()
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(result['identity']['protocol'], trial.PROTOCOL)
                self.assertEqual(result['synthesis'], synthesis)
                self.assertEqual(result['human_acceptance'], 'not_performed')
                self.assertTrue((root / 'result.json').is_file())

    def test_ready_trial_publishes_by_reusing_shared_prepared_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            ws, scope, context = self.trial_fixture(folder)
            prepared = {'decision_id': 'durable-decision', 'input_digest': 'snapshot'}
            synthesis = {'status': 'completed', 'maintenance': prepared}
            with patch.dict(trial.SCOPES, {'test': scope}), patch.object(trial, 'context_for', return_value=(context, {}, {})), \
                 patch('station.knowledge_synthesis.synthesize_library', side_effect=[synthesis, {'status': 'completed'}]) as shared:
                root, _ = trial.run_trial(ws, 'test')
                self.assertEqual(trial.publish_trial(ws, 'test', root), {'status': 'completed'})
                self.assertEqual(shared.call_count, 2)
                self.assertEqual(shared.call_args.kwargs, {'source_ids': scope['sources'], 'prepared_run': prepared})

    def test_historical_result_cannot_publish_under_shared_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            ws, scope, _ = self.trial_fixture(folder)
            _, draft, _, _ = self.fixture()
            save_json(ws.root / 'result.json', {'status': 'ready', 'identity': {'library_id': 'test'}, 'draft': draft})
            with patch.dict(trial.SCOPES, {'test': scope}), patch('station.knowledge_synthesis.synthesize_library') as shared:
                with self.assertRaisesRegex(ValueError, 'Skill protocol'):
                    trial.publish_trial(ws, 'test', ws.root)
                shared.assert_not_called()

    def test_input_change_after_trial_prevents_any_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            ws, scope, context = self.trial_fixture(folder)
            synthesis = {'status': 'completed', 'maintenance': {'decision_id': 'durable'}}
            with patch.dict(trial.SCOPES, {'test': scope}), patch.object(trial, 'context_for', return_value=(context, {}, {})) as inputs, \
                 patch('station.knowledge_synthesis.synthesize_library', return_value=synthesis) as shared:
                root, _ = trial.run_trial(ws, 'test')
                inputs.return_value = ({'sources': [{'source_version': 'changed'}]}, {}, {})
                with self.assertRaisesRegex(ValueError, 'inputs changed'):
                    trial.publish_trial(ws, 'test', root)
                self.assertEqual(shared.call_count, 1)

    def test_blocked_trial_reuses_shared_budget_state(self):
        with tempfile.TemporaryDirectory() as folder:
            ws, scope, context = self.trial_fixture(folder)
            blocked = {'status': 'blocked', 'maintenance': {'decision_id': 'same-operation', 'status': 'blocked',
                       'reasons': ['Maintenance request budget exhausted']}}
            with patch.dict(trial.SCOPES, {'test': scope}), patch.object(trial, 'context_for', return_value=(context, {}, {})), \
                 patch('station.knowledge_synthesis.synthesize_library', return_value=blocked) as shared:
                root, first = trial.run_trial(ws, 'test')
                second_root, second = trial.run_trial(ws, 'test')
                self.assertEqual(first['status'], 'blocked')
                self.assertEqual(second['synthesis'], first['synthesis'])
                self.assertEqual(root, second_root)
                self.assertEqual(shared.call_count, 2)
                self.assertTrue(all(call.kwargs['publish_pages'] is False for call in shared.call_args_list))
                with self.assertRaises(ValueError):
                    trial.publish_trial(ws, 'test', root)
                self.assertEqual(shared.call_count, 2)


if __name__ == '__main__':
    unittest.main()
