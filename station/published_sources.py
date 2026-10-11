"""Archive publisher PDFs using explicit bibliographic metadata on official pages.

Publication is a bibliographic fact, not a correctness verdict. An official paper
page without a readable full PDF must never become a full-text source.
"""
import copy
import re
from urllib.parse import urljoin, urlsplit

from .bibliography import HeadMetadata, html_metadata
from .common import ROOT, utcnow

OFFICIAL_HOSTS = {
    'aclanthology.org': 'ACL Anthology',
    'proceedings.iclr.cc': 'ICLR',
    'proceedings.neurips.cc': 'NeurIPS',
    'proceedings.nips.cc': 'NeurIPS',
    'papers.nips.cc': 'NeurIPS',
    'jmlr.org': 'JMLR',
    'www.jmlr.org': 'JMLR',
}


def is_publication_page(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or parsed.hostname not in OFFICIAL_HOSTS:
            return False
        if parsed.path.lower().endswith('.pdf'):
            return False
        if parsed.hostname == 'aclanthology.org':
            return bool(re.fullmatch(r'/[A-Za-z0-9][A-Za-z0-9.-]+/?', parsed.path))
        if parsed.hostname in {'jmlr.org', 'www.jmlr.org'}:
            return bool(re.fullmatch(r'/papers/v\d+/[A-Za-z0-9.-]+\.html', parsed.path))
        return bool(re.fullmatch(r'/paper_files/paper/\d{4}/hash/[a-f0-9]+-Abstract(?:-Conference)?\.html', parsed.path))
    except (ValueError, TypeError):
        return False


def _author_name(value):
    # citation_author commonly uses the unambiguous bibliographic "Family, Given".
    parts = value.split(', ', 1)
    return parts[1] + ' ' + parts[0] if len(parts) == 2 else value


def resolve_publication(url, fetcher=None):
    """Return official declared fields and a public PDF URL, or None for other sites."""
    if not is_publication_page(url):
        return None
    if fetcher is None:
        from .collector import fetch_bytes
        fetcher = fetch_bytes
    from .collector import public_http
    data = fetcher(url)
    parser = HeadMetadata()
    parser.feed(data.decode('utf-8', errors='replace'))
    fields = html_metadata(data)
    candidates = parser.values.get('citation_pdf_url', [])
    pdf_url = urljoin(url, candidates[0]) if candidates else ''
    if (not fields['title'] or not fields['authors'] or not fields['journal_ref']
            or not public_http(pdf_url) or urlsplit(pdf_url).hostname not in OFFICIAL_HOSTS
            or not urlsplit(pdf_url).path.lower().endswith('.pdf')):
        raise ValueError('正式出版页面缺少可核实的题名、作者、出版信息或 PDF 链接')
    declared_date = fields['first_published']
    declared_year = next(iter(parser.values.get('citation_volume', [])), '')
    year_match = re.match(r'(\d{4})(?:[-/]|$)', declared_date)
    year = int(year_match[1]) if year_match else (int(declared_year) if re.fullmatch(r'\d{4}', declared_year) else None)
    host = urlsplit(url).hostname
    authors = [{**item, 'name': _author_name(item['name'])} for item in fields['authors']]
    publication = {
        'venue': OFFICIAL_HOSTS[host], 'journal': fields['journal_ref'],
        'year': year, 'date': declared_date,
        'date_precision': 'day' if re.fullmatch(r'\d{4}[-/]\d{2}[-/]\d{2}', declared_date) else 'year' if re.fullmatch(r'\d{4}', declared_date) else 'source-declared',
        'url': url, 'pdf_url': pdf_url, 'status': 'verified-on-official-site',
        'authors': [item['name'] for item in authors], 'doi': fields['doi'],
        'checked_at': utcnow(),
    }
    bibliography = {
        **fields, 'authors': authors, 'publisher': OFFICIAL_HOSTS[host],
        'publication_status': 'published', 'original_version': 'published',
        'publication_record': publication, 'publication_date': declared_date,
        'doi_status': 'verified-on-official-site' if fields['doi'] else 'not-provided-on-official-site',
        'publication_doi': fields['doi'],
        'publication_doi_status': 'verified-on-official-site' if fields['doi'] else 'not-provided-on-official-site',
        'metadata_status': 'verified', 'method': 'official-publication-page',
        'metadata_source_url': url, 'original_url': pdf_url, 'checked_at': utcnow(),
        'field_sources': {name: {'method': 'official-publication-page', 'source_url': url}
                         for name in ['title', 'authors', 'publisher', 'journal_ref', 'first_published', 'publication_date',
                                      'publication_record', 'doi', 'publication_doi', 'publication_status', 'original_version']},
    }
    return {'bibliography': bibliography, 'pdf_url': pdf_url, 'metadata_html': data}


def capture_published_original(url, sid, fetcher=None):
    """Collector hook: archive full PDF plus metadata; unsupported pages return None.

    Raises on failures so callers can record retrieval failure instead of labeling
    a landing-page abstract as a complete paper. Uses the collector's guarded fetch.
    """
    publication = resolve_publication(url, fetcher)
    if publication is None:
        return None
    from .collector import _asset, _pdf_text, _validate_pdf, fetch_bytes
    fetcher = fetcher or fetch_bytes
    pdf = _validate_pdf(fetcher(publication['pdf_url']))
    text = _pdf_text(pdf)
    if len(text.strip()) < 500:
        raise ValueError('正式论文 PDF 没有足够的可提取全文')
    name = _asset(sid, '-published.pdf', pdf)
    metadata = ROOT / 'source_metadata' / f'{sid}-publication.html'
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_bytes(publication['metadata_html'])
    bibliography = publication['bibliography']
    bibliography['metadata_snapshot'] = str(metadata.relative_to(ROOT))
    return {
        'asset_path': name, 'pdf_path': name, 'pdf_url': publication['pdf_url'],
        'original_url': publication['pdf_url'], 'content_format': 'pdf',
        'original_title': bibliography['title'], 'text': text, 'text_format': 'pdf',
        'text_original_url': publication['pdf_url'], 'content_status': 'full-text',
        'body_level': 'original-extracted', 'bibliography': bibliography,
    }


def merge_published_version(record, official_original):
    """Retain the original preprint asset, identity and chronology when upgrading."""
    result = copy.deepcopy(record)
    previous = copy.deepcopy(record.get('bibliography', {}))
    archived = list(record.get('archived_versions', []))
    if record.get('pdf_path') and record.get('pdf_path') != official_original['pdf_path']:
        version = {
            'version': previous.get('original_version', 'arxiv-preprint' if previous.get('arxiv_id') else 'previous'),
            'pdf_path': record['pdf_path'], 'pdf_url': record.get('pdf_url', ''),
            'original_url': record.get('original_url', ''),
            'arxiv_id': previous.get('arxiv_id', ''), 'arxiv_version': previous.get('arxiv_version'),
            'bibliography': previous, 'same_work': True,
        }
        if not any(item.get('pdf_path') == version['pdf_path'] for item in archived):
            archived.append(version)
    result.update(copy.deepcopy(official_original))
    result['archived_versions'] = archived
    bibliography = {**previous, **result['bibliography']}
    bibliography['field_sources'] = {**previous.get('field_sources', {}), **result['bibliography'].get('field_sources', {})}
    if previous.get('arxiv_id'):
        bibliography['first_published'] = previous.get('first_published', '')
        bibliography['updated'] = previous.get('updated', '')
        bibliography['preprint_url'] = record.get('url', '')
        bibliography['preprint_pdf_path'] = record.get('pdf_path', '') if previous.get('publication_status') != 'published' else previous.get('preprint_pdf_path', '')
        bibliography['same_work_key'] = 'arxiv:' + previous['arxiv_id']
        for name in ('first_published', 'updated'):
            if previous.get('field_sources', {}).get(name):
                bibliography['field_sources'][name] = previous['field_sources'][name]
    result['bibliography'] = bibliography
    result['selection'] = {
        **record.get('selection', {}), 'status': '正式发表参考',
        'reason': '已从正式出版页面核实作者、出版信息与公开全文；记录同一作品的版本关系，不把版本数量作为独立研究数量。',
        'limitations': '正式发表与元数据核实不等于实验结论已复现；Wiki 的具体断言仍需逐条核对原文与适用条件。',
        'checked_at': utcnow(),
    }
    return result
