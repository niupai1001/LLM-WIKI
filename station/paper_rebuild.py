"""Publish paper readings and independently reviewed cross-paper concepts."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from .common import load_json, utcnow
from .compiler import digest
from .documents import parse_source, source_version, _save
from .paper_policy import exclusion_reason, selected_sources
from .publication import publication_lock, publish
from .workspaces import use_workspace
from .worker import check_cancelled, report_progress
from . import wiki


def _schema(db):
    for statement in (
        'CREATE TABLE IF NOT EXISTS paper_distillations (source_id TEXT PRIMARY KEY,source_version TEXT,page_slug TEXT,packet_json TEXT,reviews_json TEXT,published_at TEXT)',
        'CREATE TABLE IF NOT EXISTS knowledge_points (id TEXT PRIMARY KEY,source_id TEXT,local_id TEXT,source_version TEXT,data_json TEXT)',
        'CREATE TABLE IF NOT EXISTS concept_memberships (concept_slug TEXT,point_id TEXT,reason TEXT,PRIMARY KEY(concept_slug,point_id))',
        'CREATE TABLE IF NOT EXISTS knowledge_point_changes (point_id TEXT,old_json TEXT,new_json TEXT,reason TEXT,at TEXT)',
    ):
        db.execute(statement)
    from .knowledge_maintenance import schema
    schema(db)


def initialize(ws):
    with publication_lock(ws), sqlite3.connect(ws.db) as db:
        _schema(db)


def global_point_id(source_id, local_id):
    return 'kp_' + hashlib.sha256((source_id + ':' + local_id).encode()).hexdigest()[:20]


def _records(ws, claims, source_ids):
    parsed = {sid: parse_source(ws, sid) for sid in source_ids}
    normalized = copy.deepcopy(claims)
    for claim in normalized:
        if claim.get('status') == 'supported':
            claim['status'] = '单源支持' if len(claim.get('source_ids', [])) == 1 else '多源一致'
        for passage in claim.get('evidence_passages', []):
            loc = passage.get('locator')
            if isinstance(loc, dict):
                passage['locator'] = '第' + str(loc.get('page', '?')) + '页 · ' + str(loc.get('paragraph_id', ''))
                passage.update({k: loc[k] for k in ('page', 'page_label', 'paragraph_id', 'start', 'end') if k in loc})
    records = wiki.validate_evidence_records({'claims': normalized, 'conflicts': []}, set(source_ids), parsed_evidence=parsed)
    wiki._require_quoted_changes({}, records['claims'], 'concept')
    originals = {c['id']: c for c in normalized}
    for claim in records['claims']:
        for passage, original in zip(claim.get('evidence_passages', []), originals[claim['id']].get('evidence_passages', [])):
            sid = passage['source_id']
            matches = [p for p in parsed[sid]['paragraphs'] if ' '.join(passage['quote'].split()) in ' '.join(p['text'].split())]
            if not matches:
                raise ValueError('Evidence does not locate to an archived paragraph')
            loc = next((p for p in matches if p['id'] == original.get('paragraph_id')), matches[0])
            passage.update(page=loc['page'], page_label=loc.get('page_label', str(loc['page'])), paragraph_id=loc['id'], source_version=parsed[sid]['source_version'])
    return records['claims'], parsed


def _review_records(reviews, claims, parsed, body):
    if not reviews:
        raise ValueError('Independent source review is required')
    latest = reviews[-1]
    values = latest.get('claim_reviews', latest.get('reviews', []))
    by_id = {v.get('claim_id'): v for v in values}
    if set(by_id) != {c['id'] for c in claims} or len(values) != len(claims):
        raise ValueError('Independent review must cover every published claim')
    if latest.get('blocking_issues') or latest.get('body_issues') or latest.get('old_knowledge_issues'):
        raise ValueError('Independent review has unresolved issues')
    body_review = latest.get('body_review', {})
    if any(body_review.get(k) for k in ('unsupported_facts', 'omitted_knowledge_ids', 'omitted_key_results', 'material_issues')):
        raise ValueError('Full-body review has unresolved issues')
    rows = []
    for claim in claims:
        value = by_id[claim['id']]
        if value.get('status', value.get('verdict')) != 'supported' or not value.get('reason'):
            raise ValueError('Only supported claims can publish')
        rows.append({'claim_id': claim['id'], 'verdict': 'supported', 'reason': value['reason'], 'source_ids': claim['source_ids']})
    return [{'at': utcnow(), 'by': 'agent', 'reviewer_type': 'agent', 'reviews': rows,
             'body_issues': [], 'old_knowledge_issues': [], 'independent_research_validation': False,
             'body_digest': digest(body), 'original_versions': {sid: digest(p) for sid, p in parsed.items()}}]


def _index_body(ws, title, slug, navigation=()):
    titles = {p.stem: p.read_text().splitlines()[0].removeprefix('# ') for p in ws.wiki.glob('*.md') if p.stem != 'index'}
    titles[slug] = title
    titles.update({c['slug']: c['metadata']['title'] for c in navigation})
    return '# LLM Wiki\n\n' + '\n'.join('- [[' + s + '|' + t + ']]' for s, t in sorted(titles.items())) + '\n'


def publish_packet(ws, packet, reviews, job_id=None, slug=None):
    from .distillation import _skill, SCHEMA_VERSION
    sid = packet['source'].get('source_id', packet['source'].get('id'))
    slug = slug or 'paper-' + sid
    if wiki.safe_slug(slug) != slug:
        raise ValueError('Invalid paper page identity')
    body = wiki.page_body(packet['body_markdown'])
    if wiki.citation_ids(body) != {sid}:
        raise ValueError('A paper reading must cite its archived original')
    with use_workspace(ws), publication_lock(ws):
        check_cancelled()
        record = load_json(ws.raw / (sid + '.json'))
        if exclusion_reason(ws, record):
            raise ValueError(exclusion_reason(ws, record))
        if source_version(ws, sid) != packet['source']['source_version']:
            raise ValueError('Paper original changed after distillation')
        claims, parsed = _records(ws, packet['claims'], [sid])
        semantic = _review_records(reviews, claims, parsed, body)
        meta = {'slug': slug, 'title': packet.get('title', record['title']), 'category': record.get('category', 'agents'),
                'page_kind': 'concept', 'page_role': 'paper', 'source_ids': [sid], 'claims': claims,
                'conflicts': [], 'relations': [], 'related_slugs': [], 'aliases': [record['title']],
                'semantic_reviews': semantic, 'distillation': {k: v for k, v in packet.items() if k != 'body_markdown'},
                'distillation_version': packet.get('schema_version', 'knowledge-distillation/v2.2'),
                'distillation_skill_hash': _skill()[1] if packet.get('schema_version') == SCHEMA_VERSION else None,
                'updated_at': utcnow(),
                'publication': {'by': 'system', 'policy': 'independent-reviewed', 'at': utcnow()}}
        point_ids = [global_point_id(sid, p['id']) for p in packet['knowledge_points']]
        if len(set(point_ids)) != len(point_ids):
            raise ValueError('Repeated knowledge point IDs')
        meta['knowledge_point_ids'] = point_ids
        proposal_id = 'paper-' + sid + '-' + digest(packet)[:16]
        proposal = {**meta, 'body_markdown': body, 'job_id': job_id, 'outcome': 'approved'}
        published = wiki._publication_body(proposal, [sid])
        changes = {f'wiki/{slug}.md': published, f'wiki_meta/{slug}.json': json.dumps(meta, ensure_ascii=False, indent=2),
                   'wiki/index.md': _index_body(ws, meta['title'], slug),
                   f'proposal_history/{proposal_id}.json': json.dumps(proposal, ensure_ascii=False, indent=2)}
        # The skill produces reader text, structured knowledge and a separate
        # audit. Wiki publication is an adapter outside that skill's remit.
        artifact = {key: copy.deepcopy(packet[key]) for key in
                    ('source', 'purpose', 'motivation', 'central_answer', 'results',
                     'knowledge_points', 'source_notes', 'claims') if key in packet}
        artifact['schema_version'] = packet.get('schema_version')
        changes.update({
            f'readings/{sid}/reading.md': body,
            f'readings/{sid}/knowledge.json': json.dumps(artifact, ensure_ascii=False, indent=2),
            f'readings/{sid}/review.md': '# 独立核查记录\n\n来源版本：' + packet['source']['source_version']
                + '\n\n结论：ready（独立请求核查；未经人工验收）\n\n```json\n'
                + json.dumps(reviews, ensure_ascii=False, indent=2) + '\n```\n',
        })

        def database(db):
            _schema(db)
            if not db.execute('SELECT 1 FROM sources WHERE id=?', (sid,)).fetchone():
                raise ValueError('Archived paper was removed')
            db.execute('INSERT OR REPLACE INTO paper_distillations VALUES (?,?,?,?,?,?)',
                       (sid, packet['source']['source_version'], slug, json.dumps(packet, ensure_ascii=False), json.dumps(reviews, ensure_ascii=False), utcnow()))
            old = dict(db.execute('SELECT id,data_json FROM knowledge_points WHERE source_id=?', (sid,)))
            old_versions = dict(db.execute('SELECT id,source_version FROM knowledge_points WHERE source_id=?', (sid,)))
            removed = set(old) - set(point_ids)
            if removed:
                from .knowledge_maintenance import schema
                schema(db)
                for identifier in removed:
                    db.execute('INSERT INTO knowledge_point_changes VALUES (?,?,?,?,?)',
                               (identifier, old[identifier], None, 'Source re-distillation removed point; concept knowledge requires review', utcnow()))
                    db.execute("UPDATE concept_memberships SET status='pending_review' WHERE point_id=?", (identifier,))
            db.execute('DELETE FROM knowledge_points WHERE source_id=?', (sid,))
            for point in packet['knowledge_points']:
                identifier = global_point_id(sid, point['id'])
                value = json.dumps(point, ensure_ascii=False, sort_keys=True)
                if identifier in old and (old[identifier] != value or old_versions[identifier] != packet['source']['source_version']):
                    from .knowledge_maintenance import schema
                    schema(db)
                    db.execute("UPDATE concept_memberships SET status='pending_review' WHERE point_id=?", (identifier,))
                if identifier in old and old[identifier] != value:
                    db.execute('INSERT INTO knowledge_point_changes VALUES (?,?,?,?,?)', (identifier, old[identifier], value, 'Source understanding recompiled; old and new values retained', utcnow()))
                db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)', (identifier, sid, point['id'], packet['source']['source_version'], value))
            db.execute("UPDATE sources SET stage='approved',claim_at=NULL WHERE id=?", (sid,))
        publish(ws, changes, proposal_id, database_callback=database)
    return {'source_id': sid, 'slug': slug, 'status': 'published', 'knowledge_points': len(point_ids)}


def _check_concept_base(ws, proposal):
    for slug, expected in proposal.get('dependency_base_versions', {}).items():
        if wiki.page_version(ws.wiki / (slug + '.md')) != expected:
            raise ValueError('Dependent concept changed after maintenance decision')
    if proposal.get('dependency_membership_versions'):
        from .knowledge_maintenance import membership_version
        with sqlite3.connect(ws.db, timeout=30) as snapshot:
            for slug, expected in proposal['dependency_membership_versions'].items():
                if membership_version(snapshot, slug) != expected:
                    raise ValueError('Dependent concept memberships changed after maintenance decision')
    if 'base_version' in proposal and proposal['base_version'] is None and (ws.meta / (proposal['slug'] + '.json')).exists():
        raise ValueError('New concept target metadata already exists')
    if 'base_version' in proposal and wiki.page_version(ws.wiki / (proposal['slug'] + '.md')) != proposal['base_version']:
        raise ValueError('Concept body or metadata changed after maintenance decision')
    if proposal.get('maintenance_decision_id') and 'base_version' not in proposal:
        raise ValueError('Maintenance publication requires a concept base version')


def _check_membership_base(db, proposal):
    if 'base_membership_version' in proposal:
        from .knowledge_maintenance import membership_version
        if membership_version(db, proposal['slug']) != proposal['base_membership_version']:
            raise ValueError('Concept memberships changed after maintenance decision')


def _check_point_versions(db, snapshots):
    for identifier, expected in (snapshots or {}).items():
        row = db.execute('SELECT source_id,source_version,data_json FROM knowledge_points WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Assigned knowledge point disappeared')
        value = json.loads(row[2])
        hashed = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if expected != {'source_id': row[0], 'source_version': row[1], 'data_digest': hashed}:
            raise ValueError('Assigned knowledge point content or source version changed')


def publish_concept(ws, proposal, point_ids, job_id=None, *, prepare_only=False, known_slugs=None):
    from .topic_navigation import plan_updates
    with use_workspace(ws), publication_lock(ws):
        check_cancelled()
        slug = proposal['slug']
        if wiki.safe_slug(slug) != slug or slug == 'index':
            raise ValueError('Invalid concept identity')
        _check_concept_base(ws, proposal)
        with sqlite3.connect(ws.db) as snapshot_db:
            _check_membership_base(snapshot_db, proposal)
        versions = proposal.get('input_versions', proposal.get('source_versions', {}))
        ids = proposal['source_ids']
        if not versions or set(ids) != set(versions) or any(source_version(ws, sid) != v for sid, v in versions.items()):
            raise ValueError('Concept input versions changed')
        body = wiki.page_body(proposal['body_markdown'])
        if not wiki.citation_ids(body) <= set(ids):
            raise ValueError('Concept cites unknown original')
        claims, parsed = _records(ws, proposal['claims'], ids)
        semantic = _review_records(proposal['semantic_reviews'], claims, parsed, body)
        claim_ids = {c['id'] for c in claims}
        from .body_bindings import validate_body_locations
        bindings = [validate_body_locations(b, body) for b in proposal.get('point_bindings', [])]
        claim_bindings = [validate_body_locations(b, body) for b in proposal.get('claim_bindings', [])]
        required = proposal.get('required_point_ids', point_ids)
        if not set(required) <= set(point_ids):
            raise ValueError('Required knowledge points must be assigned')
        if {b.get('point_id') for b in bindings} != set(required) or len(bindings) != len(set(required)):
            raise ValueError('Concept omitted assigned knowledge points')
        for binding in bindings:
            if not binding.get('body_quote') or binding['body_quote'] not in body or not set(binding.get('claim_ids', [])) <= claim_ids:
                raise ValueError('Concept point is not present in body with valid claims')
        for binding in claim_bindings:
            if binding.get('claim_id') not in claim_ids or not binding.get('body_quote') or binding['body_quote'] not in body:
                raise ValueError('Concept claim is not present in body')
        if len(claim_bindings) != len(claim_ids) or {b.get('claim_id') for b in claim_bindings} != claim_ids:
            raise ValueError('Concept omitted claim bindings')
        from .relations import validate_relations
        targets = set(known_slugs or ()) | {p.stem for p in ws.wiki.glob('*.md')}
        relations = validate_relations(proposal.get('relations', []), claims, ids, targets, slug)
        from .knowledge_synthesis import _body_targets
        if proposal.get('maintenance_decision_id') and not _body_targets(body) <= targets:
            raise ValueError('Concept links to an unknown Wiki target')
        old_path = ws.meta / (slug + '.json')
        old = load_json(old_path) if old_path.exists() else {}
        if proposal.get('maintenance_decision_id'):
            decision = proposal.get('maintenance_decision', {})
            from .relations import require_approved_relations
            require_approved_relations(relations, decision.get('relation_actions', []), old.get('relations', []), slug)
            assigned = {row['point_id'] for row in decision.get('point_decisions', [])
                        if row.get('disposition') == 'linked' and any(m.get('concept_slug') == slug for m in row.get('memberships', []))}
            with sqlite3.connect(ws.db) as snapshot_db:
                _schema(snapshot_db)
                columns = {row[1] for row in snapshot_db.execute('PRAGMA table_info(concept_memberships)')}
                if 'status' in columns:
                    memberships = list(snapshot_db.execute('SELECT point_id,status FROM concept_memberships WHERE concept_slug=?', (slug,)))
                else:
                    memberships = [(row[0], 'active') for row in snapshot_db.execute('SELECT point_id FROM concept_memberships WHERE concept_slug=?', (slug,))]
            preserved = set(old.get('knowledge_point_ids', [])) | set(old.get('point_ids', [])) | {pid for pid, status in memberships if status in {'active', 'pending_review'}}
            if any(status == 'pending_review' and pid not in assigned for pid, status in memberships):
                raise ValueError('Pending-review concept membership requires a new maintenance decision')
            if set(point_ids) != preserved | assigned:
                raise ValueError('Concept knowledge point metadata must match the verified maintenance decision')
        if (not prepare_only and not proposal.get('maintenance_decision_id') and old.get('synthesis_input_digest') == proposal.get('input_digest')
                and proposal.get('input_digest') and old.get('knowledge_point_ids') == point_ids
                and (ws.wiki / (slug + '.md')).is_file()
                and wiki.page_body((ws.wiki / (slug + '.md')).read_text()) == body):
            return {'slug': slug, 'status': 'published', 'point_ids': point_ids, 'reused': True}
        if old and old.get('page_role') == 'paper':
            raise ValueError('Concept cannot overwrite a paper reading')
        wiki._require_claims_preserved(old, claims)
        wiki._require_quoted_changes(old, claims, 'concept')
        claim_changes = proposal.get('claim_changes', [])
        for claim in claims:
            previous = next((c for c in old.get('claims', []) if c.get('id') == claim['id']), None)
            if previous and any(previous.get(k) != claim.get(k) for k in ('text', 'scope')):
                change = next((c for c in claim_changes if c.get('claim_id') == claim['id']), None)
                if (not change or not change.get('reason') or
                        any(change.get('old', {}).get(k) != previous.get(k) or change.get('new', {}).get(k) != claim.get(k)
                            for k in ('text', 'scope'))):
                    raise ValueError('Changed concept claim requires old/new values and reason')
        proposal_id = 'concept-' + slug + '-' + digest(body)[:12]
        meta = {**old, 'slug': slug, 'title': proposal['title'], 'category': 'agents', 'page_kind': 'concept',
                'page_role': 'knowledge', 'source_ids': ids, 'claims': claims, 'conflicts': proposal.get('conflicts', []),
                'knowledge_topic': proposal.get('knowledge_topic', ''), 'pipeline_version': 'paper-concepts-v1',
                'relations': relations, 'related_slugs': proposal.get('related_slugs', []),
                'aliases': proposal.get('aliases', []), 'knowledge_point_ids': point_ids,
                'point_bindings': bindings, 'claim_bindings': proposal['claim_bindings'], 'semantic_reviews': semantic,
                'synthesis_input_digest': proposal.get('input_digest'), 'updated_at': utcnow(),
                'publication': {'by': 'system', 'policy': 'independent-reviewed', 'at': utcnow()}}
        meta.update(required_point_ids=list(required), maintenance_decision_id=proposal.get('maintenance_decision_id'),
                    presentation_policy=proposal.get('presentation_policy', old.get('presentation_policy')),
                    claim_changes=claim_changes, synthesis_reviews=proposal.get('synthesis_reviews', []),
                    point_review_history=copy.deepcopy(proposal.get('semantic_reviews', [])))
        if claim_changes:
            meta['claim_change_history'] = list(old.get('claim_change_history', [])) + [
                {'at': utcnow(), 'changes': copy.deepcopy(claim_changes), 'claims': copy.deepcopy(claims),
                 'reviews': copy.deepcopy(proposal.get('semantic_reviews', []))}]
        navigation = plan_updates({**proposal, 'mode': 'update', 'page_kind': 'concept', 'page_role': 'knowledge', 'category': 'agents', 'proposal_id': proposal_id}, ws.wiki, ws.meta)
        changes = {f'wiki/{slug}.md': wiki._publication_body({**proposal, **meta}, ids),
                   f'wiki_meta/{slug}.json': json.dumps(meta, ensure_ascii=False, indent=2),
                   'wiki/index.md': _index_body(ws, meta['title'], slug, navigation),
                   f'proposal_history/{proposal_id}.json': json.dumps({**proposal, 'job_id': job_id, 'outcome': 'approved'}, ensure_ascii=False, indent=2)}
        for change in navigation:
            changes[str(change['path'].relative_to(ws.root))] = change['body']
            changes[str(change['meta_path'].relative_to(ws.root))] = json.dumps(change['metadata'], ensure_ascii=False, indent=2)

        def database(db, apply_maintenance=True):
            _schema(db)
            marks = ','.join('?' for _ in point_ids)
            found = {row[0] for row in db.execute('SELECT id FROM knowledge_points WHERE id IN (' + marks + ')', point_ids)} if point_ids else set()
            if found != set(point_ids):
                raise ValueError('Assigned knowledge points changed')
            _check_concept_base(ws, proposal)
            _check_membership_base(db, proposal)
            if any(source_version(ws, sid) != version for sid, version in versions.items()):
                raise ValueError('Concept input versions changed')
            _check_point_versions(db, proposal.get('point_versions'))
            if proposal.get('maintenance_decision_id'):
                if not set(point_ids) <= set(proposal.get('point_versions', {})):
                    raise ValueError('Maintenance publication requires knowledge point snapshots')
                from .knowledge_maintenance import apply_decision
                if apply_maintenance:
                    apply_decision(db, proposal['maintenance_decision_id'], proposal['maintenance_decision'],
                                   proposal['point_versions'], concept_slugs=[slug] + proposal.get('additional_no_change_slugs', []))
            else:
                db.execute('DELETE FROM concept_memberships WHERE concept_slug=?', (slug,))
                for binding in bindings:
                    db.execute('INSERT INTO concept_memberships(concept_slug,point_id,reason) VALUES (?,?,?)',
                               (slug, binding['point_id'], binding.get('reason', binding['body_quote'])))
        if prepare_only:
            return {'changes': changes, 'database_callback': database, 'slug': slug, 'point_ids': point_ids}
        publish(ws, changes, proposal_id, database_callback=database)
    return {'slug': slug, 'status': 'published', 'point_ids': point_ids, 'topics_updated': [n['slug'] for n in navigation]}


def publish_concepts(ws, proposals, job_id=None, *, decision_id=None, decision=None, point_versions=None):
    """Prepare a connected concept group and commit pages, navigation and decision once."""
    from .topic_navigation import plan_updates
    proposals = copy.deepcopy(list(proposals))
    if not proposals:
        return []
    slugs = [p['slug'] for p in proposals]
    if len(set(slugs)) != len(slugs):
        raise ValueError('Duplicate concept in publication group')
    for proposal in proposals:
        if 'base_version' not in proposal:
            raise ValueError('Grouped publication requires concept base versions')
        if decision_id is not None:
            proposal.update(maintenance_decision_id=decision_id, maintenance_decision=decision, point_versions=point_versions)
    with use_workspace(ws), publication_lock(ws):
        prepared = [publish_concept(ws, p, p['point_ids'], job_id, prepare_only=True, known_slugs=slugs) for p in proposals]
        changes = {}
        # Replay navigation against an isolated overlay containing all group pages.
        # Each later route sees prior entries, so shared topics never lose members.
        with tempfile.TemporaryDirectory(prefix='wiki-navigation-') as temporary:
            root = Path(temporary)
            staged_wiki, staged_meta = root / 'wiki', root / 'wiki_meta'
            staged_wiki.mkdir(); staged_meta.mkdir()
            for source, target, extension in ((ws.wiki, staged_wiki, '*.md'), (ws.meta, staged_meta, '*.json')):
                for path in source.glob(extension):
                    (target / path.name).write_bytes(path.read_bytes())
            for proposal, item in zip(proposals, prepared):
                slug = proposal['slug']
                for relative, content in item['changes'].items():
                    if relative in {f'wiki/{slug}.md', f'wiki_meta/{slug}.json'} or relative.startswith('proposal_history/'):
                        changes[relative] = content
                (staged_wiki / (slug + '.md')).write_text(changes[f'wiki/{slug}.md'])
                (staged_meta / (slug + '.json')).write_text(changes[f'wiki_meta/{slug}.json'])
            topics = {}
            for proposal in proposals:
                navigation = plan_updates({**proposal, 'mode': 'update', 'page_kind': 'concept', 'page_role': 'knowledge'}, staged_wiki, staged_meta)
                for change in navigation:
                    topics[change['slug']] = change
                    change['path'].write_text(change['body'])
                    change['meta_path'].write_text(json.dumps(change['metadata'], ensure_ascii=False, indent=2))
            for slug, change in topics.items():
                if slug in slugs:
                    raise ValueError('A group concept cannot also be its topic navigation')
                changes[f'wiki/{slug}.md'] = change['body']
                changes[f'wiki_meta/{slug}.json'] = json.dumps(change['metadata'], ensure_ascii=False, indent=2)
            titles = {p.stem: load_json(staged_meta / (p.stem + '.json')).get('title', p.stem)
                      if (staged_meta / (p.stem + '.json')).exists() else p.stem
                      for p in staged_wiki.glob('*.md') if p.stem != 'index'}
            changes['wiki/index.md'] = '# LLM Wiki\n\n' + '\n'.join('- [[' + slug + '|' + title + ']]' for slug, title in sorted(titles.items())) + '\n'

        def database(db):
            for item in prepared:
                item['database_callback'](db, apply_maintenance=False)
            groups = {}
            for proposal in proposals:
                identifier = proposal.get('maintenance_decision_id')
                if identifier:
                    group = groups.setdefault(identifier, {'proposal': proposal, 'slugs': []})
                    group['slugs'].append(proposal['slug'])
                    group['slugs'].extend(proposal.get('additional_no_change_slugs', []))
            if groups:
                from .knowledge_maintenance import apply_decision
                for identifier, group in groups.items():
                    proposal = group['proposal']
                    apply_decision(db, identifier, proposal['maintenance_decision'], proposal['point_versions'], concept_slugs=group['slugs'])
        publication_id = 'concept-group-' + digest(proposals)[:20]
        publish(ws, changes, publication_id, database_callback=database)
    return [{'slug': p['slug'], 'status': 'published', 'point_ids': p['point_ids']} for p in proposals]


def compile_job(ws, payload, job_id, model=None):
    from .distillation import distill_source, revalidate_cached_structure, resume_long_source, resume_provider_context
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextvars import copy_context
    def check_skill():
        expected = payload.get('skill_hash')
        if expected:
            from .distillation import _skill
            if _skill()[1] != expected:
                raise ValueError('The selected SKILL changed during this preview; stop and create a new version-bound task')
    check_skill()
    initialize(ws)
    ids, excluded, _ = selected_sources(ws, payload.get('source_ids'))
    page_slugs = payload.get('paper_slugs', {})
    if not isinstance(page_slugs, dict) or set(page_slugs) - set(ids):
        raise ValueError('Paper page mapping must stay within the selected source scope')
    if any(not isinstance(slug, str) or wiki.safe_slug(slug) != slug for slug in page_slugs.values()):
        raise ValueError('Invalid paper page identity')
    if len(set(page_slugs.values())) != len(page_slugs):
        raise ValueError('Two papers cannot share a reading page identity')
    path = ws.root / 'runs' / job_id / 'paper-rebuild.json'
    retry_target = payload.get('retry_target')
    previous = load_json(path) if retry_target and path.is_file() else {}
    previous_rows = {row['source_id']: row for row in previous.get('results', []) if row.get('source_id')}
    retry_source, retry_concept = None, None
    if retry_target:
        from .jobs import compilation_targets
        target = next((row for row in compilation_targets(previous)
                       if row['slug'] == retry_target and row.get('status') == 'blocked'), None)
        if target is None:
            raise ValueError('Selected retry target has no persisted blocked compilation result')
        retry_source = target.get('source_id')
        retry_concept = retry_target if retry_source is None else None
    results, pending = [], []
    for sid in ids:
        check_cancelled()
        if retry_target and sid != retry_source:
            prior = previous_rows.get(sid)
            if prior is None:
                raise ValueError('Selected retry requires complete persisted source outcomes')
            results.append({**prior, 'attempted': False})
            continue
        with publication_lock(ws), sqlite3.connect(ws.db) as db:
            row = db.execute('SELECT source_version,page_slug FROM paper_distillations WHERE source_id=?', (sid,)).fetchone()
            expected_skill = payload.get('skill_hash')
            matching_skill = not expected_skill or row and (ws.meta / (row[1] + '.json')).is_file() and load_json(
                ws.meta / (row[1] + '.json')).get('distillation_skill_hash') == expected_skill
        if row and matching_skill and row[0] == source_version(ws, sid) and (ws.wiki / (row[1] + '.md')).is_file() and not payload.get('force'):
            results.append({'source_id': sid, 'slug': row[1], 'status': 'published', 'reused': True})
        else:
            pending.append(sid)

    def process(sid):
        try:
            check_skill()
            result = distill_source(ws, sid, model=model, force=payload.get('force', False))
            if result.get('reasons') in (['Paper body must cite only its own archived source'],
                                         ['Empirical knowledge point requires a supporting result']):
                result = revalidate_cached_structure(ws, sid, model=model)
            elif (result.get('reasons') == ['Whole-source reading completed, but synthesis exceeds its explicit context limit; no material was silently truncated']
                  or result.get('long_source_recovery', {}).get('status') in {'blocked', 'selection_blocked'}
                  and result.get('long_source_recovery', {}).get('new_author_requests') == 0):
                expanded = resume_provider_context(ws, sid, model=model)
                result = resume_long_source(ws, sid, model=model) if expanded.get('fallback_allowed') else expanded
            if result['status'] != 'ready':
                return {'source_id': sid, 'slug': 'paper-' + sid, 'status': 'blocked', 'reasons': result.get('reasons', [])}
            check_skill()
            if sid in page_slugs:
                return publish_packet(ws, result['packet'], result['reviews'], job_id, slug=page_slugs[sid])
            return publish_packet(ws, result['packet'], result['reviews'], job_id)
        except (ValueError, OSError) as error:
            from .jobs import JobConflict
            if isinstance(error, JobConflict):
                raise
            reason = str(error)[:1000] if isinstance(error, ValueError) else type(error).__name__
            return {'source_id': sid, 'slug': 'paper-' + sid, 'status': 'blocked', 'reasons': [reason]}

    # One durable compilation, at most two simultaneous provider requests.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='paper-reading') as pool:
        futures = [pool.submit(copy_context().run, process, sid) for sid in pending]
        for future in as_completed(futures):
            check_cancelled()
            results.append(future.result())
            report_progress('paper-rebuild', {'completed': len(results), 'total': len(ids), 'published': sum(r['status'] == 'published' for r in results), 'last_result': results[-1]})
            if len(results) % 5 == 0:
                from .search import rebuild_index
                rebuild_index(ws, embeddings=False)
    synthesis = None
    if payload.get('synthesize', True):
        check_skill()
        from .knowledge_synthesis import synthesize_library
        selected_concept = retry_concept or payload.get('target_slug')
        options = {'target_slug': selected_concept} if selected_concept else {}
        synthesis = synthesize_library(ws, model=model, job_id=job_id, **options)
        if selected_concept and not retry_concept:
            synthesis['deferred_concepts'] = [
                {'slug': target['slug'], 'reason': 'An explicit concept target was selected for this compilation'}
                for target in synthesis.get('plan', {}).get('concepts', []) if target['slug'] != selected_concept]
        if retry_concept:
            old = previous.get('synthesis') or {}
            current = {row['slug']: row for row in synthesis.get('results', [])}
            if old.get('input_digest') == synthesis.get('input_digest'):
                synthesis['results'] = [current.pop(row['slug'], {**row, 'attempted': False})
                                        for row in old.get('results', [])] + list(current.values())
                if any(row.get('status') != 'published' for row in synthesis['results']):
                    synthesis['status'] = 'blocked'
            else:
                synthesis['status'] = 'blocked'
                synthesis.setdefault('reasons', []).append('Knowledge inputs changed; other concepts require a full compilation')
    from .search import rebuild_index
    index = rebuild_index(ws, embeddings=True)
    blocked = any(r['status'] == 'blocked' for r in results) or synthesis and synthesis.get('status') == 'blocked'
    result = {'status': 'blocked' if blocked else 'completed', 'results': results, 'excluded_sources': excluded,
              'published': sum(r['status'] == 'published' for r in results), 'synthesis': synthesis, 'index': index,
              'retried_target': retry_target, 'library_id': ws.library_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(path, result)
    return result
