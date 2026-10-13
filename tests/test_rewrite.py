"""Expression-only rewrites protect knowledge and remain separate from intake."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from station import common, collector, wiki, fact_checks, citations


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        paths = {'ROOT': self.root, 'RAW': self.root/'raw', 'WIKI': self.root/'wiki',
                 'META': self.root/'wiki_meta', 'ASSETS': self.root/'originals',
                 'PENDING': self.root/'pending', 'DB': self.root/'state.db'}
        for module in [common, collector, wiki, fact_checks, citations]:
            for name, path in paths.items():
                if hasattr(module, name):
                    self.stack.enter_context(patch.object(module, name, path))
        note = self.root/'original.txt'
        note.write_text('A selected instruction has costs and benefits within the tested capacity model. '*5)
        self.sid = collector.import_file('Original', note, 'agents')
        with common.connect() as db:
            db.execute("UPDATE sources SET stage='approved' WHERE id=?", (self.sid,))
        self.body = f'# 测试概念\n\n旧解释：模型在受测条件下选择指令，不能推广到所有智能体。 [原文](/source/{self.sid})\n'
        self.meta = {'slug': 'concept', 'title': '测试概念', 'source_ids': [self.sid], 'category': 'agents',
                     'page_kind': 'concept', 'page_role': 'knowledge', 'relations': [], 'related_slugs': [],
                     'claims': [{'id': 'capacity', 'text': '容量限制影响选择', 'scope': '原论文模型',
                                 'source_ids': [self.sid], 'status': '单源支持', 'custom': {'keep': True}}],
                     'conflicts': [{'id': 'c', 'status': '保留分歧', 'custom': {'sides': [1, 2]}}],
                     'content_review': {'by': 'old reviewer'}, 'unknown': {'keep': True}}
        self.page = wiki.WIKI/'concept.md'
        self.meta_path = wiki.META/'concept.json'
        self.page.write_text(self.body)
        common.save_json(self.meta_path, self.meta)
        self.rewritten = f'指令选择要同时考虑收益和成本。这里解释的是原论文受测模型，不推广到所有智能体。 [原文](/source/{self.sid})'
        self.output = {'body_markdown': self.rewritten, 'editorial_notes': '说明选择问题与限制。', 'needs_fact_review': False}

    def create(self, **changes):
        output = {**self.output, **changes}
        with patch.object(wiki, 'call_llm', return_value=output) as model:
            pid = wiki.prepare_rewrite('concept')
        self.assertEqual(model.call_count, 1)
        return pid

    def status(self):
        with common.connect() as db:
            return db.execute('SELECT stage FROM sources WHERE id=?', (self.sid,)).fetchone()[0]

    def test_model_cannot_replace_any_protected_object_and_publication_keeps_sources(self):
        pid = self.create(claims=[], conflicts=[], source_ids=[], slug='other', title='bad')
        self.assertRegex(pid, r'^p_[a-f0-9]{32}$')
        draft = common.load_json(wiki.PENDING/(pid+'.json'))
        for key in wiki.PROTECTED_FIELDS:
            self.assertEqual(draft[key], self.meta[key])
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(self.status(), 'approved')
        wiki.approve(pid)
        published = common.load_json(self.meta_path)
        for key in wiki.PROTECTED_FIELDS:
            self.assertEqual(published[key], self.meta[key])
        self.assertEqual(published['unknown'], self.meta['unknown'])
        self.assertEqual(published['content_review'], {})
        self.assertEqual(published['review_history'], [self.meta['content_review']])
        self.assertEqual(self.status(), 'approved')
        self.assertIn(self.rewritten, self.page.read_text())
        archive = common.load_json(self.root/'proposal_history'/(pid+'.json'))
        self.assertEqual(archive['outcome'], 'approved')
        self.assertEqual(archive['base_body'], self.body)
        backup = common.load_json(self.root/'publication_backups'/(pid+'.json'))
        self.assertEqual(backup['metadata'], self.meta)

    def test_reject_changes_neither_page_nor_source_status(self):
        pid = self.create()
        wiki.reject(pid)
        self.assertEqual(self.status(), 'approved')
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(common.load_json(self.meta_path), self.meta)
        self.assertEqual(common.load_json(self.root/'proposal_history'/(pid+'.json'))['outcome'], 'rejected')

    def test_duplicate_target_does_not_invoke_model(self):
        self.create()
        with patch.object(wiki, 'call_llm') as model, self.assertRaisesRegex(ValueError, '已有待审'):
            wiki.prepare_rewrite('concept')
        model.assert_not_called()

    def test_changed_source_and_page_versions_block_approval(self):
        for change in ['page', 'metadata', 'text', 'asset']:
            with self.subTest(change=change):
                asset = common.ASSETS/'source.pdf'
                asset.write_bytes(b'original')
                source = common.load_json(common.RAW/(self.sid+'.json'))
                source['pdf_path'] = 'source.pdf'
                common.save_json(common.RAW/(self.sid+'.json'), source)
                pid = self.create()
                if change == 'page':
                    self.page.write_text(self.body+'\n人工补充。')
                elif change == 'metadata':
                    common.save_json(self.meta_path, {**self.meta, 'note': 'concurrent'})
                elif change == 'text':
                    common.save_json(common.RAW/(self.sid+'.json'), {**source, 'text': 'changed'})
                else:
                    asset.write_bytes(b'new PDF with same path')
                with self.assertRaises(ValueError):
                    wiki.approve(pid, fact_invariance_confirmed=True)
                wiki.reject(pid)
                self.page.write_text(self.body)
                common.save_json(self.meta_path, self.meta)
                common.save_json(common.RAW/(self.sid+'.json'), source)

    def test_complete_claim_scope_and_conflict_tampering_are_blocked(self):
        pid = self.create()
        path = wiki.PENDING/(pid+'.json')
        original = common.load_json(path)
        for key in ['claims', 'conflicts', 'related_slugs', 'source_ids', 'category', 'page_kind', 'title']:
            altered = copy.deepcopy(original)
            altered[key] = ['other'] if isinstance(altered[key], list) else 'other'
            if key == 'claims':
                altered[key] = copy.deepcopy(self.meta[key])
                altered[key][0]['scope'] = '所有情况'
            common.save_json(path, altered)
            with self.subTest(key=key), self.assertRaises(ValueError):
                wiki.approve(pid, fact_invariance_confirmed=True)
        common.save_json(path, original)

    def test_citation_loss_or_new_source_blocks_generation(self):
        for body in [self.rewritten.split(' [原文]')[0] + '这里不改变受测模型的适用条件与原有结论。', self.rewritten+' [other](/source/'+('f'*16)+')']:
            with patch.object(wiki, 'call_llm', return_value={**self.output, 'body_markdown': body}):
                with self.assertRaisesRegex(ValueError, '来源集合'):
                    wiki.prepare_rewrite('concept')
        self.assertEqual(list(wiki.PENDING.glob('*.json')), [])
        self.assertEqual(self.status(), 'approved')

    def test_review_flag_requires_resolution_and_true_confirmation(self):
        pid = self.create(needs_fact_review=True, editorial_notes='原结论有疑点，未自行修改。')
        with self.assertRaisesRegex(ValueError, 'Agent'):
            wiki.approve(pid, fact_invariance_confirmed=True)
        wiki.approve(pid, fact_invariance_confirmed=True, fact_review_note='原文与改写保持相同范围，疑点是术语理解而非结论改动。')

    def test_second_pass_failure_keeps_incomplete_draft_and_blocks_publication(self):
        with patch.object(wiki, 'call_llm', side_effect=[self.output, RuntimeError('upstream unavailable')]):
            pid = wiki.prepare_rewrite('concept', second_pass=True)
        draft = common.load_json(wiki.PENDING/(pid+'.json'))
        self.assertFalse(draft['editorial_reviewed'])
        self.assertTrue(draft['needs_fact_review'])
        with self.assertRaisesRegex(ValueError, '第二遍'):
            wiki.approve(pid, fact_invariance_confirmed=True, fact_review_note='cannot waive')

    def test_first_pass_failure_has_no_official_side_effects(self):
        with patch.object(wiki, 'call_llm', side_effect=RuntimeError('failed')), self.assertRaises(RuntimeError):
            wiki.prepare_rewrite('concept')
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(self.status(), 'approved')
        self.assertEqual(list(wiki.PENDING.glob('*.json')), [])

    def test_pair_restored_if_metadata_write_fails(self):
        pid = self.create()
        original_save = wiki.save_json
        def fail(path, obj):
            if path == self.meta_path:
                raise OSError('disk failure')
            original_save(path, obj)
        with patch.object(wiki, 'save_json', side_effect=fail), self.assertRaises(OSError):
            wiki.approve(pid, fact_invariance_confirmed=True)
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(common.load_json(self.meta_path), self.meta)
        self.assertTrue((wiki.PENDING/(pid+'.json')).is_file())
        self.assertFalse((self.root/'proposal_history'/(pid+'.json')).exists())
        self.assertEqual(self.status(), 'approved')

    def test_new_source_prepare_publish_and_legacy_draft_compatibility(self):
        for legacy in [False, True]:
            note = self.root/('new-'+str(legacy)+'.txt')
            note.write_text('新的完整来源证据，讨论一个独立问题及其限制。'*12)
            sid = collector.import_file('新来源'+str(legacy), note, 'agents')
            reply = {'action': 'create', 'slug': 'new-'+str(legacy).lower(), 'title': '新概念',
                     'topic_routes': [{'slug': 'test-methods', 'title': '测试方法',
                                       'overview': '这里按具体知识问题组织测试方法的阅读。',
                                       'question': '如何理解这一方法的机制和适用条件？'}],
                     'body_markdown': '新概念讨论明确的问题，机制需要对照原始资料，结论限定在该研究条件内。'*3+f'[S:{sid}]',
                     'needs_fact_review': False}
            with patch.object(wiki, 'call_llm', return_value=reply):
                self.assertEqual(wiki.prepare(source_ids=[sid]), 1)
            path = next(wiki.PENDING.glob('*.json'))
            draft = common.load_json(path)
            self.assertEqual(draft['mode'], 'update')
            if legacy:
                draft.pop('proposal_id'); draft.pop('mode')
                path.unlink()
                path = wiki.PENDING/(sid+'.json')
                common.save_json(path, draft)
            wiki.approve(path.stem)
            with common.connect() as db:
                self.assertEqual(db.execute('SELECT stage FROM sources WHERE id=?', (sid,)).fetchone()[0], 'approved')

    def test_update_cannot_silently_drop_old_claim(self):
        with common.connect() as db:
            db.execute("UPDATE sources SET stage='drafted' WHERE id=?", (self.sid,))
        common.save_json(wiki.PENDING/(self.sid+'.json'), {'source_id': self.sid, 'source_ids': [self.sid],
                         'action': 'update', 'slug': 'concept', 'title': '测试概念',
                         'base_version': wiki.page_version(self.page), 'body_markdown': self.rewritten, 'claims': []})
        with self.assertRaisesRegex(ValueError, '遗漏'):
            wiki.approve(self.sid)
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(common.load_json(self.meta_path), self.meta)

    def test_second_pass_runs_without_first_editorial_notes_and_ignores_knowledge_output(self):
        first = {'action': 'update', 'slug': 'concept', 'claims': copy.deepcopy(self.meta['claims']),
                 'body_markdown': self.rewritten}
        with patch.object(wiki, 'call_llm', return_value={**self.output, 'claims': [], 'slug': 'other'}) as editor:
            result = wiki.review_proposal([{'role': 'system', 'content': 'source instructions'}], first)
        editor.assert_called_once()
        self.assertTrue(result['editorial_reviewed'])
        self.assertEqual(result['claims'], first['claims'])
        self.assertEqual(result['slug'], 'concept')

    def test_mid_generation_source_change_cannot_produce_stale_rewrite(self):
        calls = []
        def model(messages):
            if not calls:
                path = common.RAW/(self.sid+'.json')
                record = common.load_json(path)
                record['text'] += ' A concurrent source refresh.'
                common.save_json(path, record)
            calls.append(1)
            return self.output
        with patch.object(wiki, 'call_llm', side_effect=model), self.assertRaisesRegex(ValueError, '改写期间'):
            wiki.prepare_rewrite('concept')
        self.assertEqual(list(wiki.PENDING.glob('*.json')), [])
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(self.status(), 'approved')

    def test_added_knowledge_link_is_not_an_expression_change(self):
        with patch.object(wiki, 'call_llm', return_value={**self.output, 'body_markdown':self.rewritten+' [[other|其他概念]]'}):
            with self.assertRaisesRegex(ValueError, '知识关联'):
                wiki.prepare_rewrite('concept')

    def test_archive_failure_restores_page_and_metadata(self):
        pid = self.create()
        with patch.object(wiki, '_archive', side_effect=OSError('archive failure')), self.assertRaises(OSError):
            wiki.approve(pid, fact_invariance_confirmed=True)
        self.assertEqual(self.page.read_text(), self.body)
        self.assertEqual(common.load_json(self.meta_path), self.meta)
        self.assertEqual(self.status(), 'approved')
        self.assertTrue((wiki.PENDING/(pid+'.json')).is_file())

    def test_competing_rewrites_save_only_one_proposal(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def model(messages):
            if '第二遍只优化' not in messages[0]['content']:
                barrier.wait(timeout=5)
            return self.output
        with patch.object(wiki, 'call_llm', side_effect=model), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(wiki.prepare_rewrite, 'concept') for _ in range(2)]
            results, errors = [], []
            for future in futures:
                try:
                    results.append(future.result(timeout=10))
                except ValueError as error:
                    errors.append(str(error))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(list(wiki.PENDING.glob('*.json'))), 1)
        self.assertEqual(self.status(), 'approved')


if __name__ == '__main__':
    unittest.main()
