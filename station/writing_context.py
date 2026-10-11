"""Bounded original-text selection; no model or separate search database."""
import re


SECTION = re.compile(r'^\s*(?:#{1,6}\s*|\d+(?:\.\d+)*[.\s]+)?'
                     r'(?:methods?|methodology|approach|algorithm|planning|implementation|'
                     r'experiments?|results?|evaluation|limitations?|discussion|conclusion|'
                     r'方法|规划|计划|实现|实验|结果|局限|限制|讨论|结论)\b[^\n]{0,160}', re.I | re.M)


def original_context(text, budget):
    """Keep verbatim spans from opening, sections and across a long original.

    Offsets address the archived extracted text, not PDF page numbers. Missing
    regions remain explicitly missing; this selection never claims full reading.
    """
    budget = max(1, int(budget))
    if len(text) <= budget:
        return {'text': text, 'is_excerpt': False, 'text_segments': [
            {'start': 0, 'end': len(text)}], 'original_chars': len(text)}
    marker = '\n\n[中间原文未提供]\n\n'
    # Markers also consume the same context budget.
    span_width = max(1, min(4000, budget // 8))
    budget -= min(budget // 4, len(marker) * (budget // span_width + 2))
    # Allocate at least one span to the opening, section headings and the end.
    width = max(1, min(4000, budget // 8))
    headings = [m.start() for m in SECTION.finditer(text)]
    starts = [0]
    # Round-robin section types instead of letting a long Methods section take all.
    buckets = {}
    for pos in headings:
        label = SECTION.match(text, pos)[0].strip().casefold()
        key = re.sub(r'^[#\d.\s]+', '', label).split()[0]
        buckets.setdefault(key, []).append(pos)
    while any(buckets.values()):
        for values in buckets.values():
            if values:
                starts.append(values.pop(0))
    starts.insert(2, max(0, len(text) - width))
    # Reserve half the budget for section coverage, half for distributed sampling.
    prioritized = starts[:max(2, budget // width // 2)]
    distributed = [round(i * (len(text) - width) / 5) for i in range(1, 5)]
    starts = prioritized + distributed + starts[len(prioritized):]
    intervals, remaining = [], budget
    for pos in starts:
        end = min(len(text), pos + min(width, remaining))
        # Subtract overlaps so each character consumes budget only once.
        pieces = [(pos, end)]
        for a, b in intervals:
            pieces = [(x, min(y, a)) for x, y in pieces if x < a] + [
                (max(x, b), y) for x, y in pieces if y > b]
        for a, b in pieces:
            if b > a and remaining:
                b = min(b, a + remaining)
                intervals.append((a, b)); remaining -= b - a
        if not remaining:
            break
    # Fill unused budget deterministically without losing section-selected spans.
    if remaining:
        for pos in range(0, len(text), width):
            if any(a <= pos < b for a, b in intervals):
                continue
            end = min(pos + width, len(text), pos + remaining)
            next_start = min((a for a, _ in intervals if a > pos), default=end)
            end = min(end, next_start)
            if end > pos:
                intervals.append((pos, end)); remaining -= end - pos
            if not remaining:
                break
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    segments = [{'start': a, 'end': b} for a, b in merged]
    return {'text': marker.join(text[s['start']:s['end']] for s in segments),
            'is_excerpt': True, 'text_segments': segments, 'original_chars': len(text)}


def relevance_score(query, title, body):
    """Use existing bilingual terminology to rank pages within a topic."""
    query, title, body = query.casefold(), title.casefold(), body.casefold()
    words = set(re.findall(r'[a-z][a-z0-9-]{2,}', title + ' ' + body[:12000]))
    common = {'agent', 'agents', 'model', 'models', 'language', 'paper', 'source',
              'source_ids', 'claims', 'the', 'and', 'with', 'from', 'that', 'this'}
    hits = sum(1 for w in words - common if re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', query))
    chinese = re.findall(r'[\u4e00-\u9fff]{2,}', title)
    return hits + sum(4 for phrase in chinese if phrase in query) + (8 if title and title in query else 0)


def context_size(text):
    return len(text) + (len(text) // 600 + 2) * 60


def marked_original(text, budget):
    """Give the writer addressable verbatim excerpts within the same call.

    The model selects an excerpt ID; the program copies its original bytes into
    a claim. This avoids asking the model to retype long quotations accurately.
    """
    selected = original_context(text, budget if context_size(text) <= budget else max(1, int(budget * .8)))
    blocks, lookup = [], {}
    for segment in selected['text_segments']:
        start = segment['start']
        while start < segment['end']:
            end = min(start + 1600, segment['end'])
            # Prefer a paragraph break; never reconstruct or normalize the source.
            boundary = text.rfind('\n\n', start + 600, end)
            if boundary >= start + 600:
                end = boundary + 2
            eid = 'e' + str(len(lookup) + 1)
            quote = text[start:end]
            lookup[eid] = {'quote': quote, 'locator': f'提取文本字符 {start}–{end}'}
            blocks.append(f'[E:{eid}; 字符{start}–{end}]\n' + quote)
            start = end
    return {**selected, 'text': '\n\n'.join(blocks)}, lookup
