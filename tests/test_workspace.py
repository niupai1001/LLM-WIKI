from fixture_config import CHILD_CONFIG
"""Isolated HTTP workflow: original import → topic synthesis → review → graph."""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class WorkspaceFlowTests(unittest.TestCase):
    def test_originals_topic_wiki_review_graph_and_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, STATION_DATA=root)
            script = r'''
import os
from station import wiki

def llm(messages):
    import json
    if '第二遍只优化' in messages[0]['content']:
        draft = json.loads(messages[-2]['content'].split('：\n', 1)[1])
        return {**draft, 'needs_fact_review': False}
    context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
    ids = [s['source_id'] for s in context['sources']]
    assert len(ids) == 2, 'same-category originals must be synthesized together'
    assert all('text' in s for s in context['sources'])
    assert not any(s['is_excerpt'] for s in context['sources']), 'originals fitting the budget must be passed in full'
    return {'action':'create','slug':'evidence-synthesis','title':'证据综合',
            'topic_routes':[{'slug':'test-methods', 'title':'测试方法',
                             'overview':'这里按具体知识问题组织测试方法的阅读。',
                             'question':'如何理解这一方法的机制和适用条件？'}],
            'related_slugs':[], 'needs_fact_review':False, 'body_markdown':'综合两份原始资料来组织可维护的主题知识。来源相互补充，但仍需实验验证。\n\n证据依据：' + ''.join('[S:'+sid+']' for sid in ids)}
wiki.call_llm = llm
import uvicorn
uvicorn.run('station.web:app', host='127.0.0.1', port=int(os.environ['TEST_PORT']), log_level='error')
'''
            env['TEST_PORT'] = str(port)
            process = subprocess.Popen([sys.executable, '-c', CHILD_CONFIG + script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            base = f'http://127.0.0.1:{port}'
            def request(path, payload=None, origin=None):
                headers = {'Content-Type':'application/json'}
                if origin:
                    headers['Origin'] = origin
                req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
                with urlopen(req, timeout=5) as response:
                    raw = response.read().decode()
                    return json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
            try:
                for _ in range(80):
                    if process.poll() is not None:
                        self.fail('Test server failed: ' + process.stderr.read().decode()[-2000:])
                    try:
                        request('/')
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    self.fail('Test server did not start')
                self.assertEqual(request('/api/graph'), {'nodes':[], 'edges':[]})
                self.assertIn('知识 Wiki', request('/'))
                self.assertIn('资料库是空的', request('/sources'))
                self.assertIn('graph-canvas', request('/graph'))
                with self.assertRaises(HTTPError) as blocked:
                    request('/api/import-file', {}, origin='https://evil.example')
                self.assertEqual(blocked.exception.code, 403)
                with self.assertRaises(HTTPError) as invalid_pdf:
                    request('/api/import-file', {'file_name':'broken.pdf','category':'ai', 'content_base64':base64.b64encode(b'not a PDF').decode()})
                self.assertEqual(invalid_pdf.exception.code, 400)
                from pypdf import PdfWriter
                import io
                buffer = io.BytesIO()
                writer = PdfWriter()
                writer.add_blank_page(width=100, height=100)
                writer.write(buffer)
                scan = request('/api/import-file', {'file_name':'scan.pdf','category':'other', 'content_base64':base64.b64encode(buffer.getvalue()).decode()})
                scan_page = request(scan['url'])
                self.assertIn('原件已保存 · 需 OCR',scan_page)
                self.assertNotIn('订阅摘要（非原文）',scan_page)
                ids = []
                for i in range(2):
                    text = f'# 研究原文 {i}\n\n这是完整的原创测试资料。\n\n' + '正文证据、方法和局限。' * 4200
                    result = request('/api/import-file', {'file_name':f'original-{i}.md','title':f'原文{i}', 'category':'research', 'content_base64':base64.b64encode(text.encode()).decode()})
                    sid = result['source_id']
                    ids.append(sid)
                    raw = json.loads((Path(root)/'raw'/f'{sid}.json').read_text())
                    self.assertEqual(raw['text'], text)
                    self.assertEqual(raw['category'],'research')
                    self.assertEqual(raw['content_status'],'full-text')
                    self.assertIn(text[-100:], request('/source/'+sid))
                    self.assertIn('完整的原创测试资料', request('/source/'+sid+'/original'))
                compiled = request('/api/compile', {'category':'research','draft_only':True})
                self.assertEqual(compiled['count'],1)
                pending = next((Path(root)/'pending').glob('*.json'))
                draft = json.loads(pending.read_text())
                self.assertEqual(set(draft['source_ids']),set(ids))
                self.assertIn('2 份来源', request('/drafts'))
                approved = request('/api/drafts/'+draft['proposal_id']+'/approve', {})
                page = request(approved['url'])
                self.assertIn('证据综合',page)
                for sid in ids:
                    self.assertIn('/source/'+sid,page)
                graph = request('/api/graph')
                self.assertEqual(len([e for e in graph['edges'] if e['type']=='evidence']),2)
                self.assertTrue(any(n['id']=='wiki:evidence-synthesis' for n in graph['nodes']))
                self.assertFalse(pending.exists())
                self.assertEqual(request('/api/compile', {'category':'research','draft_only':True})['count'],0)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stderr.close()

    def test_summary_excluded_and_stale_draft_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            script = r'''
from pathlib import Path
from station import common, collector, wiki
from unittest.mock import patch
root = Path(__import__('os').environ['STATION_DATA'])
with patch.object(collector, 'fetch_bytes', side_effect=OSError('unavailable')):
    with common.connect() as db:
        sid = collector.archive({'title':'Summary','url':'https://example.org/summary','summary':'Only summary','category':'ai'},db)
wiki.call_llm = lambda messages: (_ for _ in ()).throw(AssertionError('summary must not invoke model'))
assert wiki.prepare() == 0
p=root/'note.md';p.write_text('原始笔记正文，明确这是作者提出的观点，尚未经实验证实。' * 8)
sid=collector.add_note('原始观点',p)
wiki.call_llm = lambda messages: {'action':'create','slug':'test-topic','title':'主题',
    'topic_routes':[{'slug':'test-methods', 'title':'测试方法', 'overview':'这里按具体知识问题组织测试方法的阅读。', 'question':'如何理解这一方法的机制和适用条件？'}],
    'body_markdown':'个人观点需要验证，现有证据仅来自一份笔记，不能作为领域共识。' * 4}
assert wiki.prepare() == 1
(common.WIKI/'test-topic.md').write_text('# 人工并行创建的页面\n保留现有内容')
try:
    wiki.approve(next(common.PENDING.glob('*.json')).stem)
    raise AssertionError('stale draft must fail')
except ValueError:
    pass
assert '保留现有内容' in (common.WIKI/'test-topic.md').read_text()
wiki.reject(next(common.PENDING.glob('*.json')).stem)
with common.connect() as db:
    assert db.execute('SELECT stage FROM sources WHERE id=?',(sid,)).fetchone()[0]=='new'
print('STATE_AND_CONFLICT_OK')
'''
            result = subprocess.run([sys.executable,'-c',CHILD_CONFIG+script],env=dict(os.environ,STATION_DATA=root),capture_output=True,text=True,check=True)
            self.assertIn('STATE_AND_CONFLICT_OK',result.stdout)

    def test_long_source_budgets_claiming_and_invalid_citations(self):
        with tempfile.TemporaryDirectory() as root:
            script = r'''
from pathlib import Path
from station import collector,common,wiki
import json, os
root = Path(os.environ['STATION_DATA'])
os.environ['LLM_CONTEXT_CHARS']='64000'
for i in range(8):
    p=root/f'note-{i}.md'; p.write_text(f'原文{i}\n'+'证据和局限。'*2500)
    collector.add_note(f'原文{i}',p,category='ai')
def model(messages):
    context=json.loads(messages[-1]['content'].split('JSON数据：\n',1)[1])
    evidence=context['sources']
    assert len(evidence)==8
    assert all(len(s['text'])>0 for s in evidence)
    assert sum(len(s['text']) for s in evidence)<=64000
    assert all(s['is_excerpt'] for s in evidence)
    assert wiki.prepare(category='ai')==0, 'concurrent compilation must not duplicate reserved evidence'
    try:
        collector.set_category(evidence[0]['source_id'],'research')
        raise AssertionError('category change during generation should be blocked')
    except ValueError:
        pass
    return {'action':'create','slug':'long-topic','title':'综合主题',
            'topic_routes':[{'slug':'test-methods', 'title':'测试方法',
                             'overview':'这里按具体知识问题组织测试方法的阅读。',
                             'question':'如何理解这一方法的机制和适用条件？'}],
            'body_markdown':'综合多份长资料时，每份证据都有阅读预算，并保留来源和不确定性。'*3 + '[伪造引用](/source/ffffffffffffffff)'}
wiki.call_llm=model
assert wiki.prepare()==0
with common.connect() as db:
    assert db.execute("SELECT count(*) FROM sources WHERE stage='new'").fetchone()[0]==8
attempts=[]
def retry_model(messages):
    attempts.append(1)
    if len(attempts)==1:
        raise json.JSONDecodeError('Malformed model JSON','extra',0)
    return {'action':'create','slug':'long-topic','title':'综合主题',
            'topic_routes':[{'slug':'test-methods', 'title':'测试方法',
                             'overview':'这里按具体知识问题组织测试方法的阅读。',
                             'question':'如何理解这一方法的机制和适用条件？'}],
            'body_markdown':'综合多份长资料时，每份证据都有阅读预算，并保留来源和不确定性。'*3}
wiki.call_llm=retry_model
assert wiki.prepare()==1
assert len(attempts)==2, 'JSON retry only; no automatic editorial call'
assert len(common.load_json(next(common.PENDING.glob('*.json')))['source_ids'])==8
print('BUDGET_CLAIM_AND_CITATIONS_OK')
'''
            result=subprocess.run([sys.executable,'-c',CHILD_CONFIG+script],env=dict(os.environ,STATION_DATA=root),capture_output=True,text=True,check=True)
            self.assertIn('BUDGET_CLAIM_AND_CITATIONS_OK',result.stdout)

    def test_page_changed_during_model_request_requires_new_review(self):
        with tempfile.TemporaryDirectory() as root:
            script = r'''
from pathlib import Path
from station import collector,common,wiki
import os
root=Path(os.environ['STATION_DATA'])
p=root/'one.md';p.write_text('第一份完整原文，讨论主题证据与不确定性。'*6)
sid=collector.add_note('第一份原文',p,category='ai')
wiki.call_llm=lambda messages:{'needs_fact_review':False,'action':'create','slug':'evidence-topic','title':'主题',
    'topic_routes':[{'slug':'test-methods', 'title':'测试方法', 'overview':'这里按具体知识问题组织测试方法的阅读。', 'question':'如何理解这一方法的机制和适用条件？'}],
    'body_markdown':'现有资料只提供作者观点，需要更多验证，不能将其当作共识。'*3}
assert wiki.prepare()==1
wiki.approve(next(common.PENDING.glob('*.json')).stem)
p=root/'two.md';p.write_text('第二份完整原文，提供后续实验证据并讨论新的不确定性。'*6)
sid2=collector.add_note('第二份原文',p,category='ai')
def model(messages):
    target=common.WIKI/'evidence-topic.md'
    target.write_text(target.read_text()+'\n\n人工同时补充的重要信息。')
    return {'action':'update','slug':'evidence-topic','title':'主题更新',
            'topic_routes':[{'slug':'test-methods', 'title':'测试方法',
                             'overview':'这里按具体知识问题组织测试方法的阅读。',
                             'question':'如何理解这一方法的机制和适用条件？'}],
            'body_markdown':'模型请求期间知识页面被人工编辑，不能直接覆盖该更新。'*3}
wiki.call_llm=model
assert wiki.prepare()==1
try:
    wiki.approve(next(common.PENDING.glob('*.json')).stem)
    raise AssertionError('concurrent edit must invalidate old draft')
except ValueError:
    pass
assert '人工同时补充的重要信息' in (common.WIKI/'evidence-topic.md').read_text()
print('CONCURRENT_EDIT_PROTECTED')
'''
            result=subprocess.run([sys.executable,'-c',CHILD_CONFIG+script],env=dict(os.environ,STATION_DATA=root),capture_output=True,text=True,check=True)
            self.assertIn('CONCURRENT_EDIT_PROTECTED',result.stdout)
