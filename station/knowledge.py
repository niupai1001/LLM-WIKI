"""Indexes and graph relations derived only from the local, reviewed knowledge base."""
import json
import re
from pathlib import Path
from urllib.parse import quote

from .common import META, WIKI, connect
from .relations import RELATION_LABELS, validate_relations

from .workspaces import ContextPath
_CONFIG = ContextPath('config', Path(__file__).resolve().parent.parent / 'config') / 'categories.json'
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


def default_category(preferred='other'):
    active = [c['id'] for c in categories() if c['enabled']]
    if not active:
        raise ValueError('请先启用一个采集范围')
    return preferred if preferred in active else active[0]


def category_name(value):
    return next((c["name"] for c in categories() if c["id"] == value), "其他")


def _string_list(value):
    return sorted({v for v in value if isinstance(v, str)}) if isinstance(value, list) else []


def wiki_pages():
    from .workspaces import current_workspace
    from .publication import publication_lock
    workspace=current_workspace()
    if workspace:
        with publication_lock(workspace):
            return _wiki_pages()
    return _wiki_pages()


def _wiki_pages():
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
                       "page_role": meta.get('page_role') if meta.get('page_role') in {'paper', 'comparison', 'source_collection', 'knowledge_index'} else 'knowledge',
                       "knowledge_topic": meta.get("knowledge_topic", meta.get("question", "")),
                       "synthesis_status": meta.get("synthesis_status", ""),
                       "knowledge_point_ids": _string_list(meta.get("knowledge_point_ids", meta.get("point_ids", []))),
                       "knowledge_point_count": len(_string_list(meta.get("knowledge_point_ids", meta.get("point_ids", [])))),
                       "candidate_concept_slug": meta.get("candidate_concept_slug", ""),
                       "relations": meta.get('relations', []) if isinstance(meta.get('relations'), list) else [],
                       "source_ids": _string_list(meta.get("source_ids")),
                       "related_slugs": _string_list(meta.get("related_slugs")),
                       "updated_at": meta.get("updated_at", "") if isinstance(meta.get("updated_at", ""), str) else ""})
    return result


def _wikilinks(body):
    # Code examples are not assertions of a knowledge relation.
    body = re.sub(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", "", body, flags=re.S)
    body = re.sub(r"`[^`\n]*`", "", body)
    return {m.group(1).strip() for m in re.finditer(r"(?<!!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]", body)}


def topic_details(topic, pages=None):
    """Count direct reading routes and deduplicate their originals, not citations."""
    from .wiki import page_body
    pages = pages if pages is not None else wiki_pages()
    targets = _wikilinks(page_body(topic['body'])) | set(topic.get('related_slugs', []))
    members = [p for p in pages if p['slug'] in targets and p.get('page_kind', 'concept') == 'concept'
               and p.get('page_role', 'knowledge') != 'source_collection']
    return {'concepts': [p for p in members if p.get('page_role') not in {'comparison', 'knowledge_index'}],
            'knowledge_indexes': [p for p in members if p.get('page_role') == 'knowledge_index'],
            'comparisons': [p for p in members if p.get('page_role') == 'comparison'],
            'source_ids': sorted({sid for p in members for sid in p.get('source_ids', [])})}


def semantic_backlinks(slug, pages=None):
    """Reuse published, cited comparisons on their other method's reading page.

    A bare link or shared category is not a comparison. We require a prose
    paragraph citing both the current method's originals and another source.
    This does not rewrite another file or claim independent validation.
    """
    from .wiki import page_body
    pages = pages if pages is not None else wiki_pages()
    current = next((p for p in pages if p['slug'] == slug), None)
    if current is None:
        return []
    old_ids = set(current['source_ids'])
    outgoing = _wikilinks(page_body(current['body']))
    notes = []
    for other in pages:
        if other['slug'] == slug or other['slug'] in outgoing:
            continue
        body = page_body(other['body'])
        body = re.sub(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", '', body, flags=re.S)
        for paragraph in re.split(r'\n\s*\n', body):
            cited = set(re.findall(r'/source/([a-f0-9]{16})', paragraph))
            if (slug not in _wikilinks(paragraph) or not 40 <= len(paragraph) <= 1800
                    or not cited & old_ids or not cited - old_ids
                    or not cited <= set(other['source_ids']) or paragraph.lstrip().startswith(('#', '- ', '* '))):
                continue
            def remove_self(match):
                target, _, label = match[1].partition('|')
                return label or current['title'] if target.strip() == slug else match[0]
            paragraph = re.sub(r'\[\[([^\]\n]+)\]\]', remove_self, paragraph)
            notes.append({'slug': other['slug'], 'title': other['title'], 'body': paragraph, 'source_ids': sorted(cited)})
            break
        if len(notes) == 2:
            break
    return notes


def graph_data():
    from .workspaces import current_workspace
    workspace=current_workspace()
    if workspace:
        from .publication import publication_lock
        with publication_lock(workspace): return _graph_data()
    return _graph_data()


def _graph_data():
    """No model-inferred or dangling relations: every endpoint must exist locally."""
    pages = [p for p in wiki_pages() if p.get('page_role') != 'source_collection']
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

    def edge(source, target, kind, label, **details):
        key = (source, target, kind)
        if source != target and source in node_ids and target in node_ids and key not in edge_keys:
            edges.append({"source": source, "target": target, "type": kind, "label": label, **details})
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
            if page['page_kind'] != 'topic' and sid in source_by_id and source_by_id[sid]["stage"] == "approved":
                edge(key, "source:" + sid, "evidence", "原始证据")
        typed_targets = set()
        meta_path = META / (page['slug'] + '.json')
        try:
            meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
            relations = validate_relations(page.get('relations', []), meta.get('claims', []),
                                           set(page['source_ids']), slugs, page['slug'])
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            relations = []
        for relation in relations:
            target = relation['target_slug']
            typed_targets.add(target)
            label, reverse = RELATION_LABELS[relation['kind']]
            basis = relation.get('basis', 'factual')
            if basis == 'editorial':
                label += ' · 编辑关联'
            edge(key, 'wiki:' + target, relation['kind'], label, reverse_label=reverse,
                 description=relation['summary'] + ' 适用范围：' + relation['scope'],
                 source_ids=relation['source_ids'], claim_ids=relation['claim_ids'], basis=basis)
        for slug in sorted((set(page["related_slugs"]) | _wikilinks(page["body"])) - typed_targets):
            if slug in slugs:
                edge(key, "wiki:" + slug, "related", "知识链接")
    for source in sources:
        membership("source:" + source["id"], source["category"])
    return {"nodes": nodes, "edges": edges}
