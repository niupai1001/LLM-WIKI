"""HTTP integration: new-source compilation and an independent rewrite review."""
import base64
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class RewriteWorkspaceTests(unittest.TestCase):
    def test_compile_rewrite_confirm_reject_and_legacy_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            script = r'''
import json, os
from station import wiki

def model(messages):
    if '第二遍只优化' in messages[0]['content']:
        first = json.loads(messages[-2]['content'].split('：\n',1)[1])
        return {'body_markdown':first['body_markdown'],'editorial_notes':'改善概念与机制衔接。','needs_fact_review':False}
    if '表达改写' in messages[0]['content'] and '仅改写下面' in messages[-1]['content']:
        context = json.loads(messages[-1]['content'].split('：\n',1)[1])
        return {'body_markdown':context['original_body'].replace('旧表达','新表达'),
                'editorial_notes':'换用更直接的主语，限定条件保持原样。','needs_fact_review':False,
                'claims': [], 'source_ids': [], 'title':'禁止改名'}
    context = json.loads(messages[-1]['content'].split('JSON数据：\n',1)[1])
    sid = context['sources'][0]['source_id']
    return {'action':'create','slug':'concept','title':'测试知识','needs_fact_review':False,
            'claims':[{'id':'mechanism','text':'机制限于受测任务','scope':'测试原文','status':'单源支持','source_ids':[sid], 'evidence_passages':[{'source_id':sid,'excerpt_id':'e1'}]}],
            'body_markdown':'旧表达解释一个明确的知识问题，机制包含输入、处理与输出；观察限于测试条件，不能推广到所有任务。'*2+'[S:'+sid+']'}
wiki.call_llm = model
import uvicorn
uvicorn.run('station.web:app',host='127.0.0.1',port=int(os.environ['TEST_PORT']),log_level='error')
'''
            process = subprocess.Popen([sys.executable, '-c', script], env=dict(os.environ, STATION_DATA=folder, TEST_PORT=str(port)), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            def request(path, payload=None):
                data = json.dumps(payload).encode() if payload is not None else None
                with urlopen(Request(f'http://127.0.0.1:{port}'+path, data=data, headers={'Content-Type':'application/json'}), timeout=10) as response:
                    value = response.read().decode()
                    return json.loads(value) if response.headers.get_content_type() == 'application/json' else value
            def states():
                with sqlite3.connect(root/'state.db') as db:
                    return db.execute('SELECT id,stage,claim_at FROM sources').fetchall()
            try:
                for _ in range(80):
                    try:
                        request('/'); break
                    except OSError:
                        if process.poll() is not None:
                            self.fail(process.stderr.read().decode())
                        time.sleep(.05)
                imported = request('/api/import-file', {'file_name':'source.txt','title':'测试原文','category':'agents',
                    'content_base64':base64.b64encode(('完整测试原文，限定机制在受测任务内。'*10).encode()).decode()})
                compiled = request('/api/compile', {'source_id':imported['source_id']})
                self.assertEqual(compiled['published'], 1)
                self.assertEqual(list((root/'pending').glob('*.json')), [])
                initial_path = next((root/'proposal_history').glob('*.json'))
                initial = json.loads(initial_path.read_text())
                self.assertTrue(initial['generation_complete'])
                self.assertEqual(initial['writing_passes'], 1)
                self.assertNotIn('editorial_reviewed', initial)
                old_meta = json.loads((root/'wiki_meta/concept.json').read_text())
                before = states()
                automatic = request('/api/pages/concept/rewrite', {})
                self.assertEqual(automatic['status'], 'published')
                self.assertEqual(before, states())
                self.assertIn('新表达', (root/'wiki/concept.md').read_text())
                generated = request('/api/pages/concept/rewrite', {'draft_only':True})
                pid = generated['proposal_id']
                self.assertNotEqual(pid, initial['proposal_id'])
                self.assertEqual(before, states())
                view = request('/drafts')
                for marker in ['表达改写', '原正文', '编写结果', '查看正文差异']:
                    self.assertIn(marker, view)
                self.assertNotIn('<strong>编辑说明</strong>', view)
                self.assertIn('改写表达', request('/page/concept'))
                for removed in ['四个审核问题', 'fact_invariance_confirmed', '批准并发布']:
                    self.assertNotIn(removed, view)
                with self.assertRaises(HTTPError):
                    request('/api/pages/concept/rewrite', {'draft_only':True})
                request('/api/drafts/'+pid+'/approve', {'fact_invariance_confirmed':True,'fact_review_note':'原文、旧稿、新稿范围相同。'})
                self.assertEqual(before, states())
                self.assertIn('新表达', (root/'wiki/concept.md').read_text())
                new_meta = json.loads((root/'wiki_meta/concept.json').read_text())
                for field in ['claims','conflicts','source_ids','category','page_kind','related_slugs','title','slug']:
                    self.assertEqual(new_meta[field], old_meta[field])
                next_pid = request('/api/pages/concept/rewrite', {'draft_only':True})['proposal_id']
                request('/api/drafts/'+next_pid+'/reject', {})
                self.assertEqual(before, states())
                self.assertFalse((root/'pending'/(next_pid+'.json')).exists())
                self.assertEqual(json.loads((root/'proposal_history'/(next_pid+'.json')).read_text())['outcome'], 'rejected')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                process.stderr.close()
