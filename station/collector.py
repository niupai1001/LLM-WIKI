"""Classified source discovery and original-document archiving."""
import html
import http.client
import io
import os
import re
import socket
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import calendar
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from .common import ASSETS, RAW, connect, load_json, save_json, source_id, utcnow
from .knowledge import validate_category
from .bibliography import arxiv_identifier, arxiv_doi_metadata, build_bibliography, parse_arxiv_atom, query_url

MAX_DOWNLOAD = 15 * 1024 * 1024
KINDS = {"paper", "article", "news", "note", "document"}


def validate_kind(kind):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Unknown source kind")
    return kind

def public_http(url):
    """Validate URL syntax; network entry points additionally validate DNS."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in {None, 80, 443}:
            return False
        if host.lower() == "localhost" or host.lower().endswith((".local", ".internal", ".localhost")):
            return False
        try:
            return ip_address(host).is_global
        except ValueError:
            return True
    except (ValueError, TypeError):
        return False


def _validate_destination(url):
    if not public_http(url):
        raise ValueError("Non-public URL blocked")
    parsed = urlparse(url)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("Source DNS lookup failed") from None
    if not addresses or any(not ip_address(addr[4][0]).is_global for addr in addresses):
        raise ValueError("Non-public DNS destination blocked")
    return tuple(dict.fromkeys(addr[4][0] for addr in addresses))


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _pinned_connection(scheme):
    def factory(host, **kwargs):
        addresses = _validate_destination(f"{scheme}://{host}")
        connection_type = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        connection = connection_type(host, **kwargs)
        def connect_pinned(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
            last_error = None
            for ip in addresses:
                try:
                    return socket.create_connection((ip, address[1]), timeout, source_address)
                except OSError as exc:
                    last_error = exc
            raise last_error or OSError("Public connection unavailable")
        # Preserve the original host for HTTP Host and TLS SNI/certificate checks,
        # while opening the TCP connection only to the already-validated IPs.
        connection._create_connection = connect_pinned
        return connection
    return factory


class _PublicHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_pinned_connection("http"), req)


class _PublicHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_pinned_connection("https"), req, context=self._context)


def fetch_bytes(url, timeout=20):
    """Bounded fetch with validation before every redirected request."""
    _validate_destination(url)
    request = urllib.request.Request(url, headers={"User-Agent": "PiIntelWiki/0.2 (personal research archive)"})
    # Ignore ambient HTTP proxy variables: they can route public requests internally.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublicRedirect(), _PublicHTTP(), _PublicHTTPS())
    with opener.open(request, timeout=timeout) as response:
        _validate_destination(response.url)
        size = response.headers.get("Content-Length")
        if size and int(size) > MAX_DOWNLOAD:
            raise ValueError("Original exceeds 15MB download limit")
        content = response.read(MAX_DOWNLOAD + 1)
        if len(content) > MAX_DOWNLOAD:
            raise ValueError("Original exceeds 15MB download limit")
        return content


def _plain(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def _pdf_text(data):
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except PyPdfError:
        raise ValueError("PDF文件损坏、加密或无法读取") from None


def _html_text(data):
    from trafilatura import extract
    return (extract(data, output_format="markdown", include_comments=False, include_tables=True) or "").strip()


def _html_title(data):
    from trafilatura.metadata import extract_metadata
    decoded = data.decode("utf-8", errors="replace")
    try:
        metadata = extract_metadata(decoded)
        if metadata and metadata.title:
            return _plain(metadata.title)
    except (ValueError, TypeError):
        pass
    match = re.search(r"<title[^>]*>(.*?)</title>", decoded, flags=re.I | re.S)
    return _plain(match.group(1)) if match else ""


def _pdf_title(data):
    try:
        from pypdf import PdfReader
        metadata = PdfReader(io.BytesIO(data)).metadata
        return _plain(metadata.title) if metadata and isinstance(metadata.title, str) else ""
    except Exception:
        return ""


def _is_arxiv(url):
    return urlparse(url).hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}


def _source_title(requested, original, url):
    if requested and requested.strip() != url:
        return requested.strip()
    if original.get("original_title"):
        return original["original_title"]
    match = re.search(r"/(?:abs|pdf|html)/(.+)", urlparse(url).path)
    if _is_arxiv(url) and match:
        return "arXiv " + match.group(1)
    parsed = urlparse(url)
    return "网页资料 · " + (parsed.hostname or "未知来源") + (parsed.path if parsed.path != "/" else "")


def _asset(sid, suffix, data):
    ASSETS.mkdir(parents=True, exist_ok=True)
    name = f"{sid}{suffix}"
    with tempfile.NamedTemporaryFile(dir=ASSETS, prefix=f".{sid}-", delete=False) as temporary:
        temp_path = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    try:
        temp_path.replace(ASSETS / name)
    finally:
        temp_path.unlink(missing_ok=True)
    return name


def _validate_pdf(data):
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("Original exceeds 15MB download limit")
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("PDF接口未返回有效PDF文件")
    return data


def _arxiv_urls(url):
    match = re.search(r"/(?:abs|pdf|html)/((?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?)", urlparse(url).path, flags=re.I)
    if _is_arxiv(url) and match:
        ident = match.group(1)
        return f"https://arxiv.org/pdf/{ident}", f"https://arxiv.org/html/{ident}"
    return None


def _original(url, sid):
    """Preserve paper layout in a PDF; source text is a separate LLM input."""
    arxiv_urls = _arxiv_urls(url)
    candidates = [(arxiv_urls[0], "pdf"), (arxiv_urls[1], "html")] if arxiv_urls else [(url, "auto")]
    failures, captured = [], {}
    for original_url, fmt in candidates:
        try:
            data = fetch_bytes(original_url)
            is_pdf = fmt == "pdf" or data.lstrip().startswith(b"%PDF-")
            if is_pdf:
                _validate_pdf(data)
                name = _asset(sid, ".pdf", data)
                captured.update(asset_path=name, pdf_path=name, pdf_url=original_url, original_url=original_url,
                                content_format="pdf", original_title=_pdf_title(data))
                text = _pdf_text(data)
                text_format = "pdf"
            else:
                decoded = data.decode("utf-8", errors="replace")
                if arxiv_urls and ("ltx_document" not in decoded or "ltx_section" not in decoded):
                    raise ValueError("Full paper HTML unavailable")
                name = _asset(sid, ".html", data)
                title = _html_title(data)
                captured.update(html_path=name)
                if not captured.get("pdf_path"):
                    captured.update(asset_path=name, original_url=original_url, content_format="html")
                if title:
                    captured["original_title"] = title
                text = _html_text(data)
                text_format = "html"
            if not text:
                raise ValueError("Original has no extractable text")
            return {**captured, "text": text, "text_format": text_format, "text_original_url": original_url,
                    "content_status": "full-text", "body_level": "original-extracted"}
        except Exception as exc:
            failures.append(type(exc).__name__)
    return {**captured, "text": "", "content_status": "missing", "content_format": captured.get("content_format", "unknown"),
            "original_url": captured.get("original_url", url), "fetch_error": "Original retrieval/extraction failed (" + ", ".join(failures) + ")"}


def _pdf_asset_path(item):
    name = item.get("pdf_path") or (item.get("asset_path") if item.get("content_format") == "pdf" else None)
    if not isinstance(name, str):
        return None
    path = (ASSETS / name).resolve()
    if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
        return None
    if path.stat().st_size > MAX_DOWNLOAD:
        return None
    with path.open("rb") as stream:
        if not stream.read(1024).lstrip().startswith(b"%PDF-"):
            return None
    return name


def ensure_source_pdf(sid):
    """Add only a PDF attachment, preserving text, classification and review state.

    Returns {id, pdf_path, pdf_url, downloaded}; raises ValueError on failure.
    Legacy local PDF assets are promoted without a network request.
    """
    db = connect()
    try:
        row = db.execute("SELECT raw_path,stage FROM sources WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("Source not found")
        if row["stage"] == "compiling":
            raise ValueError("资料正在编写中，请完成后补充PDF原件")
        path = Path(row["raw_path"])
        item = load_json(path)
        existing = _pdf_asset_path(item)
        urls = _arxiv_urls(item.get("url", ""))
        pdf_url = urls[0] if urls else (item.get("pdf_url") or item.get("original_url", ""))
        data = None
        if not existing:
            if not urls:
                parsed = urlparse(pdf_url)
                if not public_http(pdf_url) or not parsed.path.lower().endswith(".pdf"):
                    raise ValueError("该资料没有可获取的PDF原件地址")
            try:
                data = _validate_pdf(fetch_bytes(pdf_url))
            except Exception as exc:
                raise ValueError("PDF原件下载失败（" + type(exc).__name__ + "）") from None
        # Acquire the write lock after downloading, then re-read current metadata:
        # concurrent classification/compilation cannot be overwritten by a stale copy.
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT raw_path,stage FROM sources WHERE id=?", (sid,)).fetchone()
        if current is None:
            raise ValueError("Source not found")
        if current["stage"] == "compiling":
            raise ValueError("资料正在编写中，请完成后补充PDF原件")
        path = Path(current["raw_path"])
        old_json = path.read_bytes()
        item = load_json(path)
        current_pdf = _pdf_asset_path(item)
        old_pdf = None
        pdf_path = None
        wrote_pdf = False
        try:
            if current_pdf:
                pdf_path = current_pdf
            elif data is not None:
                pdf_path = f"{sid}.pdf"
                target = ASSETS / pdf_path
                old_pdf = target.read_bytes() if target.exists() else None
                _asset(sid, ".pdf", data)
                wrote_pdf = True
            else:
                raise ValueError("PDF原件在更新前已被移除，请重试")
            item.setdefault("text_format", item.get("content_format", "unknown"))
            item.setdefault("text_original_url", item.get("original_url") or item.get("url", ""))
            if item.get("content_format") == "html" and item.get("asset_path"):
                item.setdefault("html_path", item["asset_path"])
            item.update(pdf_path=pdf_path, pdf_url=pdf_url, asset_path=pdf_path, content_format="pdf")
            if pdf_url:
                item["original_url"] = pdf_url
            save_json(path, item)
            db.commit()
        except BaseException:
            db.rollback()
            if wrote_pdf:
                if old_pdf is None:
                    (ASSETS / pdf_path).unlink(missing_ok=True)
                else:
                    _asset(sid, ".pdf", old_pdf)
            # Restore the previous metadata if the filesystem write or commit failed.
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".restore-", delete=False) as restore:
                restore.write(old_json)
                restore_path = Path(restore.name)
            restore_path.replace(path)
            raise
        return {"id": sid, "pdf_path": pdf_path, "pdf_url": pdf_url, "downloaded": wrote_pdf}
    finally:
        db.close()


def _store(item, db):
    item["bibliography"] = bibliography_for_record(item)
    path = RAW / f"{item['id']}.json"
    save_json(path, item)
    db.execute("INSERT INTO sources (id,title,url,feed,kind,published,summary,raw_path,captured_at,category,content_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (item["id"], item["title"], item["url"], item["feed"], item["kind"], item["published"], item["summary"], str(path), item["captured_at"], item["category"], item["content_status"]))
    db.commit()


def archive(record, db):
    """Idempotent source archive. Explicit text is a caller-provided original."""
    if not public_http(record["url"]):
        raise ValueError("Invalid public source link")
    category = validate_category(record.get("category", "other"))
    kind = validate_kind(record.get("kind", "article"))
    sid = source_id(record["url"])
    if db.execute("SELECT id FROM sources WHERE id = ?", (sid,)).fetchone():
        return None
    summary = _plain(record.get("summary", ""))
    if record.get("text", "").strip():
        body = record["text"]
        original = {"text": body, "content_status": "full-text", "content_format": "markdown", "original_url": record["url"],
                    "asset_path": _asset(sid, ".md", body.encode("utf-8")), "body_level": "manual-original"}
    else:
        original = _original(record["url"], sid)
        if original["content_status"] != "full-text" and summary:
            original.update(text=summary, content_status="summary-only", body_level="feed-summary")
    item = {"id": sid, "title": _source_title(record.get("title"), original, record["url"]), "url": record["url"], "category": category,
            "feed": record.get("feed", "manual"), "kind": kind,
            "title_is_custom": bool(record.get("title") and record["title"].strip() != record["url"]),
            "published": record.get("published", ""), "captured_at": utcnow(), "summary": summary, **original}
    _store(item, db)
    return sid


def _keyword_match(text, word):
    word = word.strip().casefold()
    if not word:
        return False
    if word.isascii():
        return re.search(r'(?<![a-z0-9])' + re.escape(word) + r'(?![a-z0-9])', text) is not None
    return word in text


def entry_matches(entry, feed, now=None):
    """Curated-feed discovery rules; these are relevance filters, not quality claims."""
    text = html.unescape(re.sub(r'<[^>]+>', ' ', entry.get('title', '') + '\n' + entry.get('summary', entry.get('description', '')))).casefold()
    for name in ('include_keywords', 'require_keywords'):
        words = feed.get(name, [])
        if words and not any(_keyword_match(text, word) for word in words):
            return False
    if any(_keyword_match(text, word) for word in feed.get('exclude_keywords', [])):
        return False
    age = feed.get('max_age_days')
    if age:
        date = entry.get('published_parsed') or entry.get('updated_parsed')
        try:
            if date:
                published = datetime.fromtimestamp(calendar.timegm(date), timezone.utc)
            else:
                value = entry.get('published', entry.get('updated', ''))
                try:
                    published = datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    published = parsedate_to_datetime(value)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            now = now or datetime.now(timezone.utc)
            if published < now - timedelta(days=float(age)) or published > now + timedelta(days=1):
                return False
        except (ValueError, TypeError, OverflowError):
            return False
    return True


def ingest():
    import feedparser
    feeds = load_json(Path(__file__).resolve().parent.parent / "config/feeds.json")
    db = connect()
    limit = max(1, min(int(os.getenv("MAX_PER_FEED", "12")), 100))
    count = 0
    captured_ids = []
    try:
        for feed in feeds:
            if feed.get("enabled", True) is False:
                continue
            print(f"[FEED] {feed['name']}")
            try:
                parsed = feedparser.parse(fetch_bytes(feed["url"]))
                if parsed.bozo and not parsed.entries:
                    raise ValueError("Invalid feed")
                admitted = 0
                for entry in list(parsed.entries)[:500]:
                    if not entry_matches(entry, feed):
                        continue
                    url = entry.get("link", "")
                    if not public_http(url):
                        continue
                    if db.execute('SELECT 1 FROM sources WHERE id=?', (source_id(url),)).fetchone():
                        continue
                    if admitted >= limit:
                        break
                    admitted += 1
                    sid = archive({"title": entry.get("title", url).strip(), "url": url, "feed": feed["name"],
                                   "kind": feed.get("kind", "article"), "category": feed.get("category", "other"),
                                   "published": entry.get("published", entry.get("updated", "")),
                                   "summary": entry.get("summary", entry.get("description", ""))}, db)
                    if sid:
                        count += 1
                        captured_ids.append(sid)
                        print(f"  + {sid}")
            except Exception as exc:
                print(f"[ERROR] Feed retrieval failed: {type(exc).__name__}")
    finally:
        db.close()
    if captured_ids:
        refresh_bibliographies(captured_ids, verify_doi=True)
    print(f"Ingest complete: {count} new records")
    return count


def import_file(title, file_path, category="other", kind=None):
    """Import an original .md/.txt/.pdf file; return its id, including duplicates."""
    category = validate_category(category)
    path = Path(file_path)
    suffix = path.suffix.lower()
    title = (title or path.stem).strip()
    kind = validate_kind(kind if kind is not None else ("paper" if suffix == ".pdf" else "document"))
    if suffix not in {".md", ".txt", ".pdf"}:
        raise ValueError("Supported originals: .md, .txt, .pdf")
    if path.stat().st_size > MAX_DOWNLOAD:
        raise ValueError("Original exceeds 15MB import limit")
    data = path.read_bytes()
    if suffix == ".pdf":
        _validate_pdf(data)
    try:
        body = _pdf_text(data) if suffix == ".pdf" else data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("文本文件需要UTF-8编码") from None
    if not body.strip() and suffix != ".pdf":
        raise ValueError("Original has no extractable text")
    identity = (title + "\n" + body).encode() if body.strip() else title.encode() + b"\n" + data
    sid = sha256(identity).hexdigest()[:16]
    db = connect()
    try:
        if db.execute("SELECT 1 FROM sources WHERE id = ?", (sid,)).fetchone():
            return sid
        url = f"https://notes.local.invalid/{sid}"
        item = {"id": sid, "title": title, "url": url, "original_url": "", "feed": "my-notes" if kind == "note" else "file-import", "kind": kind,
                "category": category, "published": utcnow(), "captured_at": utcnow(), "summary": body[:1000],
                "text": body, "body_level": "manual-original", "content_status": "full-text" if body.strip() else "missing",
                "content_format": {".md": "markdown", ".txt": "text", ".pdf": "pdf"}[suffix], "asset_path": _asset(sid, suffix, data)}
        if suffix == ".pdf":
            item.update(pdf_path=item["asset_path"], pdf_url="", text_format="pdf")
        if not body.strip():
            item["fetch_error"] = "Original PDF is preserved; OCR is required for searchable text"
        _store(item, db)
        return sid
    finally:
        db.close()


def add_note(title, file_path, category="research"):
    """Compatibility helper: duplicate authored notes return None."""
    db = connect()
    try:
        previous = {row[0] for row in db.execute("SELECT id FROM sources")}
    finally:
        db.close()
    sid = import_file(title, file_path, category, kind="note")
    return None if sid in previous else sid


def refresh_source(sid):
    """Retry incomplete remote originals without changing review stage or classification."""
    db = connect()
    try:
        row = db.execute("SELECT raw_path,content_status FROM sources WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("Source not found")
        if row["content_status"] == "full-text":
            return sid
        path = Path(row["raw_path"])
        item = load_json(path)
        if item.get("body_level") == "manual-original" or not public_http(item.get("url")):
            raise ValueError("Local originals cannot be fetched again; reimport searchable text")
        original = _original(item["url"], sid)
        if original["content_status"] != "full-text" and item.get("summary"):
            original.update(text=item["summary"], content_status="summary-only", body_level="feed-summary")
        item.pop("fetch_error", None)
        item.update(original)
        item["retrieved_at"] = utcnow()
        if original.get("original_title") and not item.get("title_is_custom", item.get("title") != item["url"]):
            item["title"] = _source_title(None, original, item["url"])
        save_json(path, item)
        db.execute("UPDATE sources SET title=?,content_status=? WHERE id=?", (item["title"], item["content_status"], sid))
        db.commit()
        return sid
    finally:
        db.close()


def import_url(url, category="other", title=None, kind="article", verify_metadata=False):
    validate_category(category)
    validate_kind(kind)
    if not public_http(url):
        raise ValueError("Invalid public source link")
    if kind == "article" and _is_arxiv(url):
        kind = "paper"
    db = connect()
    try:
        sid = archive({"url": url, "title": title, "category": category, "kind": kind}, db)
        existing = db.execute("SELECT content_status FROM sources WHERE id=?", (source_id(url),)).fetchone()
    finally:
        db.close()
    result = sid or source_id(url)
    if sid is None and existing["content_status"] != "full-text":
        result = refresh_source(result)
    if verify_metadata:
        refresh_bibliographies([result], verify_doi=True)
    return result


def set_category(sid, category):
    category = validate_category(category)
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT raw_path,stage FROM sources WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("Source not found")
        if row["stage"] in {"drafted", "compiling"}:
            raise ValueError("编写中或待审草稿的资料不能修改分类；请先完成或拒绝草稿")
        path = Path(row["raw_path"])
        item = load_json(path)
        item["category"] = category
        save_json(path, item)
        db.execute("UPDATE sources SET category=? WHERE id=?", (category, sid))
        db.commit()
    finally:
        db.close()


def _archived_html(record):
    name = record.get('html_path') or (record.get('asset_path') if record.get('content_format') == 'html' else None)
    if not isinstance(name, str):
        return None
    path = (ASSETS / name).resolve()
    if not path.is_relative_to(ASSETS.resolve()) or not path.is_file() or path.stat().st_size > MAX_DOWNLOAD:
        return None
    return path.read_bytes()


def bibliography_for_record(record, authoritative=None):
    """Local parsing only; safe for rendering and all archive/import code paths."""
    return build_bibliography(record, authoritative, _archived_html(record))


def _archived_arxiv_identity(record):
    identity = arxiv_identifier(record.get('url', ''))
    if not identity or identity['version']:
        return identity
    name = record.get('pdf_path') or (record.get('asset_path') if record.get('content_format') == 'pdf' else None)
    if isinstance(name, str):
        path = (ASSETS / name).resolve()
        if path.is_relative_to(ASSETS.resolve()) and path.is_file() and path.stat().st_size <= MAX_DOWNLOAD:
            try:
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 2048))
                    if b'%%EOF' not in stream.read():
                        return identity
                from pypdf import PdfReader
                page = PdfReader(path).pages[0].extract_text() or ''
                match = re.search(r'arXiv:\s*' + re.escape(identity['id']) + r'v(\d+)\b', page, re.I)
                if match:
                    identity['version'] = int(match[1])
            except Exception:
                pass
    return identity


def refresh_bibliographies(source_ids=None, fetcher=None, verify_doi=False):
    """Batch-check official arXiv metadata; update only bibliography, never originals.

    The complete Atom response is cached for the UTC day and retained as evidence.
    API failures are explicit and preserve earlier verified bibliographic facts.
    Returns {updated, verified, failed, skipped, source_count}.
    """
    fetcher = fetcher or fetch_bytes
    db = connect()
    try:
        rows = db.execute('SELECT id,raw_path FROM sources ORDER BY id').fetchall()
    finally:
        db.close()
    requested = set(source_ids) if source_ids is not None else None
    records = {}
    for row in rows:
        if requested is None or row['id'] in requested:
            records[row['id']] = load_json(Path(row['raw_path']))
    identities = {sid: identity for sid, record in records.items() if (identity := _archived_arxiv_identity(record))}
    result = {'updated': 0, 'verified': 0, 'failed': 0, 'skipped': 0, 'source_count': len(rows)}
    authoritative, failure, api_url, snapshot = {}, '', '', ''
    if identities:
        ids = [value['id'] + (f"v{value['version']}" if value['version'] else '') for value in identities.values()]
        api_url = query_url(ids)
        cache_dir = RAW.parent / 'source_metadata'
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / ('arxiv-' + utcnow()[:10] + '-' + sha256(api_url.encode()).hexdigest()[:16] + '.atom.xml')
        try:
            data = cache_file.read_bytes() if cache_file.is_file() else fetcher(api_url, timeout=20)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('Metadata response exceeded 2MB')
            authoritative = parse_arxiv_atom(data, api_url)
            if not cache_file.is_file():
                with tempfile.NamedTemporaryFile(dir=cache_dir, prefix='.metadata-', delete=False) as temporary:
                    temporary.write(data)
                    temporary_path = Path(temporary.name)
                temporary_path.replace(cache_file)
            snapshot = str(cache_file.relative_to(RAW.parent))
        except Exception as exc:
            failure = type(exc).__name__
    doi_records = {}
    if verify_doi:
        for sid, identity in identities.items():
            url = 'https://arxiv.org/abs/' + identity['id']
            cached = RAW.parent / 'source_metadata' / ('arxiv-' + identity['id'].replace('/', '-') + '.abs.html')
            try:
                data = cached.read_bytes() if cached.is_file() else fetcher(url, timeout=20)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError('Metadata page exceeded 2MB')
                doi_records[sid] = arxiv_doi_metadata(data, url)
                if not cached.is_file():
                    cached.write_bytes(data)
            except Exception as exc:
                doi_records[sid] = {'arxiv_doi_query_error': type(exc).__name__}
    for sid, snapshot_record in records.items():
        identity = identities.get(sid)
        official = authoritative.get(identity['id']) if identity else None
        db = connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT raw_path,stage FROM sources WHERE id=?', (sid,)).fetchone()
            if row is None or row['stage'] == 'compiling':
                result['skipped'] += 1
                continue
            path = Path(row['raw_path'])
            record = load_json(path)
            bibliography = bibliography_for_record(record, official)
            if sid in doi_records:
                bibliography.update(doi_records[sid])
                if bibliography.get('arxiv_doi'):
                    bibliography.setdefault('field_sources', {})['arxiv_doi'] = {'method': 'arxiv-abs-page', 'source_url': doi_records[sid].get('arxiv_doi_source_url', '')}
            if identity:
                bibliography.update(metadata_source_url=api_url, metadata_snapshot=snapshot, last_query_at=utcnow())
                if official:
                    result['verified'] += 1
                else:
                    bibliography['last_query_status'] = 'failed' if failure else 'not-found'
                    bibliography['query_error'] = failure or 'Identifier not returned by official API'
                    if bibliography.get('metadata_status') != 'verified':
                        bibliography['metadata_status'] = 'failed'
                    result['failed'] += 1
            old = path.read_bytes()
            try:
                record['bibliography'] = bibliography
                if official and official.get('title') and not record.get('title_is_custom', True):
                    record['title'] = official['title']
                    db.execute('UPDATE sources SET title=? WHERE id=?', (record['title'], sid))
                save_json(path, record)
                db.commit()
            except BaseException:
                db.rollback()
                path.write_bytes(old)
                raise
            result['updated'] += 1
        finally:
            db.close()
    return result
