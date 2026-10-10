"""Indexes and graph relations derived only from the local, reviewed knowledge base."""
import json
import re
from pathlib import Path
from urllib.parse import quote

from .common import META, WIKI, connect

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "categories.json"
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")


def categories():
    """Read the curator's categories without inventing additional model labels."""
    values = json.loads(_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ValueError("Category configuration must be a list")
    result, seen = [], set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("Invalid category configuration")
        key, name = item.get("id"), item.get("name")
        if not isinstance(key, str) or not _SLUG.fullmatch(key) or key in seen:
            raise ValueError("Invalid or duplicate category ID")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Category name is required")
        enabled, description = item.get('enabled', True), item.get('description', '')
        if not isinstance(enabled, bool) or not isinstance(description, str):
            raise ValueError('Invalid category settings')
        result.append({"id": key, "name": name.strip(), "enabled": enabled, "description": description})
        seen.add(key)
    return result


def validate_category(value, active_only=False):
    entries = {c['id']: c for c in categories()}
    if not isinstance(value, str) or value not in entries:
        raise ValueError('主题不存在，请刷新页面后重试')
    if active_only and not entries[value]['enabled']:
        raise ValueError('该主题已停用，请在“主题与订阅”中启用，或选择其他主题')
    return value


def category_name(value):
    return next((c["name"] for c in categories() if c["id"] == value), "其他")


def _string_list(value):
    return sorted({v for v in value if isinstance(v, str)}) if isinstance(value, list) else []


def wiki_pages():
    """Actual Markdown pages, with approval metadata when available; index is navigation."""
    valid_categories = {c["id"] for c in categories()}
    result = []
    for path in sorted(WIKI.glob("*.md")):
        slug = path.stem
        if slug == "index" or not _SLUG.fullmatch(slug):
            continue
        body = path.read_text(encoding="utf-8")
        meta_path = META / (slug + ".json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        except (OSError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        heading = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), slug)
        title = meta.get("title")
        category = meta.get("category", "other")
        result.append({"slug": slug, "title": title if isinstance(title, str) and title.strip() else heading,
                       "body": body, "category": category if isinstance(category, str) and category in valid_categories else "other",
                       "page_kind": 'topic' if meta.get('page_kind') == 'topic' else 'concept',
                       "source_ids": _string_list(meta.get("source_ids")),
                       "related_slugs": _string_list(meta.get("related_slugs")),
                       "updated_at": meta.get("updated_at", "") if isinstance(meta.get("updated_at", ""), str) else ""})
    return result


def _wikilinks(body):
    # Code examples are not assertions of a knowledge relation.
    body = re.sub(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", "", body, flags=re.S)
    body = re.sub(r"`[^`\n]*`", "", body)
    return {m.group(1).strip() for m in re.finditer(r"(?<!!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]", body)}


def graph_data():
    """No model-inferred or dangling relations: every endpoint must exist locally."""
    pages = wiki_pages()
    db = connect()
    try:
        sources = db.execute("SELECT id,title,category,stage FROM sources ORDER BY id").fetchall()
    finally:
        db.close()
    category_labels = {c["id"]: c["name"] for c in categories()}
    nodes, edges, node_ids, edge_keys = [], [], set(), set()

    def node(key, label, kind, category, url):
        if key not in node_ids:
            nodes.append({"id": key, "label": label, "type": kind, "category": category, "url": url})
            node_ids.add(key)

    def edge(source, target, kind, label):
        key = (source, target, kind)
        if source != target and source in node_ids and target in node_ids and key not in edge_keys:
            edges.append({"source": source, "target": target, "type": kind, "label": label})
            edge_keys.add(key)

    def membership(key, category):
        if category not in category_labels:
            category = "other"
        if category in category_labels:
            target = "category:" + category
            node(target, category_labels[category], "category", category, "/?category=" + quote(category))
            edge(key, target, "category", "属于分类")

    for page in pages:
        node("wiki:" + page["slug"], page["title"], "wiki", page["category"], "/page/" + quote(page["slug"]))
    source_by_id = {s["id"]: s for s in sources}
    for source in sources:
        category = source["category"] if source["category"] in category_labels else "other"
        node("source:" + source["id"], source["title"], "source", category, "/source/" + quote(source["id"]))
    slugs = {p["slug"] for p in pages}
    for page in pages:
        key = "wiki:" + page["slug"]
        membership(key, page["category"])
        for sid in page["source_ids"]:
            if sid in source_by_id and source_by_id[sid]["stage"] == "approved":
                edge(key, "source:" + sid, "evidence", "原始证据")
        for slug in sorted(set(page["related_slugs"]) | _wikilinks(page["body"])):
            if slug in slugs:
                edge(key, "wiki:" + slug, "related", "知识链接")
    for source in sources:
        membership("source:" + source["id"], source["category"])
    return {"nodes": nodes, "edges": edges}
