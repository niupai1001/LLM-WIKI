"""Library-local durable jobs with fenced, renewable execution leases."""
import json
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

LEASE_SECONDS = 15 * 60
HEARTBEAT_SECONDS = 30
KINDS = {'ocr', 'extract', 'index', 'compile', 'query'}


class JobConflict(ValueError):
    pass


@contextmanager
def _connect(ws):
    from .publication import publication_lock
    with publication_lock(ws):
        with _database(ws) as db:
            yield db


@contextmanager
def _database(ws):
    Path(ws.root).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(ws.root) / 'jobs.db', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, library_id TEXT NOT NULL, kind TEXT NOT NULL,
        payload TEXT NOT NULL, input_versions TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        lease_token TEXT, lease_until REAL, worker_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
        result TEXT, error TEXT)''')
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def _row(row):
    if row is None:
        return None
    value = dict(row)
    for name in ('payload', 'input_versions', 'result'):
        value[name] = json.loads(value[name]) if value[name] is not None else None
    value['cancel_requested'] = bool(value['cancel_requested'])
    return value


def enqueue(ws, kind, payload, input_versions=None):
    if kind not in KINDS or not isinstance(payload, dict):
        raise ValueError('Invalid job kind or payload')
    versions = input_versions if input_versions is not None else payload.get('input_versions', {})
    if not isinstance(versions, dict):
        raise ValueError('input_versions must be an object')
    jid, now = 'j_' + uuid.uuid4().hex, time.time()
    with _connect(ws) as db:
        db.execute('INSERT INTO jobs(id,library_id,kind,payload,input_versions,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (jid, ws.library_id, kind, json.dumps(payload, ensure_ascii=False), json.dumps(versions, sort_keys=True), 'queued', now, now))
    return get(ws, jid)


def get(ws, job_id):
    with _connect(ws) as db:
        return _row(db.execute('SELECT * FROM jobs WHERE id=? AND library_id=?', (job_id, ws.library_id)).fetchone())


get_job = get


def has_pending(ws):
    with _connect(ws) as db:
        return db.execute("SELECT 1 FROM jobs WHERE library_id=? AND status IN ('queued','running') LIMIT 1",(ws.library_id,)).fetchone() is not None


def list_jobs(ws, status=None, limit=100):
    with _connect(ws) as db:
        sql, args = 'SELECT * FROM jobs WHERE library_id=?', [ws.library_id]
        if status is not None:
            sql += ' AND status=?'; args.append(status)
        sql += ' ORDER BY created_at DESC,id DESC LIMIT ?'; args.append(max(1, min(int(limit), 1000)))
        return [_row(r) for r in db.execute(sql, args)]


def claim(ws, worker_id=None, kinds=None):
    allowed = list(KINDS if kinds is None else kinds)
    if not allowed or any(k not in KINDS for k in allowed):
        raise ValueError('Invalid job kinds')
    now = time.time()
    with _connect(ws) as db:
        db.execute('BEGIN IMMEDIATE')
        # Never silently rerun expired work: a crashed publisher may need recovery.
        db.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'failed' END,error='Execution lease expired; explicit retry required',lease_token=NULL,lease_until=NULL,updated_at=? WHERE library_id=? AND status='running' AND lease_until<=?", (now, ws.library_id, now))
        running_compile = db.execute("SELECT 1 FROM jobs WHERE library_id=? AND kind='compile' AND status='running'", (ws.library_id,)).fetchone()
        if running_compile:
            allowed = [k for k in allowed if k != 'compile']
        if not allowed:
            return None
        marks = ','.join('?' for _ in allowed)
        row = db.execute(f"SELECT * FROM jobs WHERE library_id=? AND status='queued' AND kind IN ({marks}) ORDER BY created_at,id LIMIT 1", [ws.library_id, *allowed]).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        db.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=?,worker_id=?,updated_at=? WHERE id=?", (token, now + LEASE_SECONDS, worker_id or uuid.uuid4().hex, now, row['id']))
        return _row(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())


def _owned(db, ws, job_id, lease_token):
    row = db.execute('SELECT * FROM jobs WHERE id=? AND library_id=?', (job_id, ws.library_id)).fetchone()
    if not row or row['status'] != 'running' or row['lease_token'] != lease_token or row['lease_until'] <= time.time():
        raise JobConflict('Job execution lease is stale')
    return row


def heartbeat(ws, job_id, lease_token):
    with _connect(ws) as db:
        db.execute('BEGIN IMMEDIATE')
        row = _owned(db, ws, job_id, lease_token)
        db.execute('UPDATE jobs SET lease_until=?,updated_at=? WHERE id=?', (time.time() + LEASE_SECONDS, time.time(), job_id))
        return not bool(row['cancel_requested'])


def check_active(ws, job_id, lease_token):
    with _connect(ws) as db:
        row = _owned(db, ws, job_id, lease_token)
        if row['cancel_requested']:
            raise JobConflict('Job cancellation requested')
        return True


def complete(ws, job_id, lease_token, result=None):
    with _connect(ws) as db:
        db.execute('BEGIN IMMEDIATE')
        _owned(db, ws, job_id, lease_token)
        # Cancellation is cooperative: committed work must still report success.
        status='failed' if isinstance(result,dict) and result.get('status')=='blocked' else 'succeeded'
        error='Compilation has unresolved targets; retry after reviewing the result' if status=='failed' else None
        db.execute("UPDATE jobs SET status=?,result=?,error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (status,json.dumps(result, ensure_ascii=False),error,time.time(),job_id))
    return get(ws, job_id)


def progress(ws,job_id,lease_token,phase,details=None):
    with _connect(ws) as db:
        _owned(db,ws,job_id,lease_token)
        db.execute('UPDATE jobs SET result=?,updated_at=? WHERE id=?',(json.dumps({'phase':phase,'progress':details or {}},ensure_ascii=False),time.time(),job_id))


def fail(ws, job_id, lease_token, error):
    with _connect(ws) as db:
        db.execute('BEGIN IMMEDIATE')
        row = _owned(db, ws, job_id, lease_token)
        status = 'cancelled' if row['cancel_requested'] else 'failed'
        db.execute('UPDATE jobs SET status=?,error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?', (status, str(error)[:2000], time.time(), job_id))
    return get(ws, job_id)


def cancel(ws, job_id):
    with _connect(ws) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=? AND library_id=?', (job_id, ws.library_id)).fetchone()
        if not row:
            raise JobConflict('Job not found')
        if row['status'] not in {'queued', 'running'}:
            raise JobConflict('Only queued or running jobs can be cancelled')
        status = 'cancelled' if row['status'] == 'queued' else 'running'
        db.execute('UPDATE jobs SET status=?,cancel_requested=1,updated_at=? WHERE id=?', (status, time.time(), job_id))
    return get(ws, job_id)


def retry(ws, job_id, target_slug=None):
    with _connect(ws) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=? AND library_id=?', (job_id, ws.library_id)).fetchone()
        if not row or row['status'] not in {'failed', 'cancelled'}:
            raise JobConflict('Only failed or cancelled jobs can be retried')
        payload=json.loads(row['payload'])
        if target_slug is not None:
            result=json.loads(row['result'] or '{}')
            if row['kind']!='compile' or not any(r.get('slug')==target_slug and r.get('status')=='blocked' for r in result.get('results',[])):
                raise JobConflict('Select a blocked target from this compilation result')
            payload['retry_target']=target_slug
        else:
            payload.pop('retry_target',None)
        db.execute("UPDATE jobs SET status='queued',payload=?,cancel_requested=0,lease_token=NULL,lease_until=NULL,error=NULL,result=NULL,updated_at=? WHERE id=?", (json.dumps(payload,ensure_ascii=False),time.time(), job_id))
    return get(ws, job_id)
