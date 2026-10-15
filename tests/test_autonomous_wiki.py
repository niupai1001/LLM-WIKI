import json
import unittest
from unittest.mock import patch

from station import common, collector, wiki, knowledge
import test_rewrite as fixtures


class AutonomousWikiTests(unittest.TestCase):
    def setUp(self):
        fixtures.RewriteTests.setUp(self)
        for name in ['WIKI', 'META']:
            self.stack.enter_context(patch.object(knowledge, name, getattr(wiki, name)))
    create = fixtures.RewriteTests.create
    status = fixtures.RewriteTests.status

    def new_source(self):
        text = 'An independent method selects useful instructions under a tested capacity limit. ' * 5
        path = self.root / 'new.txt'; path.write_text(text)
        return collector.import_file('Capacity method', path, 'agents')

    def model_reply(self, sid, quotes=True):
        return {'action': 'create', 'slug': 'new-method', 'title': '新方法', 'needs_fact_review': False,
                'topic_routes': [{'slug': 'test-methods', 'title': '测试方法',
                                  'overview': '这里按具体知识问题组织测试方法的阅读。',
                                  'question': '如何理解这一方法的机制和适用条件？'}],
                'body_markdown': '新方法在受测容量条件下选择指令。选择过程需要比较收益与成本，结果不能推广到其他模型。'*2 + f'[S:{sid}]',
                'claims': [{'id': 'selection', 'text': '方法选择有用指令', 'scope': '受测容量条件',
                            'source_ids': [sid], 'status': '单源支持',
                            'evidence_passages': [{'source_id': sid, 'excerpt_id': 'e1'}] if quotes else []}]}

    def test_default_public_workflow_publishes_without_confirmation_in_one_call(self):
        sid = self.new_source()
        with patch.object(wiki, 'call_llm', return_value=self.model_reply(sid)) as model:
            result = wiki.compile_and_publish(source_id=sid)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['published'], 1)
        self.assertEqual(list(wiki.PENDING.glob('*.json')), [])
        meta = common.load_json(wiki.META / 'new-method.json')
        self.assertEqual(meta['publication']['by'], 'system')
        self.assertEqual(meta['content_review'], {})
        self.assertTrue((wiki.WIKI / 'new-method.md').is_file())
        with common.connect() as db:
            self.assertEqual(db.execute('SELECT stage FROM sources WHERE id=?', (sid,)).fetchone()[0], 'approved')

    def test_reference_original_is_available_for_a_two_source_claim(self):
        sid = self.new_source()
        def model(messages):
            context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
            ref = context['reference_sources'][0]
            self.assertEqual(ref['source_id'], self.sid)
            self.assertIn('costs and benefits', ref['text'])
            reply = self.model_reply(sid)
            reply['body_markdown'] += f' 与[[concept|已有方法]]都在受测容量内选择指令。[S:{sid}][S:{self.sid}]'
            reply['claims'][0]['source_ids'].append(self.sid)
            reply['claims'][0]['evidence_passages'].append({'source_id': self.sid, 'excerpt_id': 'e1'})
            return reply
        with patch.object(wiki, 'call_llm', side_effect=model):
            result = wiki.compile_and_publish(source_id=sid)
        self.assertEqual(result['published'], 1)
        meta = common.load_json(wiki.META / 'new-method.json')
        self.assertEqual(set(meta['source_ids']), {sid, self.sid})
        self.assertEqual(self.status(), 'approved')
        backlinks = knowledge.semantic_backlinks('concept')
        self.assertEqual(backlinks[0]['slug'], 'new-method')
        self.assertIn('已有方法', backlinks[0]['body'])
        self.assertNotIn('[[concept', backlinks[0]['body'])

    def test_refresh_approved_knowledge_neither_reserves_nor_resets_sources(self):
        reply = {'action': 'update', 'slug': 'concept', 'title': self.meta['title'],
                 'topic_routes': [{'slug': 'test-methods', 'title': '测试方法',
                                   'overview': '这里按具体知识问题组织测试方法的阅读。',
                                   'question': '如何理解这一方法的机制和适用条件？'}],
                 'body_markdown': self.rewritten, 'claims': self.meta['claims'], 'needs_fact_review': False}
        with patch.object(wiki, 'call_llm', return_value=reply):
            created = []; self.assertEqual(wiki.prepare(target_slug='concept', source_ids=[self.sid], revisit=True, _created_ids=created), 1)
        self.assertEqual(self.status(), 'approved')
        draft = common.load_json(wiki.PENDING / (created[0] + '.json'))
        self.assertEqual(draft['reserved_source_ids'], [])
        self.assertEqual(wiki.automatic_publish(created[0])['status'], 'published')
        self.assertEqual(self.status(), 'approved')

    def test_missing_evidence_becomes_system_exception_without_changing_formal_page(self):
        sid = self.new_source()
        with patch.object(wiki, 'call_llm', return_value=self.model_reply(sid, quotes=False)):
            result = wiki.compile_and_publish(source_id=sid)
        self.assertEqual(result['published'], 0)
        draft = common.load_json(next(wiki.PENDING.glob('*.json')))
        self.assertIn('缺少原文摘录', draft['automation_error'])
        self.assertFalse((wiki.WIKI / 'new-method.md').exists())

    def test_automatic_rewrite_still_blocks_stale_sources_and_preserves_objects(self):
        pid = self.create()
        source = common.load_json(wiki.RAW / (self.sid + '.json'))
        common.save_json(wiki.RAW / (self.sid + '.json'), {**source, 'text': source['text'] + 'changed'})
        self.assertEqual(wiki.automatic_publish(pid)['status'], 'blocked')
        self.assertEqual(self.page.read_text(), self.body)
        common.save_json(wiki.RAW / (self.sid + '.json'), source)
        self.assertEqual(wiki.automatic_publish(pid)['status'], 'published')
        for field in wiki.PROTECTED_FIELDS:
            self.assertEqual(common.load_json(self.meta_path).get(field), self.meta.get(field))
        self.assertEqual(self.status(), 'approved')

    def test_bare_link_and_shared_category_do_not_manufacture_comparisons(self):
        pages = [{'slug': 'a', 'title': 'A', 'body': '已有方法定义。', 'source_ids': ['a'*16]},
                 {'slug': 'b', 'title': 'B', 'body': '- [[a|方法A]]', 'source_ids': ['a'*16, 'b'*16]}]
        self.assertEqual(knowledge.semantic_backlinks('a', pages), [])
        pages[1]['body'] = '只提到[[a|方法A]]，没有双方原文引用。'*5
        self.assertEqual(knowledge.semantic_backlinks('a', pages), [])

    def test_similar_source_cannot_silently_replace_previous_support(self):
        sid = self.new_source()
        reply = self.model_reply(sid)
        reply.update(action='update', slug='concept', title=self.meta['title'], body_markdown=self.rewritten + f'[S:{sid}]')
        claim = reply['claims'][0]
        claim.update(id='capacity', text=self.meta['claims'][0]['text'], scope=self.meta['claims'][0]['scope'])
        with patch.object(wiki, 'call_llm', return_value=reply):
            result = wiki.compile_and_publish(source_id=sid, target_slug='concept')
        self.assertEqual(result['published'], 0)
        self.assertIn('静默移除', result['results'][0]['error'])
        self.assertEqual(self.page.read_text(), self.body)
