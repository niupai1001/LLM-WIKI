"""Back up and journal the user-requested reset; never reset a running library."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from .paper_policy import exclusion_reason, pdf_original
from .publication import publication_lock, publish, recover


def reset_library(ws, backup_root):
    from .jobs import has_pending
    backup = Path(backup_root) / ws.library_id
    if backup.exists():
        raise ValueError('Use a new backup directory for each reset')
    with publication_lock(ws):
        if has_pending(ws):
            raise ValueError('Finish or cancel running jobs before resetting knowledge')
        recover(ws)
        backup.mkdir(parents=True, mode=0o700)
        folders = ('raw', 'originals', 'wiki', 'wiki_meta', 'pending', 'parsed', 'extractions',
                   'distillations', 'readings', 'proposal_history', 'claim_reviews', 'claim_checks', 'runs', 'config')
        for name in folders:
            path = ws.root / name
            if path.is_dir():
                shutil.copytree(path, backup / name)
        for name in ('state.db', 'jobs.db', 'search.db'):
            source = ws.root / name
            if source.is_file():
                with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
                    with sqlite3.connect(backup / name) as dst:
                        src.backup(dst)
        for name in ('redirects.json', 'acceptance.json', 'human_evaluation.json', 'query_evaluation.json'):
            if (ws.root / name).is_file():
                shutil.copy2(ws.root / name, backup / name)
        kept, excluded, changes, protected_pdfs = [], [], {}, set()
        for path in sorted(ws.raw.glob('*.json')):
            record = json.loads(path.read_text())
            reason = exclusion_reason(ws, record)
            if reason:
                excluded.append({'source_id': path.stem, 'title': record.get('title'), 'reason': reason})
                changes[str(path.relative_to(ws.root))] = None
            else:
                kept.append(path.stem)
                protected_pdfs.add(pdf_original(ws, record))
                preprint = (record.get('bibliography') or {}).get('preprint_pdf_path')
                if isinstance(preprint, str):
                    original = (ws.originals / preprint).resolve()
                    if original.is_relative_to(ws.originals.resolve()) and original.is_file() and original.suffix.lower() == '.pdf':
                        protected_pdfs.add(original)
        keep = set(kept)
        # Current pages/drafts/old extraction caches are superseded as a whole.
        for name in ('wiki', 'wiki_meta', 'pending', 'extractions', 'distillations', 'readings', 'proposal_history',
                     'claim_reviews', 'claim_checks', 'runs'):
            for path in sorted((ws.root / name).rglob('*')) if (ws.root / name).exists() else []:
                if path.is_file():
                    changes[str(path.relative_to(ws.root))] = None
        for path in sorted(ws.originals.glob('*')):
            if path.is_file() and path.resolve() not in protected_pdfs:
                changes[str(path.relative_to(ws.root))] = None
        for path in sorted((ws.root / 'parsed').glob('*.json')):
            if path.stem not in keep:
                changes[str(path.relative_to(ws.root))] = None
        for name in ('acceptance.json', 'human_evaluation.json', 'query_evaluation.json'):
            if (ws.root / name).exists():
                changes[name] = None
        changes['redirects.json'] = '{}'
        changes['wiki/index.md'] = '# LLM Wiki\n\n'
        policy = {'enabled': True, 'workflow': 'knowledge-distillation-2.2',
                  'epoch': datetime.now(timezone.utc).isoformat(), 'source_kind': 'paper',
                  'required_original': 'pdf', 'news_extraction': False}
        changes['paper-workflow.json'] = json.dumps(policy, ensure_ascii=False, indent=2)
        report = {'library_id': ws.library_id, 'backup': str(backup), 'kept_source_ids': kept,
                  'excluded_sources': excluded, 'policy': policy}
        changes['paper-reset.json'] = json.dumps(report, ensure_ascii=False, indent=2)

        def database(db):
            for row in excluded:
                db.execute('DELETE FROM sources WHERE id=?', (row['source_id'],))
            db.execute("UPDATE sources SET stage='new',claim_at=NULL")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in ('knowledge_coverage', 'paper_distillations', 'knowledge_points', 'concept_memberships', 'knowledge_concepts'):
                if name in tables:
                    db.execute('DELETE FROM ' + name)
        publish(ws, changes, 'pdf-paper-rebuild-reset', database_callback=database)
        (backup / 'reset-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report
