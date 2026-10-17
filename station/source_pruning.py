"""Reversibly remove unreferenced sources without resetting published knowledge."""
from pathlib import Path
import json
import re
import shutil
import sqlite3
from .publication import publication_lock,publish,recover
from . import jobs


def prune_unpublished(ws, source_ids, backup_root):
    ids=set(source_ids)
    if not ids or any(not re.fullmatch(r'[a-f0-9]{16}',s) for s in ids):
        raise ValueError('Select explicit archived source IDs')
    backup=Path(backup_root)/ws.library_id
    with publication_lock(ws):
        if jobs.has_pending(ws):raise ValueError('Finish or cancel pending tasks before pruning sources')
        recover(ws)
        if backup.exists():raise ValueError('Use a new backup directory')
        records={sid:json.loads((ws.raw/(sid+'.json')).read_text()) for sid in ids}
        for path in ws.meta.glob('*.json'):
            meta=json.loads(path.read_text())
            if ids & set(meta.get('source_ids',[])):
                raise ValueError('A published page still references a selected source: '+path.stem)
        with sqlite3.connect(ws.db) as db:
            tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            if 'knowledge_points' in tables and any(db.execute('select 1 from knowledge_points where source_id=?',(sid,)).fetchone() for sid in ids):
                raise ValueError('Published knowledge points still reference a selected source')
            if 'paper_distillations' in tables and any(db.execute('select 1 from paper_distillations where source_id=?',(sid,)).fetchone() for sid in ids):
                raise ValueError('A published reading still references a selected source')
        changes={}
        for sid,record in records.items():
            changes['raw/'+sid+'.json']=None
            for folder in ('parsed','extractions','distillations','readings'):
                root=ws.root/folder
                for path in root.rglob('*') if root.exists() else []:
                    if path.is_file() and (path.stem==sid or sid in path.relative_to(root).parts):
                        changes[str(path.relative_to(ws.root))]=None
            # Source-owned assets only; never delete a shared or outside original.
            for path in ws.originals.glob(sid+'*'):
                if path.is_file() and path.resolve().is_relative_to(ws.originals.resolve()):
                    for other in ws.raw.glob('*.json'):
                        if other.stem in ids:continue
                        r=json.loads(other.read_text())
                        if path.name in {r.get('asset_path'),r.get('pdf_path')}:
                            raise ValueError('Original is shared with a retained source')
                    changes[str(path.relative_to(ws.root))]=None
        backup.mkdir(parents=True,mode=0o700)
        for relative in changes:
            path=ws.root/relative
            if path.exists():
                target=backup/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        for name in ('state.db','jobs.db','search.db'):
            path=ws.root/name
            if path.is_file():
                with sqlite3.connect(path) as src,sqlite3.connect(backup/name) as dst:src.backup(dst)
        report={'library_id':ws.library_id,'removed_source_ids':sorted(ids),'backup':str(backup),
                'reason':'User accepted completed readings and declined further distillation of remaining material',
                'sources':[{'source_id':sid,'title':r.get('title')} for sid,r in sorted(records.items())]}
        changes['source-removals.json']=json.dumps(report,ensure_ascii=False,indent=2)
        def database(db):
            for sid in ids:
                if 'knowledge_coverage' in tables:db.execute('delete from knowledge_coverage where source_id=?',(sid,))
                db.execute('delete from sources where id=?',(sid,))
        publication=publish(ws,changes,'remove-unpublished-sources',database_callback=database)
        (backup/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return {**report,'publication':publication}
