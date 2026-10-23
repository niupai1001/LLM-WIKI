"""Local LLM Wiki workspace: originals, topic compilation, review and knowledge graph."""
import base64
import binascii
import html
import json
import os
import re
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from difflib import unified_diff
from functools import wraps
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .common import ASSETS, PENDING, RAW, WIKI, connect, load_json, safe_slug
from .citations import prepare_citations, evidence_html, evidence_jump, _external_url, _date
from .knowledge import categories, category_name, graph_data, wiki_pages, validate_category, default_category, semantic_backlinks, topic_details
from .wiki import approve, prepare, prepare_rewrite, reject, proposal_key, page_body, compile_and_publish, automatic_publish

app = FastAPI(title='LLM Wiki')
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')
from .v2web import router as v2_router
app.include_router(v2_router)
ASSET_VERSIONS = {name: str((Path(__file__).parent / "static" / name).stat().st_mtime_ns) for name in ("app.css", "app.js", "graph.js", "fact-checks.css", "fact-checks.js")}
MAX_UPLOAD = 15 * 1024 * 1024
E = lambda value: html.escape(str(value), quote=True)


def publication_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError, AttributeError):
        return ''

STAGES = {'new': '待编写', 'compiling': '正在编写', 'drafted': '系统处理中', 'approved': '已入 Wiki'}
KINDS = {'paper': '论文', 'article': '文章', 'news': '资讯', 'note': '个人笔记', 'document': '文档'}


@app.middleware('http')
async def local_mutations(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'DELETE', 'PATCH'}:
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.netloc != request.headers.get('host'):
                return JSONResponse({'detail': '只允许本地同源操作。'}, status_code=403)
    if request.url.path.startswith('/static/'):
        return await call_next(request)
    from . import common
    from .workspaces import resolve_workspace, use_workspace
    try:
        identity = request.query_params.get('library_id') or request.headers.get('x-library-id')
        workspace = resolve_workspace(identity, root=Path(common.ROOT))
    except ValueError as exc:
        return JSONResponse({'detail':str(exc)},status_code=400)
    with use_workspace(workspace):
        from .publication import recover
        await run_in_threadpool(recover,workspace)
        response = await call_next(request)
        content_type=response.headers.get('content-type','')
        if 'text/html' in content_type:
            raw=b''.join([chunk async for chunk in response.body_iterator]).decode('utf-8')
            def pin_link(match):
                path=html.unescape(match[2])
                if path.startswith('/static/') or path.startswith('//') or re.search(r'[?&]library_id=',path): return match[0]
                return match[1]+'="'+E(workspace.url(path))+'"'
            raw=re.sub(r'(href|action|data-pdf-url)="(/[^"\n]*)"',pin_link,raw)
            headers=dict(response.headers);headers.pop('content-length',None)
            return HTMLResponse(raw,status_code=response.status_code,headers=headers)
        if 'application/json' in content_type:
            raw=b''.join([chunk async for chunk in response.body_iterator])
            data=json.loads(raw)
            def pinned(value):
                if isinstance(value,dict): return {k:(workspace.url(v) if k in {'url','page_url'} and isinstance(v,str) and v.startswith('/') and not re.search(r'[?&]library_id=',v) else pinned(v)) for k,v in value.items()}
                if isinstance(value,list): return [pinned(v) for v in value]
                return value
            headers=dict(response.headers);headers.pop('content-length',None)
            return JSONResponse(pinned(data),status_code=response.status_code,headers=headers)
        return response


def layout(title, body, active='wiki'):
    from .workspaces import current_workspace
    workspace=current_workspace()
    library_meta=f'<meta name="library-id" content="{E(workspace.library_id)}"><script src="/static/library.js"></script>' if workspace else ''
    if workspace:
        body=f'<div class="library-context"><a href="/libraries">知识库：{E(workspace.library_id)}</a> · <a href="/tasks">处理任务</a> · <a href="/ask">知识问答</a></div>'+body
    icons = {
        'wiki': '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h6M8 17h4"/>',
        'sources': '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
        'drafts': '<path d="m14 5 5 5M4 20l5-1L20 8a2 2 0 0 0-5-5L4 14Z"/><path d="M12 20h8"/>',
        'graph': '<circle cx="12" cy="5" r="3"/><circle cx="5" cy="18" r="3"/><circle cx="19" cy="18" r="3"/><path d="m10.5 7.5-4 8M13.5 7.5l4 8M8 18h8"/>',
    }
    nav = ''.join(f'<a class="nav-link {"active" if key == active else ""}" href="{url}" {"aria-current=page" if key == active else ""}><svg viewBox="0 0 24 24" aria-hidden="true">{icons[key]}</svg><span>{name}</span></a>' for key, url, name in [
        ('wiki', '/', '知识 Wiki'), ('sources', '/sources', '原始资料'), ('drafts', '/drafts', '编写记录'), ('graph', '/graph', '知识图谱')])
    topic_links = ''.join(f'<a class="topic-link" href="/?category={quote(c["id"])}"><span class="topic-dot"></span>{E(c["name"])}</a>' for c in categories() if c['enabled'])
    nav += f'<a class="nav-link {"active" if active == "collection" else ""}" href="/collection" {"aria-current=page" if active == "collection" else ""}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg><span>采集计划</span></a>'
    nav += f'<a class="nav-link {"active" if active == "settings" else ""}" href="/settings" {"aria-current=page" if active == "settings" else ""}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/></svg><span>主题与订阅</span></a>'
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">{library_meta}<meta name="viewport" content="width=device-width,initial-scale=1"><title>{E(title)} · LLM Wiki</title><script>try{{document.documentElement.dataset.theme=localStorage.getItem('wiki-theme')==='dark'?'dark':'light'}}catch(e){{}}</script><link rel="stylesheet" href="/static/app.css?v={ASSET_VERSIONS["app.css"]}"></head><body><a class="skip-link" href="#main">跳到内容</a><aside class="sidebar"><a class="brand" href="/"><span class="brand-mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 6 4 12 4-8 4 8 4-12"/></svg></span> LLM Wiki</a><a class="sidebar-import" href="/sources#import"><span>＋</span> 导入资料</a><div class="workspace-label">工作空间</div><nav aria-label="工作空间">{nav}</nav><div class="topic-nav"><div class="workspace-label">采集范围</div>{topic_links}</div><div class="sidebar-bottom"><span class="status-dot"></span> 本地工作空间</div></aside><div class="main-shell"><header class="topbar"><div class="topbar-path"><span>工作空间</span><span class="path-divider">/</span><strong>{E(title)}</strong></div><div class="topbar-actions"><span class="local-label"><span class="status-dot"></span>本地</span><button class="icon-button" id="theme-toggle" aria-label="切换深色模式" title="切换深浅色模式"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.8 13.1A9 9 0 0 1 10.9 3.2 9 9 0 1 0 20.8 13.1Z"/></svg></button></div></header><main id="main">{body}</main></div><div id="toast" role="status" aria-live="polite"></div><script src="/static/app.js?v={ASSET_VERSIONS["app.js"]}" defer></script></body></html>''')


def library_snapshot(fn):
    """Render related files under the same lock used by publication."""
    @wraps(fn)
    def render(*args,**kwargs):
        from .workspaces import current_workspace
        from .publication import publication_lock
        workspace=current_workspace()
        if workspace:
            with publication_lock(workspace): return fn(*args,**kwargs)
        return fn(*args,**kwargs)
    return render


def md_render(source, citation_source_ids=None, evidence_prefix="knowledge", topic=False):
    import markdown, bleach
    citations = []
    if citation_source_ids is not None:
        source, citations = prepare_citations(source, citation_source_ids)
    def wiki_link(match):
        slug, _, label = match[1].partition('|')
        slug = slug.strip()
        if safe_slug(slug) != slug or not (WIKI / (slug + '.md')).is_file():
            return E(label or slug)
        if not label:
            label = next((line[2:].strip() for line in (WIKI / (slug + '.md')).read_text(encoding='utf-8').splitlines()
                          if line.startswith('# ')), slug)
        return f'<a href="/page/{quote(slug)}">{E(label)}</a>'
    source = re.sub(r'\[\[([^\]\n]+)\]\]', wiki_link, source)
    raw = markdown.markdown(source, extensions=['tables', 'fenced_code'])
    allowed = ['p','h1','h2','h3','h4','h5','h6','strong','em','ul','ol','li','a','blockquote','code','pre','hr','table','thead','tbody','tr','th','td','br']
    def allowed_attribute(tag, name, value):
        return tag == 'a' and (name in {'href', 'title'} or (name == 'class' and value == 'source-citation') or (name == 'id' and bool(re.fullmatch(r'kp_[a-f0-9]{20}', value))))
    clean = bleach.clean(raw, tags=allowed, attributes=allowed_attribute, protocols=['http','https','mailto'], strip=True)
    if topic:
        references = (f'<details class="page-evidence-records"><summary>关联原文 · {len(citations)} 份</summary>'
                      + evidence_html(citations, evidence_prefix, label='关联原文') + '</details>') if citations else ''
        return clean + references
    return evidence_jump(citations, evidence_prefix) + clean + evidence_html(citations, evidence_prefix)


def category_options(selected='', all_option=False, active_only=False, retain_selected=False):
    entries = categories()
    if active_only and not retain_selected and not any(c['id'] == selected and c['enabled'] for c in entries):
        from .knowledge import default_category
        selected = default_category()
    result = '<option value="">全部主题</option>' if all_option else ''
    return result + ''.join(f'<option value="{E(c["id"])}" {"selected" if c["id"] == selected else ""} {"disabled" if active_only and not c["enabled"] else ""}>{E(c["name"])}{"（已停用）" if not c["enabled"] else ""}</option>' for c in entries if not active_only or c['enabled'] or (retain_selected and c['id'] == selected))


def original_label(record):
    status = record.get('content_status', 'summary-only')
    if status == 'full-text':
        return '原文已获取'
    if str(record.get('asset_path', '')).lower().endswith('.pdf'):
        return '原件已保存 · 需 OCR'
    return '仅有摘要 · 待补原文' if status == 'summary-only' else '原文待补充'


def source_record(sid):
    if not re.fullmatch('[a-f0-9]{16}', sid):
        raise HTTPException(404, '资料不存在')
    path = RAW / (sid + '.json')
    if not path.exists():
        raise HTTPException(404, '资料已清空或不存在')
    return load_json(path)


def empty(title, text, action=''):
    return f'<section class="empty-state"><div class="empty-symbol"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M5.6 18.4 18.4 5.6"/></svg></div><h2>{E(title)}</h2><p>{E(text)}</p>{action}</section>'


def knowledge_excerpt(body):
    body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    body = re.sub(r'^#{1,6} .*$', '', body, flags=re.M)
    body = re.sub(r'\[来源 [^\]]*\]\(/source/[^)]*\)', '', body)
    body = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', body)
    body = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', body)
    return re.sub(r'\s+', ' ', re.sub(r'[#*`\[\]]', '', body)).strip()[:180]


def topic_counts(details):
    parts = [f'{len(details["concepts"])} 个概念'] if details['concepts'] else []
    if details.get('knowledge_indexes'):
        parts.append(f'{len(details["knowledge_indexes"])} 个知识索引')
    if details['comparisons']:
        parts.append(f'{len(details["comparisons"])} 篇研究比较')
    parts.append(f'{len(details["source_ids"])} 份关联原文')
    return ' · '.join(parts)


@app.get('/', response_class=HTMLResponse)
@library_snapshot
def home(q: str = '', category: str = ''):
    with connect() as db:
        total = db.execute('SELECT count(*) FROM sources').fetchone()[0]
        originals = db.execute("SELECT count(*) FROM sources WHERE content_status='full-text'").fetchone()[0]
        source_categories = {r['id']: r['category'] for r in db.execute('SELECT id,category FROM sources').fetchall()}
    pages = wiki_pages()
    topic_stats = {p['slug']: topic_details(p, pages) for p in pages
                   if p.get('page_kind') == 'topic' and p.get('page_role') != 'source_collection'}
    knowledge_count = sum(p.get('page_role') != 'source_collection' for p in pages)
    visible = [p for p in pages if (not category or p.get('category') == category or
               any(source_categories.get(sid) == category for sid in topic_stats.get(p['slug'], p).get('source_ids', []))) and
               (not q or q.casefold() in p['body'].casefold())]
    drafts_count = len(list(PENDING.glob('*.json')))
    pending_pages = [load_json(p) for p in PENDING.glob('*.json')]
    pending_pages = [p for p in pending_pages if (not category or p.get('category') == category) and (not q or q.casefold() in (p['title'] + p['body_markdown']).casefold())]
    body = f'<div class="page-heading"><div><h1>知识 Wiki</h1></div><a class="button primary" href="/sources#import">＋ 导入资料</a></div>'
    body += f'<div class="metrics"><div><strong>{knowledge_count}</strong><span>知识页面</span></div><div><strong>{originals}</strong><span>可阅读原文</span></div><div><strong>{drafts_count}</strong><span>系统待处理</span></div><div><strong>{total-originals}</strong><span>待补原文</span></div></div>'
    body += f'<div class="section-heading"><h2>全部知识 <span class="count-label">{sum(p.get("page_role") != "source_collection" for p in visible)}</span></h2><button class="button" data-compile="" data-category-from="wiki-category">编写 Wiki</button></div><form class="filterbar"><input name="q" value="{E(q)}" placeholder="搜索知识页面…" aria-label="搜索知识页面"><select name="category" id="wiki-category" aria-label="知识主题">{category_options(category, True)}</select><button class="button">筛选</button></form>'
    def cards(group):
        result = '<div class="wiki-grid">'
        for p in group:
            title = p['title']
            card_title = title if len(title) <= 54 else title[:53].rstrip() + '…'
            count = topic_counts(topic_stats[p['slug']]) if p['slug'] in topic_stats else (
                f'{len(set(p.get("source_ids", [])))} 份' + ('收录原文' if p.get('page_role') == 'source_collection' else '引用来源'))
            excerpt_body = re.sub(r'<!--.*?-->', '', page_body(p['body']), flags=re.S).strip().split('\n\n', 1)[0] if p['slug'] in topic_stats else p['body']
            if p.get('page_role') == 'knowledge_index':
                excerpt_body = p.get('knowledge_topic', '')
            result += f'<a class="wiki-card" href="/page/{quote(p["slug"])}"><span class="tag">{E(category_name(p.get("category", "other")))}</span><h3 title="{E(title)}">{E(card_title)}</h3><p>{E(knowledge_excerpt(excerpt_body))}</p><div class="card-foot">{E(count)} <span>阅读 →</span></div></a>'
        return result + '</div>'

    collections = [p for p in visible if p.get('page_role') == 'source_collection']
    if visible:
        for kind, heading in [('topic', '主题入口'), ('concept', '概念知识'), ('paper', '论文解读'), ('comparison', '研究比较'), ('knowledge_index', '原始知识点索引')]:
            group = [p for p in visible if p.get('page_role') != 'source_collection' and
                     ('knowledge_index' if p.get('page_role') == 'knowledge_index' else
                      'comparison' if p.get('page_role') == 'comparison' else
                      'paper' if p.get('page_role') == 'paper' else
                      'topic' if p.get('page_kind') == 'topic' else 'concept') == kind]
            if not group:
                continue
            body += f'<div class="section-heading knowledge-group-heading"><h2>{heading} <span class="count-label">{len(group)}</span></h2></div>' + cards(group)
    elif (q or category) and not pending_pages:
        body += empty('没有匹配的知识页面', '调整关键词或主题，查看其他页面。')
    elif not pending_pages:
        body += empty('暂无知识页面', '导入论文、文章或笔记。选择主题，让 LLM 综合原文，生成可核查的知识草稿。', '<div class="empty-actions"><a class="button primary" href="/sources#import">＋ 导入第一份资料</a><a class="button" href="/graph">查看知识图谱</a></div>')
    if pending_pages:
        body += '<div class="section-heading"><h2>系统待处理记录</h2><a class="text-link" href="/drafts">查看记录 →</a></div><div class="wiki-grid">'
        for draft in pending_pages:
            body += f'<a class="wiki-card" href="/drafts#draft-{E(proposal_key(draft))}"><span class="tag warning">LLM 草稿</span><h3>{E(draft["title"])}</h3><p>{E(knowledge_excerpt(draft["body_markdown"]))}</p><div class="card-foot">{len(draft.get("source_ids",[]))} 份原始证据 <span>阅读全文 →</span></div></a>'
        body += '</div>'
    if collections:
        body += f'<details class="page-evidence-records"><summary>资料清单 <span class="count-label">{len(collections)}</span></summary>' + cards(collections) + '</details>'
    return layout('知识 Wiki', body)


@app.get('/page/{slug}', response_class=HTMLResponse)
@library_snapshot
def page(slug: str):
    if safe_slug(slug) != slug or slug == 'index':
        raise HTTPException(404)
    pages = wiki_pages()
    matches = [p for p in pages if p['slug'] == slug]
    if not matches:
        from .workspaces import current_workspace
        workspace=current_workspace()
        redirects=workspace.root/'redirects.json' if workspace else None
        if redirects and redirects.exists():
            target=load_json(redirects).get(slug)
            if isinstance(target,str) and safe_slug(target)==target and (WIKI/(target+'.md')).is_file():
                from fastapi.responses import RedirectResponse
                return RedirectResponse(workspace.url('/page/'+target),status_code=307)
        raise HTTPException(404)
    current = matches[0]
    from .common import META
    metadata = load_json(META / (slug + '.json')) if (META / (slug + '.json')).exists() else {}
    reader_only = metadata.get('presentation_policy') == 'wiki-synthesis-reader'
    backlinks = [p for p in pages if slug in p.get('related_slugs', []) or re.search(r'\[\[' + re.escape(slug) + r'(?:\||\]\])', p['body'])]
    page_kind = {'comparison': '研究比较', 'source_collection': '资料清单', 'paper': '论文解读', 'knowledge_index': '原始知识点索引'}.get(
        current.get('page_role'), "主题入口" if current.get("page_kind") == "topic" else "概念条目")
    is_topic = current.get('page_kind') == 'topic' and current.get('page_role') not in {'source_collection', 'paper'}
    details = topic_details(current, pages) if is_topic else None
    summary = topic_counts(details) if is_topic else f'{len(current.get("source_ids", []))} 份引用来源 · 已发布知识 · <a href="#fact-check">逐项事实核查 ↓</a>'
    if reader_only and not is_topic:
        summary = f'{len(current.get("source_ids", []))} 份研究来源 · 概念知识'
    is_knowledge_index = current.get('page_role') == 'knowledge_index'
    if is_knowledge_index:
        summary = f'{current.get("knowledge_point_count", 0)} 条原知识点 · {len(current.get("source_ids", []))} 份原始资料 · 按论文保留的查阅索引，综合解释请阅读概念页'
        linked_concept = next((p for p in pages if p['slug'] == current.get('candidate_concept_slug')
            and p.get('page_role') == 'knowledge' and p.get('page_kind') == 'concept'
            and current.get('knowledge_point_ids') and (
                set(p.get('knowledge_point_ids', [])) == set(current['knowledge_point_ids']) or
                p['slug'] in metadata.get('synthesized_concept_slugs', []) and
                bool(set(p.get('knowledge_point_ids', [])) & set(current['knowledge_point_ids'])))), None)
        if linked_concept:
            summary += f' · <a href="/page/{E(linked_concept["slug"])}">阅读概念解读 →</a>'
    reading_sources = details['source_ids'] if is_topic else current.get('source_ids', [])
    from .paper_policy import enabled
    from .workspaces import current_workspace
    second_pass = '' if enabled(current_workspace()) else '<label class="checkbox-label"><input type="checkbox" data-second-pass> 额外润色（+1 次调用）</label>'
    # The page title belongs to the document header even when the author starts at H2.
    # Remove only an opening Markdown H1 from this view to avoid a duplicate title.
    from .reading_presentation import display_body as reading_display_body
    presented_body = reading_display_body(current_workspace(), slug, current['body'])
    display_body = re.sub(r'\A\s*# [^\n]+(?:\n|$)', '', presented_body, count=1)
    if is_knowledge_index:
        overview, separator, points = display_body.partition('\n## ')
        rendered_body = md_render(overview, reading_sources, evidence_prefix='index-overview-' + slug)
        if separator:
            rendered_body += ('<details><summary>展开原始知识点与证据</summary>'
                              + md_render('## ' + points, reading_sources, evidence_prefix='wiki-' + slug)
                              + '</details>')
    else:
        rendered_body = md_render(display_body, reading_sources, evidence_prefix='wiki-' + slug, topic=is_topic)
    rewrite_action = '' if is_knowledge_index or is_topic else f'<button class="button" data-rewrite="{E(slug)}">改写表达</button>'
    body = f'<div class="breadcrumbs"><a href="/">知识 Wiki</a> / {E(category_name(current.get("category", "other")))}</div><div class="page-heading knowledge-heading"><div><span class="tag">{E(category_name(current.get("category", "other")))}</span> <span class="tag subtle">{page_kind}</span><h1 class="knowledge-title">{E(current["title"])}</h1><p>{summary}</p></div><div class="button-row">{second_pass}{rewrite_action}<a class="button" href="/graph">在图谱中探索 ↗</a></div></div><article class="prose knowledge-document">{rendered_body}</article>'
    connections = semantic_backlinks(slug)
    if connections:
        body += '<section class="prose"><h2>方法之间的联系</h2>'
        for note in connections:
            body += f'<h3><a href="/page/{E(note["slug"])}">{E(note["title"])}</a></h3>'
            body += md_render(note['body'], note['source_ids'], evidence_prefix='connection-' + note['slug'])
        body += '</section>'
    from .fact_checks import render as fact_checks_html
    body += f'<link rel="stylesheet" href="/static/fact-checks.css?v={ASSET_VERSIONS["fact-checks.css"]}">'
    if not is_topic and not is_knowledge_index and not reader_only:
        body += '<details class="page-evidence-records"><summary>断言与原文证据</summary>' + fact_checks_html(slug, editable=False) + '</details>'
    body += f'<script src="/static/fact-checks.js?v={ASSET_VERSIONS["fact-checks.js"]}" defer></script>'
    if backlinks:
        body += '<section class="panel"><h2>引用这个页面的知识</h2>' + ''.join(f'<a class="text-link" href="/page/{quote(p["slug"])}">{E(p["title"])}</a> ' for p in backlinks) + '</section>'
    from .common import META
    metadata=load_json(META/(slug+'.json')) if (META/(slug+'.json')).exists() else {}
    if metadata.get('semantic_reviews') and not reader_only:
        body+='<details class="page-evidence-records"><summary>独立 Agent 证据核查</summary><p class="muted">记录原文支持判断，不代表独立研究复现。</p>'
        from .compiler import digest
        from .workspaces import current_workspace
        workspace=current_workspace()
        last_review=metadata['semantic_reviews'][-1]
        stale=last_review.get('body_digest')!=digest(page_body(current['body']))
        if workspace:
            for sid,version in last_review.get('original_versions',{}).items():
                parsed_path=workspace.root/'parsed'/(sid+'.json')
                if (digest(load_json(parsed_path)) if parsed_path.exists() else None)!=version: stale=True
        if stale:
            body+='<p class="notice warning">该核查记录未绑定当前正文版本，需重新核对。</p>'
        for review in metadata['semantic_reviews'][-1].get('reviews',[]):
            body+=f'<p>{E(review.get("claim_id",""))} · {E(review.get("verdict",""))}：{E(review.get("reason",""))}</p>'
        for claim in metadata.get('claims',[]):
            for passage in claim.get('evidence_passages',[]):
                location='?page='+str(passage.get('page',1))+'&paragraph_id='+quote(passage.get('paragraph_id',''))
                body+=f'<p><a href="/source/{E(passage["source_id"])}{E(location)}">{E(passage.get("locator","原文"))}</a></p>'
        body+='</details>'
    return layout(current['title'], body)


@app.get('/sources', response_class=HTMLResponse)
@library_snapshot
def sources(q: str = '', category: str = '', status: str = ''):
    from .workspaces import current_workspace
    from .paper_policy import enabled
    paper_only = enabled(current_workspace())
    with connect() as db:
        items = db.execute('SELECT * FROM sources ORDER BY captured_at DESC,id').fetchall()
    filtered = [r for r in items if (not category or r['category'] == category) and (not status or r['content_status'] == status) and (not q or q.casefold() in r['title'].casefold())]
    body = '<div class="page-heading"><div><h1>原始资料</h1></div><a class="button primary" href="#import">＋ 导入资料</a></div>'
    body += f'<form class="filterbar"><input name="q" value="{E(q)}" placeholder="搜索资料标题…"><select name="category">{category_options(category, True)}</select><select name="status"><option value="">全部原文状态</option><option value="full-text" {"selected" if status == "full-text" else ""}>原文已获取</option><option value="summary-only" {"selected" if status == "summary-only" else ""}>仅有摘要</option><option value="missing" {"selected" if status == "missing" else ""}>原文或文字待补充</option></select><button class="button">筛选</button></form>'
    if filtered:
        body += '<div class="source-list">'
        for row in filtered:
            d = source_record(row['id'])
            complete = row['content_status'] == 'full-text'
            body += f'<a class="source-card" href="/source/{row["id"]}"><div class="source-icon">{ "PDF" if str(d.get("asset_path", "")).endswith(".pdf") else "TXT" }</div><div class="source-summary"><div class="tags"><span class="tag">{E(category_name(row["category"]))}</span><span class="tag subtle">{E(KINDS.get(row["kind"],row["kind"]))}</span><span class="tag {"complete" if complete else "warning"}">{E(original_label(d))}</span>{source_version_tag(d)}</div><h3>{E(row["title"])}</h3><p>{E(re.sub(r"<[^>]+>", "", d.get("text", ""))[:180])}</p><small>{E(STAGES.get(row["stage"],row["stage"]))} · {len(d.get("text", "")):,} 字符</small></div><span class="arrow">↗</span></a>'
        body += '</div>'
    else:
        body += empty('资料库是空的' if not items else '没有匹配的资料', '导入需要的原始材料，再选择主题。自动订阅与运行状态可在“采集计划”中查看。', '<a class="button" href="/collection">查看采集计划</a>')
    url_heading = '从论文链接导入' if paper_only else '从公开链接导入'
    url_help = ('输入论文 PDF 地址或 arXiv 链接；仅归档取得真实 PDF 原件的论文。' if paper_only else
                '公开网页提取正文；arXiv 尝试 HTML / PDF 原文。无法取得原文时会明确标记。')
    file_heading = '上传 PDF 论文' if paper_only else '从本地文件导入'
    file_accept = '.pdf' if paper_only else '.pdf,.md,.txt'
    kind_field = ('<input type="hidden" name="kind" value="paper">' if paper_only else
                  '<label>资料类型<select name="kind"><option value="">按文件判断（PDF 论文 / 其他文档）</option><option value="paper">论文</option><option value="document">文档</option><option value="note">个人笔记与观点</option></select></label>')
    file_help = ('仅支持 PDF 论文，最大 15 MB。PDF 原件可直接在资料页阅读。' if paper_only else
                 '支持 PDF、Markdown 和文本，最大 15 MB。PDF 原件可直接在资料页阅读。')
    body += f'''<section class="panel import-panel" id="import"><div class="section-heading"><div><h2>导入原始资料</h2></div></div><div class="import-grid"><form id="import-url-form"><h3>{url_heading}</h3><label>原始网址<input name="url" type="url" required placeholder="https://…"></label><label>资料标题（可选）<input name="title" placeholder="留空时尝试使用页面标题"></label><label>主题分类<select name="category">{category_options('other', active_only=True)}</select></label><button class="button primary">获取原文并归档</button><small>{url_help}</small></form><form id="import-file-form"><h3>{file_heading}</h3><label>文件<input name="file" type="file" accept="{file_accept}" required></label><label>资料标题（可选）<input name="title" placeholder="默认使用文件名"></label>{kind_field}<label>主题分类<select name="category">{category_options('research', active_only=True)}</select></label><button class="button primary">上传原件并归档</button><small>{file_help}</small></form></div></section>'''
    return layout('原始资料', body, 'sources')


def source_version_tag(record):
    if record.get('kind') != 'paper':
        return ''
    bibliography = record.get('bibliography') or {}
    version = bibliography.get('original_version')
    label = '正式发表版本' if version == 'published' else '预印本' if bibliography.get('arxiv_id') else '发表状态未核实'
    return f'<span class="tag subtle">{E(label)}</span>'


def bibliography_html(record):
    """Compact identity/date summary with expandable, source-grounded metadata."""
    from .publication_metrics import publication_fields
    b = record.get('bibliography') if isinstance(record.get('bibliography'), dict) else {}
    authors = [a for a in b.get('authors', []) if isinstance(a, dict) and isinstance(a.get('name'), str)]
    names = [a['name'] for a in authors]
    author_summary = '、'.join(names[:3]) + (f' 等 {len(names)} 位作者' if len(names) > 3 else '') if names else '作者未提供'
    identity = ('arXiv ' + str(b['arxiv_id']) + (f"v{b['arxiv_version']}" if b.get('arxiv_version') else ' · 版本未核实')) if b.get('arxiv_id') else KINDS.get(record.get('kind'), '文档')
    first = _date(b.get('first_published'))
    doi = b.get('doi') or ''
    published_record = b.get('publication_record') if isinstance(b.get('publication_record'), dict) else {}
    formal = b.get('original_version') == 'published'
    arxiv_identity = identity
    if formal:
        identity = str(published_record.get('venue') or b.get('publisher') or '出版论文') + ' · 正式发表版本'
        first = _date(b.get('publication_date')) or str(published_record.get('date') or published_record.get('year') or '')
    doi_status = '出版 DOI 由源提供' if doi else '未记录出版 DOI' if b.get('doi_status') == 'not-provided' else '出版 DOI 未提供 / 未核实'
    if published_record.get('doi') and published_record.get('status') == 'verified-on-official-site':
        doi_status = '正式版本出版 DOI 已核对'
    if b.get('arxiv_doi'):
        doi_status += ' · 预印本 DOI 已记录'
    summary = f'<div class="source-identity"><strong>{E(identity)}</strong><span>{"正式发表" if formal else "首次发布"} {E(first or "未核实")}</span><span>{E(author_summary)}</span><span>{E(doi_status)}</span></div>'
    def external(value):
        safe = _external_url(value)
        return f'<a href="{E(safe)}" target="_blank" rel="noopener noreferrer">{E(safe)} ↗</a>' if safe else '未提供'
    def field(label, value):
        return f'<div><dt>{label}</dt><dd>{value}</dd></div>'
    author_lines = []
    for author in authors:
        affiliations = author.get('affiliations', [])
        affiliations = [v for v in affiliations if isinstance(v, str)] if isinstance(affiliations, list) else []
        author_lines.append(E(author['name']) + (f' <span class="muted">（{E("；".join(affiliations))}）</span>' if affiliations else ''))
    affiliation_values = b.get('affiliations', [])
    affiliation_values = [value for value in affiliation_values if isinstance(value, str)] if isinstance(affiliation_values, list) else []
    publication = 'arXiv 预印本；同行评审状态未核实' if b.get('publication_status') == 'arxiv-preprint' else '未核实'
    if b.get('journal_ref'):
        publication += ' · 源提供期刊引用，发表状态需进一步核查'
    if published_record.get('status') == 'verified-on-official-site':
        publication = ('归档为正式发表版本：' if formal else '归档为 arXiv 版本；已核对正式发表记录：') + str(published_record.get('venue', ''))
    rows = [field('作者', '<br>'.join(author_lines) or '未提供 / 未核实'),
            field('作者机构', E('；'.join(affiliation_values)) or '未提供 / 未核实'),
            field('发布平台 / 发布方', E(b.get('publisher') or '未提供 / 未核实')),
            field('原始链接', external(b.get('original_url') or record.get('url', ''))),
            field('归档原件', f'<a href="/source/{record["id"]}/original">查看已保存原件 ↗</a>' if record.get('asset_path') else '未保存'),
            field('arXiv 标识与版本', E(arxiv_identity) if b.get('arxiv_id') else '未提供'),
            field('出版 DOI', (external('https://doi.org/' + doi) + '<br><span class="muted">源提供标识符；DOI 注册记录未另核实</span>') if doi else E(doi_status)),
            field('预印本 DOI', external('https://doi.org/' + b['arxiv_doi']) + '<span class="muted"> · arXiv 页面列出的预印本标识</span>' if b.get('arxiv_doi') else '未核实 · 请查看 arXiv 标识'),
            field('原文题名', E(b.get('title') or record.get('title', '未提供'))),
            field('首次公开日期（预印本）' if formal and b.get('preprint_pdf_path') else '归档版本发布日期' if formal else '首次发布日期', E(b.get('first_published') or '未提供 / 未核实')),
            field('版本更新日期', E(b.get('updated') or '未提供 / 未核实')),
            field('订阅推送日期', E(b.get('announced_at') or record.get('published') or '未提供') + '<span class="muted"> · 不等同于首次发表</span>'),
            field('采集日期', E(record.get('captured_at') or '未提供')),
            field('资料类型', E(KINDS.get(record.get('kind'), record.get('kind', '文档')))),
            field('出版 / 预印本状态', E(publication)),
            field('期刊引用', E(b.get('journal_ref') or '未提供')),
            field('获取方式 / 正文状态', E(str(b.get('acquisition_method') or '未记录') + ' · ' + str(b.get('text_acquisition_method') or record.get('content_format', '未记录')) + ' · ' + original_label(record))),
            field('元数据查询路径', external(b.get('metadata_source_url', ''))),
            field('元数据核对时间', E(b.get('checked_at') or '尚未核对'))]
    if b.get('local_metadata_method') == 'archived-pdf-page-1':
        rows.append(field('本地书目核对依据', f'<a href="/source/{E(record["id"])}/original#page=1">归档 PDF 第 1 页</a> · 作者、机构和版本标识'))
        if b.get('local_version_date'):
            rows.append(field('PDF 标注的版本日期', E(b['local_version_date'])))
        if b.get('local_arxiv_identifier_url'):
            rows.append(field('PDF 标识对应页面', external(b['local_arxiv_identifier_url']) + '<span class="muted"> · 按 PDF 标识定位，未另做在线核对</span>'))
        rows.append(field('本地书目核对时间', E(b.get('local_checked_at', '未记录'))))
    selection = record.get('selection') if isinstance(record.get('selection'), dict) else {}
    rows.extend(field(label, value) for label, value in publication_fields(record))
    if published_record:
        rows.append(field('正式发表日期', E(published_record.get('date') or str(published_record.get('year') or '未提供'))))
        rows.append(field('正式发表记录', external(published_record.get('url', '')) + '<br>' + E(published_record.get('venue', '')) + ' · ' + E(str(published_record.get('year', '')))))
        rows.append(field('正式版本作者', E('；'.join(published_record.get('authors', []))) or '未记录'))
        rows.append(field('正式版本 DOI', external('https://doi.org/' + published_record['doi']) if published_record.get('doi') else '该核对页面未提供'))
        rows.append(field('正式版本核对时间', E(published_record.get('checked_at', '未记录')) + ('<br><span class="muted">归档原件为正式发表版本。</span>' if formal else '<br><span class="muted">上面的作者与日期属于归档预印本。</span>')))
        if published_record.get('notes'):
            rows.append(field('发表核对说明', E(published_record['notes'])))
    rows.append(field('资料入选状态', E(selection.get('status') or '自动发现的候选资料')))
    rows.append(field('入选理由', E(selection.get('reason') or '未记录')))
    if selection.get('limitations'):
        rows.append(field('尚未核实部分', E(selection['limitations'])))
    if selection.get('checked_at'):
        rows.append(field('入选核查时间', E(selection['checked_at'])))
    if b.get('query_error'):
        rows.append(field('最近查询状态', '查询未成功 · ' + E(b['query_error'])))
    return summary + '<details class="source-bibliography"><summary>来源与出版信息 <span>作者、标识符、日期及获取记录</span></summary><dl>' + ''.join(rows) + '</dl></details>'


@app.get('/source/{sid}', response_class=HTMLResponse)
@library_snapshot
def source(sid: str, page: int | None=None, paragraph_id: str | None=None, parsed_version: str | None=None):
    from .workspaces import current_workspace
    from .paper_policy import enabled
    workspace = current_workspace()
    paper_only = enabled(workspace)
    d = source_record(sid)
    with connect() as db:
        row = db.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
    if not row:
        raise HTTPException(404)
    complete = row['content_status'] == 'full-text'
    body = f'<div class="breadcrumbs"><a href="/sources">原始资料</a> / {E(category_name(row["category"]))}</div><div class="page-heading"><div><div class="tags"><span class="tag subtle">{E(KINDS.get(d["kind"],d["kind"]))}</span><span class="tag {"complete" if complete else "warning"}">{E(original_label(d))}</span>{source_version_tag(d)}</div><h1 class="document-title">{E(d["title"])}</h1><p>{len(d.get("text", "")):,} 字符 · {E(STAGES.get(row["stage"],row["stage"]))}</p></div></div>'
    body += f'<div class="source-toolbar"><label>主题分类 <select data-source-category="{sid}" {"disabled" if row["stage"] in {"drafted", "compiling"} else ""}>{category_options(row["category"], active_only=True, retain_selected=True)}</select></label>'
    if d.get('url', '').startswith(('http://','https://')) and '.invalid/' not in d['url']:
        body += f'<a class="button" href="{E(d["url"])}" target="_blank" rel="noopener noreferrer">原始链接 ↗</a>'
    if d.get('asset_path'):
        body += f'<a class="button" href="/source/{sid}/original" target="_blank">查看归档原件 ↗</a>'
    b = d.get('bibliography') or {}
    if b.get('original_version') == 'published' and b.get('preprint_pdf_path'):
        body += f'<a class="button" href="/source/{sid}/original?version=preprint" target="_blank">预印本原件 ↗</a>'
    topic_active = any(c['id'] == row['category'] and c['enabled'] for c in categories())
    if complete and row['stage'] == 'new' and topic_active:
        targets = [p for p in wiki_pages() if p['category'] == row['category'] and p['page_kind'] == 'concept'
                   and (not paper_only or p.get('page_role') not in {'paper', 'paper_reading', 'knowledge_index'})]
        if targets:
            options = '<option value="">自动选择 / 新建</option>' + ''.join(f'<option value="{E(p["slug"])}">{E(p["title"])}</option>' for p in targets)
            body += f'<label>归入 Wiki <select data-compile-target>{options}</select></label>'
        if not paper_only:
            body += '<label class="checkbox-label"><input type="checkbox" data-second-pass> 额外润色（+1 次调用）</label>'
        body += f'<button class="button primary" data-source-compile="{sid}">编写 Wiki</button>'
    elif row['stage'] == 'drafted':
        body += '<a class="button primary" href="/drafts">查看处理记录 →</a>'
    if not complete and d.get('body_level') != 'manual-original':
        body += f'<button class="button" data-refresh="{sid}">重新获取原文</button>'
    body += '</div>'
    published_readings = [p for p in wiki_pages()
                          if p.get('page_role') in {'paper', 'paper_reading'}
                          and sid in p.get('source_ids', [])]
    if published_readings:
        body += '<section class="source-reading-links" aria-label="已发布论文解读"><div><strong>这篇论文的知识解读</strong><p>阅读研究问题、方法机制与提炼的知识点。</p></div><div class="button-row">'
        body += ''.join(f'<a class="button primary" href="/page/{quote(p["slug"])}">阅读知识解读 →</a>' for p in published_readings)
        body += '</div></section>'
    if not topic_active:
        body += '<p class="field-help">该主题已停用。已有原文仍可阅读；重新编写前，可启用主题或将资料改到其他主题。</p>'
    body += bibliography_html(d)
    from .evidence import source_evidence
    body += '<details class="source-bibliography"><summary>来源核查状态</summary>' + source_evidence(d) + '</details>'
    if not complete:
        message = 'PDF 原件已保存，可在下方直接阅读；尚无可提取文字，需要 OCR 或上传可搜索版本后再编写 Wiki。' if str(d.get('asset_path','')).lower().endswith('.pdf') else '自动获取原文尚未成功。可重试，或导入原始文件；目前内容不参与 Wiki 编写。'
        body += f'<div class="notice warning">{E(message)}</div>'
    pdf = d.get('pdf_path') or (d.get('asset_path') if str(d.get('asset_path','')).lower().endswith('.pdf') else None)
    if pdf:
        body += f'<section class="pdf-document"><div class="section-heading"><h2>PDF 原件</h2><a class="button" href="/source/{sid}/original" target="_blank">在新窗口阅读 ↗</a></div><div class="pdf-reader" id="pdf-reader" data-pdf-url="/source/{sid}/original" aria-label="PDF 原始资料阅读器"></div></section><script type="module" src="/static/pdf-reader.js"></script>'
    elif row['kind'] == 'paper':
        body += f'<section class="panel"><h2>PDF 原件待补充</h2><p class="muted">论文以 PDF 原件阅读，保留公式、表格和图片排版。</p><button class="button primary" data-fetch-pdf="{sid}">获取 PDF 原件</button></section>'
    else:
        body += f'<div class="section-heading"><h2>{"原文阅读" if complete else "订阅摘要（非原文）" if row["content_status"] == "summary-only" else "提取文字"}</h2><span class="muted">保留来源内容，未经 LLM 改写</span></div>'
        text = d.get('text', '')
        rendered = md_render(text) if d.get('content_format') in {'markdown','html'} else f'<pre class="original-text">{E(text)}</pre>'
        body += '<article class="prose original-document">' + (rendered if text else '<p class="muted">暂时没有可阅读的提取文字，请查看原件或补充可搜索文件。</p>') + '</article>'
    parsed_path=workspace.root / 'parsed' / (sid+'.json') if workspace else None
    if parsed_path and parsed_path.exists():
        import hashlib
        if parsed_version and hashlib.sha256(parsed_path.read_bytes()).hexdigest()!=parsed_version:
            body+='<div class="notice warning">解析版本已改变，旧证据定位需重新核对。</div>'
        parsed=load_json(parsed_path)
        segments=parsed.get('paragraphs',[])
        if page is not None: segments=[p for p in segments if p.get('page')==page]
        if paragraph_id: segments=[p for p in segments if p.get('id')==paragraph_id]
        body+='<details class="panel" open><summary>归档文字与证据定位</summary>'
        body+=''.join(f'<section id="{E(p["id"])}"><p class="muted">PDF 页序 {E(p.get("page",1))} · {E(p["id"])}</p><blockquote>{E(p["text"])}</blockquote></section>' for p in segments)
        body+='</details>'
    return layout(d['title'], body, 'sources')


@app.get('/source/{sid}/original')
def original(sid: str, version: str = 'current'):
    d = source_record(sid)
    if version not in {'current', 'preprint'}:
        raise HTTPException(400, '未知原件版本')
    asset = (d.get('bibliography') or {}).get('preprint_pdf_path') if version == 'preprint' else d.get('pdf_path') or d.get('asset_path')
    if not asset:
        raise HTTPException(404, '没有已归档原件')
    path = (ASSETS / asset).resolve()
    if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
        raise HTTPException(404)
    media = 'application/pdf' if path.suffix == '.pdf' else 'text/plain; charset=utf-8'
    # HTML is downloaded as plain text: untrusted original scripts never execute in this origin.
    return FileResponse(path, media_type=media, headers={'Content-Disposition': 'inline', 'X-Content-Type-Options': 'nosniff'})


@app.get('/drafts', response_class=HTMLResponse)
@library_snapshot
def drafts():
    connect().close()
    items = [load_json(p) for p in sorted(PENDING.glob('*.json'))]
    body = '<div class="page-heading"><div><h1>编写记录</h1></div><button class="button primary" data-compile="">综合待编写原文</button></div>'
    if not items:
        body += empty('没有待处理项', '', '<a class="button" href="/sources">查看原始资料</a>')
    for d in items:
        pid = proposal_key(d)
        rewrite = d.get('mode') == 'rewrite'
        label = '表达改写' if rewrite else ('知识更新' if d['action'] == 'update' else '新建知识')
        body += f'<section class="draft-panel" id="draft-{E(pid)}"><div class="draft-header"><div><span class="tag">{E(category_name(d.get("category","other")))}</span><span class="tag subtle">{label}</span><h2>{E(d["title"])}</h2></div><button class="button danger" data-reject="{E(pid)}">撤销本次编写</button></div>'
        issues = d.get('writing_issues', [])
        issues = [issue for issue in issues if isinstance(issue, str) and issue.strip()] if isinstance(issues, list) else []
        if d.get('generation_complete') is False or d.get('editorial_reviewed') is False:
            issues.append(d.get('editorial_error') or '第二遍编辑未完成，请退回重新生成。')
        if d.get('needs_fact_review'):
            issues.append(d.get('automation_error') or '系统需处理事实疑点。')
            if d.get('fact_review_issues'):
                issues.append(d['fact_review_issues'])
        if issues:
            body += '<aside class="writing-check notice warning"><strong>编写检查提示</strong><ul>' + ''.join(f'<li>{E(issue)}</li>' for issue in issues) + '</ul></aside>'
        base = d.get('base_body')
        if base is None and d['action'] == 'update':
            target = WIKI / (d['slug'] + '.md')
            base = target.read_text(encoding='utf-8') if target.is_file() else ''
        if d['action'] == 'update':
            old = page_body(base or '')
            new = page_body(d['body_markdown'])
            body += '<div class="draft-comparison"><section><h3>原正文</h3><article class="prose">' + md_render(old, d.get('source_ids', []), evidence_prefix='old-' + pid) + '</article></section>'
            body += '<section><h3>编写结果</h3><article class="prose">' + md_render(new, d.get('source_ids', []), evidence_prefix='new-' + pid) + '</article></section></div>'
            diff = '\n'.join(unified_diff(old.splitlines(), new.splitlines(), fromfile='原正文', tofile='待审稿', lineterm=''))
            body += f'<details class="draft-diff"><summary>查看正文差异</summary><pre>{E(diff or "正文未变化")}</pre></details>'
        else:
            body += f'<article class="prose">{md_render(d["body_markdown"], d.get("source_ids", [d.get("source_id")]), evidence_prefix="draft-" + pid)}</article>'
        body += '<div class="draft-source-links"><strong>原始资料</strong> ' + ' · '.join(f'<a href="/source/{E(sid)}" target="_blank" rel="noopener">{E(load_json(RAW/(sid+".json")).get("title", sid) if (RAW/(sid+".json")).is_file() else sid)}</a>' for sid in d.get('source_ids', [])) + '</div>'
        body += '</section>'
    history_dir = PENDING.parent / 'proposal_history'
    history = []
    for path in history_dir.glob('*.json'):
        record = load_json(path)
        if record.get('outcome') == 'approved':
            publication = record.get('publication') if isinstance(record.get('publication'), dict) else {}
            published_at = next((value for value in (record.get('handled_at'), publication.get('at'), record.get('updated_at'))
                                 if isinstance(value, str) and value.strip()), '')
            # New publication journals identify an event by its filename, while
            # old proposal/source anchors must keep resolving as before.
            history_id = record.get('proposal_id') or record.get('source_id') or path.stem
            history.append((record, history_id, published_at))
    history.sort(key=lambda item: (item[2], str(item[1])), reverse=True)
    if history:
        body += '<div class="section-heading"><h2>已完成</h2></div><div class="wiki-grid">'
        for d, history_id, published_at in history[:12]:
            body += f'<a class="wiki-card" id="draft-{E(history_id)}" href="/page/{E(d["slug"])}"><span class="tag complete">已发布</span><h3>{E(d["title"])}</h3><p>{E(publication_time(published_at))}</p><div class="card-foot">查看知识页面 →</div></a>'
        body += '</div>'
    return layout('编写记录', body, 'drafts')


@app.get('/graph', response_class=HTMLResponse)
def graph():
    body = f'<div class="page-heading"><div><h1>知识图谱</h1></div><a class="button" href="/">回到知识库 →</a></div><div class="filterbar"><input id="graph-search" placeholder="查找节点…" aria-label="查找图谱节点"><select id="graph-category" aria-label="图谱主题">{category_options("",True)}</select><label class="checkbox-label"><input type="checkbox" id="graph-sources" checked>显示原始证据</label></div><section class="graph-panel"><div class="graph-legend"><span><i class="legend-wiki"></i>Wiki 知识</span><span><i class="legend-source"></i>原始证据</span><span><i class="legend-category"></i>主题分类</span></div><svg id="graph-canvas" tabindex="0" aria-label="知识图谱" role="img"></svg><div id="graph-empty" class="graph-empty">尚未形成知识图谱。导入资料并生成 Wiki 后，关系会在这里出现。</div><div id="graph-info" class="graph-info">拖动节点 · 滚轮缩放 · 选择节点查看关系</div></section><script src="/static/graph.js?v={ASSET_VERSIONS["graph.js"]}" defer></script>'
    return layout('知识图谱', body, 'graph')


@app.get('/api/graph')
def api_graph():
    connect().close()
    return graph_data()


@app.get('/collection', response_class=HTMLResponse)
def collection():
    from .scheduler import get_status
    from .settings import snapshot
    status = get_status(include_launchd=True)
    config = snapshot()
    feeds = config['feeds']
    active_categories = {c['id'] for c in config['topics'] if c['enabled']}
    enabled = [f for f in feeds if f.get('enabled', True) and f.get('category', 'other') in active_categories]
    last = status.get('last_run') or {}
    state = '已启用' if status.get('loaded') else '尚未启用'
    last_labels = {'succeeded': '采集完成', 'failed': '采集存在错误', 'running': '正在采集', 'skipped': '跳过重复运行', 'partial': '部分失败', 'interrupted': '上次运行中断', 'unknown': '运行状态待核实'}
    next_run = status.get('next_run') if status.get('loaded') else '等待启用任务'
    if isinstance(next_run, str):
        next_run = next_run.replace('T', ' ').replace('+08:00', '')[:16]
    body = '<div class="page-heading"><div><h1>采集计划</h1></div><a class="button" href="/settings#feeds">管理主题与订阅 →</a></div>'
    body += f'<section class="schedule-card"><div class="schedule-time"><span class="muted">每日采集</span><strong>{E(status.get("time", "08:00"))}</strong><span class="muted">北京时间 · Asia/Shanghai</span></div><div class="schedule-detail"><span class="tag {"complete" if status.get("loaded") else "subtle"}">{E(state)}</span><p>下一次计划：{E(next_run)}</p><small>最近运行：{E(last_labels.get(last.get("status"), "尚未运行"))}</small></div></section>'
    if not status.get('timezone_matches', True):
        body += '<div class="notice warning">系统时区已不在北京时间，任务的实际触发时刻会变化。请将 macOS 时区恢复为 Asia/Shanghai 后重新确认计划。</div>'
    body += f'<div class="section-heading"><h2>启用订阅 · {len(enabled)} 个节点</h2><span class="muted">按节点筛选 · 自动去重</span></div><div class="source-list">'
    for feed in enabled:
        required = feed.get('require_keywords', [])
        body += f'<section class="source-card"><div class="source-icon">{"论文" if feed.get("kind") == "paper" else "订阅"}</div><div class="source-summary"><div class="tags"><span class="tag">{E(category_name(feed.get("category", "other")))}</span><span class="tag subtle">{E(KINDS.get(feed.get("kind"), "文章"))}</span></div><h3>{E(feed["name"])}</h3><p>包含任一：{E(" · ".join(feed.get("include_keywords", [])) or "不限关键词")}</p>'
        if required:
            body += f'<p>还需匹配任一：{E(" · ".join(required))}</p>'
        window = f'仅最近 {feed["max_age_days"]} 天的有日期内容' if feed.get('max_age_days') else '不限发布时间'
        body += f'<small>{E(window)}</small> · <a class="text-link" href="{E(feed["url"])}" target="_blank" rel="noopener noreferrer">原始订阅源 ↗</a></div></section>'
    if not enabled:
        body += '<p class="muted">当前没有启用的订阅。到“主题与订阅”新增或启用节点后，下次采集会使用这些设置。</p>'
    drafting = '采集完成后，LLM 编写知识，通过系统检查后自动发布。处理结果在“编写记录”查看。' if os.getenv('AUTO_DRAFT', 'false').lower() == 'true' else '编写由你触发，通过系统检查后自动发布。'
    body += f'</div><section class="panel collection-notes"><h2>采集说明</h2><p>在<a href="/settings">主题与订阅</a>中调整关注领域，为每个订阅设置所属主题、关键词与日期范围。保存后，下次采集读取新设置。</p><p>{drafting}</p><p>定时任务在本机运行。Mac 需要保持开机并登录；睡眠期间无法准时采集，唤醒后可能补触发。关机错过的任务没有保证补跑。</p></section>'
    return layout('采集计划', body, 'collection')


@app.get('/settings', response_class=HTMLResponse)
def settings_page(notice: str = ''):
    from .settings import snapshot
    from .settings_view import settings_html
    return layout('主题与订阅', settings_html(snapshot(), notice), 'settings')


@app.get('/api/settings')
def api_settings():
    from .settings import snapshot
    return snapshot()


async def settings_change(fn, *args):
    from .settings import SettingsConflict
    try:
        result = await run_in_threadpool(fn, *args)
    except SettingsConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:240])
    except (OSError, RuntimeError):
        raise HTTPException(400, '设置保存失败，请检查项目配置文件后重试。')
    result['url'] = '/settings?notice=' + quote(result.get('message', '设置已保存'))
    return result


@app.post('/api/settings/topics')
async def api_save_topic(request: Request):
    from .settings import save_topic
    return await settings_change(save_topic, await read_payload(request))


@app.post('/api/settings/topics/{topic_id}/delete')
async def api_delete_topic(topic_id: str, request: Request):
    from .settings import delete_topic
    return await settings_change(delete_topic, topic_id, await read_payload(request))


@app.post('/api/settings/feeds')
async def api_save_feed(request: Request):
    from .settings import save_feed
    return await settings_change(save_feed, await read_payload(request))


@app.post('/api/settings/feeds/{feed_id}/delete')
async def api_delete_feed(feed_id: str, request: Request):
    from .settings import delete_feed
    return await settings_change(delete_feed, feed_id, await read_payload(request))


@app.get('/api/pages/{slug}/claim-reviews')
async def api_read_claim_reviews(slug: str):
    from .fact_checks import snapshot
    return await operation(snapshot, slug)


@app.post('/api/pages/{slug}/claim-reviews')
async def api_claim_review(slug: str, request: Request):
    from .fact_checks import save_review, ReviewConflict
    payload = await read_payload(request)
    try:
        return await run_in_threadpool(save_review, slug, payload)
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError:
        raise HTTPException(400, '核查记录保存失败，请检查数据目录权限')


async def read_payload(request):
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise HTTPException(415, '请使用 JSON 请求')
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 22 * 1024 * 1024:
            raise HTTPException(413, '上传内容过大')
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, '请求格式无效')
    if not isinstance(payload, dict):
        raise HTTPException(400, '请求格式无效')
    return payload


async def operation(fn, *args, **kwargs):
    try:
        from .workspaces import current_workspace
        workspace=current_workspace()
        def scoped():
            if workspace:
                from .publication import publication_lock
                with publication_lock(workspace): return fn(*args,**kwargs)
            return fn(*args,**kwargs)
        return await run_in_threadpool(scoped)
    except (ValueError, OSError, RuntimeError) as exc:
        # Never forward backend exception text (URLs and upstream error bodies may contain credentials).
        message = str(exc) if isinstance(exc, ValueError) and 'http' not in str(exc).lower() else '处理失败，请检查原文链接、文件或模型接口后重试。'
        raise HTTPException(400, message[:240])


@app.post('/api/import-url')
async def api_import_url(request: Request):
    from .collector import import_url
    p = await read_payload(request)
    category = await operation(validate_category, p.get('category',default_category()), active_only=True)
    url = p.get('url','')
    if not isinstance(url,str) or not url.strip():
        raise HTTPException(400, '请输入原始资料链接')
    sid = await operation(import_url, url.strip(), category=category, title=p.get('title') or None, verify_metadata=True)
    return {'source_id': sid, 'url': '/source/' + sid if sid else '/sources', 'message': '资料已归档' if sid else '这份资料已经存在'}


@app.post('/api/import-file')
async def api_import_file(request: Request):
    from .collector import import_file
    p = await read_payload(request)
    category = await operation(validate_category, p.get('category',default_category()), active_only=True)
    filename = p.get('file_name','')
    if not isinstance(filename,str):
        raise HTTPException(400, '文件名无效')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf','.md','.txt'}:
        raise HTTPException(400, '支持 PDF、Markdown 和文本文件')
    try:
        data = base64.b64decode(p.get('content_base64',''), validate=True)
    except (ValueError, binascii.Error, TypeError):
        raise HTTPException(400, '文件内容无效')
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(413, '文件为空或超过 15 MB')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / ('original' + suffix)
        path.write_bytes(data)
        sid = await operation(import_file, p.get('title') or Path(filename).stem, path, category=category, kind=p.get('kind') or None)
    return {'source_id': sid, 'url': '/source/' + sid if sid else '/sources', 'message': '原件已归档' if sid else '这份资料已经存在'}


@app.post('/api/sources/{sid}/category')
async def api_category(sid: str, request: Request):
    from .collector import set_category
    source_record(sid)
    with connect() as db:
        row = db.execute('SELECT stage FROM sources WHERE id=?', (sid,)).fetchone()
    if row and row['stage'] in {'drafted','compiling'}:
        raise HTTPException(409, '请先退回关联草稿，再修改分类')
    p = await read_payload(request)
    await operation(set_category, sid, p.get('category','other'))
    return {'message': '主题分类已更新'}


@app.post('/api/compile')
async def api_compile(request: Request):
    p = await read_payload(request)
    category = p.get('category') or None
    if category:
        await operation(validate_category, category, active_only=True)
    sid = p.get('source_id') or None
    if sid:
        source_record(sid)
    second_pass = p.get('second_pass', False)
    if not isinstance(second_pass, bool):
        raise HTTPException(400, '额外润色选项必须为布尔值')
    options = dict(limit=3, category=category, source_id=sid, target_slug=p.get('target_slug') or None, second_pass=second_pass)
    draft_only = p.get('draft_only', False)
    if not isinstance(draft_only, bool):
        raise HTTPException(400, '草稿预览选项必须为布尔值')
    from .workspaces import current_workspace
    workspace=current_workspace()
    from .paper_policy import enabled as paper_workflow_enabled
    if workspace and (workspace.library_id!='legacy' or paper_workflow_enabled(workspace)) and not draft_only:
        from .jobs import enqueue
        from .compiler import compilation_inputs
        def queued_sources():
            return compilation_inputs(workspace,[sid] if sid else None,category=category)
        ids,skipped,versions=await operation(queued_sources)
        if not ids:return {'count':0,'message':'该主题暂无原始资料'}
        job=await operation(enqueue,workspace,'compile',{'source_ids':ids,'skipped_sources':skipped,'target_slug':options['target_slug'],'revisit':bool(options['target_slug'])},input_versions=versions)
        return {**job,'url':'/tasks','message':'分阶段编译任务已入队'}
    if draft_only:
        count = await operation(prepare, **options)
        return {'count': count, 'url': '/drafts' if count else None, 'message': f'已生成 {count} 份预览稿'}
    result = await operation(compile_and_publish, **options)
    published = result['published']
    result.update(url=('/page/' + next(r['slug'] for r in result['results'] if r['status'] == 'published') if published == 1 else '/') if published else '/drafts',
                  message=f'已编写并发布 {published} 个知识页面' if published == result['count'] and published else f'系统生成 {result["count"]} 份记录，{result["count"]-published} 份待处理')
    return result


@app.post('/api/pages/{slug}/rewrite')
async def api_rewrite(slug: str, request: Request):
    p = await read_payload(request)
    second_pass = p.get('second_pass', False)
    if not isinstance(second_pass, bool):
        raise HTTPException(400, '额外润色选项必须为布尔值')
    draft_only = p.get('draft_only', False)
    if not isinstance(draft_only, bool):
        raise HTTPException(400, '草稿预览选项必须为布尔值')
    pid = await operation(prepare_rewrite, slug, second_pass=second_pass)
    if draft_only:
        return {'proposal_id': pid, 'url': '/drafts#draft-' + pid, 'message': '表达改写预览已生成'}
    result = await operation(automatic_publish, pid)
    return {**result, 'url': '/page/' + slug if result['status'] == 'published' else '/drafts#draft-' + pid,
            'message': '表达已更新' if result['status'] == 'published' else '系统尚未完成改写，请查看处理记录'}


@app.post('/api/drafts/{sid}/approve')
async def api_approve(sid: str, request: Request):
    payload = await read_payload(request)
    slug = await operation(approve, sid, fact_invariance_confirmed=payload.get('fact_invariance_confirmed') is True,
                           fact_review_note=payload.get('fact_review_note', ''))
    return {'message':'知识页面已发布', 'url':'/page/' + slug}


@app.post('/api/drafts/{sid}/reject')
async def api_reject(sid: str):
    await operation(reject, sid)
    return {'message':'草稿已退回', 'url':'/drafts'}


@app.post('/api/sources/{sid}/refresh')
async def api_refresh(sid: str):
    from .collector import refresh_source
    source_record(sid)
    await operation(refresh_source, sid)
    record = source_record(sid)
    return {'message': original_label(record), 'url':'/source/' + sid}


@app.post('/api/sources/{sid}/pdf')
async def api_source_pdf(sid: str):
    from .collector import ensure_source_pdf
    source_record(sid)
    await operation(ensure_source_pdf, sid)
    return {'message': 'PDF 原件已归档', 'url': '/source/' + sid}
