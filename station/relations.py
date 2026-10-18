"""Small, claim-backed knowledge relations stored beside existing page records."""
import re
import hashlib
import json

RELATION_LABELS = {
    'implementation': ('实现实例', '所用机制'),
    'shared_mechanism': ('共同机制', '共同机制'),
    'contrast': ('差异对照', '差异对照'),
    'design_inference': ('组合建议', '组合建议'),
}


def relation_id(slug, relation):
    """Program-owned identity; wording, evidence order and local claim IDs may evolve."""
    identity = [slug, relation.get('target_slug'), relation.get('kind'), relation.get('basis', 'factual')]
    return 'rel-' + hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:24]


def validate_relations(values, claims, source_ids, known_slugs, slug):
    if not isinstance(values, list) or len(values) > 40:
        raise ValueError('知识关系须为至多40项记录。')
    by_id = {c['id']: c for c in claims}
    result, seen, seen_keys = [], set(), set()
    for relation in values:
        if not isinstance(relation, dict):
            raise ValueError('知识关系格式错误。')
        target, kind = (relation.get(k) for k in ['target_slug', 'kind'])
        rid = relation.get('id') or relation_id(slug, relation)
        key = (target, kind, relation.get('basis', 'factual'))
        if not isinstance(rid, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', rid) or rid in seen:
            raise ValueError('知识关系标识无效或重复。')
        if not isinstance(target, str) or target == slug or target not in known_slugs or kind not in RELATION_LABELS:
            raise ValueError('知识关系类型或目标无效。')
        summary, scope = relation.get('summary'), relation.get('scope')
        if not isinstance(summary, str) or not 15 <= len(summary.strip()) <= 1200 or not isinstance(scope, str) or not 8 <= len(scope.strip()) <= 1200:
            raise ValueError('知识关系需要具体机制说明及适用范围。')
        cids, sids = relation.get('claim_ids'), relation.get('source_ids')
        if not isinstance(cids, list) or not cids or len(cids) > 8 or any(not isinstance(c, str) or c not in by_id for c in cids):
            raise ValueError('知识关系缺少已记录的断言依据。')
        if not isinstance(sids, list) or not sids or any(not isinstance(s, str) or s not in source_ids for s in sids):
            raise ValueError('知识关系引用了未知来源。')
        supporting = {s for c in cids for s in by_id[c]['source_ids']}
        quoted = {p['source_id'] for c in cids for p in by_id[c].get('evidence_passages', [])}
        if not set(sids) <= supporting & quoted:
            raise ValueError('知识关系来源缺少对应断言与原文摘录。')
        if kind in {'shared_mechanism', 'contrast', 'design_inference'} and len(set(sids)) < 2:
            raise ValueError('跨研究机制关系需要双方来源。')
        basis = relation.get('basis')
        if basis is not None and basis not in {'factual', 'editorial'}:
            raise ValueError('知识关系须区分原文事实与编辑解释。')
        if key in seen_keys:
            raise ValueError('知识关系目标、类型及依据重复。')
        seen.add(rid)
        seen_keys.add(key)
        result.append({'id': rid, 'target_slug': target, 'kind': kind, 'summary': summary.strip(),
                       'scope': scope.strip(), 'claim_ids': sorted(set(cids)), 'source_ids': sorted(set(sids)),
                       **({'basis': basis} if basis is not None else {})})
    return result


def require_approved_relations(values, actions, previous, slug):
    """Writing may express approved maintenance relations and retain old ones."""
    def key(value):
        return (value.get('target_slug'), value.get('kind'), value.get('basis', 'factual'))
    allowed = {key(v) for v in previous}
    required = {key(v) for v in actions if v.get('source_slug', v.get('concept_slug')) == slug}
    actual = {key(v) for v in values}
    if actual - allowed - required:
        raise ValueError('Concept contains a relation not approved by maintenance')
    if required - actual:
        raise ValueError('Concept omitted an approved maintenance relation')
