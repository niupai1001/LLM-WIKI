import argparse
import os
import time
from pathlib import Path
from .collector import add_note, ingest
from .common import PENDING, connect, load_json
from .wiki import approve, prepare


def main():
    p = argparse.ArgumentParser(description="Intelligence Wiki CLI")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("ingest")
    run = sub.add_parser("loop")
    run.add_argument("--minutes", type=int, default=30)
    draft = sub.add_parser("prepare")
    draft.add_argument("--limit", type=int, default=3)
    draft.add_argument("--category")
    sub.add_parser("drafts")
    accept = sub.add_parser("approve")
    accept.add_argument("source_id")
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
    decline.add_argument("source_id")
    sub.add_parser("stats")
    args = p.parse_args()
    if args.action == "ingest":
        ingest()
    elif args.action == "prepare":
        print(f"Created {prepare(max(1, min(args.limit, 20)), category=args.category)} drafts")
    elif args.action == "approve":
        approve(args.source_id)
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
        reject(args.source_id)
    elif args.action == "drafts":
        connect().close()
        for f in sorted(PENDING.glob("*.json")):
            p = load_json(f)
            print(f"{p['source_id']}  {p['action']}  {p['slug']}  {p['title']}  ({f})")
    elif args.action == "stats":
        db = connect()
        print({r["stage"]: r["n"] for r in db.execute("SELECT stage, count(*) n FROM sources GROUP BY stage")})
        db.close()
    elif args.action == "loop":
        while True:
            ingest()
            if os.getenv("AUTO_DRAFT", "false").lower() == "true":
                prepare(limit=3)
            time.sleep(max(10, args.minutes) * 60)


if __name__ == "__main__":
    main()
