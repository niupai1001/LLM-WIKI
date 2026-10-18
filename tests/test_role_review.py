"""Role-aware semantic review cannot confuse optional detail with a core omission."""
import copy
import unittest
from unittest.mock import patch
from station import knowledge_synthesis as synthesis


class RoleReviewTests(unittest.TestCase):
    def setUp(self):
        self.sid = 'a' * 16
        self.body = '该机制在指定任务条件下利用反馈改进行为。'
        self.points = {pid: {'id': pid, 'local_id': pid, 'source_id': self.sid,
            'data': {'proposition': '核心反馈机制。' if pid == 'core' else '可选实现细节。',
                     'claim_ids': ['c'], 'scope': '指定任务条件。'}} for pid in ('core', 'optional')}
        self.originals = [{'source_id': self.sid, 'parsed_version': 'v',
            'paragraphs': [{'id': 'p1', 'text': self.body, 'page': 1}]}]
        self.proposal = {'slug': 'feedback', 'body_markdown': self.body, 'claims': [
            {'id': 'c', 'text': self.body, 'scope': '指定任务条件。', 'source_ids': [self.sid], 'evidence_passages': []}]}
        self.papers = {self.sid: {'packet': {'claims': [], 'results': []}}}
        self.reply = {'reviews': [{'point_id': 'core', 'verdict': 'covered', 'reason': '核心认识准确。'},
            {'point_id': 'optional', 'verdict': 'partial', 'reason': '只选择相关细节，已写内容准确。'}],
            'issues': [], 'style_suggestions': []}

    def run_review(self):
        with patch.object(synthesis, '_invoke', return_value=copy.deepcopy(self.reply)) as invoke:
            result = synthesis._point_review(None, self.proposal, self.points, self.papers, self.originals,
                None, required={'core'}, roles=[], previous_body='旧解释仍有适用条件。')
            self.assertEqual(invoke.call_args.args[2]['previous_body'], '旧解释仍有适用条件。')
            return result

    def issue(self, category='fact_error', point='optional'):
        return {'kind': category, 'severity': 'blocking', 'body_quote': self.body,
            'reason': '原文条件与当前表达不一致。', 'point_ids': [point], 'source_ids': [self.sid],
            'evidence': [{'source_id': self.sid, 'paragraph_id': 'p1'}]}

    def test_accurate_selective_optional_expansion_is_not_a_core_omission(self):
        self.assertTrue(self.run_review()['passed'])

    def test_optional_content_actual_error_still_blocks(self):
        self.reply['issues'] = [self.issue()]
        self.assertFalse(self.run_review()['passed'])

    def test_missing_core_requires_a_located_issue_not_a_vague_verdict(self):
        self.reply['reviews'][0]['verdict'] = 'omitted'
        with self.assertRaisesRegex(ValueError, 'located blocking issue'):
            self.run_review()
        self.reply['issues'] = [self.issue('knowledge_omission', 'core')]
        self.assertFalse(self.run_review()['passed'])

    def test_optional_omission_cannot_force_full_exposition(self):
        self.reply['issues'] = [self.issue('knowledge_omission')]
        with self.assertRaisesRegex(ValueError, 'Optional knowledge'):
            self.run_review()
