"""Topic membership follows concept publication without another model call."""
import copy
import json
import unittest
import threading
from unittest.mock import patch

from station import wiki, common, knowledge, web
from station.topic_navigation import validate_routes, topic_context
import test_autonomous_wiki as workflow
import test_rewrite as fixtures


class TopicNavigationTests(unittest.TestCase):
    setUp = workflow.AutonomousWikiTests.setUp
    tearDown = fixtures.RewriteTests.tearDown
    new_source = workflow.AutonomousWikiTests.new_source
    model_reply = workflow.AutonomousWikiTests.model_reply
    status = fixtures.RewriteTests.status

    def seed_topic(self):
        path = wiki.WIKI / 'methods.md'
        path.write_text('# 方法导航\n\n保留已有读者说明。\n\n- 怎样理解原方法？ → [[concept|已有概念]]\n')
        common.save_json(wiki.META / 'methods.json', {'slug': 'methods', 'title': '方法导航', 'page_kind': 'topic',
                         'category': 'agents', 'source_ids': [self.sid], 'claims': [], 'conflicts': []})
        return path

    def reply(self, sid, slug='new-method', overview='这里按知识问题比较不同方法，并阅读它们的机制和适用条件。'):
        reply = self.model_reply(sid)
        reply['slug'] = slug
        reply['topic_routes'] = [{'slug': 'methods', 'title': '方法导航', 'overview': overview,
                                 'question': '怎样理解新增方法的机制和适用条件？'}]
        return reply

    def test_publish_updates_existing_topic_in_one_call_and_counts_current_members(self):
        path = self.seed_topic(); sid = self.new_source()
        def model(messages):
            context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
            self.assertEqual(context['topic_candidates'][0]['slug'], 'methods')
            self.assertEqual(context['topic_candidates'][0]['members'][0]['slug'], 'concept')
            return self.reply(sid)
        with patch.object(wiki, 'call_llm', side_effect=model) as model_call:
            result = wiki.compile_and_publish(source_id=sid)
        self.assertEqual(model_call.call_count, 1)
        self.assertEqual(result['published'], 1)
        self.assertEqual(result['results'][0]['topics_updated'], ['methods'])
        self.assertIn('保留已有读者说明', path.read_text())
        self.assertIn('[[new-method|', path.read_text())
        pages = knowledge.wiki_pages()
        details = knowledge.topic_details(next(p for p in pages if p['slug'] == 'methods'), pages)
        self.assertEqual(len(details['concepts']), 2)
        self.assertEqual(set(details['source_ids']), {sid, self.sid})
        self.assertEqual(self.status(), 'approved')
        self.assertFalse(any(e['source'] == 'wiki:methods' and e['type'] == 'evidence'
                             for e in knowledge.graph_data()['edges']))

    def test_new_topic_is_created_with_the_concept_and_is_not_extra_evidence(self):
        sid = self.new_source()
        with patch.object(wiki, 'call_llm', return_value=self.reply(sid)):
            result = wiki.compile_and_publish(source_id=sid)
        self.assertEqual(result['published'], 1)
        meta = common.load_json(wiki.META / 'methods.json')
        self.assertEqual(meta['page_kind'], 'topic')
        self.assertEqual(meta['claims'], [])
        self.assertEqual(meta['source_ids'], [])
        self.assertEqual(meta['topic_navigation']['entries'][0]['slug'], 'new-method')
        self.assertFalse(any(e['source'] == 'wiki:methods' and e['type'] == 'evidence'
                             for e in knowledge.graph_data()['edges']))

    def test_parallel_drafts_merge_members_and_do_not_overwrite_a_newer_overview(self):
        path = self.seed_topic(); ids = [self.new_source()]
        extra = self.root / 'different.txt'; extra.write_text('The third method has independently reported costs and benefits. ' * 8)
        from station import collector
        ids.append(collector.import_file('Third method', extra, 'agents'))
        proposals = []
        for i, sid in enumerate(ids):
            with patch.object(wiki, 'call_llm', return_value=self.reply(sid, 'method-' + str(i),
                              '先完成第一轮方法导航。' if i == 0 else '这是已经过时的第二轮导航。')):
                self.assertEqual(wiki.prepare(source_id=sid, _created_ids=proposals), 1)
        for pid in proposals:
            self.assertEqual(wiki.automatic_publish(pid)['status'], 'published')
        body = path.read_text()
        self.assertIn('[[method-0|', body); self.assertIn('[[method-1|', body)
        self.assertIn('先完成第一轮方法导航', body)
        self.assertNotIn('已经过时的第二轮导航', body)

    def test_failed_topic_write_restores_concept_topic_and_source_state(self):
        path = self.seed_topic(); before = path.read_bytes(); before_meta = (wiki.META / 'methods.json').read_bytes()
        sid = self.new_source(); created = []
        with patch.object(wiki, 'call_llm', return_value=self.reply(sid)):
            wiki.prepare(source_id=sid, _created_ids=created)
        save = wiki.save_json
        def fail_topic(target, value):
            if target == wiki.META / 'methods.json':
                raise OSError('simulated topic write failure')
            return save(target, value)
        with patch.object(wiki, 'save_json', side_effect=fail_topic):
            result = wiki.automatic_publish(created[0])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((wiki.META / 'methods.json').read_bytes(), before_meta)
        self.assertFalse((wiki.WIKI / 'new-method.md').exists())
        self.assertFalse((wiki.META / 'new-method.json').exists())
        with common.connect() as db:
            self.assertEqual(db.execute('SELECT stage FROM sources WHERE id=?', (sid,)).fetchone()[0], 'drafted')
        backup = common.load_json(self.root / 'publication_backups' / (created[0] + '.json'))
        self.assertEqual(backup['topic_navigation'][0]['body'].encode(), before)

    def test_reject_and_expression_rewrite_do_not_change_topic_membership(self):
        path = self.seed_topic(); before = path.read_bytes(); sid = self.new_source(); created = []
        with patch.object(wiki, 'call_llm', return_value=self.reply(sid)):
            wiki.prepare(source_id=sid, _created_ids=created)
        wiki.reject(created[0])
        self.assertEqual(path.read_bytes(), before)
        pid = fixtures.RewriteTests.create(self, topic_routes=[{'slug': 'wrong'}])
        result = wiki.automatic_publish(pid)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['topics_updated'], [])
        self.assertEqual(path.read_bytes(), before)

    def test_old_draft_without_routes_can_still_publish(self):
        self.seed_topic()
        reply = self.reply(self.sid); reply.update(action='update', slug='concept', title=self.meta['title'],
                                                  body_markdown=self.rewritten, claims=self.meta['claims'])
        created = []
        with patch.object(wiki, 'call_llm', return_value=reply):
            wiki.prepare(source_ids=[self.sid], target_slug='concept', revisit=True, _created_ids=created)
        path = wiki.PENDING / (created[0] + '.json'); draft = common.load_json(path)
        draft.pop('topic_routes'); draft.pop('topic_base_versions')
        common.save_json(path, draft)
        self.assertEqual(wiki.automatic_publish(created[0])['status'], 'published')

    def test_topic_targets_cannot_overwrite_a_concept_or_use_missing_navigation(self):
        self.seed_topic()
        for routes in [[], None, [{'slug': 'concept', 'question': '怎样理解这一方法？'}],
                       [{'slug': 'new-topic', 'title': '新入口', 'question': '怎样理解这一方法？'}]]:
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                validate_routes(routes, wiki.WIKI, wiki.META, 'new-method')

    def test_legacy_draft_keeps_all_four_existing_topic_parents(self):
        path = self.seed_topic()
        for i in range(3):
            slug = 'methods-' + str(i)
            (wiki.WIKI / (slug + '.md')).write_text(path.read_text())
            meta = common.load_json(wiki.META / 'methods.json'); meta['slug'] = slug
            common.save_json(wiki.META / (slug + '.json'), meta)
        reply = self.reply(self.sid); reply.update(action='update', slug='concept', title=self.meta['title'],
                                                  body_markdown=self.rewritten, claims=self.meta['claims'])
        created = []
        with patch.object(wiki, 'call_llm', return_value=reply):
            wiki.prepare(source_ids=[self.sid], target_slug='concept', revisit=True, _created_ids=created)
        pending = wiki.PENDING / (created[0] + '.json'); draft = common.load_json(pending)
        draft.pop('topic_routes'); common.save_json(pending, draft)
        result = wiki.automatic_publish(created[0])
        self.assertEqual(result['status'], 'published')
        self.assertEqual(len(result['topics_updated']), 4)

    def test_topic_snapshot_and_version_share_the_publication_lock(self):
        path = self.seed_topic(); old_version = wiki.page_version(path)
        reading, written = threading.Event(), threading.Event()
        def writer():
            reading.wait(2)
            with wiki.proposal_lock():
                path.write_text('# 方法导航\n\n较新的主题说明。\n')
                written.set()
        worker = threading.Thread(target=writer); worker.start()
        original = wiki.page_version
        def during_version(target):
            reading.set()
            self.assertFalse(written.wait(.05), 'publication must wait for the snapshot')
            return original(target)
        try:
            with patch.object(wiki, 'page_version', side_effect=during_version):
                snapshot = topic_context(wiki.WIKI, wiki.META)
        finally:
            reading.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(written.is_set())
        self.assertEqual(snapshot[0]['version'], old_version)
        self.assertIn('保留已有读者说明', snapshot[0]['overview'])

    def test_existing_knowledge_link_survives_candidate_truncation(self):
        self.seed_topic()
        (wiki.WIKI / 'old-target.md').write_text('# 旧联系\n\n既有说明。\n')
        common.save_json(wiki.META / 'old-target.json', {'category': 'other'})
        self.meta['related_slugs'] = ['old-target']; common.save_json(self.meta_path, self.meta)
        self.page.write_text(self.body + '\n\n[[old-target|旧联系]]')
        reply = self.reply(self.sid); reply.update(action='update', slug='concept', title=self.meta['title'],
                      body_markdown=self.rewritten + '\n\n[[old-target|旧联系]]', claims=self.meta['claims'],
                      related_slugs=['old-target'])
        def model(messages):
            context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
            self.assertNotIn('old-target', {c['slug'] for c in context['candidates']})
            return reply
        with patch.object(wiki, 'call_llm', side_effect=model), patch.object(wiki, 'relevance_score', return_value=0):
            result = wiki.compile_and_publish(source_ids=[self.sid], target_slug='concept', revisit=True)
        self.assertEqual(result['published'], 1)
        self.assertIn('[[old-target|旧联系]]', self.page.read_text())


class TopicCountingTests(unittest.TestCase):
    def test_many_concepts_share_originals_and_comparisons_have_their_own_count(self):
        topic = {'slug': 'topic', 'body': '# Topic\n[[a]] [[a]] [[b]] [[comparison]] [[other-topic]]\n'
                 '`[[fake]]`\n\n## 相关知识\n- [[unrelated]]', 'source_ids': ['stale']}
        pages = [topic,
                 {'slug': 'a', 'page_kind': 'concept', 'source_ids': ['one', 'one']},
                 {'slug': 'b', 'page_kind': 'concept', 'source_ids': ['one', 'two']},
                 {'slug': 'comparison', 'page_kind': 'concept', 'page_role': 'comparison', 'source_ids': ['two']},
                 {'slug': 'other-topic', 'page_kind': 'topic', 'source_ids': ['ignore']},
                 {'slug': 'fake', 'page_kind': 'concept', 'source_ids': ['ignore']},
                 {'slug': 'unrelated', 'page_kind': 'concept', 'source_ids': ['ignore']}]
        details = knowledge.topic_details(topic, pages)
        self.assertEqual(len(details['concepts']), 2)
        self.assertEqual(len(details['comparisons']), 1)
        self.assertEqual(details['source_ids'], ['one', 'two'])
        self.assertEqual(web.topic_counts(details), '2 个概念 · 1 篇研究比较 · 2 份关联原文')
        changed = copy.deepcopy(pages); changed[1]['source_ids'].append('three')
        self.assertEqual(len(knowledge.topic_details(topic, changed)['source_ids']), 3)
