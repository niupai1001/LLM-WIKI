"""Editable topic and feed forms for the local workspace."""
import html


def settings_html(snapshot, notice=''):
    escape = lambda value: html.escape(str(value), quote=True)
    topics, feeds = snapshot['topics'], snapshot['feeds']
    names = {t['id']: t['name'] for t in topics}
    def topic_options(selected):
        return ''.join(f'<option value="{escape(t["id"])}" {"selected" if t["id"] == selected else ""}>{escape(t["name"])}{"（已停用）" if not t.get("enabled", True) else ""}</option>' for t in topics)
    def topic_form(topic=None):
        topic = topic or {}
        protected = topic.get('id') == 'other'
        hidden = f'<input type="hidden" name="id" value="{escape(topic["id"])}">' if topic.get('id') else ''
        enabled = '<input type="hidden" name="enabled" value="true">' if protected else f'<label class="checkbox-label"><input type="checkbox" name="enabled" {"checked" if topic.get("enabled", True) else ""}>启用主题</label>'
        remove = f'<button type="button" class="button danger" data-settings-delete="topic" data-item-id="{escape(topic["id"])}" data-item-name="{escape(topic["name"])}">删除主题</button>' if topic.get('id') and not protected else ''
        return f'''<form data-settings-form="topic" class="settings-form">{hidden}<label>主题名称<input name="name" required maxlength="80" value="{escape(topic.get('name', ''))}" placeholder="例如：智能体记忆"></label><label>关注范围<textarea name="description" maxlength="2000" rows="2" placeholder="例如：长期记忆、上下文选择与评测">{escape(topic.get('description', ''))}</textarea></label><p class="field-help">关注范围会提供给 LLM 作为编写方向；采集筛选请在订阅节点中设置关键词。</p>{enabled}<p class="field-help">{'默认分类始终保留。' if protected else '停用后暂停关联订阅，不再用于新资料分类；已有资料和知识仍保留。'}</p><div class="settings-actions"><button class="button primary">保存主题</button>{remove}</div></form>'''
    def feed_form(feed=None):
        feed = feed or {}
        hidden = f'<input type="hidden" name="id" value="{escape(feed["id"])}">' if feed.get('id') else ''
        kinds = [('paper', '论文'), ('article', '文章'), ('news', '资讯'), ('document', '文档')]
        if feed.get('kind') == 'note': kinds.append(('note', '笔记'))
        kind_options = ''.join(f'<option value="{kind}" {"selected" if kind == feed.get("kind", "article") else ""}>{label}</option>' for kind, label in kinds)
        keywords = lambda key: escape('，'.join(feed.get(key, [])))
        remove = f'<button type="button" class="button danger" data-settings-delete="feed" data-item-id="{escape(feed["id"])}" data-item-name="{escape(feed["name"])}">删除订阅</button>' if feed.get('id') else ''
        selected = feed.get('category') or next((t['id'] for t in topics if t.get('enabled', True)), 'other')
        return f'''<form data-settings-form="feed" class="settings-form">{hidden}<div class="settings-fields"><label>订阅名称<input name="name" required maxlength="120" value="{escape(feed.get('name', ''))}" placeholder="例如：arXiv · 记忆研究"></label><label>所属主题<select name="category">{topic_options(selected)}</select></label></div><label>订阅地址<input type="url" name="url" required maxlength="2048" value="{escape(feed.get('url', ''))}" placeholder="https://example.com/feed.xml"></label><p class="field-help">使用 RSS 或 Atom 地址。保存后在下一次采集时生效。</p><div class="settings-fields"><label>资料类型<select name="kind">{kind_options}</select></label><label>最近多少天<input type="number" name="max_age_days" min="1" max="3650" step="1" value="{escape(feed.get('max_age_days') or '')}" placeholder="留空表示不限时间"></label></div><label>包含任一关键词<textarea name="include_keywords" rows="2" placeholder="agent，tool use，智能体">{keywords('include_keywords')}</textarea></label><details class="settings-advanced"><summary>更多筛选条件</summary><label>还需匹配任一关键词<textarea name="require_keywords" rows="2" placeholder="llm，language model">{keywords('require_keywords')}</textarea></label><label>排除关键词<textarea name="exclude_keywords" rows="2" placeholder="sponsored，招聘">{keywords('exclude_keywords')}</textarea></label></details><p class="field-help">关键词用逗号或换行分隔；留空表示不限制。包含与“还需匹配”两组都填写时，文章需分别命中两组。</p><label class="checkbox-label"><input type="checkbox" name="enabled" {"checked" if feed.get('enabled', True) else ''}>启用订阅</label><div class="settings-actions"><button class="button primary">保存订阅</button>{remove}</div></form>'''
    body = '<div class="page-heading"><div><h1>主题与订阅</h1><p>调整关注领域、订阅节点与筛选条件。</p></div><a class="button" href="/collection">查看采集计划 →</a></div>'
    if notice:
        body += '<div class="notice settings-saved" role="status">' + escape(notice[:240]) + '</div>'
    body += f'<div class="settings-workspace" data-settings-version="{escape(snapshot["version"])}"><nav class="settings-jumps" aria-label="设置内容"><a href="#topics">主题 <span>{len(topics)}</span></a><a href="#feeds">订阅节点 <span>{len(feeds)}</span></a></nav>'
    body += '<section id="topics" class="settings-section"><div class="section-heading"><h2>关注主题</h2><span class="muted">用于资料分类、知识导航和订阅归属</span></div><details class="settings-create"><summary>＋ 新增主题</summary>' + topic_form() + '</details><div class="settings-list">'
    for topic in topics:
        enabled = topic.get('enabled', True)
        count = sum(1 for feed in feeds if feed.get('category') == topic['id'])
        body += f'<article class="settings-item"><div class="settings-item-heading"><div><h3>{escape(topic["name"])}</h3><p>{escape(topic.get("description") or "可填写这个主题的关注范围")}</p></div><span class="tag {"complete" if enabled else "subtle"}">{"已启用" if enabled else "已停用"}</span></div><div class="settings-item-foot"><span>{count} 个关联订阅</span><a href="/sources?category={escape(topic["id"])}">查看资料 →</a></div><details class="settings-edit"><summary>编辑主题 <span>{escape(topic["name"])}</span></summary>{topic_form(topic)}</details></article>'
    body += '</div><p class="field-help">主题改名后，侧栏与资料分类同步更新。已有资料、知识或订阅引用的主题不能直接删除。</p></section>'
    body += '<section id="feeds" class="settings-section"><div class="section-heading"><h2>订阅节点</h2><span class="muted">每个节点对应一个 RSS / Atom 订阅源</span></div><details class="settings-create"><summary>＋ 新增订阅</summary>' + feed_form() + '</details><div class="settings-list">'
    for feed in feeds:
        enabled = feed.get('enabled', True)
        words = ' · '.join(feed.get('include_keywords', [])) or '不限关键词'
        window = f'最近 {feed["max_age_days"]} 天' if feed.get('max_age_days') else '不限时间'
        body += f'<article class="settings-item"><div class="settings-item-heading"><div><h3>{escape(feed["name"])}</h3><p>{escape(names.get(feed.get("category"), "其他"))} · {escape(window)}</p></div><span class="tag {"complete" if enabled else "subtle"}">{"已启用" if enabled else "已停用"}</span></div><p class="settings-feed-url">{escape(feed["url"])}</p><p class="settings-keywords">{escape(words)}</p><details class="settings-edit"><summary>编辑订阅 <span>{escape(feed["name"])}</span></summary>{feed_form(feed)}</details></article>'
    body += '</div><p class="field-help">停用或删除订阅只影响后续采集，已归档的原文和知识继续保留。关键词帮助筛选相关内容，资料质量仍需核查。</p></section></div>'
    return body
