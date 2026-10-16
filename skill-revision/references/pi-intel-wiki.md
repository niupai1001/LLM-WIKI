# PI-Intel-Wiki：与当前 LLM-WIKI 工程的真实接口

## 职责与基准

本文件说明 Skill 的语义规则如何对应仓库 niupai1001/LLM-WIKI 的当前默认分支（核查日期：2026-10-16）。它**不声称**仓库已经安装或调用本 Skill，不将旧版附件的字段与当前 V2 代码混同。

仓库源文件：
- config/wiki-writing.md
- station/documents.py
- station/extraction.py
- station/compiler.py
- station/wiki.py
- station/topic_navigation.py
- station/publication.py
- docs/v2-upgrade.md

## 当前 V2 的真实流程

**资料解析**：station/documents.py 的 parse_source 产生逐页/段落原文、OCR/缺页警告、解析版本；来源版本由 source_version 计算。

**结构化抽取**：station/extraction.py 的 extract_source 以最多 18,000 字符一批抽取 concepts、questions、claims，保留处理覆盖与缺失原因。模型批次输入包含 source_id、source_version、kind、category、paragraphs；claims 绑定 question_ids 与 paragraph_ids。缓存按来源、解析器、提示词和模型身份失效。

**概念对齐**：station/compiler.py 的 _plan 将问题分配给已有或新概念 target，或显式 deferred。检索相似度仅是候选，不能批准概念合并。category 是采集范围，topic 是知识导航。

**编写与复核**：_publish_target 调用写作器生成 body_markdown、claims、claim_bindings、claim_changes、legacy_mappings、conflicts、relations、related_slugs、topic_routes；_validate 检查结构与旧知识；_review 独立核对断言及正文。失败目标阻断，不应被旧 approve 路径绕过。

**发布**：station/publication.py 的 publish 通过文件与数据库日志保护原子性，正式文件为 wiki/slug.md 与 wiki_meta/slug.json，主题与索引同时更新。候选库与正式库的切换需单独验收。

## 两套产物不能直接互换

| Skill 产物 | 当前仓库 V2 对应物 | 接入要求 |
| --- | --- | --- |
| reading.md | 暂无同名正式产物；Wiki 正文是跨来源概念解释 | 可作为辅助阅读稿，但不得直接当 Wiki 正文 |
| knowledge.json | 当前抽取对象是 concepts/questions/claims；编写提案是另一套扩展 Schema | 需要显式转换与验证，不可直接传给 _validate |
| review.md | _review 生成 semantic_reviews，另有人工评测记录 | 可作审核辅助，不得伪造通过状态 |

Skill 的 knowledge.json 旧字段如 source、purpose、motivation、central_answer、results、knowledge_points、concept_proposals、topic_proposals、source_notes，**不是** station/extraction.py 的顶层 Schema。不要把新字段直接加到编译器要求的 JSON 中；先做转换器或将其内容作为写作上下文。

## 现有协议的关键细节

- extraction.py 的模型输出顶层为 concepts、questions、claims；concepts 使用 id/name/aliases/mechanism/scope，questions 使用 id/text/concept_id，claims 使用 id/text/scope/concept_id/question_ids/paragraph_ids。成功抽取后程序生成稳定化 ID 和原文 evidence。
- compiler.py 的编写提案不是旧版 wiki.py 的简化输出；必须有 claim_bindings、claim_changes、legacy_mappings 等顶层字段。
- claim_bindings 每条使用 claim_id 与正文中真实存在的 body_quote；不能放在单个 claim 内。
- 新 claim 的 evidence_passages 对每个 source_ids 均需真实原文，优先使用当前 originals 中真实的 excerpt_id，不能猜 e1。
- 更新旧 claim 的 text/scope 时需要 claim_changes 的旧值、新值和理由；旧 baseline 还需要完整 legacy_mappings。
- topic_routes 为 1–3 个，优先复用已有主题；现有主题不能被来源分类或论文标题取代。
- 正式 Wiki 正文继续遵守 config/wiki-writing.md 的 [S:来源ID] 近邻引用策略；Skill 的单篇 reading.md 减少重复引用的规则不直接应用于正式 Wiki。
- 独立模型核查不是人工验收；现有候选库的切换门槛仍须满足 docs/v2-upgrade.md。

## 最小适配建议（尚未实施）

1. **Prompt 级**：在 station/extraction.py 的 SYSTEM 中加入“问题—设计选择—依据—scope”的提炼质量约束，不改变已有必填 JSON 字段；更改 PROMPT_VERSION 以正确使缓存失效。
2. **写作级**：在 config/wiki-writing.md 与 compiler.py 的 V2 instruction 中加入“先论证重建、再写正文；压缩参数/排行榜；区分领域/通用机制”的要求。不能删除 V2 扩展字段或 claim_bindings。
3. **审核级**：在 compiler.py 的 _review 检查中加入设计理由是否被臆造、实验解释是否越界、领域过度泛化与论证断裂的检查；语义问题不能仅靠正则判断。
4. **可选转换**：若需要保存 Skill 的 reading.md / knowledge.json / review.md，另行设计明确的辅助产物路径与转换器，并编写 Schema 测试；不得假定当前代码已支持。

## 运行与验收

V2 CLI 示例（以项目实际帮助与文档为准）：

    python -m station.cli --library-id candidate-v2 compile --source-id <source-id> --run
    python -m station.cli --library-id candidate-v2 compare
    python -m station.cli --library-id candidate-v2 evaluate

当前 docs/v2-upgrade.md 记录 candidate-v2 尚未通过完整验收、正式活动库仍为 legacy；不要把程序测试通过或索引可用等同于知识质量达标。

## 与其他文档的边界

本文件只说明“工程现在做了什么、最少怎样接入”；Skill 产物由 artifacts.md 定义，写作规则由 reading-framework.md 定义，证据规则由 provenance.md 定义，审核规则由 review.md 定义，知识路由由 knowledge-hierarchy.md 定义。
