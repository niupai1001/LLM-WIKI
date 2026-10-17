"""Normalize and render supplied publication facts, without querying or inferring.

Additional facts live in ``bibliography.publication_record``. Rankings contain
``system, rank, year, source_url, checked_at, status='verified'``. A journal's
``impact_factor`` contains ``value, year, source_url, checked_at, status='verified'``.
The metric year belongs to the metric, not necessarily to the paper's year.
Existing publication/DOI/version fields are never rewritten by these helpers.
"""
import html
import math
import re
from datetime import datetime
from urllib.parse import urlsplit


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _year(value):
    value = str(value) if isinstance(value, int) and not isinstance(value, bool) else _text(value)
    return int(value) if re.fullmatch(r'[1-9]\d{3}', value) else None


def _url(value):
    value = _text(value)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {'http', 'https'} and parsed.hostname
                and not parsed.hostname.endswith('.invalid')
                and not parsed.username and not parsed.password):
            return value
    except ValueError:
        pass
    return ''


def _checked(value):
    value = _text(value)
    if not value:
        return ''
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    except ValueError:
        return ''


def _normalize(record):
    bibliography = _dict(_dict(record).get('bibliography'))
    publication = _dict(bibliography.get('publication_record'))
    published = publication.get('status') in {'verified', 'verified-on-official-site', 'verified-via-crossref'}
    venue = _text(publication.get('venue'))
    venue_type = publication.get('venue_type')
    venue_type = venue_type if venue_type in {'journal', 'conference'} else ''
    year = _year(publication.get('year'))
    if year is None:
        date = _text(publication.get('date'))
        year = _year(date)
        if year is None:
            try:
                year = datetime.fromisoformat(date.replace('Z', '+00:00')).year
            except ValueError:
                pass
    normalized = {'published': published, 'venue': venue if published else '',
                  'venue_type': venue_type if published else '', 'year': year if published else None,
                  'rankings': [], 'impact_factor': None}
    issues = []
    rankings = publication.get('rankings', [])
    if not isinstance(rankings, list):
        issues.append('rankings must be a list')
        rankings = []
    for position, raw in enumerate(rankings):
        item = _dict(raw)
        rank = {'system': _text(item.get('system')), 'rank': _text(item.get('rank')),
                'year': _year(item.get('year')), 'source_url': _url(item.get('source_url')),
                'checked_at': _checked(item.get('checked_at')), 'status': 'verified'}
        if item.get('status') != 'verified' or not all(rank.values()):
            issues.append(f'rankings[{position}] lacks verified system, rank, year or provenance')
        elif not published or not venue:
            issues.append(f'rankings[{position}] requires a verified publication venue')
        elif rank not in normalized['rankings']:
            normalized['rankings'].append(rank)
    raw_factor = publication.get('impact_factor')
    if raw_factor is not None:
        item = _dict(raw_factor)
        raw_value = item.get('value')
        try:
            value = float(raw_value) if not isinstance(raw_value, bool) else float('nan')
        except (ValueError, TypeError, OverflowError):
            value = float('nan')
        factor = {'value': value, 'year': _year(item.get('year')),
                  'source_url': _url(item.get('source_url')),
                  'checked_at': _checked(item.get('checked_at')), 'status': 'verified'}
        if venue_type != 'journal':
            issues.append('impact_factor requires an explicit journal venue_type')
        elif (item.get('status') != 'verified' or not math.isfinite(value) or value < 0
              or not all(factor[key] for key in ('year', 'source_url', 'checked_at'))):
            issues.append('impact_factor lacks a verified nonnegative value, metric year or provenance')
        elif not published or not venue:
            issues.append('impact_factor requires a verified publication venue')
        else:
            normalized['impact_factor'] = factor
    return normalized, issues


def normalize_publication(record):
    """Return only usable, explicitly verified facts; leave the input untouched."""
    return _normalize(record)[0]


def validate_publication_metrics(record):
    """Describe supplied metric problems; missing optional metrics are valid."""
    return _normalize(record)[1]


def _escape(value):
    return html.escape(str(value), quote=True)


def _reference(item):
    return (f'<a href="{_escape(item["source_url"])}" target="_blank" rel="noopener noreferrer">核对来源 ↗</a>'
            + ' · 核对 ' + _escape(item['checked_at']))


def publication_fields(record):
    """Return (label, escaped HTML value) pairs for the source bibliography."""
    publication = normalize_publication(record)
    if not publication['published'] or not publication['venue']:
        return []
    venue_type = {'journal': '期刊', 'conference': '会议'}.get(publication['venue_type'], '发表载体')
    venue = _escape(publication['venue'])
    if publication['year']:
        venue += ' · ' + str(publication['year'])
    fields = [(venue_type, venue)]
    for rank in publication['rankings']:
        value = (_escape(rank['system']) + ' ' + _escape(rank['rank'])
                 + f'（{rank["year"]} 年目录） · ' + _reference(rank))
        fields.append(('期刊 / 会议级别', value))
    factor = publication['impact_factor']
    if factor:
        fields.append((f'期刊影响因子（JIF，{factor["year"]}）',
                       f'{factor["value"]:g} · ' + _reference(factor)))
    return fields


def publication_summary_html(publication):
    """Render a normalized publication in a citation's compact metadata line."""
    if not publication.get('published') or not publication.get('venue'):
        return ''
    venue = _escape(publication['venue'])
    if publication.get('year'):
        venue += ' · ' + str(publication['year'])
    parts = ['<span>' + venue + '</span>']
    for rank in publication.get('rankings', []):
        label = _escape(rank['system']) + ' ' + _escape(rank['rank']) + f'（{rank["year"]}）'
        parts.append(f'<span><a href="{_escape(rank["source_url"])}" target="_blank" rel="noopener noreferrer">{label}</a></span>')
    factor = publication.get('impact_factor')
    if factor:
        parts.append(f'<span><a href="{_escape(factor["source_url"])}" target="_blank" rel="noopener noreferrer">JIF {factor["value"]:g}（{factor["year"]}）</a></span>')
    return ''.join(parts)
