import json,sqlite3,tempfile,unittest
from pathlib import Path
from station import source_pruning as p,jobs
from station.workspaces import Workspace
class SourcePruningTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.ws=Workspace('preview',self.root/'data')
  self.sid='1234567890abcdef';w=self.ws
  w.raw.mkdir(parents=True);w.originals.mkdir();w.meta.mkdir();w.wiki.mkdir()
  (w.raw/(self.sid+'.json')).write_text(json.dumps({'id':self.sid,'title':'Paper','asset_path':self.sid+'.pdf'}))
  (w.originals/(self.sid+'.pdf')).write_bytes(b'%PDF-original')
  with sqlite3.connect(w.db) as db:db.execute('create table sources(id text primary key)');db.execute('insert into sources values(?)',(self.sid,))
 def test_removal_is_backed_up_and_library_local(self):
  result=p.prune_unpublished(self.ws,[self.sid],self.root/'backup');self.assertFalse((self.ws.raw/(self.sid+'.json')).exists())
  self.assertEqual((Path(result['backup'])/'originals'/(self.sid+'.pdf')).read_bytes(),b'%PDF-original')
  with sqlite3.connect(self.ws.db) as db:self.assertEqual(db.execute('select count(*) from sources').fetchone()[0],0)
 def test_referenced_source_is_not_removed(self):
  (self.ws.meta/'page.json').write_text(json.dumps({'source_ids':[self.sid]}))
  with self.assertRaisesRegex(ValueError,'published page'):p.prune_unpublished(self.ws,[self.sid],self.root/'backup')
  self.assertTrue((self.ws.raw/(self.sid+'.json')).exists());self.assertFalse((self.root/'backup').exists())
 def test_pending_task_prevents_removal(self):
  jobs.enqueue(self.ws,'compile',{'source_ids':[self.sid]})
  with self.assertRaisesRegex(ValueError,'pending tasks'):p.prune_unpublished(self.ws,[self.sid],self.root/'backup')
  self.assertTrue((self.ws.raw/(self.sid+'.json')).exists())
