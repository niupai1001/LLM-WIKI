"""Publish paper readings and independently reviewed cross-paper concepts."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

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
            db.execute('DELETE FROM knowledge_points WHERE source_id=?', (sid,))
            for point in packet['knowledge_points']:
                identifier = global_point_id(sid, point['id'])
                value = json.dumps(point, ensure_ascii=False, sort_keys=True)
                if identifier in old and old[identifier] != value:
                    db.execute('INSERT INTO knowledge_point_changes VALUES (?,?,?,?,?)', (identifier, old[identifier], value, 'Source understanding recompiled; old and new values retained', utcnow()))
                db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)', (identifier, sid, point['id'], packet['source']['source_version'], value))
            db.execute("UPDATE sources SET stage='approved',claim_at=NULL WHERE id=?", (sid,))
        publish(ws, changes, proposal_id, database_callback=database)
    return {'source_id': sid, 'slug': slug, 'status': 'published', 'knowledge_points': len(point_ids)}


def publish_concept(ws, proposal, point_ids, job_id=None, *, prepare_only=False):
    from .topic_navigation import plan_updates
    with use_workspace(ws), publication_lock(ws):
        check_cancelled()
        slug = proposal['slug']
        if wiki.safe_slug(slug) != slug or slug == 'index':
            raise ValueError('Invalid concept identity')
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
        bindings = proposal.get('point_bindings', [])
        if {b.get('point_id') for b in bindings} != set(point_ids):
            raise ValueError('Concept omitted assigned knowledge points')
        for binding in bindings:
            if not binding.get('body_quote') or binding['body_quote'] not in body or not set(binding.get('claim_ids', [])) <= claim_ids:
                raise ValueError('Concept point is not present in body with valid claims')
        for binding in proposal.get('claim_bindings', []):
            if binding.get('claim_id') not in claim_ids or not binding.get('body_quote') or binding['body_quote'] not in body:
                raise ValueError('Concept claim is not present in body')
        if {b.get('claim_id') for b in proposal.get('claim_bindings', [])} != claim_ids:
            raise ValueError('Concept omitted claim bindings')
        old_path = ws.meta / (slug + '.json')
        old = load_json(old_path) if old_path.exists() else {}
        if (not prepare_only and old.get('synthesis_input_digest') == proposal.get('input_digest')
                and proposal.get('input_digest') and old.get('knowledge_point_ids') == point_ids
                and (ws.wiki / (slug + '.md')).is_file()
                and wiki.page_body((ws.wiki / (slug + '.md')).read_text()) == body):
            return {'slug': slug, 'status': 'published', 'point_ids': point_ids, 'reused': True}
        if old and old.get('page_role') == 'paper':
            raise ValueError('Concept cannot overwrite a paper reading')
        wiki._require_claims_preserved(old, claims)
        proposal_id = 'concept-' + slug + '-' + digest(body)[:12]
        meta = {**old, 'slug': slug, 'title': proposal['title'], 'category': 'agents', 'page_kind': 'concept',
                'page_role': 'knowledge', 'source_ids': ids, 'claims': claims, 'conflicts': proposal.get('conflicts', []),
                'knowledge_topic': proposal.get('knowledge_topic', ''), 'pipeline_version': 'paper-concepts-v1',
                'relations': proposal.get('relations', []), 'related_slugs': proposal.get('related_slugs', []),
                'aliases': proposal.get('aliases', []), 'knowledge_point_ids': point_ids,
                'point_bindings': bindings, 'claim_bindings': proposal['claim_bindings'], 'semantic_reviews': semantic,
                'synthesis_input_digest': proposal.get('input_digest'), 'updated_at': utcnow(),
                'publication': {'by': 'system', 'policy': 'independent-reviewed', 'at': utcnow()}}
        navigation = plan_updates({**proposal, 'mode': 'update', 'page_kind': 'concept', 'page_role': 'knowledge', 'category': 'agents', 'proposal_id': proposal_id}, ws.wiki, ws.meta)
        changes = {f'wiki/{slug}.md': wiki._publication_body({**proposal, **meta}, ids),
                   f'wiki_meta/{slug}.json': json.dumps(meta, ensure_ascii=False, indent=2),
                   'wiki/index.md': _index_body(ws, meta['title'], slug, navigation),
                   f'proposal_history/{proposal_id}.json': json.dumps({**proposal, 'job_id': job_id, 'outcome': 'approved'}, ensure_ascii=False, indent=2)}
        for change in navigation:
            changes[str(change['path'].relative_to(ws.root))] = change['body']
            changes[str(change['meta_path'].relative_to(ws.root))] = json.dumps(change['metadata'], ensure_ascii=False, indent=2)

        def database(db):
            _schema(db)
            marks = ','.join('?' for _ in point_ids)
            found = {row[0] for row in db.execute('SELECT id FROM knowledge_points WHERE id IN (' + marks + ')', point_ids)} if point_ids else set()
            if found != set(point_ids):
                raise ValueError('Assigned knowledge points changed')
            db.execute('DELETE FROM concept_memberships WHERE concept_slug=?', (slug,))
            for binding in bindings:
                db.execute('INSERT INTO concept_memberships VALUES (?,?,?)', (slug, binding['point_id'], binding.get('reason', binding['body_quote'])))
        if prepare_only:
            return {'changes': changes, 'database_callback': database, 'slug': slug, 'point_ids': point_ids}
        publish(ws, changes, proposal_id, database_callback=database)
    return {'slug': slug, 'status': 'published', 'point_ids': point_ids, 'topics_updated': [n['slug'] for n in navigation]}


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
