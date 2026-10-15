import argparse
import os
import time
from pathlib import Path
from .collector import add_note, ingest
from .common import PENDING, connect, load_json
from .wiki import approve, prepare, prepare_rewrite, proposal_key, compile_and_publish, automatic_publish


def main():
    p = argparse.ArgumentParser(description="Intelligence Wiki CLI")
    p.add_argument('--library-id', help='Explicit library; otherwise use the active library')
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("ingest")
    loop_parser = sub.add_parser("loop")
    loop_parser.add_argument("--minutes", type=int, default=30)
    draft = sub.add_parser("prepare")
    draft.add_argument("--limit", type=int, default=3)
    draft.add_argument("--category")
    draft.add_argument("--target", help="Merge evidence into this existing Wiki page")
    draft.add_argument("--source-id", help="Compile this original only")
    draft.add_argument("--revisit", action="store_true", help="Reuse an approved original for another knowledge question")
    draft.add_argument("--topic", help="Knowledge mechanism or question to explain")
    draft.add_argument("--second-pass", action="store_true", help="Request one extra expression-only edit")
    draft.add_argument("--draft-only", action="store_true", help="Development preview without publication")
    rewrite = sub.add_parser("rewrite", help="Generate an expression-only draft for an existing Wiki")
    rewrite.add_argument("slug")
    rewrite.add_argument("--second-pass", action="store_true", help="Request one extra expression-only edit")
    rewrite.add_argument("--draft-only", action="store_true", help="Development preview without publication")
    sub.add_parser("drafts")
    accept = sub.add_parser("approve")
    accept.add_argument("proposal_id")
    accept.add_argument("--confirm-facts", action="store_true", help="Legacy compatibility option; no user confirmation required")
    accept.add_argument("--fact-review-note", default="")
    note = sub.add_parser("add-note")
    note.add_argument("--title", required=True)
    note.add_argument("--file", required=True)
    note.add_argument("--category")
    original = sub.add_parser("import-file")
    original.add_argument("--title", required=True)
    original.add_argument("--file", required=True)
    original.add_argument("--category")
    url = sub.add_parser("import-url")
    url.add_argument("url")
    url.add_argument("--title")
    url.add_argument("--category")
    decline = sub.add_parser("reject")
    decline.add_argument("proposal_id")
    sub.add_parser("stats")
    compile_v2=sub.add_parser('compile',help='Queue multi-stage knowledge compilation')
    compile_v2.add_argument('--source-id',action='append',dest='source_ids')
    compile_v2.add_argument('--revisit',action='store_true')
    compile_v2.add_argument('--target',dest='target_slug',help='Update this existing page and defer unrelated questions')
    compile_v2.add_argument('--run',action='store_true',help='Run queued work in this process')
    search=sub.add_parser('search');search.add_argument('question')
    query=sub.add_parser('query');query.add_argument('question')
    queue=sub.add_parser('jobs');queue.add_argument('--retry');queue.add_argument('--cancel');queue.add_argument('--run',action='store_true');queue.add_argument('--target',dest='retry_target')
    sub.add_parser('recover')
    sub.add_parser('compare')
    sub.add_parser('evaluate')
    index=sub.add_parser('index');index.add_argument('--embeddings',action='store_true')
    library=sub.add_parser('library');library.add_argument('operation',choices=['list','init','sync','activate','rollback']);library.add_argument('id',nargs='?',default='candidate-v2')
    args = p.parse_args()
    from .workspaces import resolve_workspace,use_workspace
    workspace=resolve_workspace(args.library_id)
    with use_workspace(workspace):
        return run(args,workspace)


def run(args,workspace):
    if args.action in {'compile','search','query','jobs','recover','compare','evaluate','index','library'}:
        return v2_command(args,workspace)
    if args.action == "ingest":
        ingest()
    elif args.action == "prepare":
        options = dict(limit=max(1, min(args.limit, 20)), category=args.category, source_id=args.source_id, target_slug=args.target,
                       topic=args.topic, revisit=args.revisit, source_ids=[args.source_id] if args.revisit and args.source_id else None,
                       second_pass=args.second_pass)
        print(prepare(**options) if args.draft_only else compile_and_publish(**options))
    elif args.action == "approve":
        approve(args.proposal_id, fact_invariance_confirmed=args.confirm_facts, fact_review_note=args.fact_review_note)
    elif args.action == "rewrite":
        pid = prepare_rewrite(args.slug, second_pass=args.second_pass)
        print({'proposal_id': pid} if args.draft_only else automatic_publish(pid))
    elif args.action == "add-note":
        print("Archived note:", add_note(args.title, args.file, category=args.category))
    elif args.action == "import-file":
        from .collector import import_file
        print("Archived original:", import_file(args.title, args.file, category=args.category))
    elif args.action == "import-url":
        from .collector import import_url
        print("Archived original:", import_url(args.url, category=args.category, title=args.title))
    elif args.action == "reject":
        from .wiki import reject
        reject(args.proposal_id)
    elif args.action == "drafts":
        connect().close()
        for f in sorted(PENDING.glob("*.json")):
            p = load_json(f)
            print(f"{proposal_key(p)}  {p.get('mode', 'update')}/{p['action']}  {p['slug']}  {p['title']}  ({f})")
    elif args.action == "stats":
        db = connect()
        print({r["stage"]: r["n"] for r in db.execute("SELECT stage, count(*) n FROM sources GROUP BY stage")})
        db.close()
    elif args.action == "loop":
        while True:
            from .workspaces import resolve_workspace,use_workspace
            with use_workspace(resolve_workspace(getattr(args,'library_id',None))):
                ingest()
                if os.getenv("AUTO_DRAFT", "false").lower() == "true":
                    from .workspaces import current_workspace
                    active=current_workspace()
                    if active.library_id=='legacy': compile_and_publish(limit=3)
                    else: queue_auto_compile(active)
            time.sleep(max(10, args.minutes) * 60)


def v2_command(args,workspace):
    import json
    from . import jobs
    if args.action=='compile':
        from .compiler import compilation_inputs
        ids,skipped,versions=compilation_inputs(workspace,args.source_ids)
        result=jobs.enqueue(workspace,'compile',{'source_ids':ids,'skipped_sources':skipped,'revisit':args.revisit,'target_slug':args.target_slug},input_versions=versions)
        if args.run:
            from .worker import run_once
            while jobs.get(workspace,result['id'])['status']=='queued':
                if run_once(workspace) is None: break
            result=jobs.get(workspace,result['id'])
    elif args.action=='jobs':
        if args.retry: result=jobs.retry(workspace,args.retry,target_slug=args.retry_target)
        elif args.cancel: result=jobs.cancel(workspace,args.cancel)
        elif args.run:
            from .worker import run_once
            result=run_once(workspace)
        else: result=jobs.list_jobs(workspace)
    elif args.action=='search':
        from .search import search
        result=search(workspace,args.question)
    elif args.action=='query':
        from .query import answer
        result=answer(workspace,args.question)
    elif args.action=='index':
        from .search import rebuild_index
        result=rebuild_index(workspace,embeddings=args.embeddings)
    elif args.action=='recover':
        from .publication import recover
        result=recover(workspace)
    elif args.action=='compare':
        from .evaluation import compare_libraries
        from .workspaces import resolve_workspace
        result=compare_libraries(workspace,resolve_workspace('legacy'))
    elif args.action=='evaluate':
        from .evaluation import evaluate_library
        result=evaluate_library(workspace)
    else:
        from .workspaces import registry,initialize_candidate,synchronize_candidate,activate_library
        if args.operation=='list':result=registry()
        elif args.operation=='init':result={'library_id':initialize_candidate(args.id).library_id}
        elif args.operation=='sync':result=synchronize_candidate(args.id)
        else: result=activate_library(args.id,rollback=args.operation=='rollback')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def queue_auto_compile(workspace):
    from . import jobs
    from .documents import source_version
    from .publication import publication_lock
    with publication_lock(workspace):
        if any(j['kind']=='compile' and j['status'] in {'queued','running'} for j in jobs.list_jobs(workspace)):
            return None
        db=connect()
        try: ids=[r['id'] for r in db.execute("SELECT id FROM sources WHERE stage='new' AND content_status='full-text'")]
        finally: db.close()
        if not ids:return None
        return jobs.enqueue(workspace,'compile',{'source_ids':ids},input_versions={sid:source_version(workspace,sid) for sid in ids})


if __name__ == "__main__":
    main()
