"""Isolated HTTP settings workflow and its effect on existing originals/compilation."""
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


SERVER = r'''
import json, os
from pathlib import Path
from station import settings, knowledge, wiki, collector
root = Path(os.environ['TEST_CONFIG'])
settings.TOPICS_FILE = root / 'categories.json'
settings.FEEDS_FILE = root / 'feeds.json'
settings.LOCK_FILE = root / '.settings.lock'
knowledge._CONFIG = settings.TOPICS_FILE
def model(messages):
    context = json.loads(messages[-1]['content'].split('JSON数据：\n', 1)[1])
    assert context['category'] == '智能体长期记忆'
    assert context['category_focus'] == '长期记忆的评测与边界'
    sid = context['sources'][0]['source_id']
    return {'action':'create', 'slug':'agent-memory', 'title':'智能体记忆',
            'body_markdown':'这是从测试原文提炼的概念。当前证据来自一份资料，结论限于原文条件。' * 4 + '[S:' + sid + ']'}
wiki.call_llm = model
import uvicorn
uvicorn.run('station.web:app', host='127.0.0.1', port=int(os.environ['TEST_PORT']), log_level='error')
'''


class SettingsWorkspaceTests(unittest.TestCase):
    def test_save_settings_changes_navigation_selection_and_compilation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'config'
            config.mkdir()
            (config / 'categories.json').write_text(json.dumps([{'id':'other','name':'其他'}]))
            (config / 'feeds.json').write_text('[]')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, STATION_DATA=str(root / 'data'), TEST_CONFIG=str(config), TEST_PORT=str(port))
            process = subprocess.Popen([sys.executable, '-c', SERVER], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            base = f'http://127.0.0.1:{port}'

            def request(path, payload=None, origin=None):
                headers = {'Content-Type':'application/json'}
                if origin:
                    headers['Origin'] = origin
                req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
                with urlopen(req, timeout=5) as response:
                    raw = response.read().decode()
                    return json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw

            def save(path, **fields):
                return request(path, dict(version=request('/api/settings')['version'], **fields))

            try:
                for _ in range(80):
                    if process.poll() is not None:
                        self.fail('Server failed: ' + process.stderr.read().decode()[-1000:])
                    try:
                        request('/api/settings')
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    self.fail('Server did not start')
                self.assertIn('新增订阅', request('/settings'))
                with self.assertRaises(HTTPError) as blocked:
                    request('/api/settings/topics', {}, origin='https://evil.example')
                self.assertEqual(blocked.exception.code, 403)
                topic = save('/api/settings/topics', name='智能体记忆', description='记忆研究')['topics'][-1]
                tid = topic['id']
                self.assertIn('智能体记忆', request('/'))
                self.assertIn(f'value="{tid}"', request('/sources'))
                feed = save('/api/settings/feeds', name='记忆订阅', url='https://example.org/memory.xml',
                            category=tid, include_keywords=['memory'], max_age_days=14)['feeds'][0]
                self.assertIn('记忆订阅', request('/collection'))
                self.assertIn('仅最近 14 天', request('/collection'))
                stale = request('/api/settings')['version']
                save('/api/settings/topics', id=tid, name='智能体长期记忆', description='长期记忆的评测与边界')
                self.assertIn('智能体长期记忆', request('/collection'))
                with self.assertRaises(HTTPError) as conflict:
                    request('/api/settings/feeds', dict(version=stale, id=feed['id'], name='过期编辑'))
                self.assertEqual(conflict.exception.code, 409)
                source = request('/api/import-file', {'file_name':'memory.md', 'category':tid,
                    'content_base64':base64.b64encode(('测试原文：长期记忆的实验依据与适用边界。' * 30).encode()).decode()})
                stopped = save('/api/settings/topics', id=tid, enabled=False)
                self.assertFalse(stopped['feeds'][0]['enabled'])
                self.assertNotIn('记忆订阅', request('/collection'))
                self.assertIn('测试原文', request(source['url']))
                self.assertNotIn('data-source-compile', request(source['url']))
                self.assertEqual(request('/api/compile', {})['count'], 0)
                with self.assertRaises(HTTPError) as disabled:
                    request('/api/compile', {'category':tid})
                self.assertEqual(disabled.exception.code, 400)
                with self.assertRaises(HTTPError) as disabled_import:
                    request('/api/import-file', {'file_name':'disabled.md', 'category':tid,
                        'content_base64':base64.b64encode(b'original').decode()})
                self.assertEqual(disabled_import.exception.code, 400)
                resumed = save('/api/settings/topics', id=tid, enabled=True)
                self.assertFalse(resumed['feeds'][0]['enabled'])
                save('/api/settings/feeds', id=feed['id'], enabled=True,
                     url='https://example.org/memory-2.xml', include_keywords=['memory','retrieval'],
                     require_keywords=['llm'], exclude_keywords=['sponsored'], max_age_days=None)
                self.assertIn('不限发布时间', request('/collection'))
                self.assertEqual(request('/api/compile', {'category':tid})['count'], 1)
                save('/api/settings/feeds/' + feed['id'] + '/delete')
                self.assertIn('测试原文', request(source['url']))
                with self.assertRaises(HTTPError) as referenced:
                    save('/api/settings/topics/' + tid + '/delete')
                self.assertEqual(referenced.exception.code, 400)
                self.assertIn('原始资料', referenced.exception.read().decode())
                unused = save('/api/settings/topics', name='临时主题')['topics'][-1]['id']
                save('/api/settings/topics/' + unused + '/delete')
                self.assertNotIn('临时主题', request('/settings'))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stderr.close()


if __name__ == '__main__':
    unittest.main()
