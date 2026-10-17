"""Versioned, locally scoped L3 decisions; publication owns application transactions."""
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .common import utcnow
from .jobs import JobConflict

VERSION = 'knowledge-maintenance/v1'
MAX_ATTEMPTS = 3
CONTRACT = '''Return only JSON {decision_version:"knowledge-maintenance/v1",input_digest,
point_decisions:[{point_id,disposition:"linked|deferred",memberships:[{concept_slug,role,reason}],reason,change_summary}],
concept_actions:[{concept_slug,action:"create|update|no_change",title,question,reason,affected_point_ids,requires_review:false}],
relation_actions:[{source_slug,target_slug,kind,basis:"factual|editorial",claim_ids,source_ids,reason,summary,scope}],unresolved:[]}.
Relations may be empty. kind is implementation/shared_mechanism/contrast/design_inference; use only supplied claim/source IDs.
Every origin needs a create/update concept action; no self links. Exactly one decision per pending_point_ids. linked requires one or more memberships;
deferred requires none and a specific reason. Every membership target requires an action. New concepts need title/question.
Copy IDs and input_digest exactly. Existing base_versions may be omitted and will be attached by the program.
Source claims use evidence_locations with original_id pointing to source_materials.originals; text is stored once,
with quote_start/quote_end character offsets. These are complete quoted evidence, not missing citations.
Never translate a deletion, withdrawal, merge, split or supersession into an ordinary update. Mark risky actions requires_review.
required_point_ids may optionally select the knowledge essential in body; other memberships provide background support.
'''
_SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?')


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS concept_memberships (concept_slug TEXT,point_id TEXT,reason TEXT,PRIMARY KEY(concept_slug,point_id))')
    columns = {row[1] for row in db.execute('PRAGMA table_info(concept_memberships)')}
    for name, declaration in {'role': "TEXT NOT NULL DEFAULT ''", 'status': "TEXT NOT NULL DEFAULT 'active'", 'decision_id': 'TEXT', 'created_at': 'TEXT', 'updated_at': 'TEXT'}.items():
        if name not in columns:
            db.execute('ALTER TABLE concept_memberships ADD COLUMN ' + name + ' ' + declaration)
    db.execute('CREATE TABLE IF NOT EXISTS maintenance_decisions (id TEXT PRIMARY KEY,input_digest TEXT UNIQUE,input_json TEXT,decision_json TEXT,status TEXT,attempts INTEGER NOT NULL DEFAULT 0,errors_json TEXT NOT NULL DEFAULT "[]",model_identity TEXT,skill_hash TEXT,created_at TEXT,updated_at TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS point_dispositions (point_id TEXT PRIMARY KEY,disposition TEXT,reason TEXT,point_version_json TEXT,candidate_digest TEXT,reevaluate_json TEXT,decision_id TEXT,updated_at TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS maintenance_executions (decision_id TEXT,concept_slug TEXT,status TEXT,at TEXT,PRIMARY KEY(decision_id,concept_slug))')
    db.execute('CREATE TABLE IF NOT EXISTS maintenance_budgets (operation_key TEXT PRIMARY KEY,attempts INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS maintenance_attempts (id INTEGER PRIMARY KEY,decision_id TEXT,attempt INTEGER,request_json TEXT,response_json TEXT,error TEXT,at TEXT)')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be nonempty text')
    return value.strip()


def membership_version(db, slug):
    """Canonical snapshot used by decision readers and locked publication CAS."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(concept_memberships)')}
    if not columns:
        return canonical_digest([])
    selected = ['concept_slug', 'point_id', 'reason'] + [name if name in columns else default for name, default in [('role', "''"), ('status', "'active'"), ('decision_id', 'NULL')]]
    rows = db.execute('SELECT ' + ','.join(selected) + ' FROM concept_memberships WHERE concept_slug=? ORDER BY point_id', (slug,))
    return canonical_digest([list(row) for row in rows])


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(label + ' must be a list')
    return value


def point_version(point):
    data = point.get('data', json.loads(point.get('data_json', '{}')))
    return {'source_id': point.get('source_id'), 'source_version': point.get('source_version'), 'data_digest': canonical_digest(data)}


def validate_decision(value, points, existing, occupied, input_digest=None):
    if not isinstance(value, dict) or value.get('decision_version') != VERSION:
        raise ValueError('Maintenance decision_version must be ' + VERSION)
    result = copy.deepcopy(value)
    if input_digest is not None and result.get('input_digest', input_digest) != input_digest:
        raise ValueError('Maintenance input version conflict')
    result['input_digest'] = input_digest if input_digest is not None else result.get('input_digest')
    by_slug = {c['slug']: c for c in existing}
    actions = {}
    risky = {'merge', 'split', 'supersede', 'delete', 'withdraw', 'retract'}
    for action in _list(result.get('concept_actions'), 'concept_actions'):
        if not isinstance(action, dict):
            raise ValueError('Concept action must be an object')
        slug, kind = action.get('concept_slug'), action.get('action')
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug == 'index' or slug in actions:
            raise ValueError('Invalid or conflicting concept action')
        if kind not in {'create', 'update', 'no_change'} | risky:
            raise ValueError('Unknown concept action')
        if kind == 'create' and (slug in occupied or slug in by_slug):
            raise ValueError('New concept already exists')
        if kind != 'create' and slug not in by_slug:
            raise ValueError('Unknown existing concept')
        action['reason'] = _text(action.get('reason'), 'Concept action reason')
        affected = _list(action.get('affected_point_ids', []), 'affected_point_ids')
        allowed = set(points) | set(by_slug.get(slug, {}).get('point_ids', []))
        if len(affected) != len(set(affected)) or set(affected) - allowed:
            raise ValueError('Unknown or duplicate affected point ID')
        if kind == 'create':
            action['title'] = _text(action.get('title'), 'Concept title')
            action['question'] = _text(action.get('question'), 'Concept question')
        if not isinstance(action.get('requires_review', False), bool):
            raise ValueError('requires_review must be boolean')
        if kind in risky or action.get('claim_changes') or action.get('removes_point_ids'):
            action['requires_review'] = True
        actions[slug] = action
    seen = set()
    for point in _list(result.get('point_decisions'), 'point_decisions'):
        if not isinstance(point, dict):
            raise ValueError('Point decision must be an object')
        pid = point.get('point_id')
        if pid not in points or pid in seen:
            raise ValueError('Unknown or duplicate point decision')
        seen.add(pid)
        disposition = point.get('disposition')
        memberships = _list(point.get('memberships'), 'memberships')
        if disposition not in {'linked', 'deferred'} or (disposition == 'linked') != bool(memberships):
            raise ValueError('linked needs memberships; deferred must have none')
        if disposition == 'deferred':
            point['reason'] = _text(point.get('reason'), 'Deferred reason')
            point.setdefault('reevaluate_when', ['point_changed', 'related_knowledge_changed', 'candidate_changed'])
        targets, explanations = set(), set()
        for membership in memberships:
            if not isinstance(membership, dict):
                raise ValueError('Membership must be an object')
            slug = membership.get('concept_slug')
            if not isinstance(slug, str) or slug in targets or slug not in actions:
                raise ValueError('Duplicate membership or target missing concept action')
            targets.add(slug)
            membership['role'] = _text(membership.get('role'), 'Membership role')
            membership['reason'] = _text(membership.get('reason'), 'Membership reason')
            explanation = (membership['role'], membership['reason'])
            if explanation in explanations:
                raise ValueError('Multiple memberships require distinct roles and reasons')
            explanations.add(explanation)
    if seen != set(points):
        raise ValueError('Every pending point must have exactly one decision')
    for slug, action in actions.items():
        assigned_ids = {p['point_id'] for p in result['point_decisions'] for m in p['memberships'] if m['concept_slug'] == slug}
        if set(action.get('affected_point_ids', [])) - (assigned_ids | set(by_slug.get(slug, {}).get('point_ids', []))):
            raise ValueError('Affected point does not belong to action concept')
        action.setdefault('affected_point_ids', sorted(assigned_ids))
        if action['action'] in {'create', 'update'} and not by_slug.get(slug, {}).get('point_ids') and not any(m['concept_slug'] == slug for p in result['point_decisions'] for m in p['memberships']):
            raise ValueError('Concept action has no assigned knowledge')
    required_versions = {slug: by_slug[slug].get('version') for slug in actions if slug in by_slug}
    result.setdefault('relation_actions', [])
    result.setdefault('unresolved', [])
    _list(result['unresolved'], 'unresolved')
    from .relations import RELATION_LABELS
    known_claim_ids = {cid for p in points.values() for cid in p.get('data', json.loads(p.get('data_json', '{}'))).get('claim_ids', [])}
    known_source_ids = {p.get('source_id') for p in points.values()}
    for concept in existing:
        known_claim_ids.update(c['id'] for c in concept.get('meta', {}).get('claims', []))
        known_source_ids.update(concept.get('meta', {}).get('source_ids', []))
        known_source_ids.update(sid for c in concept.get('meta', {}).get('claims', []) for sid in c.get('source_ids', []))
    seen_relations = set()
    for relation in _list(result['relation_actions'], 'relation_actions'):
        if not isinstance(relation, dict):
            raise ValueError('Relation action must be an object')
        origin = relation.get('source_slug', relation.get('concept_slug'))
        if origin not in actions or actions[origin]['action'] not in {'create', 'update'} or relation.get('target_slug') == origin:
            raise ValueError('Relation origin must be an updating concept and cannot target itself')
        relation['source_slug'] = origin
        if relation.get('kind') not in RELATION_LABELS or relation.get('target_slug') not in set(by_slug) | set(actions):
            raise ValueError('Unknown relation kind or target')
        if relation.get('basis') not in {'factual', 'editorial'}:
            raise ValueError('Relation must distinguish factual and editorial basis')
        _text(relation.get('reason', relation.get('summary')), 'Relation reason')
        claims = _list(relation.get('claim_ids'), 'Relation claim_ids')
        sources = _list(relation.get('source_ids'), 'Relation source_ids')
        if not claims or not sources or set(claims) - known_claim_ids or set(sources) - known_source_ids:
            raise ValueError('Relation references unknown or missing evidence IDs')
        key = (origin, relation['target_slug'], relation['kind'])
        if key in seen_relations:
            raise ValueError('Duplicate relation action')
        seen_relations.add(key)
        if relation['target_slug'] in by_slug:
            required_versions[relation['target_slug']] = by_slug[relation['target_slug']].get('version')
    supplied = result.get('base_versions', required_versions)
    if supplied != required_versions:
        raise ValueError('Concept base version conflict')
    result['base_versions'] = required_versions
    membership_versions = {slug: by_slug[slug].get('membership_version') for slug in required_versions}
    if result.get('base_membership_versions', membership_versions) != membership_versions:
        raise ValueError('Concept membership version conflict')
    result['base_membership_versions'] = membership_versions
    return result


def _tokens(value):
    text = json.dumps(value, ensure_ascii=False).lower()
    return set(re.findall(r'[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2}', text))


def _candidates(point, existing):
    tokens = _tokens(point.get('data', {}))
    pid = point.get('id')
    scored = []
    for concept in existing:
        overlap = len(tokens & _tokens({k: concept.get(k) for k in ('title', 'question', 'aliases')}))
        linked = pid in concept.get('point_ids', [])
        if linked or overlap:
            scored.append((100000 if linked else overlap, concept['slug'], concept))
    return [c for _, _, c in sorted(scored, key=lambda item: (-item[0], item[1]))[:12]]


def _candidate_digest(candidates):
    return canonical_digest({c['slug']: {'version': c.get('version'), 'membership_version': c.get('membership_version')} for c in candidates})


def _plan(decision, points, existing):
    by_slug = {c['slug']: c for c in existing}
    concepts = []
    for action in decision['concept_actions']:
        if action['action'] not in {'create', 'update'} or action.get('requires_review'):
            continue
        slug = action['concept_slug']
        old = by_slug.get(slug, {})
        ids = list(old.get('point_ids', []))
        reasons = {r['point_id']: copy.deepcopy(r) for r in old.get('assignment_reasons', [])}
        for pid in ids:
            reasons.setdefault(pid, {'point_id': pid, 'role': 'preserved', 'reason': 'Preserve previously accepted knowledge and its conditions'})
        for point in decision['point_decisions']:
            for membership in point['memberships']:
                if membership['concept_slug'] == slug:
                    pid = point['point_id']
                    if pid not in ids:
                        ids.append(pid)
                    reasons[pid] = {'point_id': pid, 'role': membership['role'], 'reason': membership['reason']}
        if set(ids) - set(points):
            raise ValueError('Existing concept contains unavailable knowledge points; review required')
        required = action.get('required_point_ids', ids)
        if not isinstance(required, list) or set(required) - set(ids) or len(required) != len(set(required)):
            raise ValueError('Unknown or duplicate required point ID')
        concepts.append({'slug': slug, 'title': action.get('title', old.get('title')), 'question': action.get('question', old.get('question')), 'point_ids': ids, 'assignment_reasons': list(reasons.values()), 'required_point_ids': required, 'action': action['action'], 'base_version': old.get('version'), 'base_membership_version': old.get('membership_version', canonical_digest([])), 'relation_actions': [copy.deepcopy(r) for r in decision['relation_actions'] if r.get('source_slug') == slug]})
    return {'concepts': concepts, 'excluded': []}


def plan_maintenance(ws, papers, points, existing, occupied, invoke, model_identity=None, *, source_ids=None):
    skill = (Path(__file__).resolve().parent.parent / 'skills/knowledge-maintenance/SKILL.md').read_text(encoding='utf-8')
    skill_hash = hashlib.sha256(skill.encode()).hexdigest()
    versions = {pid: point_version(point) for pid, point in points.items()}
    with sqlite3.connect(ws.db, timeout=30) as db:
        schema(db)
        states = {row[0]: row[1:] for row in db.execute('SELECT p.point_id,p.point_version_json,p.disposition,p.candidate_digest,d.skill_hash,d.model_identity FROM point_dispositions p LEFT JOIN maintenance_decisions d ON d.id=p.decision_id')}
    pending, candidates_by_id = {}, {}
    for pid, point in points.items():
        if source_ids is not None and point.get('source_id') not in source_ids:
            continue
        candidates = _candidates({**point, 'id': pid}, existing)
        candidates_by_id[pid] = candidates
        state = states.get(pid)
        changed = not state or json.loads(state[0]) != versions[pid]
        if state and (state[3] != skill_hash or state[4] != json.dumps(model_identity)):
            changed = True
        if state and state[1] == 'deferred' and state[2] != _candidate_digest(candidates):
            changed = True
        if changed:
            pending[pid] = point
    # A related new point is an explicit re-evaluation trigger for deferred knowledge.
    pending_tokens = _tokens([p.get('data', {}) for p in pending.values()]) if pending else set()
    for pid, point in points.items():
        if source_ids is not None and point.get('source_id') not in source_ids:
            continue
        if pid not in pending and states.get(pid, (None, None))[1] == 'deferred' and pending_tokens & _tokens(point.get('data', {})):
            pending[pid] = point
    candidates = {c['slug']: c for pid in pending for c in candidates_by_id.get(pid, [])}
    base = {'status': 'no_change', 'reasons': [], 'decision_id': None, 'input_digest': canonical_digest({'point_versions': versions, 'skill_hash': skill_hash, 'model_identity': model_identity}), 'decision': {}, 'plan': {'concepts': [], 'excluded': []}, 'pending_point_ids': sorted(pending), 'point_versions': versions, 'candidate_slugs': sorted(candidates)}
    if not pending:
        return base
    related_ids = set(pending) | {pid for c in candidates.values() for pid in c.get('point_ids', [])}
    related_points = {pid: points[pid] for pid in sorted(related_ids) if pid in points}
    source_materials = []
    for sid in sorted({p.get('source_id') for p in related_points.values()}):
        packet = papers.get(sid, {}).get('packet', {})
        data = [p.get('data', json.loads(p.get('data_json', '{}'))) for p in related_points.values() if p.get('source_id') == sid]
        result_ids = {rid for point in data for rid in point.get('result_ids', [])}
        results = [r for r in packet.get('results', []) if r.get('id') in result_ids]
        claim_ids = {cid for point in data + results for cid in point.get('claim_ids', [])}
        originals, selected_claims = {}, []
        for claim in packet.get('claims', []):
            if claim.get('id') not in claim_ids:
                continue
            normalized = copy.deepcopy(claim)
            locations = normalized.setdefault('evidence_locations', [])
            for passage in normalized.pop('evidence_passages', []):
                quote = passage.get('quote', '')
                if not quote:
                    locations.append(passage)
                    continue
                original_id = 'evidence-' + canonical_digest({'source_id': sid, 'paragraph_id': passage.get('paragraph_id', passage.get('locator')), 'quote': quote})[:24]
                original = {k: v for k, v in passage.items() if k != 'quote'}
                originals.setdefault(original_id, {**original, 'id': original_id, 'text': quote})
                locations.append({**original, 'original_id': original_id, 'quote_start': 0, 'quote_end': len(quote)})
            selected_claims.append(normalized)
        source_materials.append({'source_id': sid, 'source_version': papers.get(sid, {}).get('source_version'), 'claims': selected_claims, 'results': results, 'originals': list(originals.values())})
    context = {'decision_version': VERSION, 'pending_point_ids': sorted(pending), 'knowledge_points': [{**p.get('data', json.loads(p.get('data_json', '{}'))), **{k:p.get(k) for k in ('source_id', 'source_version', 'local_id')}, 'id': pid} for pid, p in related_points.items()],
               'existing_concepts': [{**{k: c.get(k) for k in ('slug', 'title', 'question', 'point_ids', 'aliases', 'version', 'membership_version')}, 'body': c.get('body', '')[:16000], 'meta': copy.deepcopy({k: c.get('meta', {}).get(k) for k in ('claims', 'relations', 'knowledge_topic')})} for c in candidates.values()],
               'source_materials': source_materials, 'point_versions': {pid: versions[pid] for pid in related_points}, 'candidate_digests': {pid: _candidate_digest(candidates_by_id.get(pid, [])) for pid in pending}, 'model_identity': model_identity, 'skill_hash': skill_hash}
    # Old concept assertions can repeat the same full paragraphs as L2 claims.
    # Keep a shared evidence bank rather than multiplying those archived quotes.
    material_by_source = {m['source_id']: m for m in source_materials}
    for concept in context['existing_concepts']:
        for claim in concept['meta'].get('claims') or []:
            locations = claim.setdefault('evidence_locations', [])
            for passage in claim.pop('evidence_passages', []):
                quote, sid = passage.get('quote', ''), passage.get('source_id')
                if not quote:
                    locations.append(passage)
                    continue
                material = material_by_source.get(sid)
                if material is None:
                    material = {'source_id': sid, 'source_version': None, 'claims': [], 'results': [], 'originals': []}
                    source_materials.append(material)
                    material_by_source[sid] = material
                original_id = 'evidence-' + canonical_digest({'source_id': sid, 'paragraph_id': passage.get('paragraph_id', passage.get('locator')), 'quote': quote})[:24]
                original = {k: v for k, v in passage.items() if k != 'quote'}
                if not any(o['id'] == original_id for o in material['originals']):
                    material['originals'].append({**original, 'id': original_id, 'text': quote})
                locations.append({**original, 'original_id': original_id, 'quote_start': 0, 'quote_end': len(quote)})
    input_digest = canonical_digest(context)
    budget_key = canonical_digest({k: v for k, v in context.items() if k not in {'model_identity', 'skill_hash', 'decision_version'}})
    context['input_digest'] = input_digest
    identifier = 'maintenance-' + input_digest[:24]
    base.update(decision_id=identifier, input_digest=input_digest)
    with sqlite3.connect(ws.db, timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO maintenance_decisions (id,input_digest,input_json,status,model_identity,skill_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)', (identifier, input_digest, json.dumps(context, ensure_ascii=False), 'pending', json.dumps(model_identity), skill_hash, utcnow(), utcnow()))
    while True:
        from .worker import check_cancelled
        check_cancelled()
        with sqlite3.connect(ws.db, timeout=30) as db:
            db.execute('BEGIN IMMEDIATE')
            saved, attempts, errors = db.execute('SELECT decision_json,attempts,errors_json FROM maintenance_decisions WHERE id=?', (identifier,)).fetchone()
            if saved:
                decision = validate_decision(json.loads(saved), pending, list(candidates.values()), occupied, input_digest)
                break
            db.execute('INSERT OR IGNORE INTO maintenance_budgets (operation_key,attempts) VALUES (?,0)', (budget_key,))
            budget_attempts = db.execute('SELECT attempts FROM maintenance_budgets WHERE operation_key=?', (budget_key,)).fetchone()[0]
            if attempts >= MAX_ATTEMPTS or budget_attempts >= MAX_ATTEMPTS:
                base.update(status='blocked', reasons=json.loads(errors) or ['Maintenance request budget exhausted'])
                return base
            db.execute('UPDATE maintenance_decisions SET attempts=attempts+1,updated_at=? WHERE id=?', (utcnow(), identifier))
            db.execute('UPDATE maintenance_budgets SET attempts=attempts+1 WHERE operation_key=?', (budget_key,))
            attempt_id = db.execute('INSERT INTO maintenance_attempts (decision_id,attempt,at) VALUES (?,?,?)', (identifier, attempts + 1, utcnow())).lastrowid
        try:
            request = copy.deepcopy(context)
            if errors != '[]':
                request['validation_errors'] = json.loads(errors)[-1:]
            with sqlite3.connect(ws.db, timeout=30) as db:
                db.execute('UPDATE maintenance_attempts SET request_json=? WHERE id=?', (json.dumps(request, ensure_ascii=False), attempt_id))
            value = invoke('maintain', skill + '\n' + CONTRACT, request)
            with sqlite3.connect(ws.db, timeout=30) as db:
                db.execute('UPDATE maintenance_attempts SET response_json=? WHERE id=?', (json.dumps(value, ensure_ascii=False, default=str), attempt_id))
            if isinstance(value, str):
                value = json.loads(value)
            decision = validate_decision(value, pending, list(candidates.values()), occupied, input_digest)
            plan = _plan(decision, points, existing)
            with sqlite3.connect(ws.db, timeout=30) as db:
                db.execute('UPDATE maintenance_decisions SET decision_json=?,status=?,updated_at=? WHERE id=?', (json.dumps(decision, ensure_ascii=False), 'ready', utcnow(), identifier))
            break
        except JobConflict as exc:
            with sqlite3.connect(ws.db, timeout=30) as db:
                db.execute('UPDATE maintenance_decisions SET attempts=MAX(0,attempts-1),updated_at=? WHERE id=?', (utcnow(), identifier))
                db.execute('UPDATE maintenance_budgets SET attempts=MAX(0,attempts-1) WHERE operation_key=?', (budget_key,))
                db.execute('UPDATE maintenance_attempts SET error=? WHERE id=?', ('JobConflict: ' + str(exc), attempt_id))
            raise
        except (ValueError, TypeError, KeyError) as exc:
            with sqlite3.connect(ws.db, timeout=30) as db:
                history = json.loads(db.execute('SELECT errors_json FROM maintenance_decisions WHERE id=?', (identifier,)).fetchone()[0])
                history.append(type(exc).__name__ + ': ' + str(exc))
                db.execute('UPDATE maintenance_decisions SET errors_json=?,status=?,updated_at=? WHERE id=?', (json.dumps(history, ensure_ascii=False), 'pending', utcnow(), identifier))
                db.execute('UPDATE maintenance_attempts SET error=? WHERE id=?', (type(exc).__name__ + ': ' + str(exc), attempt_id))
        except Exception as exc:
            with sqlite3.connect(ws.db, timeout=30) as db:
                db.execute('UPDATE maintenance_attempts SET error=? WHERE id=?', (type(exc).__name__ + ': ' + str(exc), attempt_id))
            raise
    base['decision'] = decision
    try:
        base['plan'] = _plan(decision, points, existing)
    except ValueError as exc:
        base.update(status='blocked', reasons=[str(exc)])
        return base
    risks = [a['reason'] for a in decision['concept_actions'] if a.get('requires_review')]
    if risks or decision['unresolved']:
        base.update(status='blocked', reasons=risks + [str(v) for v in decision['unresolved']])
    else:
        base['status'] = 'ready' if base['plan']['concepts'] else 'no_change'
    return base


def apply_decision(db, decision_id, decision, point_versions, concept_slugs=None):
    """Apply inside the caller's publish transaction; never commit or dirty indexes."""
    schema(db)
    if decision.get('unresolved') or any(a.get('requires_review') or a['action'] not in {'create', 'update', 'no_change'} for a in decision['concept_actions']):
        raise ValueError('Pending review decision cannot be applied')
    actions = {a['concept_slug']: a for a in decision['concept_actions']}
    selected = set(actions) if concept_slugs is None else set(concept_slugs)
    if selected - set(actions):
        raise ValueError('Unknown application concept group')
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for point in decision['point_decisions']:
        pid = point['point_id']
        if pid not in point_versions:
            raise ValueError('Missing knowledge point version')
        if 'knowledge_points' in tables:
            row = db.execute('SELECT source_id,source_version,data_json FROM knowledge_points WHERE id=?', (pid,)).fetchone()
            if not row or point_version({'source_id': row[0], 'source_version': row[1], 'data_json': row[2]}) != point_versions[pid]:
                raise ValueError('Knowledge point version changed before application')
    now = utcnow()
    for point in decision['point_decisions']:
        pid = point['point_id']
        for membership in point['memberships']:
            slug = membership['concept_slug']
            if slug in selected:
                db.execute('INSERT INTO concept_memberships (concept_slug,point_id,reason,role,status,decision_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(concept_slug,point_id) DO UPDATE SET reason=excluded.reason,role=excluded.role,status=excluded.status,decision_id=excluded.decision_id,updated_at=excluded.updated_at', (slug, pid, membership['reason'], membership['role'], 'active', decision_id, now, now))
        complete = all(m['concept_slug'] in selected or db.execute('SELECT 1 FROM maintenance_executions WHERE decision_id=? AND concept_slug=?', (decision_id, m['concept_slug'])).fetchone() for m in point['memberships'])
        if complete:
            row = db.execute('SELECT input_json FROM maintenance_decisions WHERE id=?', (decision_id,)).fetchone()
            context = json.loads(row[0]) if row else {}
            candidate_digest = context.get('candidate_digests', {}).get(pid, _candidate_digest([]))
            db.execute('INSERT OR REPLACE INTO point_dispositions (point_id,disposition,reason,point_version_json,candidate_digest,reevaluate_json,decision_id,updated_at) VALUES (?,?,?,?,?,?,?,?)', (pid, point['disposition'], point.get('reason', point.get('change_summary', '')), json.dumps(point_versions[pid], sort_keys=True), candidate_digest, json.dumps(point.get('reevaluate_when', [])), decision_id, now))
    for slug in selected:
        db.execute('INSERT OR REPLACE INTO maintenance_executions (decision_id,concept_slug,status,at) VALUES (?,?,?,?)', (decision_id, slug, 'applied', now))
    done = {row[0] for row in db.execute('SELECT concept_slug FROM maintenance_executions WHERE decision_id=?', (decision_id,))}
    if set(actions) <= done:
        db.execute('UPDATE maintenance_decisions SET status=?,updated_at=? WHERE id=?', ('applied', now, decision_id))
