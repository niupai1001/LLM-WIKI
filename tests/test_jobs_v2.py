import concurrent.futures
import os
import json
import sys
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from station.workspaces import Workspace
from station import jobs, worker


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ws = SimpleNamespace(root=root, db=root / 'state.db', library_id='test')

    def test_durable_bound_payload_and_retry(self):
        job = jobs.enqueue(self.ws, 'compile', {'sources': ['a']}, {'a': 'v1'})
        self.assertEqual(jobs.get(self.ws, job['id'])['input_versions'], {'a': 'v1'})
        active = jobs.claim(self.ws)
        self.assertTrue(jobs.heartbeat(self.ws, active['id'], active['lease_token']))
        jobs.fail(self.ws, active['id'], active['lease_token'], 'fail')
        jobs.retry(self.ws, active['id'])
        again = jobs.claim(self.ws)
        self.assertEqual(again['attempts'], 2)
        self.assertNotEqual(again['lease_token'], active['lease_token'])
        with self.assertRaises(jobs.JobConflict):
            jobs.complete(self.ws, active['id'], active['lease_token'])
        jobs.complete(self.ws, again['id'], again['lease_token'], {'done': True})
        self.assertEqual(jobs.get(self.ws, again['id'])['status'], 'succeeded')

    def test_parallel_claim_has_one_compile_per_library(self):
        for _ in range(4):
            jobs.enqueue(self.ws, 'compile', {})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(lambda _: jobs.claim(self.ws), range(4)))
        self.assertEqual(sum(x is not None for x in claimed), 1)
        extraction = jobs.enqueue(self.ws, 'extract', {})
        self.assertEqual(jobs.claim(self.ws)['id'], extraction['id'])

    def test_target_retry_is_bound_to_the_failed_job_result(self):
        job=jobs.enqueue(self.ws,'compile',{})
        active=jobs.claim(self.ws)
        jobs.complete(self.ws,job['id'],active['lease_token'],{'status':'blocked','results':[{'slug':'concept-one','status':'blocked'},{'slug':'concept-two','status':'published'}]})
        with self.assertRaises(jobs.JobConflict):jobs.retry(self.ws,job['id'],target_slug='concept-two')
        queued=jobs.retry(self.ws,job['id'],target_slug='concept-one')
        self.assertEqual(queued['payload']['retry_target'],'concept-one')
        active=jobs.claim(self.ws);jobs.fail(self.ws,job['id'],active['lease_token'],'interrupted')
        queued=jobs.retry(self.ws,job['id'])
        self.assertNotIn('retry_target',queued['payload'])

    def test_expired_lease_is_fenced_and_requires_retry(self):
        job = jobs.enqueue(self.ws, 'compile', {})
        active = jobs.claim(self.ws)
        with sqlite3.connect(self.ws.root / 'jobs.db') as db:
            db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
        self.assertIsNone(jobs.claim(self.ws))
        self.assertEqual(jobs.get(self.ws, job['id'])['status'], 'failed')
        with self.assertRaises(jobs.JobConflict):
            jobs.heartbeat(self.ws, job['id'], active['lease_token'])

    def test_running_cancel_is_cooperative(self):
        job = jobs.enqueue(self.ws, 'compile', {})
        active = jobs.claim(self.ws)
        self.assertEqual(jobs.cancel(self.ws, job['id'])['status'], 'running')
        self.assertFalse(jobs.heartbeat(self.ws, job['id'], active['lease_token']))
        with self.assertRaises(jobs.JobConflict):
            jobs.check_active(self.ws, job['id'], active['lease_token'])
        jobs.fail(self.ws, job['id'], active['lease_token'], 'cancelled at boundary')
        self.assertEqual(jobs.get(self.ws, job['id'])['status'], 'cancelled')

    def test_worker_runs_explicit_workspace_without_environment_mutation(self):
        old = dict(os.environ)
        job = jobs.enqueue(self.ws, 'compile', {'title': 'test'}, {'a': 'v1'})
        def handler(ws, payload, jid):
            self.assertIs(ws, self.ws)
            self.assertEqual(payload['input_versions'], {'a': 'v1'})
            self.assertEqual(jid, job['id'])
            worker.check_cancelled()
            return {'ok': True}
        result = worker.run_once(self.ws, {'compile': handler})
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(dict(os.environ), old)

    def test_worker_cancel_before_publication(self):
        job = jobs.enqueue(self.ws, 'compile', {})
        def handler(ws, payload, jid):
            jobs.cancel(ws, jid)
            worker.check_cancelled()
            self.fail('must not publish')
        result = worker.run_once(self.ws, {'compile': handler})
        self.assertEqual(result['status'], 'cancelled')

    def test_library_identity_does_not_cross(self):
        job = jobs.enqueue(self.ws, 'index', {})
        other = SimpleNamespace(root=self.ws.root, db=self.ws.db, library_id='other')
        self.assertIsNone(jobs.get(other, job['id']))
        self.assertIsNone(jobs.claim(other))

    def test_default_index_handler_uses_real_workspace_and_search_module(self):
        ws = Workspace('index-smoke', self.ws.root / 'index-smoke')
        ws.wiki.mkdir(parents=True)
        ws.meta.mkdir()
        ws.raw.mkdir()
        (ws.root / 'config').mkdir()
        (ws.config / 'embedding.json').write_text('{}')
        (ws.wiki / 'lease.md').write_text('# Lease renewal\nRenew execution leases before publishing.')
        job = jobs.enqueue(ws, 'index', {'embeddings': False})
        result = worker.run_once(ws)
        self.assertEqual(result['id'], job['id'])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(result['result']['library_id'], ws.library_id)
        self.assertEqual(result['result']['documents'], 1)
        self.assertGreater(result['result']['chunks'], 0)
        with sqlite3.connect(ws.root / 'search.db') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0], 1)
        from station.workspaces import current_workspace
        self.assertIsNone(current_workspace())

    def test_main_follows_active_library_but_explicit_library_stays_fixed(self):
        from station import workspaces
        for explicit in (False, True):
            root = (self.ws.root / ('explicit' if explicit else 'active')).resolve()
            root.mkdir()
            second = root / 'libraries' / 'second'
            second.mkdir(parents=True)
            (root / 'library-registry.json').write_text(json.dumps({
                'version': 1, 'active': 'legacy', 'history': [{'from':'second','to':'legacy'}],
                'libraries': {'legacy': {'root': str(root)}, 'second': {'root': str(second)}}}))
            visited = []
            def one(ws):
                visited.append(ws.library_id)
                if len(visited) == 1:
                    workspaces.activate_library('second', root, rollback=True)
                    return {'processed': True}
                raise StopIteration('test loop complete')
            args = ['worker'] + (['legacy'] if explicit else [])
            with patch.object(sys, 'argv', args), patch.object(workspaces, 'base_root', return_value=root), patch.object(worker, 'run_once', side_effect=one):
                with self.assertRaises(StopIteration):
                    worker.main()
            self.assertEqual(visited, ['legacy', 'legacy' if explicit else 'second'])
