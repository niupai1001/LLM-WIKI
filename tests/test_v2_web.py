"""Real HTTP library routing and immediate durable job creation."""
import json
import base64
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError


class V2WebTests(unittest.TestCase):
    def test_candidate_task_routes_and_links_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copytree(Path(__file__).resolve().parent.parent/'config',root/'config')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            process=subprocess.Popen([sys.executable,'-m','uvicorn','station.web:app','--host','127.0.0.1','--port',str(port),'--log-level','error'],env=dict(os.environ,STATION_DATA=str(root)),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            self.addCleanup(process.stderr.close)
            base=f'http://127.0.0.1:{port}'
            def request(path,data=None):
                req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','Origin':base})
                with urlopen(req,timeout=5) as response:
                    body=response.read().decode();return json.loads(body) if 'json' in response.headers.get('Content-Type','') else body
            try:
                for _ in range(100):
                    try:request('/api/libraries');break
                    except (OSError,ConnectionError):time.sleep(.03)
                request('/')
                sid='1234567890abcdef'
                raw=root/'raw'/(sid+'.json')
                raw.write_text(json.dumps({'id':sid,'title':'Archived input','url':'https://example.org/input','kind':'note','category':'research','content_status':'full-text','text':'Conditions must remain visible when archived input is compiled.'}))
                with sqlite3.connect(root/'state.db') as db:
                    db.execute('INSERT INTO sources(id,title,url,feed,kind,raw_path,captured_at,category,content_status) VALUES(?,?,?,?,?,?,?,?,?)',(sid,'Archived input','https://example.org/input','manual','note',str(raw),'2026-01-01','research','full-text'))
                created=request('/api/libraries',{'library_id':'candidate-v2'})
                self.assertEqual(created['library_id'],'candidate-v2')
                self.assertIn('library_id=candidate-v2',created['url'])
                category_path=root/'libraries'/'candidate-v2'/'config'/'categories.json'
                scoped_categories=json.loads(category_path.read_text())
                for category in scoped_categories: category['enabled']=category['id']=='agents'
                category_path.write_text(json.dumps(scoped_categories))
                imported=request('/api/import-file?library_id=candidate-v2',{'file_name':'agent-default.txt','content_base64':base64.b64encode(b'Agent evidence imported without a manual category.').decode()})
                imported_record=json.loads((root/'libraries'/'candidate-v2'/'raw'/(imported['source_id']+'.json')).read_text())
                self.assertEqual(imported_record['category'],'agents')
                comparison=request('/libraries?library_id=candidate-v2')
                self.assertIn('href="/?library_id=legacy"',comparison)
                page=request('/tasks?library_id=candidate-v2')
                self.assertIn('content="candidate-v2"',page)
                self.assertIn('/ask?library_id=candidate-v2',page)
                job=request('/api/jobs?library_id=candidate-v2',{'kind':'index','payload':{'embeddings':False}})
                self.assertEqual(job['status'],'queued');self.assertEqual(job['library_id'],'candidate-v2')
                self.assertEqual(request('/api/jobs')['jobs'],[])
                self.assertEqual(request('/api/jobs?library_id=candidate-v2')['jobs'][0]['id'],job['id'])
                with self.assertRaises(HTTPError) as error:request('/api/jobs/'+job['id'])
                self.assertEqual(error.exception.code,404)
                compilation=request('/api/compile?library_id=candidate-v2',{'source_id':sid,'target_slug':'existing-concept'})
                self.assertEqual(compilation['status'],'queued')
                self.assertEqual(compilation['kind'],'compile')
                self.assertEqual(compilation['payload']['target_slug'],'existing-concept')
                self.assertTrue(compilation['payload']['revisit'])
                self.assertIn(sid,compilation['input_versions'])
                self.assertEqual(request('/api/jobs')['jobs'],[])
                with self.assertRaises(HTTPError):request('/api/libraries/candidate-v2/activate',{})
                self.assertEqual(request('/api/libraries')['active'],'legacy')
            finally:
                process.terminate();process.wait(timeout=5)
