#!/usr/bin/env python3
"""Create an immutable-text reading snapshot from parsed JSON or plain text."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def prepare(path, source_id=None):
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if path.suffix.lower() == '.json':
        original = json.loads(data)
        paragraphs = original.get('paragraphs')
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError('JSON input must contain nonempty paragraphs; use parsed text, not metadata')
        units = []
        for paragraph in paragraphs:
            text = paragraph['text']
            if not isinstance(text, str):
                raise ValueError('paragraph.text must be a string')
            units.append({k: paragraph[k] for k in (
                'id', 'text', 'page', 'page_label', 'start', 'end',
                'global_start', 'global_end', 'method') if k in paragraph})
        sid = source_id or original.get('source_id') or path.stem
        version = original.get('source_version') or digest
        boundary = original.get('coverage', {})
        title = original.get('title', path.stem)
        status = original.get('status', 'unknown')
    else:
        text = data.decode('utf-8')
        units = []
        for n, match in enumerate(re.finditer(r'\S.*?(?=\n\s*\n|\Z)', text, re.S), 1):
            units.append({'id': f'u{n:04}', 'text': match.group(),
                          'global_start': match.start(), 'global_end': match.end(),
                          'method': 'utf8-text'})
        sid, version = source_id or path.stem, digest
        title, status = path.stem, 'text-only'
        boundary = {'boundaries': 'Text paragraphs only; no verified PDF page or figure coordinates'}
        original = {}
    if not units or any(not u.get('id') for u in units):
        raise ValueError('Input has no identifiable reading units')
    if len({u['id'] for u in units}) != len(units):
        raise ValueError('Reading unit IDs are not unique')
    return {'schema_version': 'knowledge-source/v1', 'source_id': sid,
            'title': title, 'source_version': version, 'artifact_sha256': digest,
            'original_path': str(path.resolve()), 'library_id': original.get('library_id'),
            'parser_version': original.get('parser_version', 'utf8-paragraphs-v1'),
            'status': status, 'coverage': boundary,
            'warnings': original.get('warnings', []), 'units': units}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-id')
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Output cannot overwrite the original')
    result = prepare(args.input, args.source_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'source_id': result['source_id'], 'units': len(result['units']),
                      'status': result['status'], 'output': str(args.output.resolve())}, ensure_ascii=False))


if __name__ == '__main__':
    main()
