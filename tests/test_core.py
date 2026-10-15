from fixture_config import CHILD_CONFIG
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from station.common import canonical, identifier, safe_slug, source_id


class CoreTests(unittest.TestCase):
    def test_canonical_ignores_tracking(self):
        self.assertEqual(canonical("https://Example.com/news/?utm_source=abc&id=7#foo"), "https://example.com/news?id=7")

    def test_deduplicate_arxiv_versions(self):
        self.assertEqual(identifier("https://arxiv.org/abs/2410.12345v2"), "arxiv:2410.12345")
        self.assertEqual(source_id("https://arxiv.org/abs/2410.12345v2"), source_id("https://arxiv.org/abs/2410.12345v1"))

    def test_safe_slug(self):
        self.assertEqual(safe_slug("  My WIKI Page!  "), "my-wiki-page")

    def test_raw_archive_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            # Child process ensures data root is configured at module import time.
            env = dict(os.environ, STATION_DATA=t)
            script = """from station.common import connect
from station.collector import archive
record={'title':'Research','url':'https://example.org/article','feed':'test','kind':'paper','summary':'Abstract','text':'Original evidence'}
db=connect()
assert archive(record,db)
assert archive(record,db) is None
assert db.execute('SELECT count(*) FROM sources').fetchone()[0]==1
print('ARCHIVE_OK')
"""
            result = subprocess.run([sys.executable, "-c", CHILD_CONFIG + script], capture_output=True, text=True, env=env, check=True)
            self.assertIn("ARCHIVE_OK", result.stdout)


class WikiFlowTests(unittest.TestCase):
    def test_note_to_reviewed_wiki(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, STATION_DATA=root)
            script = r'''
from pathlib import Path
from station.collector import add_note
from station import wiki
from station.common import connect
p=Path('note-mvp-test.txt'); p.write_text('这是一个个人想法：关注森林冠层的季节变化，不是已证实结论。',encoding='utf-8')
try:
    sid=add_note('森林物候监测思路',str(p))
    wiki.call_llm=lambda messages: {'needs_fact_review':False,'action':'create','slug':'forest-phenology', 'title':'森林物候观测',
        'topic_routes':[{'slug':'test-methods', 'title':'测试方法', 'overview':'这里按具体知识问题组织测试方法的阅读。', 'question':'如何理解这一方法的机制和适用条件？'}],
        'body_markdown':'这是一个来源于个人笔记的研究设想，需要后续实验验证。\n\n可以探索森林冠层随季节变化的趋势及潜在影响。'}
    assert wiki.prepare(limit=1)==1
    wiki.approve(next(wiki.PENDING.glob('*.json')).stem)
    page=(Path(__import__('os').environ['STATION_DATA'])/'wiki'/'forest-phenology.md').read_text(encoding='utf-8')
    assert sid in page and '需要后续实验验证' in page
    assert connect().execute('SELECT stage FROM sources WHERE id=?',(sid,)).fetchone()[0]=='approved'
    print('WIKI_APPROVAL_OK')
finally:
    p.unlink(missing_ok=True)
'''
            result = subprocess.run([sys.executable, "-c", CHILD_CONFIG + script], capture_output=True, text=True, env=env, check=True)
            self.assertIn("WIKI_APPROVAL_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
