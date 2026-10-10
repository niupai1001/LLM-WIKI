import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station import collector, common, wiki


class TopicSelectionTests(unittest.TestCase):
    def test_topic_selection_preserves_unselected_evidence_and_rejects_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            names = {'ROOT': root, 'RAW': root/'raw', 'WIKI': root/'wiki', 'META': root/'wiki_meta', 'ASSETS': root/'originals', 'PENDING': root/'pending', 'DB': root/'state.db'}
            patches = [patch.object(module, name, path) for module in [common, collector, wiki] for name, path in names.items() if hasattr(module, name)]
            for p in patches:
                p.start()
            try:
                ids = []
                for i in range(3):
                    path = root / f'note-{i}.md'
                    path.write_text('独立原始证据与机制解释。'*20)
                    ids.append(collector.import_file(f'资料{i}', path, 'agents'))
                def reply(messages):
                    context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
                    self.assertEqual(context['knowledge_topic'], '智能体规划机制')
                    self.assertEqual({s['source_id'] for s in context['sources']}, set(ids[:2]))
                    return {'action': 'create', 'slug': 'agent-planning', 'title': '智能体规划机制', 'body_markdown': '这是针对规划问题的知识提炼。证据限定于选中的两份原文，仍需核查结论与局限。'*3 + f'[S:{ids[0]}][:{ids[1]}] 相关概念 [[future-page|上下文管理]]。'}
                with patch.object(wiki, 'call_llm', side_effect=reply):
                    self.assertEqual(wiki.prepare(category='agents', source_ids=ids[:2], topic='智能体规划机制'), 1)
                proposal = common.load_json(next(common.PENDING.glob('*.json')))
                self.assertIn('上下文管理', proposal['body_markdown'])
                self.assertNotIn('[[future-page', proposal['body_markdown'])
                self.assertEqual(proposal['unresolved_wiki_links'], ['future-page'])
                self.assertIn('/source/' + ids[1], proposal['body_markdown'])
                self.assertNotIn('[:', proposal['body_markdown'])
                with patch.object(wiki, 'call_llm', return_value={'action': 'create', 'slug': 'unknown-evidence', 'body_markdown': '来源必须属于已提供原文。'*8 + '[:ffffffffffffffff]'}):
                    self.assertEqual(wiki.prepare(source_ids=[ids[2]]), 0)
                with common.connect() as db:
                    self.assertEqual(db.execute('SELECT stage FROM sources WHERE id=?', (ids[2],)).fetchone()[0], 'new')
                with self.assertRaises(ValueError):
                    wiki.prepare(source_ids=[ids[2], 'f'*16])
                with self.assertRaises(ValueError):
                    wiki.prepare(source_ids=[])
            finally:
                for p in reversed(patches):
                    p.stop()


if __name__ == '__main__':
    unittest.main()
