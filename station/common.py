import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dotenv import load_dotenv

# Local runs read the project configuration; explicit process variables win.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from .workspaces import ContextPath
_DEFAULT_ROOT = Path(os.getenv("STATION_DATA", "data"))
ROOT = ContextPath('root', _DEFAULT_ROOT)
RAW = ContextPath('raw', _DEFAULT_ROOT / 'raw')
WIKI = ContextPath('wiki', _DEFAULT_ROOT / 'wiki')
PENDING = ContextPath('pending', _DEFAULT_ROOT / 'pending')
ASSETS = ContextPath('originals', _DEFAULT_ROOT / 'originals')
META = ContextPath('meta', _DEFAULT_ROOT / 'wiki_meta')
DB = ContextPath('db', _DEFAULT_ROOT / 'state.db')


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_slug(value):
    value = re.sub(r"[^a-z0-9-]+", "-", str(value).strip().lower()).strip("-")
    return value[:64]


def canonical(url):
    p = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", query, ""))


def identifier(url):
    m = re.search(r"(?:doi\.org/|doi:)(10\.\d{4,9}/[^\s?#]+)", url, flags=re.I)
    if m:
        return "doi:" + m.group(1).lower()
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/((?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7}))(?:v\d+)?", url, flags=re.I)
    if m:
        return "arxiv:" + m.group(1).lower()
    return canonical(url)


def source_id(url):
    return hashlib.sha256(identifier(url).encode("utf-8")).hexdigest()[:16]


def connect():
    ROOT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    WIKI.mkdir(parents=True, exist_ok=True)
    PENDING.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    META.mkdir(parents=True, exist_ok=True)
    if not (WIKI / "index.md").exists():
        (WIKI / "index.md").write_text("# 私人情报知识库\n\n这里展示通过审核后形成的 Wiki 页面。\n", encoding="utf-8")
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
            feed TEXT NOT NULL, kind TEXT NOT NULL, published TEXT,
            summary TEXT, raw_path TEXT NOT NULL, captured_at TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'new'
        );
        CREATE INDEX IF NOT EXISTS idx_sources_time ON sources(captured_at DESC);
    """)
    db.execute('BEGIN IMMEDIATE')
    columns = {r['name'] for r in db.execute('PRAGMA table_info(sources)')}
    for name, definition in {
        'category': "TEXT NOT NULL DEFAULT 'other'",
        'content_status': "TEXT NOT NULL DEFAULT 'summary-only'",
        'claim_at': 'TEXT',
    }.items():
        if name not in columns:
            db.execute(f'ALTER TABLE sources ADD COLUMN {name} {definition}')
    # A killed model request must not leave evidence reserved indefinitely.
    db.execute("UPDATE sources SET stage='new',claim_at=NULL WHERE stage='compiling' AND (claim_at IS NULL OR julianday(claim_at) < julianday('now','-10 minutes'))")
    db.commit()
    return db


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
