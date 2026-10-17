"""Durable request budgets and an exclusive L3 runner per workspace.

Reserve before provider I/O so a timeout or process death cannot renew a budget.
These operational records do not publish knowledge or dirty search indexes.
"""
from contextlib import contextmanager
import fcntl
import sqlite3

from .jobs import JobConflict
from .publication import publication_lock
from .worker import check_cancelled


@contextmanager
def synthesis_lock(ws):
    ws.root.mkdir(parents=True, exist_ok=True)
    with (ws.root / '.knowledge-synthesis.lock').open('a+b') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise JobConflict('Another knowledge maintenance run is active in this workspace') from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def budget_call(ws, key, phase, callback, limit=3):
    check_cancelled()
    with publication_lock(ws), sqlite3.connect(ws.db, timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS model_request_budgets (operation_key TEXT PRIMARY KEY, phase TEXT, attempts INTEGER NOT NULL DEFAULT 0)')
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO model_request_budgets(operation_key,phase) VALUES (?,?)', (key, phase))
        attempts = db.execute('SELECT attempts FROM model_request_budgets WHERE operation_key=?', (key,)).fetchone()[0]
        if attempts >= limit:
            raise ValueError(phase + ' request budget exhausted; prior attempts are retained')
        db.execute('UPDATE model_request_budgets SET attempts=attempts+1 WHERE operation_key=?', (key,))
    try:
        return callback()
    except JobConflict:
        with publication_lock(ws), sqlite3.connect(ws.db, timeout=30) as db:
            db.execute('UPDATE model_request_budgets SET attempts=attempts-1 WHERE operation_key=?', (key,))
        raise
