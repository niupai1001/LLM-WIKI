"""Small, version-bound trials of the Wiki synthesis skill, separate from old jobs."""
import copy
import hashlib
import json
from pathlib import Path

from . import common
from .compiler import digest
from .documents import source_version
from .knowledge_synthesis import _inputs, _model_identity
from .workspaces import use_workspace

SKILL = Path(__file__).resolve().parent.parent / 'skills/wiki-knowledge-synthesis/SKILL.md'
SCOPES = {
    'failure': {
        'title': '失败归因', 'topic': 'trial-failure-diagnosis',
        'sources': ['0b44ee6389202cb6', 'eddf2edee3305425'],
        'pages': [{'slug': 'wiki-failure-attribution', 'title': '失败归因'},
                  {'slug': 'wiki-attribution-step-representation', 'title': '面向归因的步骤表示'}],
        'question': '如何从失败执行中找到真正需要纠正的步骤？',
    },
    'reflection': {
        'title': '反思与记忆', 'topic': 'trial-reflection-memory',
        'sources': ['3fb5d6b0a837235b', 'accd5feccfb2c025'],
        'pages': [{'slug': 'wiki-verbal-reflection', 'title': '言语反思'},
                  {'slug': 'wiki-memory-admission', 'title': '持久记忆准入'}],
        'question': '如何把行动反馈变成经验，并决定哪些经验进入下一次决策？',
    },
}


def context_for(ws, scope):
    """Full internal semantics, excluding review logs and obsolete index bodies."""
    papers, points, stale = _inputs(ws)
    selected = set(scope['sources'])
    if selected - papers.keys() or any(s.get('source_id') in selected for s in stale):
        raise ValueError('Trial inputs are missing or stale')
    sources, claims, chosen = [], {}, {}
    for sid in scope['sources']:
        paper = papers[sid]
        from .knowledge_navigation import _reviewed
        _reviewed(paper)
        source_claims = []
        for c in paper['packet']['claims']:
            key = sid + ':' + c['id']
            claims[key] = copy.deepcopy(c)
            source_claims.append({'ref': key, 'text': c['text'], 'scope': c.get('scope', ''), 'passages': [
                {'source_id': p['source_id'], 'quote': p['quote'], 'locator': p['locator']}
                for p in c.get('evidence_passages', [])]})
        source_points = []
        for pid, point in points.items():
            if point['source_id'] == sid:
                chosen[pid] = point
                source_points.append({'id': pid, **{k: copy.deepcopy(point['data'].get(k)) for k in (
                    'proposition', 'explanation', 'scope', 'type', 'basis', 'value', 'transferability', 'uncertainty',
                    'claim_ids', 'result_ids')}, 'source_version': paper['source_version'],
                    'claim_refs': [sid + ':' + c for c in point['data']['claim_ids']]})
        sources.append({'source_id': sid, 'source_version': paper['source_version'], 'title': paper['record']['title'],
                        'reading': paper['packet']['body_markdown'],
                        'points': source_points, 'claims': source_claims})
    return {'topic': scope['title'], 'question': scope['question'], 'pages': scope['pages'], 'sources': sources}, claims, chosen


# Historical draft validators below remain useful for inspecting old artifacts.
# New trials deliberately have no separate author/reviewer prompts.
PROTOCOL = "shared-maintenance-synthesis/v1"


def validate_draft(draft, scope, claims, points):
    from .wiki import citation_ids
    expected = {p['slug']: p['title'] for p in scope['pages']}
    pages = draft.get('pages', [])
    if len(pages) != len(expected) or {p.get('slug') for p in pages} != set(expected):
        raise ValueError('Trial must return exactly the selected pages')
    for page in pages:
        body = page.get('body_markdown', '')
        if page.get('title') != expected[page['slug']] or len(body) < 600 or '\n## ' not in body:
            raise ValueError('Trial page lacks a complete formatted explanation')
        refs = [b['claim_ref'] for b in page.get('claim_bindings', [])]
        if not refs or len(refs) != len(set(refs)) or set(refs) - claims.keys():
            raise ValueError('Unknown or duplicate trial claims')
        sids = {r.split(':', 1)[0] for r in refs}
        if citation_ids(body) != sids:
            raise ValueError('Citations must match actual bound source claims')
        for binding in page['claim_bindings']:
            if not binding.get('body_quote') or binding['body_quote'] not in body:
                raise ValueError('Claim binding does not locate in body: ' + page['slug'] + ' / ' + binding['claim_ref'])
        seen = set()
        for binding in page.get('point_bindings', []):
            pid = binding['point_id']
            if pid not in points or pid in seen or not binding.get('body_quote') or binding['body_quote'] not in body:
                raise ValueError('Point binding missing, duplicate or not in body: ' + page['slug'] + ' / ' + str(pid))
            seen.add(pid)
            available = {points[pid]['source_id'] + ':' + c for c in points[pid]['data']['claim_ids']}
            selected = set(binding.get('claim_refs', []))
            if not selected or not selected <= set(refs) or not available & selected:
                raise ValueError('Point binding needs page-bound source claims: ' + page['slug'] + ' / ' + pid
                                 + '; missing from page: ' + ','.join(sorted(selected - set(refs))))
        if not seen:
            raise ValueError('Trial page has no actual knowledge membership')
    topic = draft.get('topic_body', '')
    if len(topic) < 100 or any('[[' + slug not in topic for slug in expected):
        raise ValueError('Topic must explain routes to both entries')


def validate_review(draft, review):
    rows = review.get('pages', [])
    if not isinstance(review.get('topic_issues'), list):
        raise ValueError('Independent check omitted the topic reading')
    if len(rows) != len(draft['pages']) or {r.get('slug') for r in rows} != {p['slug'] for p in draft['pages']}:
        raise ValueError('Independent check omitted a page')
    issues = list(review.get('topic_issues', []))
    for page in draft['pages']:
        row = next(r for r in rows if r['slug'] == page['slug'])
        if not isinstance(row.get('issues'), list):
            raise ValueError('Independent check omitted the full body reading')
        checks = row.get('claim_reviews', [])
        refs = {b['claim_ref'] for b in page['claim_bindings']}
        if len(checks) != len(refs) or {r.get('claim_id') for r in checks} != refs:
            raise ValueError('Independent check omitted a bound fact')
        issues.extend(row.get('issues', []))
        issues.extend(c.get('reason', 'Unsupported claim') for c in checks if c.get('status') != 'supported' or not c.get('reason'))
    return issues


def run_trial(ws, name, model=None):
    """Draft with the production pipeline; never publish trial pages implicitly."""
    from .knowledge_synthesis import synthesize_library, writing_system
    scope = SCOPES[name]
    ctx, _, _ = context_for(ws, scope)
    result = synthesize_library(ws, model=model, source_ids=scope['sources'], publish_pages=False)
    identity = {'library_id': ws.library_id, 'scope': name, 'protocol': PROTOCOL,
                'context_digest': digest(ctx), 'writing_hash': digest(writing_system()),
                'model': _model_identity(model)}
    root = ws.root / 'wiki_experiments' / name / PROTOCOL.replace('/', '-') / digest(identity)
    status = 'ready' if result['status'] == 'completed' else 'blocked'
    output = {'status': status, 'identity': identity, 'synthesis': result,
              'human_acceptance': 'not_performed'}
    common.save_json(root / 'result.json', output)
    return root, output


def publish_trial(ws, name, root):
    """Apply a ready shared-pipeline trial with the production version checks."""
    from .knowledge_synthesis import synthesize_library, writing_system
    from .publication import publication_lock
    result = common.load_json(Path(root) / 'result.json')
    identity = result.get('identity', {})
    if (result.get('status') != 'ready' or identity.get('protocol') != PROTOCOL
            or identity.get('library_id') != ws.library_id or identity.get('scope') != name
            or identity.get('writing_hash') != digest(writing_system())):
        raise ValueError('Trial is not ready for this workspace and Skill protocol')
    with use_workspace(ws), publication_lock(ws):
        ctx, _, _ = context_for(ws, SCOPES[name])
        if identity.get('context_digest') != digest(ctx):
            raise ValueError('Trial inputs changed after review')
        return synthesize_library(ws, source_ids=SCOPES[name]['sources'],
            prepared_run=result['synthesis']['maintenance'])
