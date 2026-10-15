from fixture_config import CHILD_CONFIG
import os
import subprocess
import sys
import tempfile
import unittest


class KnowledgeTests(unittest.TestCase):
    def run_isolated(self, script):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, STATION_DATA=root)
            subprocess.run([sys.executable, "-c", CHILD_CONFIG + script], env=env, text=True,
                           capture_output=True, check=True)

    def test_empty_graph_does_not_invent_nodes(self):
        self.run_isolated("""
from station.knowledge import graph_data, wiki_pages
assert graph_data() == {'nodes': [], 'edges': []}
assert wiki_pages() == []
""")

    def test_graph_links_only_existing_pages_and_approved_evidence(self):
        self.run_isolated(r'''
from station.common import connect, WIKI, META, RAW, save_json
from station.knowledge import graph_data, wiki_pages
sid = '0123456789abcdef'
db = connect()
save_json(RAW / (sid + '.json'), {'id':sid, 'title':'原始实验', 'text':'完整实验正文'})
db.execute('INSERT INTO sources(id,title,url,feed,kind,raw_path,captured_at,stage,category,content_status) VALUES(?,?,?,?,?,?,?,?,?,?)',
           (sid,'原始实验','https://example.org/experiment','manual','article',str(RAW / (sid + '.json')),'2026-10-09','approved','ai','full-text'))
db.commit()
WIKI.joinpath('attention.md').write_text('# 注意力\n\n[[transformer|Transformer]] 与 [[invented]]。\n\n`[[code-example]]`\n', encoding='utf-8')
WIKI.joinpath('transformer.md').write_text('# Transformer\n\n已有知识。', encoding='utf-8')
save_json(META / 'attention.json', {'category':'ai', 'source_ids':[sid, sid, 'missing-source'], 'related_slugs':['transformer','transformer','invented'], 'updated_at':'2026-10-09'})
save_json(META / 'transformer.json', {'category':'ai'})
pages = wiki_pages()
assert len(pages) == 2 and pages[0]['title'] == '注意力'
assert pages[0]['updated_at'] == '2026-10-09'
graph = graph_data()
ids = {n['id'] for n in graph['nodes']}
assert ids == {'wiki:attention', 'wiki:transformer', 'source:'+sid, 'category:ai'}, ids
edges = {(e['source'],e['target'],e['type']) for e in graph['edges']}
assert len(edges) == len(graph['edges']), graph['edges']
assert ('wiki:attention','wiki:transformer','related') in edges
assert ('wiki:attention','source:'+sid,'evidence') in edges
assert all(e['source'] in ids and e['target'] in ids for e in graph['edges'])
assert len([e for e in graph['edges'] if e['type'] == 'category']) == 3
db.execute('UPDATE sources SET stage="new" WHERE id=?', (sid,)); db.commit(); db.close()
assert not any(e['type'] == 'evidence' for e in graph_data()['edges'])
''')

    def test_categories_and_malformed_optional_metadata(self):
        self.run_isolated(r'''
from station.common import connect, WIKI, META
from station.knowledge import categories, category_name, validate_category, wiki_pages
connect().close()
assert category_name(validate_category('ai')) == '人工智能'
assert any(c['id'] == 'other' for c in categories())
for invalid in ['unknown', '../ai', None, []]:
    try:
        validate_category(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid category accepted')
WIKI.joinpath('topic.md').write_text('# 主题\n正文', encoding='utf-8')
META.joinpath('topic.json').write_text('{broken', encoding='utf-8')
page = wiki_pages()[0]
assert page['category'] == 'other' and page['source_ids'] == []
META.joinpath('topic.json').write_text('{"category":[],"source_ids":"invalid"}', encoding='utf-8')
assert wiki_pages()[0]['category'] == 'other'
''')


if __name__ == '__main__':
    unittest.main()
