"""Version-bound indexes over reviewed paper points, without synthesizing facts.

Alignment names are navigation labels only. This adapter never consumes author
drafts or modifies readings, reviews, points, or audited concept memberships.
"""
import copy
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

from .compiler import digest
from .knowledge_synthesis import _assert_current, _digest as alignment_digest, _inputs, _validate_plan
from .publication import publication_lock, publish

POLICY = "reviewed-point-navigation-v1"
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_INDEX_LABELS = {"agent-conclusion-boundaries": "各研究的适用条件索引"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _label(value):
    """Keep label text while preventing a label from injecting Markdown links."""
    return str(value).replace("\n", " ").replace("[", "\\[").replace("]", "\\]")


def _url(ws, path):
    return ws.url(path)


def _require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Navigation requires " + name)
    return value


def _reviewed(paper):
    reviews = json.loads(paper["reviews_json"])
    if not isinstance(reviews, list) or not reviews or not isinstance(reviews[-1], dict):
        raise ValueError("Paper has no independent content review")
    latest = reviews[-1]
    if (latest.get("conclusion") != "ready" or latest.get("request_independent") is not True
            or latest.get("blocking_issues")):
        raise ValueError("Paper independent content review is not ready")
    if any((latest.get("body_review") or {}).get(k) for k in
           ("unsupported_facts", "omitted_knowledge_ids", "omitted_key_results", "material_issues")):
        raise ValueError("Paper independent content review has unresolved body issues")
    rows = latest.get("claim_reviews")
    if not isinstance(rows, list) or len({r.get("claim_id") for r in rows}) != len(rows):
        raise ValueError("Paper independent claim review is missing or duplicated")
    if ({r.get("claim_id") for r in rows} != {c["id"] for c in paper["packet"]["claims"]}
            or any(r.get("status", r.get("verdict")) != "supported" for r in rows)):
        raise ValueError("Paper independent content review does not support every claim")
    return reviews


def _bound_plan(ws, path, papers, points):
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    identity = json.loads(path.with_name("input-identity.json").read_text(encoding="utf-8"))
    if value.get("library_id") != ws.library_id or identity.get("library_id") != ws.library_id:
        raise ValueError("Navigation plan belongs to another library")
    data = identity.get("identity", {})
    if value.get("input_digest") != identity.get("input_digest") or alignment_digest(data) != identity.get("input_digest"):
        raise ValueError("Navigation plan identity is invalid")
    current_papers = {sid: {k: p[k] for k in ("source_version", "page_slug", "packet_json")}
                      for sid, p in papers.items()}
    current_points = {pid: {k: p[k] for k in ("source_id", "local_id", "source_version", "data_json")}
                      for pid, p in points.items()}
    if data.get("papers") != current_papers or data.get("points") != current_points:
        raise ValueError("Navigation plan is stale for the current paper packets or points")
    plan = _validate_plan(copy.deepcopy(value.get("plan", {})), points, [], set())
    if plan["excluded"]:
        raise ValueError("Navigation must retain every reviewed point, including explicitly excluded points")
    return plan, value["input_digest"]


def _groups(groups, concepts):
    if not isinstance(groups, list) or not groups:
        raise ValueError("Navigation requires explicit topic groups")
    assigned, slugs = set(), set()
    output = copy.deepcopy(groups)
    reserved = {"idx-" + c for c in concepts} | set(concepts) | {"index"}
    for group in output:
        slug = group.get("slug")
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug in reserved or slug in slugs:
            raise ValueError("Invalid or colliding navigation topic slug")
        slugs.add(slug)
        for key in ("title", "overview"):
            _require_text(group.get(key), "topic " + key)
        members = group.get("concept_slugs")
        if (not isinstance(members, list) or not members
                or any(not isinstance(v, str) for v in members)
                or len(members) != len(set(members)) or set(members) - set(concepts)
                or set(members) & assigned):
            raise ValueError("Unknown or multiply assigned navigation concept")
        assigned.update(members)
    if assigned != set(concepts):
        raise ValueError("Navigation topic groups omitted concepts")
    return output


def _evidence_links(ws, point, paper, parsed):
    sid, data = point["source_id"], point["data"]
    references = (data.get("evidence") or {}).get("passage_refs")
    if not isinstance(references, list) or not references:
        raise ValueError("Knowledge point has no archived paragraph references")
    paragraphs = {p["id"]: p for p in parsed["paragraphs"]}
    claims = {c["id"] for c in paper["packet"]["claims"]}
    links = []
    for ref in references:
        if ref.get("source_id") != sid or ref.get("source_version") != point["source_version"]:
            raise ValueError("Knowledge point evidence crosses a source or version boundary")
        if ref.get("claim_id") not in claims or ref.get("claim_id") not in data["claim_ids"]:
            raise ValueError("Knowledge point evidence cites an unrelated paper claim")
        locator = ref.get("locator") or {}
        paragraph = paragraphs.get(locator.get("paragraph_id"))
        if paragraph is None or locator.get("page") != paragraph.get("page"):
            raise ValueError("Knowledge point evidence paragraph is missing or has changed")
        if any(key in locator and locator[key] != paragraph.get(key) for key in ("start", "end")):
            raise ValueError("Knowledge point evidence character bounds have changed")
        query = urlencode({"page": paragraph["page"], "paragraph_id": paragraph["id"],
                           "parsed_version": digest(parsed)})
        link = "[PDF 第 " + str(paragraph["page"]) + " 页 · " + paragraph["id"] + "](" + _url(ws, "/source/" + sid + "?" + query) + ")"
        if link not in links:
            links.append(link)
    return links


def _index_body(ws, concept, papers, points, parsed):
    lines = ["# " + concept["title"], "", "**知识问题：** " + concept["question"], ""]
    sids = sorted({points[pid]["source_id"] for pid in concept["point_ids"]})
    for sid in sids:
        paper = papers[sid]
        title = paper["packet"].get("title") or paper["record"].get("title") or sid
        lines.extend(["## " + str(title), "", "[阅读论文解读](" + _url(ws, "/page/" + paper["page_slug"]) + ") · [查看归档原件](" + _url(ws, "/source/" + sid + "/original") + ")", ""])
        for pid in concept["point_ids"]:
            point = points[pid]
            if point["source_id"] != sid:
                continue
            data = point["data"]
            lines.extend(["<a id=\"" + pid + "\"></a>", "", "### 知识点 " + point["local_id"], "", data["proposition"], ""])
            for field, label in (("explanation", "机制与解释"), ("scope", "适用范围"),
                                 ("type", "知识类型"), ("transferability", "可迁移范围"),
                                 ("uncertainty", "尚未确定的内容")):
                text = data.get(field)
                if text:
                    if not isinstance(text, str):
                        raise ValueError("Knowledge point field is not text: " + field)
                    lines.extend(["**" + label + "**", "", text, ""])
            lines.extend(["**原文依据：** " + " · ".join(_evidence_links(ws, point, paper, parsed[sid])), ""])
    return "\n".join(lines).rstrip() + "\n"


def build_navigation(ws, plan_path, topic_groups):
    """Publish all index/topic files atomically; never call a model or rewrite facts.

    ``plan_path`` must have the synthesis input-identity.json beside it. Groups
    explicitly assign every plan concept once via slug/title/overview/concept_slugs.
    The returned audit distinguishes navigation coverage from audited synthesis.
    """
    with publication_lock(ws):
        papers, points, stale = _inputs(ws)
        if stale or not papers or not points:
            raise ValueError("Navigation requires current reviewed papers and points: " + _json(stale))
        plan, plan_digest = _bound_plan(ws, plan_path, papers, points)
        concepts = {c["slug"]: c for c in plan["concepts"]}
        groups = _groups(topic_groups, concepts)
        # Only the directory label changes: the source plan/question/assignments
        # remain intact, and per-paper conditions are never presented as one law.
        for slug, title in _INDEX_LABELS.items():
            if slug in concepts:
                concepts[slug]["title"] = title
        parsed, provenance = {}, {}
        for sid, paper in papers.items():
            reviews = _reviewed(paper)
            parsed[sid] = json.loads((ws.root / "parsed" / (sid + ".json")).read_text(encoding="utf-8"))
            if (parsed[sid].get("library_id") != ws.library_id or parsed[sid].get("source_id") != sid
                    or parsed[sid].get("source_version") != paper["source_version"]):
                raise ValueError("Archived paragraph snapshot is stale or belongs to another library")
            body = (ws.wiki / (paper["page_slug"] + ".md")).read_bytes()
            provenance[sid] = {"source_version": paper["source_version"], "paper_slug": paper["page_slug"],
                               "packet_digest": digest(paper["packet"]),
                               "reviews_digest": digest(reviews),
                               "body_digest": hashlib.sha256(body).hexdigest(),
                               "parsed_digest": digest(parsed[sid])}
        snapshot = {"library_id": ws.library_id, "policy": POLICY, "render_version": 2, "plan_digest": plan_digest,
                    "groups": groups, "sources": provenance,
                    "display_labels": {slug: title for slug, title in _INDEX_LABELS.items() if slug in concepts},
                    "points_digest": digest({pid: p["data"] for pid, p in points.items()})}
        snapshot_digest = digest(snapshot)
        entries, memberships = {}, []
        for original_slug, concept in concepts.items():
            slug = "idx-" + original_slug
            if not _SLUG.fullmatch(slug):
                raise ValueError("Navigation index slug exceeds the Wiki page identity limit")
            point_ids = list(concept["point_ids"])
            entries[slug] = (_index_body(ws, concept, papers, points, parsed), {
                "slug": slug, "title": concept["title"], "question": concept["question"],
                "page_kind": "concept", "page_role": "knowledge_index", "category": "agents",
                "candidate_concept_slug": original_slug, "knowledge_point_ids": point_ids,
                "source_ids": sorted({points[pid]["source_id"] for pid in point_ids}),
                "related_slugs": sorted(papers[sid]["page_slug"] for sid in {points[pid]["source_id"] for pid in point_ids}),
                "claims": [], "semantic_reviews": [], "relations": [], "aliases": [], "conflicts": []})
            memberships.extend((pid, slug, points[pid]["source_id"], points[pid]["source_version"], snapshot_digest) for pid in point_ids)
        for group in groups:
            source_ids = sorted({points[pid]["source_id"] for s in group["concept_slugs"] for pid in concepts[s]["point_ids"]})
            text = ["# " + group["title"], "", group["overview"], "", "## 知识索引", ""]
            for slug in group["concept_slugs"]:
                text.extend(["- [" + _label(concepts[slug]["title"]) + "](" + _url(ws, "/page/idx-" + slug) + ")", "  " + concepts[slug]["question"]])
            entries[group["slug"]] = ("\n".join(text) + "\n", {
                "slug": group["slug"], "title": group["title"], "overview": group["overview"],
                "page_kind": "topic", "page_role": "knowledge", "category": "agents",
                "related_slugs": ["idx-" + s for s in group["concept_slugs"]], "source_ids": source_ids,
                "knowledge_point_ids": [pid for s in group["concept_slugs"] for pid in concepts[s]["point_ids"]],
                "claims": [], "semantic_reviews": [], "relations": [], "aliases": [], "conflicts": []})
        changes = {}
        for slug, (body, metadata) in entries.items():
            bp, mp = ws.wiki / (slug + ".md"), ws.meta / (slug + ".json")
            if bp.exists() or mp.exists():
                old = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
                if old.get("navigation_policy") != POLICY or old.get("library_id") != ws.library_id:
                    raise ValueError("Navigation cannot overwrite an unmanaged existing page: " + slug)
            metadata.update(library_id=ws.library_id, navigation_policy=POLICY,
                            synthesis_status="navigation_only", independent_research_validation=False,
                            navigation_snapshot=snapshot_digest, provenance={sid: provenance[sid] for sid in metadata["source_ids"]})
            changes["wiki/" + slug + ".md"] = body
            changes["wiki_meta/" + slug + ".json"] = _json(metadata)
        # A changed grouping may remove an old managed directory page. Remove
        # only our own output; paper/concept pages are never deletion candidates.
        for mp in ws.meta.glob("*.json"):
            old = json.loads(mp.read_text(encoding="utf-8"))
            if (old.get("navigation_policy") == POLICY and old.get("library_id") == ws.library_id
                    and mp.stem not in entries):
                changes["wiki_meta/" + mp.name] = None
                changes["wiki/" + mp.stem + ".md"] = None
        titles = {p.stem: p.read_text(encoding="utf-8").splitlines()[0].removeprefix("# ")
                  for p in ws.wiki.glob("*.md") if p.stem != "index" and changes.get("wiki/" + p.name, "keep") is not None}
        titles.update({s: m["title"] for s, (_, m) in entries.items()})
        changes["wiki/index.md"] = "# LLM Wiki\n\n" + "\n".join("- [[" + s + "|" + title + "]]" for s, title in sorted(titles.items())) + "\n"
        changes["knowledge_navigation/snapshot.json"] = _json(snapshot)
        _assert_current(ws, papers, points)

        def database(db):
            db.execute("""CREATE TABLE IF NOT EXISTS navigation_memberships (
                point_id TEXT PRIMARY KEY, index_slug TEXT NOT NULL, source_id TEXT NOT NULL,
                source_version TEXT NOT NULL, snapshot_digest TEXT NOT NULL)""")
            db.execute("DELETE FROM navigation_memberships")
            db.executemany("INSERT INTO navigation_memberships VALUES (?,?,?,?,?)", memberships)

        unchanged = all((ws.root / path).exists() and (ws.root / path).read_text(encoding="utf-8") == content
                        if content is not None else not (ws.root / path).exists() for path, content in changes.items())
        if unchanged:
            with closing(sqlite3.connect(ws.db, timeout=30)) as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='navigation_memberships'").fetchone()
                unchanged = bool(exists) and set(db.execute("SELECT * FROM navigation_memberships")) == set(memberships)
        if not unchanged:
            publish(ws, changes, "navigation-" + snapshot_digest, database_callback=database)
        return {"status": "unchanged" if unchanged else "published", "library_id": ws.library_id,
                "synthesis_status": "navigation_only", "papers": len(papers), "knowledge_points": len(points),
                "knowledge_indexes": len(concepts), "topics": len(groups), "navigation_memberships": len(memberships),
                "snapshot_digest": snapshot_digest, "plan_digest": plan_digest,
                "index_slugs": ["idx-" + s for s in concepts], "topic_slugs": [g["slug"] for g in groups],
                "synthesized_concepts_published": 0, "original_readings_modified": False,
                "independent_research_validation": False}
