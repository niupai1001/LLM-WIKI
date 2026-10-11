"""Real HTTP reviews use isolated originals and enforce versions and quotes."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SERVER = r'''
import os
from pathlib import Path
from station import common,settings,knowledge
root=common.ROOT
config=root/'config';config.mkdir(parents=True,exist_ok=True)
settings.TOPICS_FILE=config/'categories.json';settings.FEEDS_FILE=config/'feeds.json';settings.LOCK_FILE=config/'.lock'
knowledge._CONFIG=settings.TOPICS_FILE
common.save_json(settings.TOPICS_FILE,[{'id':'other','name':'其他','enabled':True,'description':''}])
common.save_json(settings.FEEDS_FILE,[])
common.connect().close()
sid='a'*16
text='The agent retrieves relevant memories before composing the working context.'
common.save_json(common.RAW/(sid+'.json'),{'id':sid,'title':'核查测试原文','url':'https://example.org/paper','kind':'paper','category':'other','text':text,'content_status':'full-text','bibliography':{}})
(common.WIKI/'qa-review.md').write_text('# 核查测试\n\n先检索记忆，再组织上下文。[/source](/source/'+sid+')')
common.save_json(common.META/'qa-review.json',{'title':'核查测试','slug':'qa-review','category':'other','source_ids':[sid], 'claims':[{'id':'memory','text':'先检索记忆，再组织上下文。','scope':'Section 2','source_ids':[sid]}]})
import uvicorn
uvicorn.run('station.web:app',host='127.0.0.1',port=int(os.environ['TEST_PORT']),log_level='error')
'''


class ReviewWorkspaceTests(unittest.TestCase):
    def test_api_quote_version_origin_and_reviewer_type(self):
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env=dict(os.environ,STATION_DATA=folder,TEST_PORT=str(port))
            process=subprocess.Popen([sys.executable,'-c',SERVER],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            base=f'http://127.0.0.1:{port}'
            def request(path,payload=None,origin=None):
                headers={'Content-Type':'application/json'}
                if origin:headers['Origin']=origin
                req=Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
                with urlopen(req,timeout=5) as response:return response.read().decode()
            try:
                for _ in range(80):
                    try:
                        page=request('/page/qa-review');break
                    except OSError:time.sleep(.05)
                else:self.fail('Isolated review server failed to start')
                import re
                def hidden(name):return re.search(f'name="{name}" value="([^"]+)"',page)[1]
                payload={name:hidden(name) for name in ['page_version','claim_version','claim_id']}
                payload.update(source_id='a'*16,source_version=re.search('data-source-version="([^"]+)"',page)[1],
                    verdict='supported',by='QA user',locator='Section 2',quote='The agent retrieves relevant memories before composing the working context.',
                    rationale='原文明确给出这两个环节的次序。',reviewer_type='agent')
                endpoint='/api/pages/qa-review/claim-reviews'
                with self.assertRaises(HTTPError) as blocked:request(endpoint,payload,origin='https://evil.example')
                self.assertEqual(blocked.exception.code,403)
                with self.assertRaises(HTTPError) as invalid:request(endpoint,dict(payload,quote='A fabricated quote that never appears in the original text.'))
                self.assertEqual(invalid.exception.code,400)
                self.assertIn('#fact-check-memory',request(endpoint,payload))
                updated=request('/page/qa-review')
                self.assertIn('人工复核 1 条',updated)
                self.assertNotIn('代理逐项核对',updated)
                with self.assertRaises(HTTPError) as stale:request(endpoint,dict(payload,page_version='outdated'))
                self.assertEqual(stale.exception.code,409)
                from pathlib import Path
                (Path(folder)/'wiki/qa-review.md').write_text('# 新正文\n正文改变。')
                self.assertIn('需重新核查',request('/page/qa-review'))
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                process.stderr.close()


if __name__=='__main__':unittest.main()
