"""Maintain topic reading routes in the same publication as their concept."""
import copy
import re

from .common import load_json, utcnow

START, END = '<!-- wiki-topic:start -->', '<!-- wiki-topic:end -->'
_SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?')


def _text(value, limit, required=True):
    if not required and value in (None, ''):
        return ''
    if not isinstance(value, str) or not 4 <= len(value.strip()) <= limit or re.search(r'[\n\r<>\[\]#]', value):
        raise ValueError('主题导航需使用简短普通文字，不能添加引用、链接或新事实记录。')
    return value.strip()


def validate_routes(values, wiki_root, meta_root, concept_slug, *, max_routes=3):
    if not isinstance(values, list) or not values or (max_routes is not None and len(values) > max_routes):
        raise ValueError('概念编写须指定1至3个主题入口，优先复用已有入口。')
    routes, seen = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('主题归属格式错误。')
        slug = value.get('slug')
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug in {'index', concept_slug} or slug in seen:
            raise ValueError('主题入口标识无效或重复。')
        path, mp = wiki_root / (slug + '.md'), meta_root / (slug + '.json')
        exists = path.exists()
        meta = load_json(mp) if mp.exists() else {}
        if exists and (meta.get('page_kind') != 'topic' or meta.get('page_role') == 'source_collection'):
            raise ValueError('主题归属不能覆盖概念、研究比较或资料清单。')
        if not exists and mp.exists():
            raise ValueError('主题入口记录不完整，需先修复。')
        routes.append({'slug': slug, 'title': _text(value.get('title'), 120, not exists),
                       'overview': _text(value.get('overview'), 800, not exists),
                       'question': _text(value.get('question'), 240)})
        seen.add(slug)
    return routes


def topic_context(wiki_root, meta_root):
    from .wiki import proposal_lock
    with proposal_lock():
        return _topic_context(wiki_root, meta_root)


def _topic_context(wiki_root, meta_root):
    from .wiki import page_body, page_version
    from .knowledge import _wikilinks
    result = []
    for mp in sorted(meta_root.glob('*.json')):
        meta = load_json(mp)
        path = wiki_root / (mp.stem + '.md')
        if meta.get('page_kind') != 'topic' or meta.get('page_role') == 'source_collection' or not path.exists():
            continue
        body = page_body(path.read_text(encoding='utf-8'))
        members = []
        for slug in sorted(_wikilinks(body)):
            target = wiki_root / (slug + '.md')
            if target.exists():
                text = page_body(target.read_text(encoding='utf-8'))
                title = load_json(meta_root / (slug + '.json')).get('title', slug) if (meta_root / (slug + '.json')).exists() else slug
                members.append({'slug': slug, 'title': title, 'opening': text[:400]})
        result.append({'slug': mp.stem, 'title': meta.get('title', mp.stem), 'overview': body[:1000],
                       'members': members, 'version': page_version(path)})
    return result


def render_navigation(overview, entries, pages, excluded=()):
    groups = {'concept': [], 'comparison': []}
    for entry in entries:
        target = pages.get(entry['slug'])
        if not target or entry['slug'] in excluded:
            continue
        label = re.sub(r'[\[\]|<>\n]', '', target['title'])
        kind = 'comparison' if target.get('page_role') == 'comparison' else 'concept'
        groups[kind].append(f"- {entry['question']} → [[{entry['slug']}|{label}]]")
    parts = [START, overview] if overview else [START]
    for key, title in [('concept', '按问题阅读'), ('comparison', '研究与实现参考')]:
        if groups[key]:
            parts.extend(['## ' + title, '\n'.join(groups[key])])
    parts.append(END)
    return '\n\n'.join(parts)


def plan_updates(proposal, wiki_root, meta_root):
    """Caller holds publication lock; plan merges and backups before writing."""
    from .wiki import page_body, page_version
    from .knowledge import _wikilinks
    if proposal.get('mode', 'update') != 'update' or proposal.get('page_kind', 'concept') != 'concept':
        return []
    topics = _topic_context(wiki_root, meta_root)
    routes = proposal.get('topic_routes')
    legacy = routes is None
    if legacy:  # Old drafts remain reviewable; update all their existing parents.
        routes = []
        for t in topics:
            if proposal['slug'] not in {m['slug'] for m in t['members']}:
                continue
            meta = load_json(meta_root / (t['slug'] + '.json'))
            question = next((e['question'] for e in meta.get('topic_navigation', {}).get('entries', [])
                             if e['slug'] == proposal['slug']), '怎样理解' + proposal['title'] + '？')
            routes.append({'slug': t['slug'], 'question': question})
        if not routes:
            return []
    routes = validate_routes(routes, wiki_root, meta_root, proposal['slug'], max_routes=None if legacy else 3)
    pages = {}
    for path in wiki_root.glob('*.md'):
        mp = meta_root / (path.stem + '.json')
        meta = load_json(mp) if mp.exists() else {}
        if meta.get('page_kind', 'concept') == 'concept':
            pages[path.stem] = {'title': meta.get('title', path.stem), 'page_role': meta.get('page_role', 'knowledge')}
    pages[proposal['slug']] = {'title': proposal['title'], 'page_role': proposal.get('page_role', 'knowledge')}
    changes = []
    for route in routes:
        slug = route['slug']; path, mp = wiki_root / (slug + '.md'), meta_root / (slug + '.json')
        old_body = path.read_text(encoding='utf-8') if path.exists() else None
        old_meta = load_json(mp) if mp.exists() else None
        meta = copy.deepcopy(old_meta or {'slug': slug, 'title': route['title'], 'category': proposal.get('category', 'other'),
                                          'page_kind': 'topic', 'source_ids': [], 'claims': [], 'conflicts': [], 'relations': []})
        navigation = copy.deepcopy(meta.get('topic_navigation', {'overview': '', 'entries': []}))
        entries = {e['slug']: e for e in navigation['entries']}
        entries[proposal['slug']] = {'slug': proposal['slug'], 'question': route['question']}
        before = page_body(old_body) if old_body else ''
        managed = re.search(re.escape(START) + r'.*?' + re.escape(END), before, re.S)
        # Preserve unmanaged user prose; legacy links remain visible outside our block.
        prefix = before[:managed.start()].rstrip() if managed else before
        suffix = before[managed.end():].strip() if managed else ''
        excluded = _wikilinks(prefix + '\n' + suffix)
        expected = proposal.get('topic_base_versions', {}).get(slug)
        overview_current = page_version(path) == expected if expected is not None else old_body is None
        if route['overview'] and overview_current:
            navigation['overview'] = route['overview']
        navigation['entries'] = list(entries.values())
        block = render_navigation(navigation['overview'], navigation['entries'], pages, excluded)
        body = '\n\n'.join(p for p in [prefix, block, suffix] if p)
        if body == before and old_meta and meta.get('topic_navigation') == navigation:
            continue
        meta.update(topic_navigation=navigation, updated_at=utcnow())
        meta['navigation_publication'] = {'by': 'system', 'proposal_id': proposal.get('proposal_id'), 'at': utcnow()}
        new_body = '# ' + meta['title'] + '\n\n' + body + '\n'
        changes.append({'slug': slug, 'path': path, 'meta_path': mp, 'body': new_body, 'metadata': meta,
                        'old_body': old_body, 'old_metadata': old_meta,
                        'old_meta_bytes': mp.read_bytes() if mp.exists() else None})
    return changes
