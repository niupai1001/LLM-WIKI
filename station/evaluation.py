"""Acceptance evidence; no model self-scores or fabricated human labels.

Human records live in human_evaluation.json and bind library_id, manifest_digest,
legacy_manifest_digest, evaluation_input_digest, and fixture_digest. Each record needs case_id,
reviewer_type='human', by, rationale, target_slug, checked_source_ids, claim_ids,
and boolean dimensions named by the fixture. Agent records never count as human.
Separate claim_records identify target_slug/claim_id and a human support verdict;
each current claim on a fixture target page is counted once, across all cases.

Previously generated query outputs may be saved in query_evaluation.json with the
same five bindings and records [{case_id, reviewer_type, answer}]. This module
checks citation locations against originals and never generates answers. Candidate
Recall@8 measures page candidates for the knowledge cases. A ready embedding index
allows actual hybrid query retrieval outside publication locks; lexical fallback is
diagnostic evidence and never satisfies the hybrid acceptance gate.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .publication import publication_lock
from .workspaces import Workspace, snapshot_manifest, resolve_workspace, _save

FIXTURE_PATH = Path(__file__).resolve().parent.parent / 'tests/fixtures/v2-evaluation.json'
_SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_SOURCE = re.compile(r'[a-f0-9]{16}')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _read(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else (default or {})
    except (OSError, ValueError):
        return default or {}


def _pages(ws):
    return {p.stem: _read(p) for p in sorted(ws.meta.glob('*.json')) if _SLUG.fullmatch(p.stem)}


def _coverage(ws):
    if not ws.db.is_file():
        return []
    try:
        with sqlite3.connect(ws.db.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM knowledge_coverage ORDER BY source_id,cache_key,question_id')]
    except sqlite3.Error:
        return []


def _resolve_target(ws, slug):
    redirects, seen = _read(ws.root / 'redirects.json'), set()
    while isinstance(slug, str) and _SLUG.fullmatch(slug) and slug not in seen:
        if (ws.wiki / (slug + '.md')).is_file():
            return slug
        seen.add(slug)
        slug = redirects.get(slug)
    return None


def _input_digest(ws, coverage):
    from .documents import PARSER_VERSION
    from .extraction import SCHEMA_VERSION, PROMPT_VERSION
    from .compiler import VERSION
    values = {'processing_versions': {'parser': PARSER_VERSION, 'extraction_schema': SCHEMA_VERSION,
                                      'extraction_prompt': PROMPT_VERSION, 'compiler': VERSION}}
    for directory in ('parsed', 'extractions'):
        for path in sorted((ws.root / directory).rglob('*.json')):
            values[str(path.relative_to(ws.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    values['coverage'] = coverage
    values['redirects'] = _read(ws.root / 'redirects.json')
    return _digest(values)


def _integrity(ws):
    from .search import source_segments
    from .documents import source_version
    pages, issues, total, quoted = _pages(ws), [], 0, 0
    segments = {}
    for slug, meta in pages.items():
        path = ws.wiki / (slug + '.md')
        if not path.is_file():
            issues.append({'code': 'missing_page', 'slug': slug}); continue
        body = path.read_text(encoding='utf-8')
        registered = set(meta.get('source_ids', []))
        cited = set(re.findall(r'/source/([a-f0-9]{16})|\[S:([a-f0-9]{16})\]', body))
        cited = {a or b for a, b in cited}
        for sid in sorted(registered | cited):
            if not isinstance(sid, str) or not _SOURCE.fullmatch(sid) or not (ws.raw / (sid + '.json')).is_file():
                issues.append({'code': 'missing_source', 'slug': slug, 'source_id': sid})
        if cited - registered:
            issues.append({'code': 'unregistered_citation', 'slug': slug, 'source_ids': sorted(cited - registered)})
        claims = meta.get('claims', [])
        if not isinstance(claims, list):
            issues.append({'code': 'invalid_claims', 'slug': slug}); continue
        if meta.get('page_kind', 'concept') == 'concept' and meta.get('page_role') != 'source_collection' and not claims:
            issues.append({'code': 'missing_claims', 'slug': slug})
        ids = set()
        bindings = {b.get('claim_id'): b.get('body_quote') for b in meta.get('claim_bindings', []) if isinstance(b, dict)}
        for claim in claims:
            total += 1
            if not isinstance(claim, dict) or not claim.get('id') or claim['id'] in ids:
                issues.append({'code': 'invalid_claim_id', 'slug': slug}); continue
            cid = claim['id']; ids.add(cid)
            required = set(claim.get('source_ids', []))
            if not required or not required <= registered or not claim.get('scope') or not claim.get('text'):
                issues.append({'code': 'invalid_claim_scope_or_sources', 'slug': slug, 'claim_id': cid})
            if meta.get('pipeline_version') and (not isinstance(bindings.get(cid), str) or not bindings[cid].strip() or bindings[cid] not in body):
                issues.append({'code': 'missing_claim_body_binding', 'slug': slug, 'claim_id': cid})
            supported = set()
            for passage in claim.get('evidence_passages', []):
                if not isinstance(passage, dict):
                    continue
                sid, quote = passage.get('source_id'), passage.get('quote')
                if not isinstance(sid, str) or not _SOURCE.fullmatch(sid) or not isinstance(quote, str):
                    continue
                if sid not in segments:
                    try:
                        segments[sid] = source_segments(ws, sid)
                    except (OSError, ValueError, TypeError):
                        segments[sid] = []
                normalized = ' '.join(quote.split())
                if sid in required and passage.get('locator') and len(normalized) >= 20 and any(normalized in ' '.join(s['text'].split()) for s in segments[sid]):
                    supported.add(sid)
            if required and required <= supported:
                quoted += 1
            else:
                issues.append({'code': 'unmatched_or_missing_evidence', 'slug': slug, 'claim_id': cid, 'source_ids': sorted(required - supported)})
    for path in ws.wiki.glob('*.md'):
        if path.stem != 'index' and path.stem not in pages:
            issues.append({'code': 'missing_metadata', 'slug': path.stem})
    rows = _coverage(ws)
    latest = {}
    for path in sorted((ws.root / 'extractions').glob('*/*.json')):
        item = _read(path)
        sid = item.get('source_id')
        if not isinstance(sid, str) or not _SOURCE.fullmatch(sid) or not item.get('cache_key'):
            continue
        try:
            current = source_version(ws, sid)
        except (OSError, ValueError, TypeError):
            continue
        if item.get('source_version') == current and item.get('library_id') == ws.library_id:
            if sid not in latest or item.get('created_at', '') > latest[sid].get('created_at', ''):
                latest[sid] = item
    unresolved, documented_missing, questions, completed = [], [], 0, 0
    by_key = {(r['source_id'], r['cache_key'], r['question_id']): r for r in rows}
    for path in sorted(ws.raw.glob('*.json')):
        sid = path.stem
        extraction = latest.get(sid)
        if extraction is None:
            unresolved.append({'source_id': sid, 'reason': 'no_current_extraction'}); continue
        if extraction.get('coverage', {}).get('complete') is not True:
            unresolved.append({'source_id': sid, 'reason': 'incomplete_paragraph_coverage'})
        for question in extraction.get('questions', []):
            questions += 1
            row = by_key.get((sid, extraction['cache_key'], question['id']))
            target = row.get('target_slug') if row else ''
            target_meta = pages.get(target, {})
            if row and row['status'] == 'published' and target_meta and (ws.wiki / (target + '.md')).is_file() and sid in target_meta.get('source_ids', []):
                completed += 1
            elif row and row['status'] == 'deferred' and isinstance(row.get('reason'), str) and row['reason'].strip():
                documented_missing.append({'source_id': sid, 'question_id': question['id'],
                                           'status': 'deferred', 'reason': row['reason'].strip()})
            else:
                unresolved.append({'source_id': sid, 'question_id': question['id'], 'status': row.get('status') if row else 'unrecorded', 'reason': row.get('reason') if row else 'no_coverage_record'})
    return {'pages': len(pages), 'claims': total, 'claims_with_all_source_quotes': quoted,
            'evidence_coverage': quoted / total if total else None, 'issues': issues,
            'questions': questions, 'published_questions': completed,
            'documented_missing_questions': documented_missing,
            'accounted_questions': completed + len(documented_missing), 'unresolved': unresolved}


def compare_libraries(ws, legacy_ws):
    """Explicit and exact mappings only; matches are not semantic approval."""
    with ExitStack() as stack:
        for library in sorted({str(ws.root): ws, str(legacy_ws.root): legacy_ws}.values(), key=lambda x: str(x.root)):
            stack.enter_context(publication_lock(library))
        old, new = _pages(legacy_ws), _pages(ws)
        explicit = {}
        for slug, meta in new.items():
            for mapping in meta.get('legacy_mappings', []):
                if isinstance(mapping, dict):
                    explicit.setdefault((mapping.get('old_slug'), mapping.get('old_claim_id')), []).append({**mapping, 'target_slug': slug})
        mappings, unresolved = [], []
        old_count = new_count = 0
        for meta in new.values(): new_count += len(meta.get('claims', []))
        for slug, meta in old.items():
            for claim in meta.get('claims', []):
                old_count += 1
                records = explicit.get((slug, claim.get('id')), [])
                if not records:
                    target = _resolve_target(ws, slug)
                    matched = next((c for c in new.get(target, {}).get('claims', []) if c.get('id') == claim.get('id')), None)
                    if matched:
                        records = [{'target_slug': target, 'new_claim_ids': [claim['id']], 'disposition': 'exact-content' if all(matched.get(k) == claim.get(k) for k in ('text','scope','source_ids')) else 'changed-needs-review'}]
                valid = bool(records) and all(r.get('disposition') in {'preserved', 'revised', 'exact-content'} and r.get('new_claim_ids') and set(r['new_claim_ids']) <= {c.get('id') for c in new.get(r['target_slug'], {}).get('claims', [])} for r in records)
                mappings.append({'old_slug': slug, 'old_claim_id': claim.get('id'), 'mappings': records, 'structurally_resolved': valid})
                if not valid: unresolved.append({'old_slug': slug, 'old_claim_id': claim.get('id')})
        integrity = _integrity(ws)
        return {'library_id': ws.library_id, 'legacy_library_id': legacy_ws.library_id,
                'mapping': mappings, 'claims': {'legacy': old_count, 'candidate': new_count},
                'evidence_coverage': integrity['evidence_coverage'], 'unresolved': unresolved,
                'limits': 'Explicit/exact mapping checks do not establish preservation of meaning.'}


def _source_sync(ws, legacy_ws):
    from .documents import source_version
    missing, changed = [], []
    for folder in ('raw', 'originals', 'source_metadata'):
        for path in sorted((legacy_ws.root / folder).rglob('*')):
            if not path.is_file(): continue
            relative = path.relative_to(legacy_ws.root)
            target = ws.root / relative
            if not target.is_file(): missing.append(str(relative))
            elif hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest(): changed.append(str(relative))
    # A missing original is part of a source version. Files still present in
    # legacy alone cannot detect an original removed there but retained here.
    source_version_changes = []
    for path in sorted(legacy_ws.raw.glob('*.json')):
        if not _SOURCE.fullmatch(path.stem): continue
        try:
            if source_version(legacy_ws, path.stem) != source_version(ws, path.stem):
                source_version_changes.append(path.stem)
        except (OSError, ValueError, TypeError):
            source_version_changes.append(path.stem)
    return {'ready': not missing and not changed and not source_version_changes,
            'missing': missing, 'changed': changed, 'source_version_changes': source_version_changes,
            'legacy_library_id': legacy_ws.library_id}


def _review_digest(ws):
    return _digest({name: hashlib.sha256((ws.root / name).read_bytes()).hexdigest() if (ws.root / name).is_file() else None
                    for name in ('human_evaluation.json', 'query_evaluation.json')})


def _semantic_state(ws):
    from .search import capabilities, _config, _backend
    state = dict(capabilities(ws)['semantic'])
    state['backend_digest'] = _backend(_config(ws))
    state['index_digest'] = None
    state['total_chunks'] = 0
    if (ws.root / 'search.db').is_file():
        try:
            with sqlite3.connect((ws.root / 'search.db').as_uri() + '?mode=ro', uri=True) as db:
                total = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
                current = db.execute('SELECT COUNT(*) FROM chunks WHERE vector IS NOT NULL AND vector_backend=?', (_backend(_config(ws)),)).fetchone()[0]
                state['total_chunks'] = total
                state['index_digest'] = _digest(db.execute('SELECT chunk_id,source_version,vector_backend,dimension,vector FROM chunks ORDER BY chunk_id').fetchall())
                state['ready'] = bool(state.get('ready') and total and current == total)
                if current != total:
                    state['reason'] = 'Some chunks lack vectors for the current embedding backend'
        except sqlite3.Error:
            state['ready'] = False
            state['reason'] = 'Embedding index could not be verified'
    return state


def acceptance_current(ws, report, *, legacy_ws=None, fixture_path=None):
    """Reject approval after evidence, labels, fixtures, index or sources changed."""
    if not isinstance(report, dict) or report.get('schema_version') != 2 or report.get('passed') is not True:
        return False
    fixture = _read(fixture_path or FIXTURE_PATH)
    if legacy_ws is None:
        root = ws.root.parent.parent if ws.root.parent.name == 'libraries' else ws.root
        legacy_ws = resolve_workspace('legacy', root)
    with ExitStack() as stack:
        for library in sorted({str(ws.root): ws, str(legacy_ws.root): legacy_ws}.values(), key=lambda x: str(x.root)):
            stack.enter_context(publication_lock(library))
        current = {'library_id': ws.library_id, 'manifest_digest': snapshot_manifest(ws)['digest'],
                   'legacy_manifest_digest': snapshot_manifest(legacy_ws)['digest'],
                   'evaluation_input_digest': _input_digest(ws, _coverage(ws)), 'fixture_digest': _digest(fixture)}
        if not _bindings_match(report, current) or report.get('review_records_digest') != _review_digest(ws):
            return False
        semantic = _semantic_state(ws)
        if not semantic.get('ready') or report.get('semantic_index') != semantic:
            return False
        retrieval = report.get('retrieval', {})
        recall = retrieval.get('recall_at_8')
        if (not retrieval.get('ready') or retrieval.get('mode') != 'hybrid-online'
                or isinstance(recall, bool) or not isinstance(recall, (int, float)) or not .9 <= recall <= 1):
            return False
        return bool(_source_sync(ws, legacy_ws)['ready'] and _human(ws, fixture, current)['ready']
                    and _query_records(ws, fixture, current)['ready'])


def _bindings_match(record, bindings):
    return all(record.get(k) == v for k, v in bindings.items())


def _human(ws, fixture, bindings):
    labels = _read(ws.root / 'human_evaluation.json')
    rows = labels.get('records', []) if _bindings_match(labels, bindings) else []
    rows = rows if isinstance(rows, list) else []
    cases, evaluated, passed, invalid, blocking_cases = fixture['knowledge_cases'], 0, 0, [], []
    pages = _pages(ws)
    core_pages = {_resolve_target(ws, c['expected_target_slug']) for c in cases}
    core_pages.discard(None)
    # Repeated extraction/update cases do not multiply the same claim. All
    # claims on the fixture's core pages require a verdict, avoiding cherry-picks.
    core_claims = {(slug, c.get('id')): c for slug in core_pages
                   for c in pages[slug].get('claims', []) if isinstance(c, dict) and c.get('id')}
    for case in cases:
        matches = [r for r in rows if isinstance(r, dict) and r.get('case_id') == case['id']]
        label = matches[0] if len(matches) == 1 else {}
        target = _resolve_target(ws, case['expected_target_slug'])
        dims = label.get('dimensions', {})
        valid = (label.get('reviewer_type') == 'human' and isinstance(label.get('by'), str) and bool(label['by'].strip())
                 and isinstance(label.get('rationale'), str) and bool(label['rationale'].strip())
                 and target and label.get('target_slug') == target and isinstance(label.get('checked_source_ids'), list)
                 and all(isinstance(s, str) for s in label['checked_source_ids'])
                 and set(case['expected_source_ids']) <= set(label['checked_source_ids'])
                 and isinstance(label.get('claim_ids'), list) and bool(label['claim_ids'])
                 and all(isinstance(c, str) for c in label['claim_ids'])
                 and set(label['claim_ids']) <= {c.get('id') for c in pages.get(target, {}).get('claims', [])}
                 and isinstance(dims, dict) and all(isinstance(dims.get(k), bool) for k in case['required_human_dimensions']))
        if valid:
            evaluated += 1
            passed += all(dims[k] for k in case['required_human_dimensions'])
            blocking = set(case.get('blocking_human_dimensions', [])) | {
                k for k in case['required_human_dimensions'] if k != 'claim_support'}
            failed = sorted(k for k in blocking if dims.get(k) is not True)
            if failed: blocking_cases.append({'case_id': case['id'], 'dimensions': failed})
        else:
            invalid.append(case['id'])
    complete = evaluated == len(cases) and bool(cases)
    rate = passed / evaluated if complete else None
    threshold = max(.95, fixture['thresholds'].get('human_case_pass_rate', .95))
    claim_rows = labels.get('claim_records', []) if _bindings_match(labels, bindings) else []
    claim_rows = claim_rows if isinstance(claim_rows, list) else []
    reviewed_claims, supported_claims, missing_claims = 0, 0, []
    for (slug, cid), claim in sorted(core_claims.items()):
        matches = [r for r in claim_rows if isinstance(r, dict) and r.get('target_slug') == slug and r.get('claim_id') == cid]
        row = matches[0] if len(matches) == 1 else {}
        valid = (row.get('reviewer_type') == 'human' and isinstance(row.get('by'), str) and bool(row['by'].strip())
                 and isinstance(row.get('rationale'), str) and bool(row['rationale'].strip())
                 and row.get('support') in {'supported','partially_supported','contradicted','unverifiable'}
                 and isinstance(row.get('checked_source_ids'), list)
                 and all(isinstance(s, str) for s in row['checked_source_ids'])
                 and bool(claim.get('source_ids')) and set(claim['source_ids']) <= set(row['checked_source_ids']))
        if valid:
            reviewed_claims += 1
            supported_claims += row['support'] == 'supported'
        else:
            missing_claims.append({'target_slug': slug, 'claim_id': cid,
                                   'reason': 'duplicate_records' if len(matches) > 1 else 'missing_or_invalid_human_record'})
    claims_complete = bool(core_claims) and reviewed_claims == len(core_claims)
    precision = supported_claims / reviewed_claims if claims_complete else None
    support_threshold = max(.95, fixture['thresholds'].get('core_claim_support_precision', .95))
    claim_support = {'required': len(core_claims), 'reviewed': reviewed_claims, 'supported': supported_claims,
                     'precision': precision, 'threshold': support_threshold,
                     'ready': claims_complete and precision >= support_threshold,
                     'missing_or_invalid_claims': missing_claims, 'core_pages': sorted(core_pages),
                     'counting_policy': 'Every current claim on fixture target pages, unique by target_slug and claim_id; supported verdicts only.'}
    return {'required': len(cases), 'reviewed': evaluated, 'passed_cases': passed,
            'pass_rate': rate, 'threshold': threshold,
            'ready': bool(complete and rate >= threshold and not blocking_cases and claim_support['ready']),
            'blocking_cases': blocking_cases, 'claim_support': claim_support,
            'missing_or_invalid_cases': invalid, 'record_bindings_current': _bindings_match(labels, bindings),
            'limits': 'Only explicit human records count; agent/model reviews never count toward this score.'}


def _query_records(ws, fixture, bindings):
    from .query import _still_current, _evidence_url
    saved = _read(ws.root / 'query_evaluation.json')
    rows = saved.get('records', []) if _bindings_match(saved, bindings) else []
    rows = rows if isinstance(rows, list) else []
    checks, located, total = [], 0, 0
    for case in fixture['query_cases']:
        matches = [r for r in rows if isinstance(r, dict) and r.get('case_id') == case['id']]
        row = matches[0] if len(matches) == 1 else {}
        answer = row.get('answer', {})
        citations = answer.get('citations', []) if isinstance(answer, dict) else []
        expected_status=case.get('expected_status','answered')
        eligible = (row.get('reviewer_type') in {'human','agent'} and isinstance(answer, dict)
                    and answer.get('library_id') == ws.library_id and answer.get('question') == case['question']
                    and answer.get('status') == expected_status and isinstance(citations, list)
                    and (bool(citations) if expected_status=='answered' else
                         expected_status=='insufficient_evidence' and not citations
                         and answer.get('answer_markdown')=='现有归档证据不足以回答这个问题。'))
        good = 0
        if eligible:
            for item in citations:
                total += 1
                try:
                    valid = (isinstance(item, dict) and item.get('source_id') in case['expected_source_ids']
                             and isinstance(item.get('id'), str) and re.fullmatch(r'E[1-9][0-9]*', item['id'])
                             and bool(item.get('locator')) and _still_current(ws, item)
                             and item.get('url') == _evidence_url(ws, item['source_id'], item['location'])
                             and '[' + item['id'] + '](' + item['url'] + ')' in answer.get('answer_markdown', ''))
                except (KeyError, TypeError, ValueError, OSError):
                    valid = False
                good += bool(valid); located += bool(valid)
        checks.append({'case_id': case['id'], 'expected_status':expected_status,'ready': bool(eligible and good == len(citations)), 'reviewer_type': row.get('reviewer_type'), 'valid_locations': good, 'citations': len(citations) if isinstance(citations, list) else 0})
    rate = located / total if total else None
    return {'cases': checks, 'required': len(checks), 'completed': sum(c['ready'] for c in checks),
            'citation_location_rate': rate, 'ready': bool(checks) and all(c['ready'] for c in checks) and (total==0 or rate >= fixture['thresholds']['citation_location_rate']),
            'limits': 'Mechanical current-original location match; not answer entailment or human review.'}


def _retrieval(ws, fixture, targets, page_versions, semantic_ready):
    """No caller publication lock: search may call the remote embedding service."""
    from .search import search
    cases = []
    for case in fixture['knowledge_cases']:
        expected = set(targets[case['id']])
        try:
            result = search(ws, case['question'], limit=8, semantic=semantic_ready, kind='page')
            retrieved = result['results']
            pages = [r for r in retrieved if r.get('kind') == 'page']
            actual = {r['id'] for r in pages}
            current = len(pages) == len(retrieved) and all(
                r.get('source_version') == page_versions.get(r['id']) for r in pages)
            semantic_used = result.get('semantic_used') is True
            warnings = result.get('warnings', [])
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            pages, actual, current, semantic_used = [], set(), False, False
            warnings = ['Retrieval failed (' + type(exc).__name__ + ')']
        recall = len(expected & actual) / len(expected) if expected else 0
        cases.append({'case_id': case['id'], 'question': case['question'], 'expected': sorted(expected),
                      'retrieved': [r['id'] for r in pages], 'recall_at_8': recall,
                      'semantic_used': semantic_used, 'input_current': current, 'warnings': warnings})
    diagnostic = sum(c['recall_at_8'] for c in cases) / len(cases) if cases else None
    hybrid = bool(semantic_ready and cases and all(c['semantic_used'] and c['input_current'] for c in cases))
    threshold = max(.9, fixture['thresholds']['recall_at_8'])
    return {'mode': 'hybrid-online' if hybrid else 'hybrid-incomplete' if semantic_ready else 'lexical-only-diagnostic',
            'candidate_kind': 'page', 'recall_at_8': diagnostic if hybrid else None,
            'diagnostic_recall_at_8': diagnostic, 'threshold': threshold,
            'ready': bool(hybrid and diagnostic >= threshold), 'cases': cases}


def evaluate_library(ws, write=True, *, legacy_ws=None, fixture_path=None):
    """Evaluate actual candidates and bound human records, never model self-scores.

    Content snapshots, citation checks and report writes take the library locks.
    Query embedding requests execute between snapshots; a changed authoritative
    input, index, fixture or review record invalidates the resulting report.
    """
    from .search import rebuild_index, _digest as search_digest
    fixture_file = Path(fixture_path or FIXTURE_PATH)
    fixture = _read(fixture_file)
    if not isinstance(fixture.get('knowledge_cases'), list) or not isinstance(fixture.get('query_cases'), list) or not fixture.get('thresholds'):
        raise ValueError('Invalid evaluation fixture')
    if legacy_ws is None:
        root = ws.root.parent.parent if ws.root.parent.name == 'libraries' else ws.root
        legacy_ws = resolve_workspace('legacy', root)
    # Only the disposable lexical index is refreshed here. Preparing missing
    # vectors is a separate explicit job, not an unexpected evaluation workload.
    rebuild_index(ws, embeddings=False)
    libraries = sorted({str(ws.root): ws, str(legacy_ws.root): legacy_ws}.values(), key=lambda x: str(x.root))
    with ExitStack() as stack:
        for library in libraries: stack.enter_context(publication_lock(library))
        manifest = snapshot_manifest(ws)
        coverage = _coverage(ws)
        bindings = {'library_id': ws.library_id, 'manifest_digest': manifest['digest'],
                    'legacy_manifest_digest': snapshot_manifest(legacy_ws)['digest'],
                    'evaluation_input_digest': _input_digest(ws, coverage), 'fixture_digest': _digest(fixture)}
        integrity = _integrity(ws)
        comparison = compare_libraries(ws, legacy_ws)
        sync = _source_sync(ws, legacy_ws)
        semantic = _semantic_state(ws)
        legacy_digest = bindings['legacy_manifest_digest']
        review_digest = _review_digest(ws)
        targets = {c['id']: [(_resolve_target(ws, slug) or slug)
                            for slug in c.get('expected_page_slugs', [c['expected_target_slug']])]
                   for c in fixture['knowledge_cases']}
        page_versions = {slug: search_digest({'body': (ws.wiki / (slug + '.md')).read_text(encoding='utf-8'), 'meta': meta})
                         for slug, meta in _pages(ws).items() if (ws.wiki / (slug + '.md')).is_file()}
    retrieval = _retrieval(ws, fixture, targets, page_versions, semantic.get('ready') is True)
    with ExitStack() as stack:
        for library in libraries: stack.enter_context(publication_lock(library))
        human = _human(ws, fixture, bindings)
        query = _query_records(ws, fixture, bindings)
        blockers = []
        if ws.library_id == legacy_ws.library_id: blockers.append('candidate_library_required')
        if not integrity['pages'] or not integrity['claims']: blockers.append('candidate_knowledge_empty')
        if integrity['issues']: blockers.append('structural_integrity_failed')
        if integrity['unresolved'] or not integrity['questions']: blockers.append('extraction_or_question_coverage_incomplete')
        if comparison['unresolved']: blockers.append('legacy_claim_mappings_unresolved')
        if not sync['ready']: blockers.append('sources_not_synchronized')
        if not semantic.get('ready'): blockers.append('semantic_index_not_ready')
        if not retrieval['ready']:
            blockers.append('hybrid_retrieval_not_measured' if retrieval['recall_at_8'] is None else 'recall_at_8_below_threshold')
        if not human['ready']: blockers.append('human_evaluation_missing_stale_or_below_threshold')
        if not query['ready']: blockers.append('query_outputs_missing_or_unlocatable')
        current = {'library_id': ws.library_id, 'manifest_digest': snapshot_manifest(ws)['digest'],
                   'legacy_manifest_digest': snapshot_manifest(legacy_ws)['digest'],
                   'evaluation_input_digest': _input_digest(ws, _coverage(ws)), 'fixture_digest': _digest(_read(fixture_file))}
        if not _bindings_match(bindings, current) or snapshot_manifest(legacy_ws)['digest'] != legacy_digest:
            blockers.append('library_changed_during_evaluation')
        if _review_digest(ws) != review_digest: blockers.append('review_records_changed_during_evaluation')
        if _semantic_state(ws) != semantic: blockers.append('semantic_index_changed_during_evaluation')
        report = {**bindings, 'review_records_digest': review_digest, 'legacy_manifest_digest': legacy_digest,
                  'schema_version': 2, 'suite_id': fixture.get('suite_id'),
                  'evaluated_at': datetime.now(timezone.utc).isoformat(), 'passed': not blockers,
                  'blocked_reasons': blockers, 'integrity': integrity, 'comparison': comparison,
                  'source_sync': sync, 'semantic_index': semantic, 'retrieval': retrieval,
                  'human_evaluation': human, 'query_evaluation': query,
                  'limits': ['Fixture expectations originate from public wiki metadata and need original-source human checking.',
                             'Only page candidates from actual hybrid retrieval count toward Recall@8; lexical fallback is diagnostic.',
                             'Case dimension checks and unique human claim support precision are separate measurements.',
                             'Quoted text existence and citation location are not semantic entailment.']}
        if write: _save(ws.root / 'acceptance.json', report)
        return report


acceptance = evaluate_library
