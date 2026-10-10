"""Source-declared bibliographic facts and their provenance, never inferred by an LLM."""
import copy
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlencode
from xml.etree import ElementTree as ET

from .common import utcnow

ARXIV_API = 'https://export.arxiv.org/api/query'
_NS = {'a': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
_ARXIV = re.compile(r'/(?:abs|pdf|html)/((?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7}))(?:v(\d+))?(?:\.pdf)?(?:/|$)', re.I)


def arxiv_identifier(url):
    try:
        parsed = urlsplit(url)
        if parsed.hostname not in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'}:
            return None
        match = _ARXIV.search(parsed.path)
        return {'id': match[1], 'version': int(match[2]) if match[2] else None} if match else None
    except (ValueError, TypeError):
        return None


def clean_text(value):
    return re.sub(r'\s+', ' ', value).strip() if isinstance(value, str) else ''


def normalize_doi(value):
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/|^doi:\s*', '', clean_text(value), flags=re.I)
    return value if re.fullmatch(r'10\.\d{4,9}/[^\s<>"\']+', value) else ''


def query_url(ids):
    ids = sorted(set(ids))
    if not ids or any(not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?', value, re.I) for value in ids):
        raise ValueError('Invalid arXiv metadata identifiers')
    return ARXIV_API + '?' + urlencode({'id_list': ','.join(ids), 'max_results': len(ids)})


def parse_arxiv_atom(data, source_url):
    root = ET.fromstring(data)
    result = {}
    for entry in root.findall('a:entry', _NS):
        identity = arxiv_identifier(entry.findtext('a:id', '', _NS))
        if not identity:
            continue
        text = lambda name: clean_text(entry.findtext(name, '', _NS))
        authors = []
        for author in entry.findall('a:author', _NS):
            name = clean_text(author.findtext('a:name', '', _NS))
            if name:
                affiliations = [clean_text(node.text) for node in author.findall('arxiv:affiliation', _NS) if clean_text(node.text)]
                authors.append({'name': name, 'affiliations': affiliations})
        doi = normalize_doi(text('arxiv:doi'))
        fields = {'title': text('a:title'), 'authors': authors, 'arxiv_id': identity['id'], 'arxiv_version': identity['version'],
                  'first_published': text('a:published'), 'updated': text('a:updated'), 'doi': doi,
                  'doi_status': 'source-provided' if doi else 'not-provided', 'journal_ref': text('arxiv:journal_ref'),
                  'publisher': 'arXiv', 'publication_status': 'arxiv-preprint', 'metadata_status': 'verified',
                  'method': 'arxiv-api', 'metadata_source_url': source_url, 'checked_at': utcnow(),
                  'arxiv_categories': [node.get('term') for node in entry.findall('a:category', _NS) if node.get('term')]}
        if doi.lower().startswith('10.48550/arxiv.'):
            fields.update(arxiv_doi=doi, arxiv_doi_status='source-provided', arxiv_doi_source_url=source_url, doi='', doi_status='not-provided')
        fields.update(publication_doi=fields['doi'], publication_doi_status=fields['doi_status'])
        fields['field_sources'] = {name: {'method': 'arxiv-api', 'source_url': source_url} for name in ['title', 'authors', 'first_published', 'updated', 'arxiv_id', 'arxiv_version', 'doi', 'journal_ref']}
        result[identity['id']] = fields
    return result


class HeadMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('name') or attrs.get('property')
            value = attrs.get('content')
            if key and value:
                self.values.setdefault(key.casefold(), []).append(clean_text(value))
        elif tag == 'a' and attrs.get('id') == 'arxiv-doi-link':
            doi = normalize_doi(attrs.get('href', ''))
            if doi:
                self.values.setdefault('arxiv-issued-doi', []).append(doi)


def html_metadata(data):
    parser = HeadMetadata()
    parser.feed(data.decode('utf-8', errors='replace') if isinstance(data, bytes) else data)
    values = parser.values
    first = lambda *keys: next((values[k][0] for k in keys if values.get(k)), '')
    authors = [{'name': name, 'affiliations': []} for name in values.get('citation_author', values.get('dc.creator', values.get('author', [])))]
    return {'authors': authors, 'publisher': first('citation_publisher', 'dc.publisher', 'og:site_name'),
            'doi': normalize_doi(first('citation_doi', 'dc.identifier')), 'first_published': first('citation_date', 'citation_publication_date', 'article:published_time', 'dc.date'),
            'updated': first('article:modified_time', 'citation_online_date'), 'journal_ref': first('citation_journal_title'),
            'title': first('citation_title', 'og:title', 'dc.title')}


def arxiv_doi_metadata(data, source_url):
    parser = HeadMetadata()
    parser.feed(data.decode('utf-8', errors='replace') if isinstance(data, bytes) else data)
    values = parser.values.get('arxiv-issued-doi', [])
    doi = values[0] if values and values[0].lower().startswith('10.48550/arxiv.') else ''
    return {'arxiv_doi': doi, 'arxiv_doi_status': 'verified-on-arxiv' if doi else 'not-provided-on-page',
            'arxiv_doi_source_url': source_url, 'arxiv_doi_checked_at': utcnow()}


def html_affiliations(data):
    """Use explicit author/affiliation elements; do not guess from body mentions."""
    from lxml import html
    try:
        page = html.fromstring(data)
    except (ValueError, TypeError):
        return {}, []
    by_author, all_affiliations = {}, []
    css_class = lambda name: 'contains(concat(" ", normalize-space(@class), " "), " ' + name + ' ")'
    for creator in page.xpath('//*[' + css_class('ltx_role_author') + ']'):
        names = creator.xpath('.//*[' + css_class('ltx_personname') + ']')
        affiliations = []
        for node in creator.xpath('.//*[' + css_class('ltx_role_affiliation') + ']'):
            value = re.sub(r'^Affiliation:\s*', '', clean_text(node.text_content()), flags=re.I)
            if value and value not in affiliations:
                affiliations.append(value)
            if value and value not in all_affiliations:
                all_affiliations.append(value)
        if len(names) == 1:
            name = clean_text(names[0].text_content())
            if name and affiliations:
                by_author[name] = affiliations
    return by_author, all_affiliations


def build_bibliography(record, authoritative=None, archived_html=None):
    previous = record.get('bibliography')
    result = copy.deepcopy(previous) if isinstance(previous, dict) else {}
    identity = arxiv_identifier(record.get('url', ''))
    defaults = {'authors': [], 'affiliations': [], 'publisher': 'arXiv' if identity else '', 'original_url': record.get('url', ''),
                'arxiv_id': identity['id'] if identity else '', 'arxiv_version': identity['version'] if identity else None,
                'doi': '', 'doi_status': 'unverified', 'publication_doi': '', 'publication_doi_status': 'unverified', 'arxiv_doi': '', 'arxiv_doi_status': 'unverified', 'first_published': '', 'updated': '', 'captured_at': record.get('captured_at', ''),
                'source_type': record.get('kind', 'document'), 'publication_status': 'arxiv-preprint' if identity else 'unverified',
                'journal_ref': '', 'metadata_status': 'unverified', 'method': 'record-only', 'metadata_source_url': '', 'checked_at': '',
                'acquisition_method': 'local-file' if record.get('body_level') == 'manual-original' else 'public-download',
                'text_acquisition_method': record.get('text_format') or record.get('content_format', 'unknown'),
                'content_status': record.get('content_status', 'missing'), 'announced_at': record.get('published', ''), 'field_sources': {}}
    for key, value in defaults.items():
        result.setdefault(key, value)
    if archived_html:
        declared = html_metadata(archived_html)
        html_url = record.get('text_original_url') or record.get('original_url') or record.get('url', '')
        for key, value in declared.items():
            if value and not result.get(key):
                result[key] = value
                result['field_sources'][key] = {'method': 'archived-html-head', 'source_url': html_url}
        by_author, affiliations = html_affiliations(archived_html)
        result['affiliations'] = list(dict.fromkeys(result.get('affiliations', []) + affiliations))
        if affiliations:
            result['field_sources']['affiliations'] = {'method': 'archived-html-author-affiliations', 'source_url': html_url}
        if result['metadata_status'] != 'verified' and (any(declared.values()) or affiliations):
            result.update(metadata_status='partial', method='archived-html', metadata_source_url=html_url, checked_at=utcnow())
    else:
        by_author = {}
    if authoritative:
        field_sources = {**result.get("field_sources", {}), **authoritative.get("field_sources", {})}
        result.update(copy.deepcopy(authoritative))
        result["field_sources"] = field_sources
    for author in result.get('authors', []):
        if isinstance(author, dict) and author.get('name') in by_author and not author.get('affiliations'):
            author['affiliations'] = by_author[author['name']]
    result['affiliations'] = list(dict.fromkeys(result.get('affiliations', []) + [aff for author in result.get('authors', []) if isinstance(author, dict) for aff in author.get('affiliations', [])]))
    if result.get('doi') and result['doi_status'] == 'unverified':
        result['doi_status'] = 'source-provided'
    # Operational fields are always current; bibliographic dates never use capture/RSS dates.
    result.update(captured_at=record.get('captured_at', ''), content_status=record.get('content_status', 'missing'), source_type=record.get('kind', 'document'))
    return result
