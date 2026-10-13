import json
import unittest
from unittest.mock import patch

from station import common, wiki, bibliography
from station.writing_context import original_context, marked_original, relevance_score
import test_rewrite as fixtures


class OriginalContextTests(unittest.TestCase):
    def test_full_original_is_not_duplicated_or_excerpted(self):
        text = 'Methods and limits belong to the same complete original.'
        result = original_context(text, 1000)
        self.assertEqual(result['text'], text)
        self.assertFalse(result['is_excerpt'])
        self.assertNotIn('text', result['text_segments'][0])

    def test_late_method_and_limits_survive_small_budget(self):
        text = ('Opening problem.\n' + 'Introduction context. '*6000 +
                '\n3 Methods\nThe planner first decomposes the task, then executes the subtasks.\n' +
                'Implementation detail. '*3000 + '\n6 Limitations\nThe evaluation covers only synthetic tasks.\n')
        result = original_context(text, 8000)
        self.assertTrue(result['is_excerpt'])
        self.assertIn('The planner first decomposes', result['text'])
        self.assertIn('evaluation covers only synthetic tasks', result['text'])
        self.assertLessEqual(len(result['text']), 8000)
        spans = result['text_segments']
        self.assertTrue(all(0 <= s['start'] < s['end'] <= len(text) for s in spans))
        for a, b in zip(spans, spans[1:]):
            self.assertLess(a['end'], b['start'])

    def test_existing_english_concept_beats_generic_agent_text(self):
        query = 'ReAct interleaves reasoning and acting with external tools.'
        score = relevance_score(query, 'ReAct 推理与行动循环', 'ReAct reasoning acting')
        self.assertGreater(score, relevance_score(query, '常驻上下文选择', 'agent language model'))

    def test_addressed_excerpts_are_copied_from_original(self):
        text = 'Planning uses an explicit list of steps with executor feedback. '*1000
        context, excerpts = marked_original(text, 8000)
        self.assertLessEqual(len(context['text']), 8000)
        self.assertIn('[E:e1;', context['text'])
        self.assertTrue(all(e['quote'] in text for e in excerpts.values()))

    def test_official_blog_metadata_uses_declared_dates_not_capture_time(self):
        raw = '''<main><script type="application/ld+json">{"@type":"BlogPosting",
                 "headline":"Planning Agents","datePublished":"2024-02-13",
                 "author":{"name":"Example Team"},"publisher":{"name":"Engineering Blog"}}
                 </script></main>'''
        value = bibliography.html_metadata(raw)
        self.assertEqual(value['first_published'], '2024-02-13')
        self.assertEqual(value['publisher'], 'Engineering Blog')
        self.assertEqual(value['authors'][0]['name'], 'Example Team')
        self.assertEqual(value['doi'], '')

    def test_newsletter_component_is_removed_without_filtering_article_words(self):
        from station.collector import _html_text
        article = 'An agent can summarize a newsletter after retrieving its content. ' * 20
        data = ('<html><body><div class="NewsletterSubscribe__wrapper"><h2>Subscribe now</h2>'
                '<p>Promotional signup text only.</p></div><article><h1>Agent Workflow</h1><p>'
                + article + '</p></article></body></html>').encode()
        extracted = _html_text(data)
        self.assertNotIn('Promotional signup', extracted)
        self.assertIn('summarize a newsletter', extracted)


class SinglePassTests(unittest.TestCase):
    setUp = fixtures.RewriteTests.setUp
    create = fixtures.RewriteTests.create
    status = fixtures.RewriteTests.status
    def test_optional_edit_costs_one_extra_call_and_still_freezes_knowledge(self):
        with patch.object(wiki, 'call_llm', return_value={**self.output, 'claims': []}) as call:
            pid = wiki.prepare_rewrite('concept', second_pass=True)
        self.assertEqual(call.call_count, 2)
        draft = common.load_json(wiki.PENDING / (pid + '.json'))
        self.assertEqual(draft['claims'], self.meta['claims'])
        self.assertEqual(draft['writing_passes'], 2)
        self.assertTrue(draft['generation_complete'])

    def test_similar_new_source_can_enrich_existing_claim_then_publish(self):
        from station import collector
        note = self.root / 'confirmation.txt'
        quote = 'Independent analysis reports a selection cost under the same tested capacity model.'
        note.write_text(quote * 5)
        sid = collector.import_file('Capacity selection study', note, 'agents')
        claim = {k: v for k, v in self.meta['claims'][0].items() if k != 'custom'}
        claim['source_ids'] = [self.sid, sid]
        claim['evidence_passages'] = [{'source_id': sid, 'locator': 'Paragraph 1', 'quote': quote}]
        def model(messages):
            context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
            self.assertEqual(context['target_slug'], 'concept')
            self.assertIn('capacity', [c['id'] for c in context['candidates'][0]['claims']])
            return {'action': 'update', 'slug': 'concept', 'title': self.meta['title'],
                    'topic_routes': [{'slug': 'test-methods', 'title': '测试方法',
                                      'overview': '这里按具体知识问题组织测试方法的阅读。',
                                      'question': '如何理解这一方法的机制和适用条件？'}],
                    'body_markdown': self.rewritten + f' 新研究也限定在受测容量模型中。[S:{sid}]',
                    'used_source_ids': [sid], 'claims': [claim], 'needs_fact_review': False}
        with patch.object(wiki, 'call_llm', side_effect=model) as call:
            self.assertEqual(wiki.prepare(source_id=sid, target_slug='concept'), 1)
        self.assertEqual(call.call_count, 1)
        draft = common.load_json(next(wiki.PENDING.glob('*.json')))
        self.assertEqual(draft['writing_passes'], 1)
        self.assertNotIn('editorial_reviewed', draft)
        self.assertEqual(self.page.read_text(), self.body)
        wiki.approve(draft['proposal_id'])
        meta = common.load_json(self.meta_path)
        self.assertEqual(len(meta['claims']), 1)
        self.assertEqual(meta['claims'][0]['id'], 'capacity')
        self.assertEqual(set(meta['source_ids']), {self.sid, sid})
        self.assertEqual(self.status(), 'approved')

    def test_failed_legacy_edit_still_cannot_publish(self):
        pid = self.create()
        path = wiki.PENDING / (pid + '.json')
        draft = common.load_json(path)
        for k in ['generation_complete', 'generation_policy', 'second_pass_requested', 'writing_passes']:
            draft.pop(k, None)
        draft['editorial_reviewed'] = False
        common.save_json(path, draft)
        with self.assertRaisesRegex(ValueError, '第二遍'):
            wiki.approve(pid, fact_invariance_confirmed=True)

    def test_model_selects_real_excerpt_without_retyping_quote(self):
        source = common.load_json(wiki.RAW / (self.sid + '.json'))
        _, lookup = marked_original(source['text'], 8000)
        claim = {'id': 'new', 'text': '选择具有成本', 'scope': '原文模型',
                 'source_ids': [self.sid], 'status': '单源支持',
                 'evidence_passages': [{'source_id': self.sid, 'excerpt_id': 'e1', 'quote': 'ignored invented text'}]}
        validated = wiki.validate_evidence_records({'claims': [claim]}, {self.sid}, {self.sid: lookup})
        passage = validated['claims'][0]['evidence_passages'][0]
        self.assertEqual(passage['quote'], lookup['e1']['quote'].strip())
        self.assertIn('提取文本字符', passage['locator'])
        claim['evidence_passages'][0]['excerpt_id'] = 'e9999'
        with self.assertRaisesRegex(ValueError, 'excerpt'):
            wiki.validate_evidence_records({'claims': [claim]}, {self.sid}, {self.sid: lookup})
