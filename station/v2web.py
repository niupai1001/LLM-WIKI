"""Library-bound task, search, read-only query and migration interfaces."""
import html
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .workspaces import current_workspace, registry, initialize_candidate, synchronize_candidate, activate_library, resolve_workspace
from . import jobs

router = APIRouter()


def ws():
    return current_workspace() or resolve_workspace()


async def invoke(fn, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except (ValueError, RuntimeError, OSError) as error:
        # Upstream credential-bearing diagnostics do not enter the browser.
        message = str(error) if isinstance(error, ValueError) and 'http' not in str(error).lower() else '处理未完成，请检查任务记录与接口配置。'
        raise HTTPException(400, message[:500])


@router.get('/api/libraries')
def api_libraries():
    return registry(ws().registry_root)


@router.post('/api/libraries')
async def api_initialize(request: Request):
    from .web import read_payload
    data = await read_payload(request)
    library = await invoke(initialize_candidate, data.get('library_id','candidate-v2'),root=ws().registry_root)
    return {'library_id':library.library_id, 'url':library.url('/libraries')}


@router.post('/api/libraries/{library_id}/sync')
async def api_sync(library_id: str):
    return await invoke(synchronize_candidate,library_id,root=ws().registry_root)


@router.post('/api/libraries/{library_id}/activate')
async def api_activate(library_id: str):
    return await invoke(activate_library,library_id,root=ws().registry_root)


@router.post('/api/libraries/{library_id}/rollback')
async def api_rollback(library_id: str):
    return await invoke(activate_library,library_id,root=ws().registry_root,rollback=True)


@router.get('/api/libraries/compare')
async def api_compare(library_id: str='candidate-v2'):
    from .evaluation import compare_libraries
    return await invoke(compare_libraries,resolve_workspace(library_id,root=ws().registry_root),resolve_workspace('legacy',root=ws().registry_root))


@router.post('/api/libraries/{library_id}/evaluate')
async def api_evaluate(library_id: str):
    from .evaluation import evaluate_library
    return await invoke(evaluate_library,resolve_workspace(library_id,root=ws().registry_root))


@router.post('/api/jobs')
async def api_enqueue(request: Request):
    from .web import read_payload
    from .documents import source_version
    data = await read_payload(request)
    workspace = ws()
    if data.get('library_id') and data['library_id'] != workspace.library_id: raise HTTPException(400,'请求知识库与任务知识库不一致')
    payload=data.get('payload',{})
    if not isinstance(payload,dict): raise HTTPException(400,'任务参数须为对象')
    ids=payload.get('source_ids') or ([payload['source_id']] if payload.get('source_id') else [])
    if data.get('kind')=='compile':
        from .compiler import compilation_inputs
        ids,skipped,versions=await invoke(compilation_inputs,workspace,ids or None)
        payload={**payload,'source_ids':ids,'skipped_sources':skipped}
    else:
        versions=await invoke(lambda: {sid:source_version(workspace,sid) for sid in ids})
    job=await invoke(jobs.enqueue,workspace,data.get('kind'),payload,input_versions=versions)
    return {**job,'url':workspace.url('/tasks')}


@router.get('/api/jobs')
def api_jobs(status: str | None=None):
    return {'library_id':ws().library_id,'jobs':jobs.list_jobs(ws(),status)}


@router.get('/api/jobs/{job_id}')
def api_job(job_id: str):
    result=jobs.get(ws(),job_id)
    if result is None: raise HTTPException(404,'任务不存在')
    return result


@router.post('/api/jobs/{job_id}/{action}')
async def api_job_action(job_id: str,action: str,request: Request):
    if action not in {'cancel','retry'}: raise HTTPException(404)
    if action=='retry':
        from .web import read_payload
        data=await read_payload(request)
        return await invoke(jobs.retry,ws(),job_id,target_slug=data.get('target_slug'))
    return await invoke(getattr(jobs,action),ws(),job_id)


@router.get('/api/search')
async def api_search(q: str):
    from .search import search
    return await invoke(search,ws(),q)


@router.get('/api/capabilities')
def api_capabilities():
    from .search import capabilities
    from .documents import _ocr_capability
    return {'library_id':ws().library_id,'search':capabilities(ws()),'ocr':_ocr_capability()}


@router.post('/api/query')
async def api_query(request: Request):
    from .web import read_payload
    from .query import answer
    data=await read_payload(request)
    result=await invoke(answer,ws(),data.get('question',''))
    from .web import md_render
    result['answer_html']=md_render(result.get('answer_markdown',''))
    return result


@router.get('/api/coverage')
def api_coverage():
    from .compiler import coverage
    return {'library_id':ws().library_id,'questions':coverage(ws())}


@router.get('/tasks')
def tasks_page():
    from .web import layout
    kinds={'compile':'知识编译','ocr':'原文解析','extract':'概念抽取','index':'检索索引','query':'知识问答'}
    statuses={'queued':'待处理','running':'处理中','succeeded':'已完成','failed':'受阻','cancelled':'已取消'}
    rows=[]
    for job in jobs.list_jobs(ws()):
        buttons=''
        if job['status'] in {'queued','running'}: buttons=f'<button class="button" data-job-action="cancel" data-job-id="{job["id"]}">取消</button>'
        if job['status'] in {'failed','cancelled','blocked'}: buttons=f'<button class="button" data-job-action="retry" data-job-id="{job["id"]}">重试</button>'
        if job['status']=='failed':
            for target in (job.get('result') or {}).get('results',[]):
                if target.get('status')=='blocked':
                    slug=html.escape(target['slug'],quote=True)
                    buttons+=f'<button class="button" data-job-action="retry" data-job-id="{job["id"]}" data-target-slug="{slug}">重试 {slug}</button>'
        result=html.escape(json.dumps(job.get('result') or {},ensure_ascii=False,indent=2))
        rows.append(f'<section class="panel task-panel"><div class="section-heading task-heading"><h2>{html.escape(kinds.get(job["kind"],job["kind"]))} · {html.escape(statuses.get(job["status"],job["status"]))}</h2><div class="button-row">{buttons}</div></div><p class="muted">{job["id"]} · 尝试 {job["attempts"]} 次</p><p>{html.escape(job.get("error") or "")}</p><details><summary>任务结果</summary><pre>{result}</pre></details></section>')
    body='<div class="page-heading"><div><h1>处理任务</h1><p>任务绑定当前知识库，可查看结果、取消或重试。</p></div><button class="button primary" data-new-job="compile">编译当前库资料</button><button class="button" data-new-job="index">更新语义索引</button></div>'
    body+=''.join(rows) or '<p>还没有任务。</p>'
    body+='<script src="/static/v2.js" defer></script>'
    return layout('处理任务',body,'tasks')


@router.get('/ask')
def ask_page():
    from .web import layout
    return layout('知识问答','''<div class="page-heading"><div><h1>知识问答</h1><p>依据当前知识库的归档原文回答，保留来源和适用条件。</p></div></div>
    <form class="panel" id="query-form"><label for="question">知识问题</label><textarea id="question" name="question" rows="3" required maxlength="2000" placeholder="例如：规划与执行之间如何传递反馈？"></textarea><button class="button primary" type="submit">查找并回答</button></form>
    <section class="panel" id="query-result" aria-live="polite"></section><script src="/static/v2.js" defer></script>''','ask')


@router.get('/libraries')
def libraries_page():
    from .web import layout
    items=[]
    info=registry(ws().registry_root)
    for identity,item in info['libraries'].items():
        lib=resolve_workspace(identity,root=ws().registry_root)
        actions=f'<a class="button" href="{html.escape(lib.url("/"))}">浏览</a>'
        if identity!='legacy':
            actions+=f'<button class="button" data-library-action="sync" data-library="{identity}">同步来源</button><button class="button" data-library-action="evaluate" data-library="{identity}">验收检查</button><button class="button" data-library-action="activate" data-library="{identity}">切换为正式库</button>'
        elif info['active']!='legacy': actions+=f'<button class="button" data-library-action="rollback" data-library="legacy">回退到旧库</button>'
        items.append(f'<section class="panel"><h2>{html.escape(identity)} {"· 当前正式库" if identity==info["active"] else "· 对照库"}</h2><div class="button-row">{actions}</div></section>')
    body='<div class="page-heading"><div><h1>知识库对照</h1><p>查看知识变化与验收结果，再切换正式库。</p></div><button class="button primary" id="create-candidate">建立候选库</button><button class="button" id="compare-libraries">查看两库差异</button></div>'+''.join(items)+'<section class="panel"><pre id="library-result" aria-live="polite"></pre></section><script src="/static/v2.js" defer></script>'
    return layout('知识库对照',body,'libraries')
