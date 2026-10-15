"""Per-library derived search index: FTS5, aliases and optional remote embeddings.

The index is disposable. Neither a search nor index construction alters a source,
page or metadata record. All authoritative paths come from the explicit workspace.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import numbers
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlencode, urlsplit
import urllib.request

from .publication import publication_lock

CHUNK_SIZE = 1600
CHUNK_OVERLAP = 200
RECALL_LIMIT = 40
RRF_K = 60
INDEX_VERSION = "hybrid-v1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def is_library_file(ws, path):
    """Do not follow a source/page/cache symlink into another library."""
    root = Path(ws.root).resolve()
    try:
        local = root / Path(path).relative_to(Path(ws.root))
    except ValueError:
        return False
    if not local.resolve().is_relative_to(root):
        return False
    return not any(node.is_symlink() for node in (local, *local.parents)
                   if node != root and node.is_relative_to(root))


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def tokenize(text):
    """English words and overlapping Chinese/Japanese character bigrams."""
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    tokens = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text)
    for run in re.findall(r"[\u3400-\u9fff\uf900-\ufaff]+", text):
        tokens.extend([run] if len(run) == 1 else
                      [run[i:i + 2] for i in range(len(run) - 1)])
    return tokens


def _config(ws=None):
    saved = {}
    error = ""
    if ws is not None:
        path = Path(ws.config) / "embedding.json"
        if path.is_file():
            try:
                saved = _read_json(path)
            except (OSError, ValueError):
                error = "Embedding configuration is unreadable"
    result = {}
    for name, env in (("base_url", "EMBEDDING_BASE_URL"),
                      ("model", "EMBEDDING_MODEL"), ("api_key", "EMBEDDING_API_KEY")):
        value = saved.get(name, os.getenv(env, ""))
        if value is None:
            value = ""
        if not isinstance(value, str):
            error = "Embedding configuration fields must be strings"
            value = ""
        result[name] = value.strip()
    result["error"] = error
    return result


def _backend(config):
    # Credentials deliberately do not occur in the stored identity or API output.
    return _digest({"base_url": config["base_url"].rstrip("/"),
                    "model": config["model"]})


def _configured(config):
    return not config.get("error") and bool(config["base_url"] and config["model"])


def _vector(value, dimension=None):
    if (not isinstance(value, list) or not 1 <= len(value) <= 8192
            or (dimension is not None and len(value) != dimension)
            or any(isinstance(n, bool) or not isinstance(n, numbers.Real)
                   or not math.isfinite(n) for n in value)):
        raise ValueError("Embedding must have a consistent bounded finite dimension")
    vector = [float(n) for n in value]
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding must have a nonzero finite norm")
    return [n / norm for n in vector]


def embed_texts(config, texts):
    """A small OpenAI-compatible /embeddings client; no writing-model fallback."""
    if not _configured(config):
        raise RuntimeError("Embedding base URL and model are not configured")
    base = config["base_url"].rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Invalid embedding base URL")
    if not isinstance(texts, list) or not 1 <= len(texts) <= 32:
        raise ValueError("Embedding batches must contain 1 to 32 texts")
    endpoint = base if base.endswith("/embeddings") else base + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = "Bearer " + config["api_key"]
    request = urllib.request.Request(endpoint,
        data=json.dumps({"model": config["model"], "input": texts},
                        ensure_ascii=False).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        reply = json.load(response)
    values = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(values, list) or len(values) != len(texts):
        raise ValueError("Embedding response has an unexpected item count")
    ordered = {}
    for item in values:
        if (not isinstance(item, dict) or isinstance(item.get("index"), bool)
                or not isinstance(item.get("index"), int)
                or not 0 <= item["index"] < len(texts) or item["index"] in ordered):
            raise ValueError("Embedding response has invalid indices")
        ordered[item["index"]] = item.get("embedding")
    first = _vector(ordered[0])
    return [first] + [_vector(ordered[i], len(first)) for i in range(1, len(texts))]


@contextmanager
def _db(ws):
    Path(ws.root).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(ws.root) / "search.db", timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY, doc_key TEXT NOT NULL,
                kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                title TEXT NOT NULL, aliases TEXT NOT NULL, text TEXT NOT NULL,
                start INTEGER NOT NULL, end INTEGER NOT NULL,
                location TEXT NOT NULL, source_version TEXT NOT NULL,
                vector TEXT, vector_backend TEXT, dimension INTEGER
            );
            CREATE INDEX IF NOT EXISTS chunks_document ON chunks(doc_key);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                chunk_id UNINDEXED, title, aliases, text, tokenize='unicode61'
            );
        """)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def source_segments(ws, sid):
    """Return versioned original spans, prioritizing cached PDF/OCR paragraphs."""
    with publication_lock(ws):
        return _source_segments(ws, sid)


def _source_segments(ws, sid):
    if not isinstance(sid, str) or not _SAFE_ID.fullmatch(sid):
        return []
    raw_path = Path(ws.raw) / (sid + ".json")
    if not raw_path.is_file() or not is_library_file(ws, raw_path):
        return []
    raw = _read_json(raw_path)
    from .documents import source_version
    version = source_version(ws, sid)
    parsed_path = Path(ws.root) / "parsed" / (sid + ".json")
    if parsed_path.is_file():
        if not is_library_file(ws, parsed_path):
            raise ValueError("Parsed source path is outside its library")
        parsed = _read_json(parsed_path)
        if parsed.get("source_version") != version:
            raise ValueError("Cached source parse is stale; parse this source again")
        parsed_version = sha256(parsed_path.read_bytes()).hexdigest()
        segments = []
        for page in parsed.get("pages", []):
            if not isinstance(page, dict):
                continue
            paragraphs = page.get("paragraphs") or [{"id": "page", "text": page.get("text", "")}]
            for paragraph in paragraphs:
                if not isinstance(paragraph, dict) or not str(paragraph.get("text", "")).strip():
                    continue
                segments.append({"text": paragraph["text"], "source_version": version,
                    "location": {"page": page.get("number", page.get("page_number")),
                        "page_label": page.get("label"), "paragraph_id": paragraph.get("id"),
                        "parsed_version": parsed_version, "origin": "parsed",
                        "method": page.get("method"), "paragraph_start": paragraph.get("start"),
                        "paragraph_end": paragraph.get("end")}})
        # A partial/missing parse must not silently be presented as a complete raw read.
        return segments
    text = raw.get("text", "")
    if raw.get("content_status") != "full-text" or not isinstance(text, str) or not text.strip():
        return []
    return [{"text": text, "source_version": version,
             "location": {"origin": "raw", "paragraph_id": None}}]


def _documents(ws, warnings):
    documents = {}
    for path in sorted(Path(ws.wiki).glob("*.md")):
        if path.name == "index.md" or not _SAFE_ID.fullmatch(path.stem):
            continue
        try:
            if not is_library_file(ws, path):
                raise ValueError("Page path crosses a library boundary")
            body = path.read_text(encoding="utf-8")
            meta_path = Path(ws.meta) / (path.stem + ".json")
            if meta_path.exists() and not is_library_file(ws, meta_path):
                raise ValueError("Page metadata crosses a library boundary")
            meta = _read_json(meta_path) if meta_path.is_file() else {}
            aliases = meta.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            aliases = [v for v in aliases if isinstance(v, str)]
            title = str(meta.get("title") or (body.splitlines()[0].lstrip("# ") if body else path.stem))
            documents["page:" + path.stem] = {"kind": "page", "id": path.stem,
                "title": title, "aliases": aliases, "segments": [{"text": body,
                    "location": {"origin": "wiki"}, "source_version": _digest({"body": body, "meta": meta})}]}
        except (ValueError, OSError):
            warnings.append("A page could not be indexed: " + path.stem)
    for path in sorted(Path(ws.raw).glob("*.json")):
        if not _SAFE_ID.fullmatch(path.stem):
            continue
        try:
            if not is_library_file(ws, path):
                raise ValueError("Original path crosses a library boundary")
            record = _read_json(path)
            segments = source_segments(ws, path.stem)
            if segments:
                documents["source:" + path.stem] = {"kind": "source", "id": path.stem,
                    "title": str(record.get("title") or path.stem), "aliases": [], "segments": segments}
        except (ValueError, OSError, TypeError):
            warnings.append("An original could not be indexed: " + path.stem)
    return documents


def _chunk_document(key, document):
    chunks = []
    for segment_number, segment in enumerate(document["segments"]):
        text = segment["text"]
        for start in range(0, len(text), CHUNK_SIZE - CHUNK_OVERLAP):
            end = min(start + CHUNK_SIZE, len(text))
            location = dict(segment["location"], start=start, end=end)
            locator = (f"p.{location['page']} / {location.get('paragraph_id') or 'page'}"
                       if location.get("page") is not None else f"characters {start}–{end}")
            location["locator"] = locator
            chunks.append({"chunk_id": _digest([key, segment_number, start, segment["source_version"]]),
                "doc_key": key, "kind": document["kind"], "entity_id": document["id"],
                "title": document["title"], "aliases": " ".join(document["aliases"]),
                "text": text[start:end], "start": start, "end": end,
                "location": json.dumps(location, ensure_ascii=False),
                "source_version": segment["source_version"]})
            if end == len(text):
                break
    return chunks


def capabilities(ws=None):
    config = _config(ws)
    result = {"lexical": {"ready": True, "backend": "sqlite-fts5-bm25"},
              "semantic": {"configured": _configured(config), "ready": False,
                           "model": config["model"], "indexed_chunks": 0,
                           "dimension": None}}
    if not _configured(config):
        result["semantic"]["reason"] = config.get("error") or "Embedding base URL and model are not configured"
    elif ws is not None and (Path(ws.root) / "search.db").is_file():
        try:
            with sqlite3.connect(f"file:{Path(ws.root) / 'search.db'}?mode=ro", uri=True) as db:
                row = db.execute("SELECT COUNT(*), MIN(dimension), MAX(dimension) FROM chunks "
                                 "WHERE vector IS NOT NULL AND vector_backend=?", (_backend(config),)).fetchone()
                total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                result["semantic"].update(indexed_chunks=row[0], dimension=row[1],
                    total_chunks=total, ready=row[0] > 0 and row[0] == total and row[1] == row[2])
        except sqlite3.Error:
            result["semantic"]["reason"] = "Embedding index is unavailable"
    if _configured(config) and not result["semantic"]["ready"]:
        result["semantic"].setdefault("reason", "Embedding index needs to be built for this backend")
    return result


def rebuild_index(ws, embeddings=False):
    """Incrementally index changed content, removing deleted/stale chunks."""
    with publication_lock(ws):
        indexed = _rebuild_lexical(ws)
    return _rebuild_vectors(ws, indexed, embeddings)


def _rebuild_lexical(ws):
    warnings = []
    documents = _documents(ws, warnings)
    changed = removed = 0
    with _db(ws) as db:
        existing = dict(db.execute("SELECT doc_key,fingerprint FROM documents"))
        for key in set(existing) - set(documents):
            db.execute("DELETE FROM chunk_fts WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_key=?)", (key,))
            db.execute("DELETE FROM chunks WHERE doc_key=?", (key,))
            db.execute("DELETE FROM documents WHERE doc_key=?", (key,))
            removed += 1
        for key, document in documents.items():
            fingerprint = _digest([INDEX_VERSION, document])
            if existing.get(key) == fingerprint:
                continue
            # Parser/locator or alias changes can leave the embedded text intact.
            # Reuse only vectors for byte-identical text in this library/document;
            # the backend identity is still checked before any vector is used.
            vectors={row['text']:(row['vector'],row['vector_backend'],row['dimension'])
                     for row in db.execute('SELECT text,vector,vector_backend,dimension FROM chunks WHERE doc_key=? AND vector IS NOT NULL',(key,))}
            db.execute("DELETE FROM chunk_fts WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_key=?)", (key,))
            db.execute("DELETE FROM chunks WHERE doc_key=?", (key,))
            for chunk in _chunk_document(key, document):
                db.execute("INSERT INTO chunks(chunk_id,doc_key,kind,entity_id,title,aliases,text,start,end,location,source_version) "
                           "VALUES(:chunk_id,:doc_key,:kind,:entity_id,:title,:aliases,:text,:start,:end,:location,:source_version)", chunk)
                if chunk['text'] in vectors:
                    db.execute('UPDATE chunks SET vector=?,vector_backend=?,dimension=? WHERE chunk_id=?',(*vectors[chunk['text']],chunk['chunk_id']))
                db.execute("INSERT INTO chunk_fts(chunk_id,title,aliases,text) VALUES(?,?,?,?)",
                           (chunk["chunk_id"], " ".join(tokenize(chunk["title"])),
                            " ".join(tokenize(chunk["aliases"])), " ".join(tokenize(chunk["text"]))))
            db.execute("INSERT OR REPLACE INTO documents VALUES(?,?)", (key, fingerprint))
            changed += 1
        counts = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    # The disposable index has caught up to this locked authoritative snapshot.
    state_path = getattr(ws, "db", None)
    if state_path and Path(state_path).is_file() and not warnings:
        with sqlite3.connect(state_path, timeout=30) as state:
            if state.execute("SELECT 1 FROM sqlite_master WHERE name='publication_state'").fetchone():
                state.execute("UPDATE publication_state SET index_dirty=0 WHERE library_id=?", (ws.library_id,))
    return {"library_id": ws.library_id, "documents": len(documents), "chunks": counts,
            "changed": changed, "removed": removed, "warnings": warnings}


def _rebuild_vectors(ws, indexed, embeddings):
    warnings = list(indexed["warnings"])
    config = _config(ws)
    embedded = 0
    if embeddings:
        if not _configured(config):
            warnings.append("Semantic search is not ready: configure independent embedding base URL and model")
        else:
            backend = _backend(config)
            with _db(ws) as db:
                pending = db.execute("SELECT chunk_id,text FROM chunks WHERE vector IS NULL OR vector_backend != ?", (backend,)).fetchall()
                dimension_row = db.execute("SELECT MIN(dimension),MAX(dimension) FROM chunks WHERE vector_backend=? AND vector IS NOT NULL", (backend,)).fetchone()
            dimension = dimension_row[0]
            if dimension_row[0] != dimension_row[1]:
                raise ValueError("Stored embedding index has inconsistent dimensions; rebuild required")
            for start in range(0, len(pending), 32):
                batch = pending[start:start + 32]
                try:
                    values = embed_texts(config, [row["text"] for row in batch])
                    if not isinstance(values, list) or len(values) != len(batch):
                        raise ValueError("Embedding result count changed")
                    normalized = [_vector(v, dimension) for v in values]
                    if dimension is None:
                        dimension = len(normalized[0])
                    if any(len(v) != dimension for v in normalized):
                        raise ValueError("Embedding dimension changed within a batch")
                    with _db(ws) as db:
                        for row, vector in zip(batch, normalized):
                            db.execute("UPDATE chunks SET vector=?,vector_backend=?,dimension=? WHERE chunk_id=?",
                                (json.dumps(vector), backend, dimension, row["chunk_id"]))
                    embedded += len(batch)
                except Exception as exc:
                    warnings.append("Embedding index incomplete (" + type(exc).__name__ + "); lexical search remains available")
                    break
    return {**indexed, "embedded": embedded, "capabilities": capabilities(ws), "warnings": warnings}


def _url(ws, row, location):
    if row["kind"] == "page":
        return ws.url("/page/" + row["entity_id"])
    parameters = {key: location[key] for key in ("page", "paragraph_id", "start", "end", "parsed_version")
                  if location.get(key) is not None}
    base = "/source/" + row["entity_id"]
    return ws.url(base + ("?" + urlencode(parameters) if parameters else ""))


def search(ws, question, limit=8, semantic=True, kind=None):
    """Fuse up to 40 lexical/vector candidates; return distinct entities."""
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("Search question must contain 1 to 4000 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 40:
        raise ValueError("Search limit must be between 1 and 40")
    if kind not in {None, "page", "source"}:
        raise ValueError("Search kind must be page or source")
    indexed = rebuild_index(ws, embeddings=False)
    warnings = list(indexed["warnings"])
    tokens = list(dict.fromkeys(tokenize(question)))[:128]
    config = _config(ws)
    with _db(ws) as db:
        rows = {row["chunk_id"]: dict(row) for row in db.execute("SELECT * FROM chunks")}
        lexical = []
        if tokens:
            match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            lexical = [row[0] for row in db.execute("SELECT chunk_fts.chunk_id FROM chunk_fts "
                "JOIN chunks ON chunks.chunk_id=chunk_fts.chunk_id WHERE chunk_fts MATCH ? "
                "AND (? IS NULL OR chunks.kind=?) "
                "ORDER BY bm25(chunk_fts,0,5,8,1),chunk_fts.chunk_id LIMIT ?", (match, kind, kind, RECALL_LIMIT))]
    vector_ranks = []
    semantic_used = False
    if semantic:
        ready = indexed["capabilities"]["semantic"]
        if ready["ready"]:
            try:
                output = embed_texts(config, [question])
                if not isinstance(output, list) or len(output) != 1:
                    raise ValueError("Query embedding count changed")
                vector = _vector(output[0], ready["dimension"])
                ranked = []
                for cid, row in rows.items():
                    if kind is not None and row["kind"] != kind:
                        continue
                    if row["vector"] is None or row["vector_backend"] != _backend(config):
                        continue
                    candidate = _vector(json.loads(row["vector"]), len(vector))
                    similarity = sum(a * b for a, b in zip(candidate, vector))
                    ranked.append((similarity, cid))
                ranked.sort(key=lambda item: (-item[0], item[1]))
                vector_ranks = [cid for _, cid in ranked[:RECALL_LIMIT]]
                semantic_used = True
            except Exception as exc:
                warnings.append("Semantic search unavailable (" + type(exc).__name__ + "); results use lexical search")
        else:
            warnings.append("Semantic search is not ready: " + ready.get("reason", "index unavailable"))
    scores = {}
    for ranking in (lexical, vector_ranks):
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0.0) + 1 / (RRF_K + rank)
    results, seen = [], set()
    for cid in sorted(scores, key=lambda k: (-scores[k], k)):
        row = rows[cid]
        if row["doc_key"] in seen:
            continue
        seen.add(row["doc_key"])
        location = json.loads(row["location"])
        result = {key: row[key] for key in ("kind", "title", "text", "start", "end", "source_version")}
        result.update(id=row["entity_id"], score=scores[cid], location=location,
                      locator=location["locator"], url=_url(ws, row, location))
        result["slug" if row["kind"] == "page" else "source_id"] = row["entity_id"]
        results.append(result)
        if len(results) >= limit:
            break
    return {"library_id": ws.library_id, "question": question.strip(), "results": results,
            "capabilities": indexed["capabilities"], "semantic_used": semantic_used,
            "warnings": warnings}
