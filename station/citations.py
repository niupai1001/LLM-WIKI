"""Readable, page-local citations for knowledge pages; original evidence is untouched."""
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .common import RAW, load_json
from .publication_metrics import normalize_publication, publication_summary_html

_SID = re.compile(r"[a-f0-9]{16}")
_CITATION = re.compile(r'(?<!!)\[[^\]\n]*\]\(\s*/source/([a-f0-9]{16})(?:\s+"[^"\n]*")?\s*\)|(?<!!)\[S:([a-f0-9]{16})\]')
_CODE = re.compile(r'(^[ \t]{0,3}`{3,}[^\n]*\n.*?^[ \t]{0,3}`{3,}[ \t]*$|^[ \t]{0,3}~{3,}[^\n]*\n.*?^[ \t]{0,3}~{3,}[ \t]*$|`+[^`\n]+`+)', re.M | re.S)
_EVIDENCE_HEADING = re.compile(r'^([ \t]{0,3})(#{2,6})[ \t]+(?:原始证据|参考来源|来源与证据|References|Sources)[ \t]*$', re.M | re.I)


@dataclass(frozen=True)
class Citation:
    source_id: str
    number: int
    title: str
    short_title: str
    published: str
    original_url: str
    available: bool
    authors: tuple = ()
    doi: str = ""
    doi_status: str = "unverified"
    publisher: str = ""
    publication_status: str = "unverified"
    identifier: str = ""
    arxiv_doi: str = ""
    publication: dict = field(default_factory=dict)


def _escape(value):
    return html.escape(str(value), quote=True)


def _prefix(value):
    return re.sub(r'[^a-zA-Z0-9_-]', '-', str(value))[:80] or 'knowledge'


def _date(value):
    if not isinstance(value, str) or not value.strip():
        return ''
    # Publisher metadata can declare only a year or a month. Preserve that
    # precision instead of hiding it or inventing a publication day.
    partial = value.strip().replace('/', '-')
    if re.fullmatch(r'\d{4}', partial) and 1 <= int(partial) <= 9999:
        return partial
    if re.fullmatch(r'\d{4}-\d{2}', partial):
        try:
            datetime.strptime(partial, '%Y-%m')
            return partial
        except ValueError:
            return ''
    try:
        return datetime.fromisoformat(partial.replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            return ''


def _external_url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.hostname.endswith('.invalid') and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ''


def source_info(sid):
    if not isinstance(sid, str) or not _SID.fullmatch(sid):
        return None
    try:
        item = load_json(RAW / (sid + '.json'))
        return item if isinstance(item, dict) else None
    except (OSError, ValueError):
        return None


def short_title(title):
    short = title.split(':', 1)[0].strip() or title
    return short if len(short) <= 35 else short[:34].rstrip() + '…'


def strip_legacy_evidence(source):
    """Remove only references-only legacy sections, without rewriting prose/code."""
    code_ranges = [(m.start(), m.end()) for m in _CODE.finditer(source)]
    deletions = []
    for heading in _EVIDENCE_HEADING.finditer(source):
        if any(start <= heading.start() < end for start, end in code_ranges):
            continue
        depth = len(heading[2])
        tail = source[heading.end():]
        next_heading = re.search(r'^#{1,' + str(depth) + r'}[ \t]+', tail, re.M)
        end = heading.end() + next_heading.start() if next_heading else len(source)
        section = source[heading.end():end]
        lines = [line.strip() for line in section.splitlines() if line.strip() and not re.fullmatch(r'[-*_]{3,}', line.strip())]
        if lines and all(re.match(r'(?:[-*+] |\d+\. )', line) and re.search(r'\]\(/source/[a-f0-9]{16}\)', line) for line in lines):
            start = heading.start()
            preceding_rule = re.search(r'\n[ \t]*[-*_]{3,}[ \t]*\n\s*$', source[:start])
            if preceding_rule:
                start = preceding_rule.start()
            deletions.append((start, end))
    for start, end in reversed(deletions):
        source = source[:start] + source[end:]
    return source.rstrip()


def prepare_citations(source, source_ids=(), lookup=None):
    """Return (renderable Markdown, ordered Citation objects).

    Body appearance establishes numbering; metadata evidence absent from the body
    follows in its existing order. Source URLs, never model-authored labels, identify
    citations. Fenced and inline code stays unchanged.
    """
    lookup = lookup or source_info
    source = strip_legacy_evidence(source)
    entries, numbered = [], {}

    def ensure(sid):
        if sid not in numbered:
            info = lookup(sid)
            info = info if isinstance(info, dict) else {}
            title = info.get('title')
            title = re.sub(r'\s+', ' ', title).strip() if isinstance(title, str) and title.strip() else '来源记录缺失'
            original = next((safe for value in [info.get('pdf_url'), info.get('original_url'), info.get('url')] if (safe := _external_url(value))), '')
            if not original and (info.get('pdf_path') or info.get('asset_path')):
                original = f'/source/{sid}/original'
            b = info.get('bibliography') if isinstance(info.get('bibliography'), dict) else {}
            names = tuple(a['name'] for a in b.get('authors', []) if isinstance(a, dict) and isinstance(a.get('name'), str))
            identity = ('arXiv ' + str(b['arxiv_id']) + (f"v{b['arxiv_version']}" if b.get('arxiv_version') else '')) if b.get('arxiv_id') else ''
            publication_date = b.get('publication_date') if b.get('original_version') == 'published' else None
            citation = Citation(sid, len(entries) + 1, title, short_title(title), _date(publication_date or b.get('first_published') or info.get('published')), original, bool(info),
                                names, b.get('doi') if isinstance(b.get('doi'), str) else '', b.get('doi_status', 'unverified'),
                                b.get('publisher', ''), b.get('publication_status', 'unverified'), identity, b.get('arxiv_doi', ''),
                                normalize_publication(info))
            numbered[sid] = citation
            entries.append(citation)
        return numbered[sid]

    def link(match):
        citation = ensure(match[1] or match[2])
        authors = '；'.join(citation.authors[:3]) + (' 等' if len(citation.authors) > 3 else '')
        tooltip = f'[{citation.number}] {citation.title} · 发布日期 {citation.published or "未提供"}' + (f' · {authors}' if authors else '') + (f' · DOI {citation.doi}' if citation.doi else '')
        label = f'[{citation.number} · {citation.short_title}]'
        return f'<a class="source-citation" href="/source/{citation.source_id}" title="{_escape(tooltip)}">{_escape(label)}</a>'

    pieces, previous = [], 0
    for code in _CODE.finditer(source):
        pieces.extend([_CITATION.sub(link, source[previous:code.start()]), code[0]])
        previous = code.end()
    pieces.append(_CITATION.sub(link, source[previous:]))
    for sid in source_ids if isinstance(source_ids, (list, tuple)) else ():
        if isinstance(sid, str) and _SID.fullmatch(sid):
            ensure(sid)
    return ''.join(pieces), entries


def evidence_jump(entries, prefix='knowledge'):
    if not entries:
        return ''
    return f'<div class="citation-navigation"><a href="#evidence-{_prefix(prefix)}">原始证据 ↓</a><span>{len(entries)} 份原文 · 编号对应正文引用</span></div>'


def evidence_html(entries, prefix='knowledge', label='原始证据'):
    if not entries:
        return ''
    prefix = _prefix(prefix)
    items = []
    for citation in entries:
        date = f'<time datetime="{citation.published}">{citation.published}</time>' if citation.published else '未提供'
        external = f'<a href="{_escape(citation.original_url)}" target="_blank" rel="noopener noreferrer">原文链接 ↗</a>' if citation.original_url else ''
        missing = '<span class="evidence-missing">来源记录缺失，需补充归档</span>' if not citation.available else ''
        author_text = '; '.join(citation.authors[:4]) + (' 等' if len(citation.authors) > 4 else '')
        author_line = f'<div class="evidence-authors">{_escape(author_text)}</div>' if author_text else ''
        doi_url = _external_url('https://doi.org/' + citation.doi) if citation.doi else ''
        doi = f'<span>出版 DOI <a href="{_escape(doi_url)}" target="_blank" rel="noopener noreferrer">{_escape(citation.doi)}</a></span>' if doi_url else '<span>出版 DOI 官方API未提供</span>' if citation.doi_status == 'not-provided' else '<span>出版 DOI 未核实</span>'
        if not doi_url and citation.doi_status == 'not-provided-on-official-site':
            doi = '<span>出版 DOI 官方出版页面未提供</span>'
        if citation.arxiv_doi:
            doi += f'<span>预印本 DOI <a href="{_escape("https://doi.org/" + citation.arxiv_doi)}" target="_blank" rel="noopener noreferrer">{_escape(citation.arxiv_doi)}</a></span>'
        publication = '<span>arXiv 预印本</span>' if citation.publication_status == 'arxiv-preprint' else '<span>正式发表版本</span>' if citation.publication_status == 'published' else ''
        publication += publication_summary_html(citation.publication)
        identifier = '<span>' + _escape(citation.identifier) + '</span>' if citation.identifier else ''
        items.append(f'<li id="evidence-{prefix}-{citation.number}"><span class="evidence-number">[{citation.number}]</span><div class="evidence-details"><a class="evidence-title" href="/source/{citation.source_id}">{_escape(citation.title)}</a>{author_line}<div class="evidence-meta"><span>发布日期 {date}</span>{identifier}{doi}{publication}{external}{missing}</div></div></li>')
    return f'<section class="evidence-panel" id="evidence-{prefix}"><h2>{_escape(label)} <span>{len(entries)} 份来源</span></h2><ol class="evidence-list">' + ''.join(items) + '</ol></section>'
