"""Addressable, immutable-source parsing for explicitly selected libraries.

Page numbers always mean physical PDF pages (one based); labels are separate.
Offsets are Python character offsets in the saved page text. OCR quality reports
recognition quality, never factual support. No acquisition or raw-source mutation
occurs in this module.
"""
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from pypdf import PdfReader

from .publication import publication_lock


SCHEMA_VERSION = 1
PARSER_VERSION = "addressable-pages-v2.3"
OCR_LANGUAGES = ("eng", "chi_sim", "chi_tra")
MIN_PAGE_CHARACTERS = 80
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_PAGES = 2000
MAX_PAGE_PIXELS = 50_000_000
MAX_RENDER_BYTES = 48 * 1024 * 1024
MAX_TEXT_CHARACTERS = 20_000_000
MAX_PARAGRAPH_CHARACTERS = 2400
_SID = re.compile(r"[a-f0-9]{16}")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save(path, value):
    """Atomic cache publication; interruption cannot replace a valid cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _record(ws, sid):
    if not isinstance(sid, str) or not _SID.fullmatch(sid):
        raise ValueError("Invalid source ID")
    path = ws.raw / (sid + ".json")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Archived source record is unavailable") from error
    if not isinstance(record, dict) or record.get("id", sid) != sid:
        raise ValueError("Archived source identity does not match")
    return record


def _original(ws, record):
    name = record.get("pdf_path") or record.get("asset_path")
    if not name:
        return None
    if not isinstance(name, str):
        raise ValueError("Invalid archived original path")
    path = (ws.originals / name).resolve()
    if not path.is_relative_to(ws.originals.resolve()):
        raise ValueError("Archived original is outside its library")
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Archived original exceeds the parsing size limit")
    return path


def _snapshot(ws, sid):
    """Brief library lock for record/asset identity; expensive parsing runs unlocked."""
    with publication_lock(ws):
        record = _record(ws, sid)
        original = _original(ws, record)
        identity = {"asset_hash": _hash(original.read_bytes()) if original else None,
                    "text": record.get("text", ""), "content_status": record.get("content_status", "missing"),
                    "content_format": record.get("content_format", ""),
                    "original_url": record.get("text_original_url", record.get("original_url", ""))}
        version = _hash(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode())
        return record, original, version


def source_version(ws, sid):
    """Content identity shared by tasks, extraction, indexing and stale checks."""
    return _snapshot(ws, sid)[2]


def _ocr_capability():
    rasterizer, recognizer = shutil.which("pdftoppm"), shutil.which("tesseract")
    languages = []
    warnings = []
    if not rasterizer:
        warnings.append("poppler-unavailable")
    if not recognizer:
        warnings.append("tesseract-unavailable")
    if recognizer:
        try:
            result = subprocess.run([recognizer, "--list-langs"], capture_output=True,
                                    text=True, timeout=10, check=True)
            languages = sorted(set(result.stdout.splitlines()[1:]).intersection(OCR_LANGUAGES))
        except (OSError, subprocess.SubprocessError):
            warnings.append("ocr-language-inventory-failed")
    missing = sorted(set(OCR_LANGUAGES) - set(languages))
    if missing:
        warnings.append("ocr-languages-missing:" + ",".join(missing))
    return {"pdftoppm": rasterizer, "tesseract": recognizer,
            "languages": languages, "missing_languages": missing, "warnings": warnings}


def _paragraphs(text, number, tokens=None):
    """Split without rewriting bytes, and bound long paragraphs for model batches."""
    output = []
    tokens = tokens or []
    bounds, cursor = [], 0
    for separator in re.finditer(r"\n[ \t]*\n+", text):
        bounds.append((cursor, separator.start()))
        cursor = separator.end()
    bounds.append((cursor, len(text)))
    for start, stop in bounds:
        while start < stop and text[start].isspace():
            start += 1
        while stop > start and text[stop - 1].isspace():
            stop -= 1
        while start < stop:
            end = min(start + MAX_PARAGRAPH_CHARACTERS, stop)
            if end < stop:
                boundary = text.rfind(" ", start + MAX_PARAGRAPH_CHARACTERS // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            paragraph = {"id": f"p{number:04d}-{len(output) + 1:04d}", "page": number,
                         "start": start, "end": end, "text": text[start:end]}
            aligned = [t for t in tokens if t["start"] < end and t["end"] > start]
            if aligned:
                left = min(t["bbox"][0] for t in aligned)
                top = min(t["bbox"][1] for t in aligned)
                right = max(t["bbox"][0] + t["bbox"][2] for t in aligned)
                bottom = max(t["bbox"][1] + t["bbox"][3] for t in aligned)
                paragraph.update(bbox=[left, top, right - left, bottom - top],
                                 ocr_confidence=sum(t["confidence"] for t in aligned) / len(aligned))
            output.append(paragraph)
            start = end
    return output


def _read_tsv(value):
    """Build text from recognized tokens while retaining its exact token offsets."""
    tokens, fragments, length, previous = [], [], 0, None
    rows = csv.DictReader(io.StringIO(value), delimiter="\t")
    for row in rows:
        text = row.get("text", "").strip()
        if not text or row.get("level") != "5":
            continue
        try:
            confidence = float(row["conf"])
            bbox = [int(row[k]) for k in ("left", "top", "width", "height")]
            group = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid OCR token record") from error
        if not 0 <= confidence <= 100 or min(bbox) < 0:
            raise ValueError("Invalid OCR token quality or location")
        if previous is None:
            separator = ""
        elif group[:3] != previous[:3]:
            separator = "\n\n"
        elif group != previous:
            separator = "\n"
        else:
            separator = " "
        fragments.extend((separator, text))
        length += len(separator)
        tokens.append({"text": text, "start": length, "end": length + len(text),
                       "bbox": bbox, "confidence": confidence})
        length += len(text)
        previous = group
    return "".join(fragments), tokens


def _ocr_page(path, number, width, height, capability):
    if not capability["pdftoppm"] or not capability["tesseract"] or not capability["languages"]:
        raise ValueError("ocr-unavailable")
    if width <= 0 or height <= 0 or width * height * (300 / 72) ** 2 > MAX_PAGE_PIXELS:
        raise ValueError("ocr-page-size-limit")
    with tempfile.TemporaryDirectory(prefix="station-ocr-") as temporary:
        prefix = Path(temporary) / "page"
        subprocess.run([capability["pdftoppm"], "-f", str(number), "-l", str(number),
                        "-singlefile", "-r", "300", "-png", str(path), str(prefix)],
                       capture_output=True, timeout=90, check=True)
        image = prefix.with_suffix(".png")
        if not image.exists() or image.stat().st_size > MAX_RENDER_BYTES:
            raise ValueError("ocr-render-size-limit")
        result = subprocess.run([capability["tesseract"], str(image), "stdout", "-l",
                                 "+".join(capability["languages"]), "--dpi", "300", "tsv"],
                                capture_output=True, text=True, timeout=90, check=True)
        text, tokens = _read_tsv(result.stdout)
        if not text.strip():
            raise ValueError("ocr-no-readable-text")
        return text, tokens


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        if not self.ignored and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section", "tr"}:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.ignored:
            self.ignored -= 1
        if not self.ignored and tag in {"p", "div", "li", "h1", "h2", "h3", "section", "tr"}:
            self.parts.append("\n\n")

    def handle_data(self, value):
        if not self.ignored:
            self.parts.append(value)


@contextmanager
def _pdf_warnings():
    warnings=[]
    owner=threading.get_ident()
    class Capture(logging.Handler):
        def emit(self,record):
            if record.thread==owner and any(word in record.getMessage().lower() for word in ('skipped','cannot','failed','unable','exceeded')):
                warnings.append('pdf-parser-content-incomplete')
    handler=Capture(level=logging.WARNING);logger=logging.getLogger('pypdf');logger.addHandler(handler)
    try: yield warnings
    finally: logger.removeHandler(handler)


def parse_source(ws, sid, ocr=True, force=False):
    """Return a versioned parsed source and cache it under ``ws.root/parsed``.

    Sparse pages remain visibly partial when OCR fails or lacks requested Chinese
    languages, even if some text is usable. Calling with ``force`` retries them.
    """
    if not isinstance(ocr, bool) or not isinstance(force, bool):
        raise ValueError("OCR and force must be booleans")
    record, original, content_version = _snapshot(ws, sid)
    is_pdf = bool(original and (original.suffix.lower() == ".pdf" or record.get("content_format") == "pdf"))
    capability = _ocr_capability() if is_pdf and ocr else {"languages": [], "warnings": [], "missing_languages": []}
    cache_identity = {"source_version": content_version, "parser": PARSER_VERSION, "schema": SCHEMA_VERSION,
                      "ocr": ocr, "languages": capability["languages"],
                      "metadata": [record.get("title"), record.get("kind"), record.get("category")],
                      "ocr_tools": bool(capability.get("pdftoppm") and capability.get("tesseract"))}
    cache_key = _hash(json.dumps(cache_identity, sort_keys=True).encode())
    cache = ws.root / "parsed" / (sid + ".json")
    if not force and cache.exists():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if (saved.get("cache_key") == cache_key and saved.get("library_id") == ws.library_id
                    and saved.get("schema_version") == SCHEMA_VERSION):
                return saved
        except (OSError, ValueError, AttributeError):
            pass
    pages, warnings = [], []
    if is_pdf:
        try:
            reader = PdfReader(str(original), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("encrypted-pdf")
            page_count = len(reader.pages)
            if page_count > MAX_PAGES:
                raise ValueError("pdf-page-count-limit")
            labels = reader.page_labels
        except Exception as error:
            raise ValueError("Archived PDF cannot be parsed") from error
        total_text = 0
        for index in range(page_count):
            from .worker import check_cancelled
            check_cancelled()
            page = {"number": index + 1, "label": labels[index] if index < len(labels) else str(index + 1),
                    "text": "", "method": "pypdf", "status": "full", "warnings": [], "ocr_tokens": []}
            try:
                pdf_page = reader.pages[index]
                with _pdf_warnings() as extraction_warnings:
                    page["text"] = pdf_page.extract_text() or ""
                if extraction_warnings:
                    page['warnings'].extend(sorted(set(extraction_warnings)))
                    page['status']='partial'
                if any(0xD800<=ord(character)<=0xDFFF for character in page['text']):
                    page['text']=page['text'].encode('utf-16-le',errors='surrogatepass').decode('utf-16-le',errors='replace')
                    page['warnings'].append('pdf-font-map-unreadable-characters')
                    page['status']='partial'
            except Exception:
                page["warnings"].append("pdf-text-extraction-failed")
                pdf_page = None
            if sum(character.isalnum() for character in page["text"]) < MIN_PAGE_CHARACTERS:
                page["status"] = "partial"
                if ocr and pdf_page is not None:
                    page["warnings"].extend(capability["warnings"])
                    try:
                        page["text"], page["ocr_tokens"] = _ocr_page(
                            original, index + 1, float(pdf_page.mediabox.width),
                            float(pdf_page.mediabox.height), capability)
                        page["method"] = "tesseract"
                        page["dpi"] = 300
                        page["ocr_languages"] = capability["languages"]
                        page["status"] = "partial" if capability["missing_languages"] else "full"
                    except ValueError as error:
                        page["warnings"].append(str(error) if str(error).startswith("ocr-") else "ocr-invalid-output")
                    except (OSError, subprocess.SubprocessError):
                        page["warnings"].append("ocr-process-failed")
                else:
                    page["warnings"].append("ocr-disabled" if not ocr else "ocr-page-unavailable")
                if not page["text"].strip():
                    page["status"] = "missing"
            total_text += len(page["text"])
            if total_text > MAX_TEXT_CHARACTERS:
                page.update(text="", ocr_tokens=[], status="missing")
                page["warnings"].append("source-text-size-limit")
            page["paragraphs"] = _paragraphs(page["text"], page["number"], page["ocr_tokens"])
            pages.append(page)
        warnings.append("PDF figure structure and complex formulas require the archived original")
    else:
        text = record.get("text", "") if record.get("content_status") == "full-text" else ""
        method = "archived-text"
        if not isinstance(text, str):
            raise ValueError("Archived text must be a string")
        if not text and original and original.suffix.lower() in {".md", ".txt", ".html", ".htm"}:
            text = original.read_text(encoding="utf-8")
            method = "archived-original"
            if original.suffix.lower() in {".html", ".htm"}:
                html = _HTMLText()
                html.feed(text)
                text = "".join(html.parts)
                method = "html-text"
        if len(text) > MAX_TEXT_CHARACTERS:
            raise ValueError("Archived text exceeds the parsing size limit")
        if not original and record.get("asset_path"):
            warnings.append("archived-original-missing")
        pages = [{"number": 1, "label": "1", "text": text, "method": method,
                  "status": "full" if text.strip() else "missing", "warnings": [],
                  "paragraphs": _paragraphs(text, 1), "ocr_tokens": []}]
    paragraphs, global_start = [], 0
    for page in pages:
        page["global_start"] = global_start
        for paragraph in page["paragraphs"]:
            paragraph["global_start"] = global_start + paragraph["start"]
            paragraph["global_end"] = global_start + paragraph["end"]
            paragraphs.append(dict(paragraph, method=page["method"], page_label=page["label"]))
        global_start += len(page["text"]) + 2
    full_pages = [p["number"] for p in pages if p["status"] == "full"]
    missing_pages = [p["number"] for p in pages if p["status"] == "missing"]
    result = {"schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION, "library_id": ws.library_id,
              "source_id": sid, "source_version": content_version, "cache_key": cache_key,
              "title": record.get("title", sid), "kind": record.get("kind", "document"),
              "category": record.get("category", "other"), "created_at": _now(),
              "status": "full" if len(full_pages) == len(pages) and pages else "partial" if paragraphs else "missing",
              "pages": pages, "paragraphs": paragraphs, "warnings": warnings,
              "coverage": {"total_pages": len(pages), "full_pages": full_pages, "missing_pages": missing_pages,
                           "partial_pages": [p["number"] for p in pages if p["status"] == "partial"],
                           "total_paragraphs": len(paragraphs), "readable_characters": sum(len(p["text"]) for p in pages),
                           "boundaries": "Page labels are separate from physical page numbers; figures and formulas are not structurally parsed"}}
    with publication_lock(ws):
        from .worker import check_cancelled
        check_cancelled()
        if source_version(ws, sid) != content_version:
            raise ValueError("Archived source changed while it was being parsed")
        _save(cache, result)
    return result
