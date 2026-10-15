"""Explicit-workspace job worker; never changes process environment variables."""
import argparse
import contextvars
import importlib
import os
import re
import threading
import time
import uuid
from . import jobs

_CURRENT = contextvars.ContextVar('station_job_execution', default=None)


def check_cancelled():
    """Call immediately before irreversible writes and between expensive stages."""
    current = _CURRENT.get()
    if current is not None:
        ws, job_id, token = current
        jobs.check_active(ws, job_id, token)


def report_progress(phase,details=None):
    current=_CURRENT.get()
    if current is not None:
        ws,job_id,token=current
        jobs.progress(ws,job_id,token,phase,details)


def _invoke(module, function, ws, *args, **kwargs):
    from .workspaces import use_workspace
    with use_workspace(ws):
        return getattr(importlib.import_module('.' + module, __package__), function)(ws, *args, **kwargs)


def default_handlers():
    return {
        'compile': lambda ws, payload, jid: _invoke('compiler', 'compile_job', ws, payload, jid),
        'ocr': lambda ws, payload, jid: _invoke('documents', 'parse_source', ws, payload['source_id'],ocr=payload.get('ocr',True),force=payload.get('force',False)),
        'extract': lambda ws, payload, jid: _invoke('extraction', 'extract_source', ws, payload['source_id'],force=payload.get('force',False)),
        'index': lambda ws, payload, jid: _invoke('search', 'rebuild_index', ws, embeddings=payload.get('embeddings', False)),
        'query': lambda ws, payload, jid: _invoke('query', 'answer', ws, payload['question']),
    }


def run_once(ws, handlers=None, worker_id=None):
    handlers = default_handlers() if handlers is None else handlers
    if not handlers:
        return None
    job = jobs.claim(ws, worker_id=worker_id or f'{os.getpid()}-{uuid.uuid4().hex}', kinds=handlers.keys())
    if job is None:
        return None
    stop = threading.Event()
    token = job['lease_token']
    def renew():
        while not stop.wait(jobs.HEARTBEAT_SECONDS):
            try:
                jobs.heartbeat(ws, job['id'], token)
            except Exception:
                # The main thread rechecks the durable lease before committing.
                return
    thread = threading.Thread(target=renew, name='job-heartbeat', daemon=True)
    thread.start()
    context_token = _CURRENT.set((ws, job['id'], token))
    try:
        from .publication import recover
        recover(ws)
        check_cancelled()
        payload = dict(job['payload'])
        payload['input_versions'] = job['input_versions']
        if job['input_versions']:
            from .documents import source_version
            if any(source_version(ws,sid)!=version for sid,version in job['input_versions'].items() if re.fullmatch('[a-f0-9]{16}',sid)):
                raise ValueError('Queued source version changed; enqueue a fresh task')
        result = handlers[job['kind']](ws, payload, job['id'])
        return jobs.complete(ws, job['id'], token, result)
    except Exception as exc:
        try:
            message=str(exc) if isinstance(exc,ValueError) and 'http' not in str(exc).lower() else type(exc).__name__
            return jobs.fail(ws, job['id'], token, message)
        except jobs.JobConflict:
            return jobs.get(ws, job['id'])
    finally:
        _CURRENT.reset(context_token)
        stop.set()
        thread.join(timeout=2)


def main():
    from . import common  # Load .env before resolving the registry's data root.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library_id', nargs='?', default=None)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--poll-seconds', type=float, default=2)
    args = parser.parse_args()
    from .workspaces import resolve_workspace,registry
    fixed_workspace = resolve_workspace(args.library_id) if args.library_id is not None else None
    while True:
        libraries=[fixed_workspace] if fixed_workspace is not None else [resolve_workspace(identity) for identity in dict.fromkeys([registry()['active'],*registry()['libraries']])]
        result=None
        for ws in libraries:
            result=run_once(ws)
            if result is not None: break
        if args.once:
            return result
        if result is None:
            time.sleep(max(.1, args.poll_seconds))


if __name__ == '__main__':
    main()
