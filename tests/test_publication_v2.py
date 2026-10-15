from pathlib import Path
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from station import publication


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ws = SimpleNamespace(root=root, db=root / 'state.db', library_id='test')
        (root / 'wiki').mkdir()
        (root / 'wiki' / 'old.md').write_text('old')
        with sqlite3.connect(self.ws.db) as db:
            db.execute('CREATE TABLE example(value TEXT)')
            db.execute("INSERT INTO example VALUES('old')")

    def test_file_and_database_commit_and_dirty(self):
        result = publication.publish(self.ws, {'wiki/old.md': 'new', 'meta/new.json': b'{}'}, 'p1',
                                     lambda db: db.execute("UPDATE example SET value='new'"))
        self.assertEqual(result['state'], 'finished')
        self.assertEqual((self.ws.root / 'wiki/old.md').read_text(), 'new')
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'new')
            self.assertEqual(db.execute('SELECT index_dirty FROM publication_state').fetchone()[0], 1)
        self.assertEqual(publication.recover(self.ws), [])

    def test_mid_replace_failure_rolls_back_all_files_and_sql(self):
        replace = os.replace
        count = 0
        def fail_second(src, dst):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('injected disk failure')
            return replace(src, dst)
        with patch.object(publication.os, 'replace', fail_second):
            with self.assertRaises(OSError):
                publication.publish(self.ws, {'wiki/old.md': 'new', 'wiki/new.md': 'new'}, 'p2',
                                    lambda db: db.execute("UPDATE example SET value='new'"))
        self.assertEqual((self.ws.root / 'wiki/old.md').read_text(), 'old')
        self.assertFalse((self.ws.root / 'wiki/new.md').exists())
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'old')

    @unittest.skipUnless(hasattr(signal, 'SIGKILL'), 'requires POSIX kill')
    def test_sigkill_after_first_replace_recovers_prepared_publication(self):
        script = '''import os,signal,sqlite3,sys
from pathlib import Path
from types import SimpleNamespace
from station import publication
root=Path(sys.argv[1]); ws=SimpleNamespace(root=root,db=root/'state.db',library_id='test')
original=publication.os.replace
def die(src,dst):
 original(src,dst)
 os.kill(os.getpid(),signal.SIGKILL)
publication.os.replace=die
publication.publish(ws,{'wiki/old.md':'new','wiki/new.md':'new'},'killed',lambda db:db.execute("UPDATE example SET value='new'"))
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.ws.root)], capture_output=True)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        self.assertEqual((self.ws.root / 'wiki/old.md').read_text(), 'new')
        recovered = publication.recover(self.ws)
        self.assertEqual(recovered[0]['state'], 'rolled_back')
        self.assertEqual((self.ws.root / 'wiki/old.md').read_text(), 'old')
        self.assertFalse((self.ws.root / 'wiki/new.md').exists())
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'old')
        self.assertEqual(list(self.ws.root.rglob('.publication-*')), [])

    @unittest.skipUnless(hasattr(signal, 'SIGKILL'), 'requires POSIX kill')
    def test_sigkill_after_commit_keeps_new_files_and_database(self):
        script = """import os,signal,sys
from pathlib import Path
from types import SimpleNamespace
from station import publication
root=Path(sys.argv[1]); ws=SimpleNamespace(root=root,db=root/'state.db',library_id='test')
def die(*args):
 os.kill(os.getpid(),signal.SIGKILL)
publication._cleanup=die
publication.publish(ws,{'wiki/old.md':'new','wiki/new.md':'new'},'committed',lambda db:db.execute("UPDATE example SET value='new'"))
"""
        result = subprocess.run([sys.executable, '-c', script, str(self.ws.root)], capture_output=True)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT state FROM publication_journal').fetchone()[0], 'committed')
            self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'new')
        recovered = publication.recover(self.ws)
        self.assertEqual(recovered[0]['state'], 'finished')
        self.assertEqual((self.ws.root / 'wiki/old.md').read_text(), 'new')
        self.assertEqual((self.ws.root / 'wiki/new.md').read_text(), 'new')
        self.assertEqual(publication.recover(self.ws), [])

    def test_callback_cannot_commit_early(self):
        with self.assertRaises(sqlite3.DatabaseError):
            publication.publish(self.ws, {'wiki/old.md': 'new'}, 'bad', lambda db: db.commit())
        self.assertEqual((self.ws.root / 'wiki/old.md').read_text(), 'old')

    def test_paths_are_confined_and_lock_is_reentrant(self):
        for path in ('../escape', str(self.ws.root / 'outside.md'), 'state.db', '.publication.lock'):
            with self.assertRaises(ValueError):
                publication.publish(self.ws, {path: 'bad'}, 'invalid')
        with publication.publication_lock(self.ws):
            publication.publish(self.ws, {'wiki/old.md': None}, 'delete')
        self.assertFalse((self.ws.root / 'wiki/old.md').exists())
