"""Version-bound, claim-level reviews grounded in archived original passages.

Quote existence is checked mechanically; entailment is the reviewer's recorded
judgment. Editorial approval and publication venue never certify a claim.
"""
import fcntl
import hashlib
import html
import json
import re
import threading
from contextlib import contextmanager
from urllib.parse import quote

from .common import ROOT, WIKI, META, RAW, ASSETS, save_json, utcnow

REVIEWS = ROOT / 'claim_reviews'
_LOCK = threading.RLock()
_SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?')
VERDICTS = {'supported': '原文支持', 'partial': '部分支持', 'contradicted': '与原文不符', 'unverifiable': '无法核查'}


class ReviewConflict(ValueError):
    pass


def _read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _list(value):
    return value if isinstance(value, list) else []


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _quote_text(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _claim_version(claim):
    return _digest({k: claim.get(k) for k in ('id', 'text', 'scope', 'source_ids')})


def _asset_version(source):
    name = source.get('pdf_path') or source.get('asset_path')
    if not isinstance(name, str) or not name:
        return 'not-archived'
    path = (ASSETS / name).resolve()
    if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
        return 'missing'
    try:
        if path.stat().st_size > 15 * 1024 * 1024:
            return 'oversized'
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return 'unreadable'


def _source_version(source):
    if not source:
        return 'missing'
    b = source.get('bibliography') if isinstance(source.get('bibliography'), dict) else {}
    publication = b.get('publication_record') if isinstance(b.get('publication_record'), dict) else {}
    return _digest({'text': source.get('text'), 'status': source.get('content_status'),
                    'url': source.get('url'), 'asset_path': source.get('asset_path'), 'pdf_path': source.get('pdf_path'),
                    'asset_version': _asset_version(source), 'pdf_url': source.get('pdf_url'),
                    'version': b.get('original_version'), 'doi': b.get('doi'), 'arxiv_id': b.get('arxiv_id'),
                    'arxiv_version': b.get('arxiv_version'), 'title': b.get('title'), 'authors': b.get('authors'),
                    'publication_date': b.get('publication_date'), 'publication_url': publication.get('url')})


def _page(slug):
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug == 'index':
        raise ValueError('Wiki 页面标识无效')
    path = WIKI / (slug + '.md')
    if not path.is_file():
        raise ValueError('Wiki 页面不存在')
    body = path.read_text(encoding='utf-8')
    return _read(META / (slug + '.json')), body, hashlib.sha256(body.encode()).hexdigest()


def _claim_sources(claim):
    return [sid for sid in _list(claim.get('source_ids')) if isinstance(sid, str) and re.fullmatch('[a-f0-9]{16}', sid)]


def snapshot(slug):
    meta, body, version = _page(slug)
    claims = [c for c in _list(meta.get('claims')) if isinstance(c, dict)]
    reviews = _read(REVIEWS / (slug + '.json')).get('reviews', [])
    if not isinstance(reviews, list):
        reviews = []
    result = []
    counts = dict(checked=0, human=0, stale=0, supported=0, partial=0, contradicted=0, unverifiable=0)
    for claim in claims:
        sources = {}
        for sid in _claim_sources(claim):
            source = _read(RAW / (sid + '.json'))
            sources[sid] = {'id': sid, 'title': source.get('title') or sid, 'version': _source_version(source),
                            'full_text': source.get('content_status') == 'full-text', '_record': source}
        history = [r for r in reviews if isinstance(r, dict) and r.get('claim_id') == claim.get('id')]
        latest = history[-1] if history else None
        valid, reason = False, ''
        if latest:
            sid = latest.get('source_id')
            source = sources.get(sid)
            valid = (latest.get('page_version') == version and latest.get('claim_version') == _claim_version(claim)
                     and source is not None and latest.get('source_version') == source['version']
                     and latest.get('verdict') in VERDICTS and latest.get('reviewer_type') in {'human', 'agent'})
            if valid and latest.get('verdict') != 'unverifiable':
                valid = (source['full_text'] and bool(_quote_text(latest.get('quote')))
                         and _quote_text(latest['quote']) in _quote_text(source['_record'].get('text', '')))
            if not valid:
                reason = '正文、断言或来源已变更，需重新核查'
                counts['stale'] += 1
            else:
                counts['checked'] += 1
                counts[latest['verdict']] += 1
                counts['human'] += latest['reviewer_type'] == 'human'
        for source in sources.values():
            source.pop('_record')
        result.append({'claim': claim, 'claim_version': _claim_version(claim), 'sources': sources,
                       'review': latest, 'history': history[:-1], 'valid': bool(valid), 'stale_reason': reason})
    registered = {sid for sid in _list(meta.get('source_ids')) if isinstance(sid, str)}
    cited = set(re.findall(r'/source/([a-f0-9]{16})', body))
    available = sum(bool(_read(RAW / (sid + '.json'))) for sid in cited)
    return {'slug': slug, 'title': meta.get('title') or slug, 'page_kind': meta.get('page_kind', 'concept'),
            'page_version': version, 'claims': result, 'counts': counts,
            'citation_count': len(cited), 'citation_available': available, 'unregistered_citations': sorted(cited - registered),
            'related_slugs': _list(meta.get('related_slugs'))}


@contextmanager
def _locked():
    REVIEWS.mkdir(parents=True, exist_ok=True)
    with _LOCK, (REVIEWS / '.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def save_review(slug, payload, reviewer_type='human'):
    if not isinstance(payload, dict) or reviewer_type not in {'human', 'agent'}:
        raise ValueError('核查记录格式无效')
    with _locked():
        state = snapshot(slug)
        if payload.get('page_version') != state['page_version']:
            raise ReviewConflict('页面已更新，请刷新后重新核查')
        row = next((r for r in state['claims'] if r['claim'].get('id') == payload.get('claim_id') and isinstance(payload.get('claim_id'), str)), None)
        if row is None:
            raise ValueError('断言不存在，请刷新页面')
        if payload.get('claim_version') != row['claim_version']:
            raise ReviewConflict('断言已更新，请刷新后重新核查')
        sid = payload.get('source_id')
        source = row['sources'].get(sid) if isinstance(sid, str) else None
        if source is None:
            raise ValueError('请选择这条断言所引用的来源')
        if payload.get('source_version') != source['version']:
            raise ReviewConflict('来源版本已更新，请刷新后重新核查')
        verdict = payload.get('verdict')
        if not isinstance(verdict, str) or verdict not in VERDICTS:
            raise ValueError('核查结论无效')
        values = {}
        for key, label, limit in (('by', '核查者', 100), ('locator', '原文位置', 300), ('quote', '原文摘录', 3000), ('rationale', '判断理由', 2000)):
            value = payload.get(key, '')
            if not isinstance(value, str) or len(value.strip()) > limit:
                raise ValueError(f'{label}格式无效，最多 {limit} 字')
            values[key] = value.strip()
        if not values['by'] or not values['rationale']:
            raise ValueError('请填写核查者和判断理由')
        if verdict != 'unverifiable':
            if not source['full_text'] or not values['locator'] or len(_quote_text(values['quote'])) < 20:
                raise ValueError('请提供可核对全文、原文位置和至少 20 字符的原文摘录')
            text = _read(RAW / (sid + '.json')).get('text', '')
            if _quote_text(values['quote']) not in _quote_text(text):
                raise ValueError('摘录未匹配归档原文，请直接复制原文，勿使用译文或改写')
        # Recheck after reading evidence so concurrent page replacement cannot certify another body.
        fresh = snapshot(slug)
        fresh_row = next((r for r in fresh['claims'] if r['claim'].get('id') == payload['claim_id']), None)
        if (fresh['page_version'] != state['page_version'] or not fresh_row
                or fresh_row['claim_version'] != row['claim_version']
                or fresh_row['sources'].get(sid, {}).get('version') != source['version']):
            raise ReviewConflict('页面或来源已更新，请刷新后重新核查')
        record = dict(values, claim_id=payload['claim_id'], source_id=sid, verdict=verdict,
                      page_version=state['page_version'], claim_version=row['claim_version'],
                      source_version=source['version'], reviewer_type=reviewer_type, at=utcnow())
        path = REVIEWS / (slug + '.json')
        history = _read(path).get('reviews', [])
        history = history if isinstance(history, list) else []
        save_json(path, {'slug': slug, 'reviews': (history + [record])[-200:]})
        return {'message': '核查记录已保存', 'url': '/page/' + quote(slug) + '#fact-check-' + quote(payload['claim_id'])}


def render(slug, editable=True):
    state = snapshot(slug)
    E = lambda v: html.escape(str(v), quote=True)
    counts = state['counts']
    body = '<section class="panel fact-checks" id="fact-check"><h2>逐项事实核查</h2>'
    if state['page_kind'] == 'topic' and not state['claims']:
        body += '<p class="muted">主题导航页 · 查看概念条目的断言核查</p><div class="fact-related">'
        for related in state['related_slugs']:
            if isinstance(related, str) and _SLUG.fullmatch(related) and (WIKI / (related + '.md')).is_file():
                title = _read(META / (related + '.json')).get('title') or related
                body += f'<a class="button" href="/page/{E(related)}#fact-check">{E(title)} →</a>'
        return body + '</div></section>'
    body += f'<div class="fact-overview"><span>{counts["checked"]}/{len(state["claims"])} 条有当前版本核查记录</span><span>人工复核 {counts["human"]} 条</span><span>部分支持 {counts["partial"]} 条</span><span>与原文不符 {counts["contradicted"]} 条</span></div>'
    body += f'<p class="muted">来源记录可用 {state["citation_available"]}/{state["citation_count"]} · 核查范围：归档原文一致性 · 独立复现未登记</p>'
    if state['unregistered_citations']:
        body += '<p class="notice warning">正文存在未登记的引用，请补齐来源记录。</p>'
    if not state['claims']:
        body += '<p>尚未登记核心断言，当前没有逐项事实核查结果。</p>'
    for row in state['claims']:
        claim, review = row['claim'], row['review'] or {}
        cid = claim.get('id')
        if not isinstance(cid, str) or not cid:
            body += '<p class="notice warning">断言缺少标识，需补齐记录后核查。</p>'
            continue
        verdict = VERDICTS.get(review.get('verdict'), '待核查') if row['valid'] else '需重新核查' if review else '待核查'
        status_class = 'complete' if row['valid'] and review.get('verdict') == 'supported' else 'warning'
        body += f'<article class="fact-claim" id="fact-check-{E(cid)}"><div class="fact-claim-heading"><h3>{E(claim.get("text", ""))}</h3><span class="tag {status_class}">{E(verdict)}</span></div><p class="fact-scope">适用范围：{E(claim.get("scope") or "未记录")}</p>'
        if review:
            who = '人工复核' if review.get('reviewer_type') == 'human' else '代理逐项核对'
            body += f'<p class="fact-review-meta">{who} · {E(review.get("by", ""))} · {E(review.get("at", "").replace("T", " ").replace("+00:00", " UTC"))}</p>'
            if row['stale_reason']:
                body += f'<p class="notice warning">{E(row["stale_reason"])}</p>'
            sid = review.get('source_id', '')
            title = row['sources'].get(sid, {}).get('title', sid)
            body += f'<p><a href="/source/{E(sid)}">{E(title)} ↗</a> · {E(review.get("locator", "未记录位置"))}</p>'
            if review.get('quote'):
                body += f'<details class="fact-quote"><summary>原文摘录</summary><blockquote>{E(review["quote"])}</blockquote></details>'
            body += f'<p class="fact-rationale"><strong>判断理由：</strong>{E(review.get("rationale", ""))}</p>'
            if row['history']:
                body += f'<details class="fact-quote"><summary>历史核查 · {len(row["history"])} 条</summary>'
                for previous in reversed(row['history']):
                    who = '人工复核' if previous.get('reviewer_type') == 'human' else '代理逐项核对'
                    body += f'<article><p><strong>{E(VERDICTS.get(previous.get("verdict"), "未记录"))}</strong> · {E(who)} · {E(previous.get("by", ""))} · {E(previous.get("at", ""))}</p><p>{E(previous.get("locator", ""))}</p><p>{E(previous.get("rationale", ""))}</p>'
                    if previous.get('quote'):
                        body += f'<blockquote>{E(previous["quote"])}</blockquote>'
                    body += '</article>'
                body += '</details>'
        else:
            body += '<p class="muted">未登记逐项对照记录</p>'
            for passage in _list(claim.get('evidence_passages')):
                if isinstance(passage, dict):
                    body += f'<details class="fact-quote"><summary>模型提议摘录 · 待核对</summary><p>{E(passage.get("locator", ""))} · <a href="/source/{E(passage.get("source_id", ""))}">查看来源 ↗</a></p><blockquote>{E(passage.get("quote", ""))}</blockquote></details>'
        options = ''.join(f'<option value="{E(sid)}" data-source-version="{E(source["version"])}">{E(source["title"])}</option>' for sid, source in row['sources'].items())
        if options and editable:
            statuses = ''.join(f'<option value="{E(value)}">{E(label)}</option>' for value, label in VERDICTS.items())
            body += f'''<details class="fact-review-edit"><summary>记录人工复核</summary><form data-claim-review="{E(slug)}"><input type="hidden" name="page_version" value="{E(state['page_version'])}"><input type="hidden" name="claim_version" value="{E(row['claim_version'])}"><input type="hidden" name="claim_id" value="{E(cid)}"><label>核查来源<select name="source_id">{options}</select></label><label>核查结论<select name="verdict">{statuses}</select></label><label>原文位置<input name="locator" maxlength="300" placeholder="例如：第 3 节，表 2，PDF 第 6 页"></label><label>原文摘录<textarea name="quote" rows="4" maxlength="3000" placeholder="直接复制原文，系统会核对是否存在于归档文字"></textarea></label><label>判断理由<textarea name="rationale" rows="3" maxlength="2000" required placeholder="说明原文如何支持、限制或反驳这条断言"></textarea></label><label>核查者<input name="by" maxlength="100" value="本地用户" required></label><button type="submit" class="button primary">保存核查</button><p class="fact-form-error" role="status"></p></form></details>'''
        body += '</article>'
    return body + '</section>'
