"""Explain recorded evidence and its limits; never score truth or infer consensus.

Wiki metadata may contain ``claims`` (text, source_ids, scope, status),
``conflicts`` (title, sides[{text, source_ids, scope}], status, handling),
and curator-verified ``independent_source_groups`` (id, source_ids, basis,
status="verified"). Source bibliography verification checks metadata only.
All helpers are local, read-only and return escaped HTML.
"""
import html
import json
import re
from urllib.parse import quote, urlsplit

from .common import META, RAW, canonical, identifier

_SID = re.compile(r"[a-f0-9]{16}")
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_CLAIM_STATUSES = {"单源支持", "多源一致", "范围差异", "未解决冲突", "证据不足"}
_CONFLICT_STATUSES = {"未解决", "保留分歧", "范围已区分", "待核查"}
_KINDS = {"paper": "论文", "article": "文章", "news": "资讯", "note": "个人笔记", "document": "文档"}
_CONTENT = {"full-text": "全文文本已获取", "summary-only": "只有摘要，未取得全文文本", "missing": "全文文本缺失"}


def _text(value, default=""):
    return value.strip() if isinstance(value, str) and value.strip() else default


def _escape(value):
    return html.escape(_text(value), quote=True)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _ids(value):
    return sorted({v for v in _list(value) if isinstance(v, str) and _SID.fullmatch(v)})


def _read(path):
    try:
        return _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _url(value):
    value = _text(value)
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def _bibliography(record):
    return _dict(record.get("bibliography"))


def _metadata_status(record):
    bib = _bibliography(record)
    fallback = _dict(record.get("metadata_verification"))
    status = _text(bib.get("metadata_status"), _text(fallback.get("status"), "unverified"))
    status_text = {"verified": "来源元数据已核对", "partial": "来源元数据部分核对",
                   "failed": "来源元数据查询失败", "unverified": "来源元数据未核实"}.get(status, "来源元数据未核实")
    method = _text(bib.get("method"), _text(fallback.get("by")))
    when = _text(bib.get("checked_at"), _text(fallback.get("at")))
    details = []
    if method:
        details.append("方法：" + method)
    if when:
        details.append("核对时间：" + when)
    if not details and status == "verified":
        status_text = "已登记元数据核对，核对方法与时间未记录"
    return status_text, details


def _publication(record):
    published = _dict(_bibliography(record).get('publication_record'))
    if published.get('status') == 'verified-on-official-site':
        return '已核对正式发表记录：' + _text(published.get('venue'), '出版来源') + '；当前原件为 arXiv 版本'
    status = _text(_bibliography(record).get("publication_status"), _text(record.get("publication_status"), "unknown"))
    return {"arxiv-preprint": "arXiv 预印本版本；同行评审状态未核实",
            "preprint": "来源标注为预印本；同行评审状态未核实",
            "journal-reference-provided": "来源提供期刊引用；同行评审状态未核实",
            "peer-reviewed": "来源标注为同行评审论文；评审状态仍需独立核实"}.get(status, "发表与同行评审状态未核实")


def _source_summary(record):
    kind = _KINDS.get(_text(record.get("kind")), "来源类型未核实")
    content = _CONTENT.get(_text(record.get("content_status")), "原文获取状态未记录")
    if record.get("content_status") == "missing" and _text(record.get("asset_path")):
        content = "已登记归档原件，缺少可提取的全文文本"
    meta_status, _ = _metadata_status(record)
    return " · ".join([kind, content, meta_status, _publication(record)])


def source_evidence(record):
    """A source's acquisition and verification record, without contacting its host."""
    record = _dict(record)
    meta_status, details = _metadata_status(record)
    bib = _bibliography(record)
    checks = [v for v in _list(_dict(record.get("metadata_verification")).get("checks")) if isinstance(v, str)]
    if isinstance(bib.get("field_sources"), dict):
        checks.extend(k for k in bib["field_sources"] if isinstance(k, str))
    body = '<section class="panel evidence-panel"><h2>这份来源的证据状态</h2>'
    body += "<p>" + _escape(_source_summary(record)) + "</p>"
    if details:
        body += "<p>" + _escape(" · ".join(details)) + "</p>"
    if checks:
        body += "<p>已登记核对字段：" + _escape("、".join(sorted(set(checks)))) + "</p>"
    if note := _text(_dict(record.get("metadata_verification")).get("notes")):
        body += "<p>核对说明：" + _escape(note) + "</p>"
    if url := _url(bib.get("metadata_source_url")):
        body += '<p><a href="' + html.escape(url, quote=True) + '" target="_blank" rel="noopener noreferrer">查看元数据核对来源 ↗</a></p>'
    body += "<p class=\"muted\">元数据核对确认的是来源身份和书目信息，不代表研究结论已证实。全文已获取也不等于内容可靠。</p>"
    if meta_status.endswith("未核实"):
        body += "<p>这份来源尚缺元数据核查记录。</p>"
    return body + "</section>"


def _identity_keys(record, sid):
    """Identify the same work across URL forms, versions, and supplied DOI links."""
    bib = _bibliography(record)
    keys = set()
    for value in [bib.get("doi"), record.get("doi")]:
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", _text(value), flags=re.I).strip().lower()
        if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            keys.add("doi:" + doi)
    for value in [bib.get("arxiv_id"), record.get("arxiv_id")]:
        value = _text(value)
        if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", value, flags=re.I):
            keys.add("arxiv:" + re.sub(r"v\d+$", "", value, flags=re.I).lower())
    for field in ["url", "original_url", "text_original_url", "pdf_url"]:
        if url := _url(record.get(field)):
            keys.add(identifier(url))
            keys.add("url:" + canonical(url))
    return keys or {"local-source:" + sid}


def _work_groups(records):
    parent = {sid: sid for sid in records}

    def find(sid):
        while parent[sid] != sid:
            parent[sid] = parent[parent[sid]]
            sid = parent[sid]
        return sid

    owners = {}
    for sid, record in records.items():
        for key in _identity_keys(record, sid):
            if key in owners:
                parent[find(sid)] = find(owners[key])
            else:
                owners[key] = sid
    return {sid: find(sid) for sid in records}


def _links(ids, records):
    result = []
    for sid in ids:
        if sid in records:
            title = _text(records[sid].get("title"), sid)
            result.append('<a href="/source/' + quote(sid) + '">' + _escape(title) + "</a>")
        else:
            result.append("来源 " + _escape(sid) + "（本地记录缺失，未核实）")
    return "；".join(result) or "未记录来源，未核实"


def _independence(meta, records, works):
    result, used_sources, used_works, seen = [], set(), set(), set()
    for value in _list(meta.get("independent_source_groups")):
        group = _dict(value)
        key, basis = _text(group.get("id")), _text(group.get("basis"))
        ids = _ids(group.get("source_ids"))
        group_works = {works[sid] for sid in ids if sid in works}
        if (key and key not in seen and basis and group.get("status") == "verified" and ids
                and all(sid in records for sid in ids) and not used_sources.intersection(ids)
                and not used_works.intersection(group_works)):
            result.append((key, basis, ids))
            seen.add(key)
            used_sources.update(ids)
            used_works.update(group_works)
    return result, used_sources


def knowledge_evidence(slug):
    """Render source coverage, scoped claim records and unresolved disagreements."""
    meta = _read(META / (slug + ".json")) if isinstance(slug, str) and _SLUG.fullmatch(slug) else {}
    return _render_knowledge_evidence(meta)


def proposal_evidence(proposal):
    """Expose the actual claim/conflict proposal before publication approval."""
    proposal = _dict(proposal)
    meta = {key: proposal.get(key, []) for key in ['claims', 'conflicts']}
    meta['source_ids'] = proposal.get('referenced_source_ids', proposal.get('source_ids', []))
    return _render_knowledge_evidence(meta, 'draft-trust-' + _text(proposal.get('source_id')))


def _render_knowledge_evidence(meta, element_id='knowledge-trust'):
    ids = _ids(meta.get("source_ids"))
    records = {sid: record for sid in ids if (record := _read(RAW / (sid + ".json")))}
    works = _work_groups(records)
    independent, covered = _independence(meta, records, works)
    body = '<section class="panel evidence-panel" id="' + _escape(element_id) + '"><h2>证据与可信边界</h2>'
    body += f"<p>登记来源 {len(ids)} 条 · 本地可核对 {len(records)} 条 · 按作品标识去重 {len(set(works.values()))} 组。</p>"
    body += "<p>来源数量、不同网址和已审核标记均不等于可信度。不同版本与重复出版物不算独立验证；独立研究还要核对作者、数据与方法。</p>"
    if independent:
        body += f"<p>来源独立性核查记录：{len(independent)} 组，覆盖 {len(covered)}/{len(records)} 条本地来源（按维护者记录）。</p><ul>"
        for key, basis, group_ids in independent:
            body += "<li>" + _escape(key) + "：" + _escape(basis) + "<br>" + _links(group_ids, records) + "</li>"
        body += "</ul>"
    if not records or len(covered) < len(records):
        body += "<p>来源独立性尚未完整核实，作品去重组数不能当作独立研究数。</p>"
    if ids:
        body += "<details><summary>逐份来源状态</summary><ul>"
        for sid in ids:
            body += "<li>" + _links([sid], records)
            if sid in records:
                body += "<br>" + _escape(_source_summary(records[sid]))
            body += "</li>"
        body += "</ul></details>"
    else:
        body += "<p>没有来源登记，当前知识证据未核实。</p>"
    review = _dict(meta.get("content_review"))
    if review:
        body += "<p>内容核查记录：" + _escape(_text(review.get("by"), "核查者未记录"))
        body += " · " + _escape(_text(review.get("at"), "时间未记录")) + "。"
        body += _escape(_text(review.get("notes"), "核查范围未记录")) + "</p>"
    else:
        body += "<p>没有内容核查记录，主要断言尚未逐项核实。</p>"
    body += "<p class=\"muted\">Codex 核查指对照原文和编辑记录的检查，不是同行评审、独立复现实验或真实性认证。</p>"
    body += "<h3>主要断言与适用范围</h3>"
    claims = [v for v in _list(meta.get("claims")) if isinstance(v, dict)]
    if not claims:
        body += "<p>尚未建立逐项断言证据记录；页面不能据此视为已证实。</p>"
    for claim in claims:
        claim_ids = _ids(claim.get("source_ids"))
        known = [sid for sid in claim_ids if sid in records]
        scope = _text(claim.get("scope"))
        status = _text(claim.get("status"))
        problems = []
        if not _text(claim.get("text")):
            problems.append("断言内容未记录")
        if not scope:
            problems.append("适用范围未记录")
        if not claim_ids or len(known) != len(claim_ids):
            problems.append("来源证据不完整")
        if any(records[sid].get("content_status") != "full-text" for sid in known):
            problems.append("部分来源缺少全文文本")
        if status not in _CLAIM_STATUSES:
            problems.append("支持状态未核实")
        if status == "多源一致" and len({works[sid] for sid in known}) < 2:
            problems.append("去重后不足两份作品")
        label = "证据不足（" + "；".join(problems) + "）" if problems else status
        body += '<div class="claim-evidence"><p><strong>' + _escape(_text(claim.get("text"), "断言内容未记录")) + "</strong></p>"
        body += '<p><span class="tag">' + _escape(label) + "</span> · 按维护者记录"
        if claim.get('proposed_by') == 'LLM':
            body += "；模型提议，尚待逐项核查"
        if status == "多源一致" and not problems:
            body += "，一致结论不代表独立复现"
        body += "</p><p>适用范围：" + _escape(scope or "未记录，未核实") + "</p>"
        body += "<p>支持来源：" + _links(claim_ids, records) + "</p></div>"
    body += "<h3>差异与冲突如何处理</h3>"
    conflicts = [v for v in _list(meta.get("conflicts")) if isinstance(v, dict)]
    if not conflicts:
        body += "<p>未登记差异或冲突；这不代表已排除矛盾。</p>"
    for conflict in conflicts:
        sides = [v for v in _list(conflict.get("sides")) if isinstance(v, dict)]
        complete = len(sides) >= 2 and all(_text(s.get("text")) and _text(s.get("scope")) and _ids(s.get("source_ids"))
            and all(sid in records for sid in _ids(s.get("source_ids"))) for s in sides)
        status = _text(conflict.get("status"))
        label = status if complete and status in _CONFLICT_STATUSES else "冲突记录不完整，处理状态未核实"
        body += '<div class="conflict-evidence"><h4>' + _escape(_text(conflict.get("title"), "差异记录")) + "</h4>"
        body += "<p>记录状态：" + _escape(label) + "</p><ul>"
        for side in sides:
            body += "<li>" + _escape(_text(side.get("text"), "该侧表述未记录"))
            body += "<br>适用范围：" + _escape(_text(side.get("scope"), "未记录，未核实"))
            body += "<br>来源：" + _links(_ids(side.get("source_ids")), records) + "</li>"
        body += "</ul><p>处理说明：" + _escape(_text(conflict.get("handling"), "尚未记录处理依据，保留各侧表述并待核查")) + "</p></div>"
    body += "<p class=\"muted\">发生差异时先核对版本、定义、样本和实验设置；范围差异分别保留，未解决冲突持续标记，不按数量或模型意见自动选胜者。</p>"
    return body + "</section>"
