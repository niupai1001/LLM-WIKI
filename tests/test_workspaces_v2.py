"""Independent library copies, request-local compatibility and explicit activation gates."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from station import common
from station.workspaces import Workspace,initialize_candidate,resolve_workspace,synchronize_candidate,use_workspace,activate_library,registry

SID='1234567890abcdef'


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.legacy=Workspace('legacy',self.root)
        self.legacy.root.joinpath('config').mkdir()
        shutil.copytree(Path(__file__).resolve().parent.parent/'config',self.root/'config',dirs_exist_ok=True)
        with use_workspace(self.legacy),common.connect() as db:
            record={'id':SID,'title':'公开测试资料','url':'https://example.org/public','kind':'paper','category':'research','content_status':'full-text','text':'An archived public source supports this conditional mechanism.','asset_path':SID+'.txt'}
            common.save_json(self.legacy.raw/(SID+'.json'),record)
            (self.legacy.originals/(SID+'.txt')).write_text(record['text'])
            db.execute('INSERT INTO sources(id,title,url,feed,kind,raw_path,captured_at,category,content_status) VALUES (?,?,?,?,?,?,?,?,?)',(SID,record['title'],record['url'],'test','paper',str(self.legacy.raw/(SID+'.json')),common.utcnow(),'research','full-text'))
            (self.legacy.wiki/'original.md').write_text('# 旧页面\n\n保留旧版解释。')
            common.save_json(self.legacy.meta/'original.json',{'slug':'original','title':'旧页面','claims':[]})

    def test_copy_is_independent_and_baseline_is_not_official_wiki(self):
        ws=initialize_candidate(root=self.root)
        original=self.legacy.originals/(SID+'.txt');copied=ws.originals/original.name
        self.assertNotEqual(original.stat().st_ino,copied.stat().st_ino)
        copied.write_text('candidate only')
        self.assertNotEqual(original.read_text(),copied.read_text())
        self.assertTrue((ws.root/'baseline/wiki/original.md').exists())
        self.assertFalse((ws.wiki/'original.md').exists())
        with sqlite3.connect(ws.db) as db:
            self.assertEqual(db.execute('SELECT raw_path FROM sources').fetchone()[0],str(ws.raw/(SID+'.json')))
        self.assertEqual(registry(self.root)['active'],'legacy')

    def test_thread_contexts_and_configuration_do_not_leak(self):
        candidate=initialize_candidate(root=self.root)
        before=dict(os.environ)
        def read(ws):
            with use_workspace(ws):
                return str(common.RAW),str(common.ROOT/'claim_reviews'),str(__import__('station.settings',fromlist=['CONFIG_DIR']).CONFIG_DIR)
        with ThreadPoolExecutor(max_workers=2) as pool:
            legacy,new=list(pool.map(read,[self.legacy,candidate]))
        self.assertEqual(legacy[0],str(self.legacy.raw));self.assertEqual(new[0],str(candidate.raw))
        self.assertNotEqual(legacy[1],new[1]);self.assertNotEqual(legacy[2],new[2])
        self.assertEqual(dict(os.environ),before)

    def test_sync_source_and_gate_do_not_activate_unverified_library(self):
        ws=initialize_candidate(root=self.root)
        record=common.load_json(self.legacy.raw/(SID+'.json'));record['text']+=' New evidence.'
        common.save_json(self.legacy.raw/(SID+'.json'),record)
        result=synchronize_candidate(root=self.root)
        self.assertEqual(result['changed_source_ids'],[SID])
        self.assertEqual(common.load_json(ws.raw/(SID+'.json')),record)
        with self.assertRaises(ValueError):activate_library('candidate-v2',root=self.root)
        self.assertEqual(resolve_workspace(root=self.root).library_id,'legacy')

    def test_asset_only_sync_invalidates_parse_and_resets_source_state(self):
        from station import documents, search
        ws=initialize_candidate(root=self.root)
        parsed=documents.parse_source(ws,SID)
        raw_before=(ws.raw/(SID+'.json')).read_bytes()
        with sqlite3.connect(ws.db) as db:
            db.execute("UPDATE sources SET stage='approved',claim_at='reserved' WHERE id=?",(SID,))
        original=self.legacy.originals/(SID+'.txt')
        original.write_text('A corrected archived asset with different conditions.')
        result=synchronize_candidate(root=self.root)
        self.assertEqual(result['changed_source_ids'],[SID])
        self.assertEqual((ws.originals/original.name).read_bytes(),original.read_bytes())
        self.assertEqual((ws.raw/(SID+'.json')).read_bytes(),raw_before)
        self.assertNotEqual(documents.source_version(ws,SID),parsed['source_version'])
        with self.assertRaisesRegex(ValueError,'stale'):search.source_segments(ws,SID)
        with sqlite3.connect(ws.db) as db:
            self.assertEqual(db.execute('SELECT stage,claim_at FROM sources WHERE id=?',(SID,)).fetchone(),('new',None))
            self.assertEqual(db.execute("SELECT state FROM publication_journal WHERE proposal_id='source-sync'").fetchone()[0],'finished')
        self.assertEqual(synchronize_candidate(root=self.root)['changed_source_ids'],[])

    def test_sync_preserves_candidate_additions_and_copies_unreferenced_assets(self):
        ws=initialize_candidate(root=self.root)
        added=ws.originals/'candidate-only.txt';added.write_text('Candidate knowledge is retained on rollback.')
        supplemental=self.legacy.originals/'supplement.txt';supplemental.write_text('A new archived supplement.')
        synchronize_candidate(root=self.root)
        self.assertEqual((ws.originals/supplemental.name).read_text(),supplemental.read_text())
        self.assertTrue(added.exists())

    def test_sync_missing_referenced_original_keeps_missing_content_visible(self):
        from station.documents import source_version
        ws=initialize_candidate(root=self.root)
        (self.legacy.originals/(SID+'.txt')).unlink()
        result=synchronize_candidate(root=self.root)
        self.assertEqual(result['changed_source_ids'],[SID])
        self.assertFalse((ws.originals/(SID+'.txt')).exists())
        self.assertEqual(source_version(ws,SID),source_version(self.legacy,SID))

    def test_unregistered_library_and_registry_path_escape_are_rejected(self):
        with self.assertRaises(ValueError):resolve_workspace('../outside',root=self.root)
        info=registry(self.root);info['libraries']['bad']={'root':'/tmp/outside-library'}
        common.save_json(self.root/'library-registry.json',info)
        with self.assertRaises(ValueError):resolve_workspace('bad',root=self.root)

    def test_url_keeps_physical_page_and_pins_identity(self):
        self.assertEqual(self.legacy.url('/source/id?page=2&library_id=bad#quote'),'/source/id?page=2&library_id=legacy#quote')

    def test_rollback_cannot_activate_a_never_verified_candidate(self):
        initialize_candidate(root=self.root)
        with self.assertRaisesRegex(ValueError,'曾经启用'):activate_library('candidate-v2',root=self.root,rollback=True)
        self.assertEqual(registry(self.root)['active'],'legacy')

    def test_rollback_preserves_additions_and_reports_unsynchronized_knowledge(self):
        ws=initialize_candidate(root=self.root)
        info=registry(self.root);info['active']='candidate-v2'
        # Fixture simulates an already completed cutover; no real acceptance is bypassed.
        info['history']=[{'from':'legacy','to':'candidate-v2'}]
        common.save_json(self.root/'library-registry.json',info)
        (ws.wiki/'new-mechanism.md').write_text('# New mechanism\n\nRetained candidate knowledge.')
        common.save_json(ws.raw/('f'*16+'.json'),{'id':'f'*16,'text':'New candidate source'})
        result=activate_library('legacy',root=self.root,rollback=True)
        self.assertEqual(result['active'],'legacy')
        report=result['rollback_report']
        self.assertIn({'id':'new-mechanism','change':'added'},report['unsynchronized_pages'])
        self.assertIn({'id':'f'*16,'change':'added'},report['unsynchronized_sources'])
        self.assertTrue((ws.wiki/'new-mechanism.md').exists())
        backup=Path(result['backup_path'])
        self.assertTrue((backup/'candidate-v2/wiki/new-mechanism.md').exists())
        with sqlite3.connect(backup/'legacy/state.db') as db:
            self.assertEqual(db.execute('SELECT id FROM sources').fetchone()[0],SID)

    def test_old_queued_job_outside_recent_history_still_blocks_cutover(self):
        from station import jobs
        ws=initialize_candidate(root=self.root)
        info=registry(self.root);info['active']='candidate-v2'
        common.save_json(self.root/'library-registry.json',info)
        pending=jobs.enqueue(ws,'index',{})
        with sqlite3.connect(ws.root/'jobs.db') as db:
            db.executemany("INSERT INTO jobs(id,library_id,kind,payload,input_versions,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",[(f'done-{i}',ws.library_id,'index','{}','{}','succeeded',pending['created_at']+i+1,pending['created_at']+i+1) for i in range(1001)])
        self.assertFalse(any(j['status']=='queued' for j in jobs.list_jobs(ws,limit=1000)))
        with self.assertRaisesRegex(ValueError,'排队'):activate_library('legacy',root=self.root,rollback=True)
