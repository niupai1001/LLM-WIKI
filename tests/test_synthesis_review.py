"""L3 review issue contracts remain strict without treating advice as a veto."""
import copy
import unittest
from unittest.mock import patch

from station.compiler import _review


class KnowledgeReviewContractTests(unittest.TestCase):
    def setUp(self):
        self.proposal = {'body_markdown': '# Topic\nThe measured result was 60% in task A.',
                         'claims': [{'id': 'c1', 'text': 'The measured result was 60%.', 'source_ids': ['s1']}]}
        self.originals = [{'source_id': 's1', 'parsed_version': 'v1',
                           'paragraphs': [{'id': 'p1', 'text': 'Task A achieved 60%; task B achieved 50%.'}]}]
        self.assessment = {'reviews': [{'claim_id': 'c1', 'verdict': 'supported',
                                       'reason': 'Task A agrees with the archived result.', 'source_ids': ['s1']}],
                           'issues': [], 'style_suggestions': []}

    def review(self, assessment=None, old=None, protocol=True):
        with patch('station.compiler._request', return_value=copy.deepcopy(assessment or self.assessment)) as request:
            result = _review(self.proposal, old or {}, self.originals, None,
                             request_context={'review_protocol': 'knowledge-review/v2'} if protocol else {})
            return result, request.call_count

    def issue(self, **changes):
        value = {'kind': 'fact_error', 'severity': 'blocking', 'body_quote': '60% in task A',
                 'reason': 'Task identity changes the result.', 'claim_ids': ['c1'], 'source_ids': ['s1'],
                 'evidence': [{'source_id': 's1', 'quote': 'task B achieved 50%'}]}
        value.update(changes)
        return value

    def test_explicit_advice_is_retained_without_veto(self):
        self.assessment['issues'] = [self.issue(kind='expression', severity='advisory', evidence=[],
                                              reason='Consider a shorter sentence.')]
        result, calls = self.review()
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['issues']), 1)
        self.assertEqual(result['body_issues'], [])
        self.assertEqual(calls, 1)

    def test_review_selects_body_paragraphs_without_copying_or_rewriting_text(self):
        issue = self.issue()
        issue.pop('body_quote')
        issue.update(body_paragraph_ids=['b0001'], body_location='current')
        self.assessment['issues'] = [issue]
        result, calls = self.review()
        self.assertFalse(result['passed'])
        self.assertEqual(result['issues'][0]['body_quote'], 'The measured result was 60% in task A.')
        self.assertEqual(calls, 1)
        issue['body_paragraph_ids'] = ['invented']
        with self.assertRaisesRegex(ValueError, 'unknown body paragraph'):
            self.review()

    def test_previous_paragraph_catalogue_uses_same_normalized_body_as_resolver(self):
        old = {'body_markdown': '# Prior heading\n\nOld boundary is restricted to task A.',
               'claims': [{'id': 'old1', 'text': 'Old boundary is restricted to task A.'}]}
        self.assessment['issues'] = [self.issue(kind='knowledge_omission', claim_ids=['old1'],
            source_ids=[], evidence=[{'previous_claim_id': 'old1'}], body_paragraph_ids=['b0001'],
            body_location='previous', body_quote='Old boundary is restricted to task A.')]
        self.assertFalse(self.review(old=old)[0]['passed'])

    def test_archived_paragraph_identity_extracts_evidence_without_model_copying(self):
        self.assessment['issues'] = [self.issue(evidence=[{'source_id': 's1', 'paragraph_id': 'p1'}])]
        result, calls = self.review()
        self.assertFalse(result['passed'])
        self.assertEqual(result['issues'][0]['evidence'][0]['quote'], self.originals[0]['paragraphs'][0]['text'])
        self.assertEqual(calls, 1)
        self.assessment['issues'][0]['evidence'][0]['paragraph_id'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'unknown paragraph'):
            self.review()

    def test_real_classified_errors_still_block(self):
        for kind in ('fact_error', 'mechanism_merge', 'relation_error', 'contract_error'):
            with self.subTest(kind=kind):
                self.assessment['issues'] = [self.issue(kind=kind)]
                result, _ = self.review()
                self.assertFalse(result['passed'])
                self.assertEqual(result['body_issues'][0]['kind'], kind)

    def test_omission_can_anchor_preserved_claim(self):
        old = {'claims': [{'id': 'old1', 'text': 'Old boundary: task A only.'}]}
        self.assessment['issues'] = [self.issue(kind='knowledge_omission', body_quote='Old boundary: task A only.',
                                              claim_ids=['old1'], source_ids=[],
                                              evidence=[{'previous_claim_id': 'old1'}])]
        result, _ = self.review(old=old)
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['old_knowledge_issues']), 1)

    def test_review_schema_errors_are_not_style_or_success(self):
        variants = [self.issue(severity='maybe'), self.issue(kind='unknown'), self.issue(body_quote='invented body'),
                    self.issue(evidence=[]), self.issue(kind='expression'), self.issue(claim_ids=['unknown']),
                    self.issue(evidence=[{'source_id': 's1', 'quote': 'invented original'}]),
                    self.issue(evidence=[{'previous_claim_id': 'c1'}])]
        for issue in variants:
            with self.subTest(issue=issue):
                self.assessment['issues'] = [issue]
                with self.assertRaises(ValueError):
                    self.review()

    def test_legacy_empty_schema_compatible_but_unclassified_issue_rejected(self):
        self.assessment.pop('issues')
        self.assessment.update(body_issues=[], old_knowledge_issues=[])
        self.assertTrue(self.review()[0]['passed'])
        self.assessment['body_issues'] = [{'body_quote': '60%', 'reason': 'accurate, no issue'}]
        with self.assertRaisesRegex(ValueError, 'classified issues'):
            self.review()
        # Legacy compiler semantics are preserved, including its veto policy.
        self.assertFalse(self.review(protocol=False)[0]['passed'])

    def test_mixed_new_and_legacy_issues_cannot_hide_veto(self):
        self.assessment['body_issues'] = [{'body_quote': '60%', 'reason': 'unclassified'}]
        with self.assertRaisesRegex(ValueError, 'unclassified legacy'):
            self.review()

    def test_supported_claims_still_required_even_when_issues_empty(self):
        self.assessment['reviews'][0]['verdict'] = 'partial'
        with self.assertRaisesRegex(ValueError, 'located blocking issue'):
            self.review()
        self.assessment['issues'] = [self.issue()]
        self.assertFalse(self.review()[0]['passed'])

    def test_every_registered_claim_and_source_must_be_judged(self):
        for reviews in ([], [self.assessment['reviews'][0]] * 2,
                        [{**self.assessment['reviews'][0], 'source_ids': []}]):
            with self.subTest(reviews=reviews):
                self.assessment['reviews'] = reviews
                with self.assertRaises(ValueError):
                    self.review()
