"""Recoverable library-local publication of files and SQLite changes.

The prepared before-images are durable before replacing files. Database changes
and the committed marker share one transaction. Readers needing a consistent
multi-file snapshot must acquire publication_lock too.
"""
import fcntl
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


def _root(ws):
    return Path(ws.root).resolve()


@contextmanager
def publication_lock(ws):
    root = _root(ws)
    root.mkdir(parents=True, exist_ok=True)
    key = str(root)
    with _LOCKS_GUARD:
        mutex = _LOCKS.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_LOCAL, 'held', {})
        if key in held:
            yield
            return
        with (root / '.publication.lock').open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _LOCAL.held = {**held, key: True}
            try:
                yield
            finally:
                _LOCAL.held = held
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _db(ws):
    path = Path(ws.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA synchronous=FULL')
    db.execute('''CREATE TABLE IF NOT EXISTS publication_journal (
        id TEXT PRIMARY KEY, library_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
        state TEXT NOT NULL, created_at REAL NOT NULL, completed_at REAL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS publication_files (
        publication_id TEXT NOT NULL, path TEXT NOT NULL, before BLOB, after BLOB,
        PRIMARY KEY(publication_id,path))''')
    db.execute('''CREATE TABLE IF NOT EXISTS publication_state (
        library_id TEXT PRIMARY KEY, index_dirty INTEGER NOT NULL DEFAULT 0)''')
    db.commit()
    try:
        yield db
    finally:
        db.close()


def _path(ws, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError('Publication paths must be relative to the library root')
    root = _root(ws)
    target = root / path
    if not target.resolve().is_relative_to(root):
        raise ValueError('Publication path escapes library root')
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root and p.is_relative_to(root)):
        raise ValueError('Publication cannot traverse symbolic links')
    reserved = {Path(ws.db).resolve(), root / 'jobs.db', root / '.publication.lock'}
    reserved |= {Path(str(p) + suffix) for p in tuple(reserved) for suffix in ('-wal', '-shm', '-journal')}
    if target.resolve() in reserved or target.name.startswith('.publication-'):
        raise ValueError('Publication cannot replace operational state')
    if target.exists() and not target.is_file():
        raise ValueError('Publication target is not a regular file')
    return target


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage(target, data, publication_id):
    missing, parent = [], target.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _sync_dir(directory.parent)
    temp = target.with_name('.publication-' + publication_id + '-' + target.name)
    with temp.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(target.parent)
    return temp


def _replace(target, data, publication_id):
    if data is None:
        target.unlink(missing_ok=True)
        if target.parent.exists():
            _sync_dir(target.parent)
    else:
        temp = _stage(target, data, publication_id)
        os.replace(temp, target)
        _sync_dir(target.parent)


def _cleanup(ws, publication_id, rows):
    for row in rows:
        target = _path(ws, row['path'])
        temp = target.with_name('.publication-' + publication_id + '-' + target.name)
        temp.unlink(missing_ok=True)
        if target.parent.exists():
            _sync_dir(target.parent)


def _recover_locked(ws, db):
    recovered = []
    journals = db.execute("SELECT * FROM publication_journal WHERE library_id=? AND state IN ('prepared','committed') ORDER BY created_at,id", (ws.library_id,)).fetchall()
    for journal in journals:
        rows = db.execute('SELECT * FROM publication_files WHERE publication_id=? ORDER BY path', (journal['id'],)).fetchall()
        if journal['state'] == 'prepared':
            for row in rows:
                _replace(_path(ws, row['path']), row['before'], journal['id'])
            state = 'rolled_back'
        else:
            # File replacements were fsynced before the committed DB transaction.
            # Do not replay old after-images over later externally edited content.
            state = 'finished'
            db.execute('INSERT INTO publication_state(library_id,index_dirty) VALUES(?,1) ON CONFLICT(library_id) DO UPDATE SET index_dirty=1', (ws.library_id,))
        _cleanup(ws, journal['id'], rows)
        db.execute('UPDATE publication_journal SET state=?,completed_at=? WHERE id=?', (state, time.time(), journal['id']))
        db.commit()
        recovered.append({'id': journal['id'], 'proposal_id': journal['proposal_id'], 'state': state})
    return recovered


def recover(ws):
    with publication_lock(ws), _db(ws) as db:
        return _recover_locked(ws, db)


def publish(ws, changes, proposal_id, database_callback=None):
    """Publish relative file changes; None deletes a file.

    database_callback(connection) must use this supplied transaction and must not
    commit, rollback, perform external writes, or call executescript (which commits).
    """
    if not isinstance(changes, dict) or not changes or not isinstance(proposal_id, str) or not proposal_id:
        raise ValueError('A proposal ID and nonempty file changes are required')
    with publication_lock(ws), _db(ws) as db:
        _recover_locked(ws, db)
        entries = []
        seen = set()
        for relative, value in changes.items():
            target = _path(ws, relative)
            key = str(target.relative_to(_root(ws)))
            if key in seen or value is not None and not isinstance(value, (str, bytes)):
                raise ValueError('Duplicate path or invalid publication content')
            seen.add(key)
            data = value.encode('utf-8') if isinstance(value, str) else value
            entries.append((key, target.read_bytes() if target.exists() else None, data))
        publication_id = uuid.uuid4().hex
        db.execute('INSERT INTO publication_journal VALUES(?,?,?,?,?,NULL)', (publication_id, ws.library_id, proposal_id, 'prepared', time.time()))
        db.executemany('INSERT INTO publication_files VALUES(?,?,?,?)', [(publication_id, *entry) for entry in entries])
        db.commit()
        try:
            staged = {}
            for relative, before, after in entries:
                if after is not None:
                    staged[relative] = _stage(_path(ws, relative), after, publication_id)
            db.execute('BEGIN IMMEDIATE')
            if database_callback is not None:
                # Reject transaction control inside callbacks before it can commit.
                def authorize(action, arg1, arg2, dbname, trigger):
                    return sqlite3.SQLITE_DENY if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH} else sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                try:
                    database_callback(db)
                finally:
                    db.set_authorizer(None)
            for relative, before, after in entries:
                target = _path(ws, relative)
                if after is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(staged[relative], target)
                if target.parent.exists():
                    _sync_dir(target.parent)
            db.execute("UPDATE publication_journal SET state='committed' WHERE id=?", (publication_id,))
            db.execute('INSERT INTO publication_state(library_id,index_dirty) VALUES(?,1) ON CONFLICT(library_id) DO UPDATE SET index_dirty=1', (ws.library_id,))
            db.commit()
        except BaseException:
            db.rollback()
            _recover_locked(ws, db)
            raise
        _recover_locked(ws, db)
        return {'id': publication_id, 'proposal_id': proposal_id, 'state': 'finished', 'index_dirty': True}
