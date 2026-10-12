import argparse
import os
import time
from pathlib import Path
from .collector import add_note, ingest
from .common import PENDING, connect, load_json
from .wiki import approve, prepare, prepare_rewrite, proposal_key, compile_and_publish, automatic_publish


def main():
    p = argparse.ArgumentParser(description="Intelligence Wiki CLI")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("ingest")
    run = sub.add_parser("loop")
    run.add_argument("--minutes", type=int, default=30)
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
    note.add_argument("--category", default="research")
    original = sub.add_parser("import-file")
    original.add_argument("--title", required=True)
    original.add_argument("--file", required=True)
    original.add_argument("--category", default="other")
    url = sub.add_parser("import-url")
    url.add_argument("url")
    url.add_argument("--title")
    url.add_argument("--category", default="other")
    decline = sub.add_parser("reject")
    decline.add_argument("proposal_id")
    sub.add_parser("stats")
    args = p.parse_args()
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
            ingest()
            if os.getenv("AUTO_DRAFT", "false").lower() == "true":
                compile_and_publish(limit=3)
            time.sleep(max(10, args.minutes) * 60)


if __name__ == "__main__":
    main()
