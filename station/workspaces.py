"""Explicit library identities, independent snapshots and request-local legacy adapters."""
from contextlib import contextmanager,ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROJECT = Path(__file__).resolve().parent.parent
_CURRENT = ContextVar('station_workspace', default=None)
_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')


@dataclass(frozen=True)
class Workspace:
    library_id: str
    root: Path
    config_dir: Path | None = None
    registry_dir: Path | None = None

    def __post_init__(self):
        if not _ID.fullmatch(self.library_id):
            raise ValueError('Invalid library ID')
        object.__setattr__(self, 'root', Path(self.root).resolve())
        if self.config_dir is not None:
            object.__setattr__(self, 'config_dir', Path(self.config_dir).resolve())

    @property
    def raw(self): return self.root / 'raw'
    @property
    def wiki(self): return self.root / 'wiki'
    @property
    def meta(self): return self.root / 'wiki_meta'
    @property
    def originals(self): return self.root / 'originals'
    @property
    def pending(self): return self.root / 'pending'
    @property
    def db(self): return self.root / 'state.db'
    @property
    def config(self):
        return self.config_dir or (self.root / 'config' if self.library_id != 'legacy' or (self.root / 'config').is_dir() else PROJECT / 'config')
    @property
    def registry_root(self):
        return Path(self.registry_dir).resolve() if self.registry_dir is not None else (self.root if self.library_id=='legacy' else self.root.parent.parent)

    def url(self, path):
        parsed = urlsplit(path)
        query = [(k, v) for k, v in parse_qsl(parsed.query) if k != 'library_id']
        query.append(('library_id', self.library_id))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def current_workspace():
    return _CURRENT.get()


@contextmanager
def use_workspace(workspace):
    token = _CURRENT.set(workspace)
    try:
        yield workspace
    finally:
        _CURRENT.reset(token)


class ContextPath(os.PathLike):
    """Compatibility path delegates to an explicit boundary context, never globals/env."""
    def __init__(self, field, fallback, parts=()):
        self.field, self.fallback, self.parts = field, Path(fallback), parts

    def path(self):
        ws = current_workspace()
        value = getattr(ws, self.field) if ws else self.fallback
        return value.joinpath(*self.parts)

    def __fspath__(self): return str(self.path())
    def __str__(self): return str(self.path())
    def __repr__(self): return f'ContextPath({self.path()!s})'
    def __truediv__(self, child):
        return ContextPath(self.field, self.fallback, self.parts + (child,))
    def __getattr__(self, name): return getattr(self.path(), name)


def base_root(root=None):
    return Path(root or os.getenv('STATION_DATA', 'data')).resolve()


def _read_registry(root):
    path = root / 'library-registry.json'
    if not path.exists():
        return {'version': 1, 'active': 'legacy', 'libraries': {'legacy': {'root': str(root)}} , 'history': []}
    result = json.loads(path.read_text(encoding='utf-8'))
    if result.get('active') not in result.get('libraries', {}):
        raise ValueError('Library registry is invalid')
    return result


def registry(root=None):
    root = base_root(root)
    return _read_registry(root)


def resolve_workspace(library_id=None, root=None):
    root = base_root(root)
    info = _read_registry(root)
    identity = library_id or info['active']
    if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity not in info['libraries']:
        raise ValueError('知识库不存在')
    path = Path(info['libraries'][identity]['root']).resolve()
    if path != root and not path.is_relative_to(root / 'libraries'):
        raise ValueError('Library root is outside the registry')
    return Workspace(identity, path, registry_dir=root)


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def registry_lock(root=None):
    root = base_root(root); root.mkdir(parents=True, exist_ok=True)
    with (root / '.library-registry.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: yield root
        finally: fcntl.flock(lock, fcntl.LOCK_UN)


def initialize_candidate(library_id='candidate-v2', root=None):
    if not _ID.fullmatch(library_id) or library_id == 'legacy':
        raise ValueError('Invalid candidate library ID')
    with registry_lock(root) as root,ExitStack() as stack:
        info = _read_registry(root)
        if library_id in info['libraries']:
            return resolve_workspace(library_id, root)
        legacy = resolve_workspace('legacy', root)
        from .publication import publication_lock
        stack.enter_context(publication_lock(legacy))
        stack.enter_context(use_workspace(legacy))
        from .settings import _locked
        stack.enter_context(_locked())
        from .common import connect
        with use_workspace(legacy):
            connect().close()
        target = root / 'libraries' / library_id
        if target.exists():
            raise ValueError('Unregistered candidate directory exists')
        temporary = target.with_name('.' + library_id + '-initializing')
        if temporary.exists(): shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        try:
            # Copies are independent: candidate parsing cannot overwrite baseline evidence.
            for name in ('raw', 'originals', 'source_metadata'):
                source = legacy.root / name
                if source.exists(): shutil.copytree(source, temporary / name)
            shutil.copytree(legacy.config, temporary / 'config', ignore=shutil.ignore_patterns('.settings.lock', '*.tmp'))
            baseline = temporary / 'baseline'; baseline.mkdir()
            for name in ('wiki', 'wiki_meta', 'claim_reviews'):
                if (legacy.root / name).exists(): shutil.copytree(legacy.root / name, baseline / name)
            (temporary / 'wiki').mkdir(); (temporary / 'wiki_meta').mkdir(); (temporary / 'pending').mkdir()
            if legacy.db.exists():
                from contextlib import closing
                with closing(sqlite3.connect(legacy.db.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(temporary / 'state.db')) as dst:
                    src.backup(dst)
                    dst.execute('UPDATE sources SET raw_path=? || id || ?, claim_at=NULL', (str(target / 'raw') + '/', '.json'))
                    dst.execute("UPDATE sources SET stage='new' WHERE stage IN ('drafted','compiling')")
                    dst.commit()
            manifest = snapshot_manifest(legacy)
            _save(temporary / 'baseline' / 'manifest.json', manifest)
            temporary.rename(target)
            info['libraries'][library_id] = {'root': str(target), 'created_at': _now(), 'baseline': manifest['digest'], 'status': 'candidate'}
            _save(root / 'library-registry.json', info)
            return Workspace(library_id, target, registry_dir=root)
        except BaseException:
            if temporary.exists(): shutil.rmtree(temporary)
            raise


def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def snapshot_manifest(ws):
    entries = {}
    for name in ('raw', 'originals', 'wiki', 'wiki_meta', 'claim_reviews'):
        folder = ws.root / name
        for path in sorted(folder.rglob('*')) if folder.exists() else []:
            if path.is_file() and not path.name.startswith('.'):
                entries[str(path.relative_to(ws.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    return {'library_id': ws.library_id, 'at': _now(), 'files': entries, 'digest': digest}


def synchronize_candidate(library_id='candidate-v2', root=None):
    ws, legacy = resolve_workspace(library_id, root), resolve_workspace('legacy', root)
    if ws.library_id == legacy.library_id: raise ValueError('Cannot synchronize legacy into itself')
    from .publication import publication_lock, publish
    from .documents import source_version
    changed, records, changes = [], {}, {}
    with publication_lock(legacy), publication_lock(ws):
        # Asset versions can change without a corresponding raw JSON edit.
        # Collect all changes first, then commit assets, records and source state
        # through the same recovery journal used for knowledge publication.
        for path in sorted(legacy.raw.glob('*.json')):
            target = ws.raw / path.name
            record = json.loads(path.read_text())
            sid = record['id']
            raw_changed = not target.exists() or target.read_bytes() != path.read_bytes()
            version_changed = not target.exists() or source_version(ws, sid) != source_version(legacy, sid)
            if raw_changed or version_changed:
                changed.append(sid); records[sid] = record
            if raw_changed:
                changes[str(target.relative_to(ws.root))] = path.read_bytes()
            for key in ('asset_path', 'pdf_path'):
                name = record.get(key)
                if not name: continue
                asset = (legacy.originals / name).resolve()
                if not asset.is_relative_to(legacy.originals.resolve()):
                    raise ValueError('Archived original is outside its library')
                dest = ws.originals / asset.relative_to(legacy.originals.resolve())
                if not asset.exists() and dest.is_file():
                    changes[str(dest.relative_to(ws.root))] = None
        for folder in ('originals', 'source_metadata'):
            for asset in sorted((legacy.root / folder).rglob('*')):
                if not asset.is_file(): continue
                if not asset.resolve().is_relative_to((legacy.root / folder).resolve()):
                    raise ValueError('Archived asset is outside its library')
                relative = asset.relative_to(legacy.root); dest = ws.root / relative
                if not dest.exists() or hashlib.sha256(dest.read_bytes()).digest() != hashlib.sha256(asset.read_bytes()).digest():
                    changes[str(relative)] = asset.read_bytes()
        changes['sync-status.json'] = json.dumps({'at': _now(), 'source_manifest': snapshot_manifest(legacy)['digest'], 'changed_source_ids': changed}, ensure_ascii=False)
        def update_sources(db):
            fields = {r[1] for r in db.execute('PRAGMA table_info(sources)')}
            for sid, record in records.items():
                data = {k: record.get(k, '') for k in fields if k not in {'stage', 'claim_at'}}
                data.update(raw_path=str(ws.raw / (sid + '.json')), id=sid, stage='new', claim_at=None)
                cols = list(data)
                db.execute('INSERT OR REPLACE INTO sources (' + ','.join(cols) + ') VALUES (' + ','.join('?' for _ in cols) + ')', [data[k] for k in cols])
        publish(ws, changes, 'source-sync', database_callback=update_sources)
    return {'library_id': library_id, 'changed_source_ids': changed}


def _cutover_backup(root,libraries,info):
    from contextlib import closing
    folder=root/'migration-backups';folder.mkdir(parents=True,exist_ok=True)
    backup=Path(tempfile.mkdtemp(prefix='cutover-',dir=folder))
    try:
        _save(backup/'library-registry.json',info)
        for library in libraries:
            target=backup/library.library_id;target.mkdir()
            for name in ('raw','originals','source_metadata','wiki','wiki_meta','claim_reviews','proposal_history','baseline','parsed','extractions','runs'):
                source=library.root/name
                if source.exists():shutil.copytree(source,target/name)
            if library.config.exists():shutil.copytree(library.config,target/'config',ignore=shutil.ignore_patterns('*.lock','*.tmp'))
            for name in ('state.db','jobs.db'):
                source=library.root/name
                if source.exists():
                    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/name)) as dst:
                        src.backup(dst)
            for name in ('redirects.json','sync-status.json','acceptance.json','human_evaluation.json','query_evaluation.json'):
                source=library.root/name
                if source.exists():shutil.copy2(source,target/name)
            _save(target/'manifest.json',snapshot_manifest(library))
        for path in backup.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:os.fsync(stream.fileno())
        for directory in sorted([p for p in backup.rglob('*') if p.is_dir()]+[backup,folder],key=lambda p:len(p.parts),reverse=True):
            fd=os.open(directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        return backup
    except BaseException:
        shutil.rmtree(backup)
        raise


def activate_library(library_id, root=None, rollback=False):
    with registry_lock(root) as root,ExitStack() as stack:
        target = resolve_workspace(library_id, root)
        from .jobs import has_pending
        from .publication import recover
        info = _read_registry(root); old = resolve_workspace(info['active'], root)
        from .publication import publication_lock
        libraries=sorted({str(ws.root):ws for ws in (old,target)}.values(),key=lambda ws:str(ws.root))
        for library in libraries:
            stack.enter_context(publication_lock(library))
        for library in libraries:recover(library)
        if any(has_pending(ws) for ws in (old, target)):
            raise ValueError('请先完成或取消排队及运行任务，再切换知识库')
        previous_official={'legacy'}|{entry.get(key) for entry in info['history'] for key in ('from','to')}
        if rollback and library_id not in previous_official:
            raise ValueError('只能回退到曾经启用的正式库；新候选库须先通过验收')
        if library_id != 'legacy' and not rollback:
            report_path = target.root / 'acceptance.json'
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            from .evaluation import acceptance_current
            if not report.get('passed') or not acceptance_current(target,report):
                raise ValueError('候选库尚未通过当前版本验收')
            if (old.root / 'raw').exists():
                for source in old.raw.glob('*.json'):
                    copy = target.raw / source.name
                    if not copy.exists() or copy.read_bytes() != source.read_bytes():
                        raise ValueError('候选库尚未同步最新来源')
        rollback_report=None
        if rollback:
            def differences(folder):
                result=[]
                for path in sorted((old.root/folder).glob('*')):
                    if not path.is_file() or path.name=='index.md':continue
                    counterpart=target.root/folder/path.name
                    if not counterpart.exists() or path.read_bytes()!=counterpart.read_bytes():
                        result.append({'id':path.stem,'change':'added' if not counterpart.exists() else 'changed'})
                return result
            rollback_report={'retained_library_id':old.library_id,'destination_library_id':target.library_id,
                             'unsynchronized_sources':differences('raw'),'unsynchronized_pages':differences('wiki'),
                             'unsynchronized_metadata':differences('wiki_meta'),
                             'note':'回退仅切换活动指针，以上新增或变化仍保留在原知识库，尚未同步到回退目标。'}
        backup=_cutover_backup(root,libraries,info)
        old_id = info['active']; info['active'] = library_id
        info['history'].append({'from': old_id, 'to': library_id, 'at': _now(), 'rollback': rollback,'backup':str(backup)})
        _save(root / 'library-registry.json', info)
        result={'active': library_id, 'previous': old_id, 'rollback': rollback,'backup_path':str(backup)}
        if rollback_report is not None:result['rollback_report']=rollback_report
        return result
