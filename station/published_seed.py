"""Explicit, idempotent import of four verified published Agent references.

Run from the project directory: python -m station.published_seed
No LLM calls or Wiki edits. Original preprints and source IDs are preserved.
"""
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .common import ASSETS, DB, RAW, connect, load_json, save_json, source_id, utcnow
from .published_sources import capture_published_original, merge_published_version

REFERENCES = [
    ('4a3710e0ff43d563', 'https://proceedings.nips.cc/paper_files/paper/2023/hash/d842425e4bf79ba039352da0f658a906-Abstract-Conference.html', ''),
    ('3fb5d6b0a837235b', 'https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html', ''),
    ('b36ffb44e7a44c44', 'https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html', ''),
    (None, 'https://proceedings.iclr.cc/paper_files/paper/2024/hash/b3075b88e583a0e98d8b24338a613060-Abstract-Conference.html', 'https://arxiv.org/abs/2310.11667'),
]


def import_references():
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('artifacts') / 'published-source-backup' / timestamp
    backup.mkdir(parents=True)
    db = connect()
    copy_db = sqlite3.connect(backup / 'state.db')
    db.backup(copy_db)
    copy_db.close()
    reports = []
    try:
        for existing_sid, url, preprint_url in REFERENCES:
            sid = existing_sid or source_id(url)
            row = db.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
            if row and row['stage'] == 'compiling':
                raise ValueError('资料正在编写中；完成后再升级正式 PDF')
            raw = Path(row['raw_path']) if row else RAW / f'{sid}.json'
            if row:
                shutil.copy2(raw, backup / raw.name)
                old = load_json(raw)
                for key in ('pdf_path', 'html_path', 'asset_path'):
                    asset = old.get(key)
                    if asset and (ASSETS / asset).is_file():
                        shutil.copy2(ASSETS / asset, backup / asset)
            else:
                old = {'id': sid, 'url': url, 'category': 'agents', 'kind': 'paper',
                       'feed': 'manual', 'published': '', 'captured_at': utcnow(),
                       'summary': '', 'title_is_custom': False}
            official = capture_published_original(url, sid)
            updated = merge_published_version(old, official)
            if not row:
                updated['title'] = official['bibliography']['title']
                if preprint_url:
                    preprint_id = preprint_url.rsplit('/', 1)[-1]
                    updated['bibliography'].update(arxiv_id=preprint_id, preprint_url=preprint_url,
                                                  same_work_key='arxiv:' + preprint_id)
            save_json(raw, updated)
            if not row:
                db.execute('INSERT INTO sources (id,title,url,feed,kind,published,summary,raw_path,captured_at,category,content_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (sid, updated['title'], url, 'manual', 'paper', '', '', str(raw), updated['captured_at'], 'agents', 'full-text'))
            else:
                db.execute("UPDATE sources SET content_status='full-text' WHERE id=?", (sid,))
            db.commit()
            report = {'id': sid, 'title': updated['title'], 'pdf_path': updated['pdf_path'],
                      'publication_record': updated['bibliography']['publication_record'],
                      'retained_preprint_pdf': updated['bibliography'].get('preprint_pdf_path', ''),
                      'text_characters': len(updated['text'])}
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        db.close()
        save_json(backup / 'report.json', {'references': reports, 'backup': str(backup)})
    return {'references': reports, 'backup': str(backup)}


if __name__ == '__main__':
    print(json.dumps(import_references(), ensure_ascii=False, indent=2))
