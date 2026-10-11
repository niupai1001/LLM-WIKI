import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station import fact_checks as facts


class FactCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = []
        for name in ('REVIEWS', 'WIKI', 'META', 'RAW'):
            directory = self.root / name.lower()
            directory.mkdir()
            p = patch.object(facts, name, directory)
            p.start()
            self.patches.append(p)
        self.sid = '0000000000000001'
        self.text = 'The agent interleaves reasoning and actions while receiving observations from the environment.'
        self.source = {'id': self.sid, 'title': 'Original', 'text': self.text, 'content_status':'full-text', 'url':'https://example.org/paper'}
        self.meta = {'title':'概念', 'source_ids':[self.sid], 'content_review': {'by':'Codex', 'notes':'编辑记录'},
                     'claims':[{'id':'mechanism','text':'交替推理与行动','scope':'原实验范围','source_ids':[self.sid]}]}
        (facts.WIKI / 'concept.md').write_text('# 概念\n说明[来源](/source/' + self.sid + ')')
        self.write()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def write(self):
        (facts.META / 'concept.json').write_text(json.dumps(self.meta))
        (facts.RAW / (self.sid + '.json')).write_text(json.dumps(self.source))

    def payload(self, **values):
        state = facts.snapshot('concept')
        row = state['claims'][0]
        return dict(page_version=state['page_version'], claim_version=row['claim_version'],
                    source_version=row['sources'][self.sid]['version'], claim_id='mechanism', source_id=self.sid,
                    verdict='supported', by='Reviewer', locator='Section 2, page 3', quote=self.text,
                    rationale='此摘录明确描述断言所说的机制，结论仅限该设计。', **values)

    def test_editing_record_does_not_certify_claims_and_read_is_nonmutating(self):
        self.assertEqual(facts.snapshot('concept')['counts']['checked'], 0)
        self.assertIn('待核查', facts.render('concept'))
        self.assertEqual(list(facts.REVIEWS.iterdir()), [])

    def test_verified_quote_review_and_human_agent_separate(self):
        facts.save_review('concept', self.payload(), reviewer_type='agent')
        state = facts.snapshot('concept')
        self.assertEqual(state['counts']['checked'], 1)
        self.assertEqual(state['counts']['human'], 0)
        self.assertIn('代理逐项核对', facts.render('concept'))
        facts.save_review('concept', self.payload(), reviewer_type='human')
        self.assertEqual(facts.snapshot('concept')['counts']['human'], 1)
        self.assertIn('原文摘录', facts.render('concept'))

    def test_reject_fabricated_quote_missing_position_and_unknown_source(self):
        for field, value in [('quote', 'This sentence is not in the actual original source.'), ('locator',''),
                             ('source_id','ffffffffffffffff'), ('rationale','')]:
            payload = self.payload()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                facts.save_review('concept', payload)
        self.assertEqual(facts.snapshot('concept')['counts']['checked'], 0)

    def test_body_claim_and_source_updates_invalidate_reviews(self):
        facts.save_review('concept', self.payload())
        original = (facts.WIKI / 'concept.md').read_text()
        (facts.WIKI / 'concept.md').write_text(original + '\n更新的正文')
        self.assertFalse(facts.snapshot('concept')['claims'][0]['valid'])

        self.assertIn('需重新核查', facts.render('concept'))
        (facts.WIKI / 'concept.md').write_text(original)
        self.meta['claims'][0]['scope'] = '另一个范围'
        self.write()
        self.assertFalse(facts.snapshot('concept')['claims'][0]['valid'])
        facts.save_review('concept', self.payload())
        self.source['text'] += ' Changed original.'
        self.write()
        self.assertFalse(facts.snapshot('concept')['claims'][0]['valid'])

    def test_replacing_original_bytes_without_renaming_invalidates_review(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(facts,'ASSETS',Path(folder)):
            asset=Path(folder)/'paper.pdf';asset.write_bytes(b'%PDF-original')
            self.source['pdf_path']='paper.pdf';self.write()
            facts.save_review('concept',self.payload())
            self.assertTrue(facts.snapshot('concept')['claims'][0]['valid'])
            asset.write_bytes(b'%PDF-replaced')
            self.assertFalse(facts.snapshot('concept')['claims'][0]['valid'])

    def test_stale_submission_rejected_at_each_version_boundary(self):
        payload = self.payload()
        self.source['pdf_path'] = 'new-version.pdf'
        self.write()
        with self.assertRaises(facts.ReviewConflict):
            facts.save_review('concept', payload)
        payload = self.payload()
        self.meta['claims'][0]['text'] = '另一个断言'
        self.write()
        with self.assertRaises(facts.ReviewConflict):
            facts.save_review('concept', payload)
        payload = self.payload()
        (facts.WIKI / 'concept.md').write_text('# Changed page')
        with self.assertRaises(facts.ReviewConflict):
            facts.save_review('concept', payload)

    def test_summary_cannot_be_supported_but_unverifiable_can_be_recorded(self):
        self.source['content_status'] = 'summary-only'
        self.write()
        with self.assertRaises(ValueError):
            facts.save_review('concept', self.payload())
        payload = self.payload()
        payload.update(verdict='unverifiable', quote='', locator='')
        facts.save_review('concept', payload)
        self.assertEqual(facts.snapshot('concept')['counts']['unverifiable'], 1)
        self.assertIn('无法核查', facts.render('concept'))

    def test_ui_escapes_review_and_claims_and_preserves_history(self):
        payload = self.payload()
        payload.update(by='<script>x()</script>', rationale='<img src=x onerror=bad()>')
        facts.save_review('concept', payload)
        payload.update(verdict='partial', rationale='仅支持机制，不支持泛化')
        facts.save_review('concept', payload)
        html = facts.render('concept')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('部分支持', html)
        self.assertEqual(len(json.loads((facts.REVIEWS / 'concept.json').read_text())['reviews']), 2)


if __name__ == '__main__':
    unittest.main()
