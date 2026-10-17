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
    def test_context_does_not_forward_point_labels_or_old_review_commentary(self):
        secret = 'UNWANTED-FIELD-CONTENT'
        point = {'source_id': '0123456789abcdef', 'data': {'proposition': '有意义的知识命题', 'claim_ids': ['c'],
            **{k: secret for k in ['scope', 'type', 'explanation', 'uncertainty', 'transferability']}}}
        papers = {'0123456789abcdef': {'source_version': 'v1', 'record': {'title': '论文'}, 'reviews_json': secret,
            'packet': {'body_markdown': '完整论文论证', 'claims': [{'id': 'c', 'text': '支持事实',
            'scope': secret, 'status': secret, 'evidence_passages': [{'source_id': '0123456789abcdef',
            'quote': '原文实际语句', 'locator': {'page': 1}, 'confidence': secret}]}]}}}
        with patch.object(trial, '_inputs', return_value=(papers, {'p': point}, [])), patch('station.knowledge_navigation._reviewed'):
            ctx, _, _ = trial.context_for(None, {'sources': ['0123456789abcdef'], 'title': '主题', 'question': '问题', 'pages': []})
        self.assertNotIn(secret, str(ctx))
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

    def test_trial_preflights_every_entry_before_any_file_is_published(self):
        scope, draft, claims, points = self.fixture()
        scope.update(title='主题实验', topic='topic', question='本主题解释什么')
        scope['pages'].append({'slug': 'second', 'title': '第二概念'})
        second = copy.deepcopy(draft['pages'][0])
        second.update(slug='second', title='第二概念')
        draft['pages'].append(second)
        draft['topic_body'] += '[[second|第二概念]]'
        ctx = {'sources': [{'source_id': '0123456789abcdef', 'source_version': 'snapshot-version'}]}
        identity = {'library_id': 'test', 'context_digest': digest(ctx), 'skill_hash': digest(trial.SKILL.read_text()),
                    'prompt_hash': digest([trial.AUTHOR, trial.REVIEW])}
        checked = {'pages': [{'slug': p['slug'], 'claim_reviews': [{'claim_id': '0123456789abcdef:c',
            'status': 'supported', 'reason': '原文支持。'}], 'issues': []} for p in draft['pages']], 'topic_issues': []}
        with tempfile.TemporaryDirectory() as folder:
            ws = Workspace('test', Path(folder))
            save_json(ws.root / 'result.json', {'status': 'ready', 'identity': identity, 'draft': draft, 'check': checked})
            with patch.dict(trial.SCOPES, {'test': scope}), patch.object(trial, 'context_for', return_value=(ctx, claims, points)), \
                 patch('station.paper_rebuild.publish_concept', side_effect=[{'changes': {}, 'database_callback': None}, ValueError('second entry invalid')]) as prepare, \
                 patch('station.publication.publish') as publish:
                with self.assertRaisesRegex(ValueError, 'second entry invalid'):
                    trial.publish_trial(ws, 'test', ws.root)
                self.assertEqual(prepare.call_args_list[0].args[1]['input_versions'], {'0123456789abcdef': 'snapshot-version'})
                self.assertTrue(prepare.call_args_list[0].kwargs['prepare_only'])
                publish.assert_not_called()

    def test_restart_reuses_attempts_instead_of_refreshing_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = Workspace('test', Path(folder))
            scope = {'title': '试验', 'sources': ['0123456789abcdef'], 'pages': []}
            calls = []
            def model(messages):
                calls.append(messages)
                return {'pages': [], 'topic_body': ''}
            with patch.dict(trial.SCOPES, {'test': scope}), patch.object(trial, 'context_for', return_value=({}, {}, {})):
                _, result = trial.run_trial(ws, 'test', model=model)
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(len(calls), 3)
                _, repeated = trial.run_trial(ws, 'test', model=model)
                self.assertEqual(repeated['status'], 'blocked')
                self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
