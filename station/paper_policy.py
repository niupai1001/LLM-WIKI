"""Explicit admission rules for the PDF-only paper rebuild."""
import json
from pathlib import Path


def configuration(ws):
    if ws is None:
        return {}
    path = ws.root / 'paper-workflow.json'
    return json.loads(path.read_text()) if path.exists() else {}


def enabled(ws):
    return configuration(ws).get('enabled') is True


class AutomaticWritingPaused(ValueError):
    """This library assigns writing to a human-driven Codex session."""


def require_automatic_writing(ws, operation='编写'):
    """Guard writing entry points without disabling query or other LLM uses."""
    if configuration(ws).get('writing_executor') == 'codex_manual':
        raise AutomaticWritingPaused(
            f'{operation}自动编写已暂停（writing_executor=codex_manual）；'
            '请由 Codex GPT-6.1-sol（low）手动执行。当前未接入 Codex API，'
            '不会回退调用 DeepSeek。')


def pdf_original(ws, record):
    for name in (record.get('pdf_path'), record.get('asset_path')):
        if not isinstance(name, str) or Path(name).suffix.lower() != '.pdf':
            continue
        path = (ws.originals / name).resolve()
        if not path.is_relative_to(ws.originals.resolve()) or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(5) == b'%PDF-':
                return path
    return None


def exclusion_reason(ws, record):
    if record.get('kind') != 'paper':
        return '资讯及网页文章暂不提炼，等待专用流程'
    if pdf_original(ws, record) is None:
        return '没有真实PDF原件；TXT及其他非PDF来源不入库'
    return ''


def selected_sources(ws, source_ids=None, category=None):
    from .common import load_json
    from .documents import source_version
    import re
    ids = source_ids if source_ids is not None else [p.stem for p in sorted(ws.raw.glob('*.json'))]
    selected, excluded, versions = [], [], {}
    for sid in ids:
        if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{16}', sid):
            raise ValueError('Select valid archived source IDs')
        path = ws.raw / (sid + '.json')
        if not path.is_file():
            raise ValueError('原始资料已移除或不存在')
        record = load_json(path)
        if category and record.get('category') != category:
            continue
        reason = exclusion_reason(ws, record)
        if reason:
            if source_ids is not None:
                raise ValueError(reason)
            excluded.append({'source_id': sid, 'reason': reason})
            continue
        selected.append(sid)
        versions[sid] = source_version(ws, sid)
    if not selected:
        raise ValueError('没有可提炼的PDF论文')
    return selected, excluded, versions
