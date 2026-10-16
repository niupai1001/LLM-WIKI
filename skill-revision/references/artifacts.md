# Artifacts：Skill 产物与兼容约束

## 职责

定义 Skill 产物的语义边界，避免把单篇论文解释稿、机器证据、审核记录与当前仓库 V2 正式发布对象混为一谈。

## 产物一：reading.md

面向人类读者的一篇连贯论文解读。它回答：问题为何存在、方法为什么这样设计、证据证明了什么、知识能迁移到哪里。

推荐阅读顺序（标题可灵活调整）：

1. 研究问题与现有瓶颈；
2. 核心回答与关键设计的作用机制；
3. 关键选择背后的理由及作者未解释之处；
4. 实验如何检验上述主张，结果的解释与边界；
5. 可复用认识、领域特殊性和未解决问题。

不把“研究目标、动机、方法、实验”机械拼成短语表。不把复现参数和完整排行榜作为主体。整篇默认归属该论文，不重复为每段加相同来源引用；例外见 provenance.md。

## 产物二：knowledge.json

这是 Skill 的结构化中间成果，不是当前 station/extraction.py 或 station/compiler.py 的原生 Schema。

**保留原 Skill 既有顶层字段**：source、purpose、motivation、central_answer、results、knowledge_points、concept_proposals、topic_proposals、source_notes。若实际原文件定义有更严格的字段类型、枚举、必填要求，以原文件为准；本修订不宣称已经取得并逐字核对旧版 Schema。

字段的内容约束：

| 字段 | 应表达什么 | 不应表达什么 |
| --- | --- | --- |
| source | 真实来源身份与版本 | 虚构 DOI、页码或 ID |
| purpose | 可检验的研究目标 | “提高性能”这种空话 |
| motivation | 现有方法的具体瓶颈与后果 | 与目的重复的口号 |
| central_answer | 作者解决瓶颈的核心思路 | 模块名罗列 |
| results | 代表性结果及其检验的主张 | 全部实验表格 |
| knowledge_points | 有依据、有适用范围、有解释价值的完整命题 | 参数、零件、排行榜 |
| concept_proposals | update/create/defer 的候选与理由 | 看到新论文就 create |
| topic_proposals | 围绕学习问题的导航建议 | 论文批次或采集分类 |
| source_notes | 原文缺口、未知理由、推论边界、复现补充 | 把未知写成已证实 |

原有子字段不得随意更名。若现有知识点对象没有专用的 design_rationale、knowledge_type 或 domain_scope 字段，应先利用原有解释、scope、notes 等承载语义，**不得未经适配擅自新增必填字段**。如需扩展，先给出兼容迁移与测试。

## 产物三：review.md

独立核查报告。至少记录来源版本、审查范围、逐项问题及定位、hard block / warning / suggestion、处理结果与未解决项。它不能替代当前项目的 semantic_reviews、人工 human_evaluation.json 或发布日志。

## 产物间一致性

- reading.md 的重要事实必须能回到 knowledge.json 的命题/结果和来源；
- knowledge.json 中的重要实证知识必须有原文依据；
- review.md 的阻断问题修复后应重新核查，而不是仅删除审核文字；
- 事实通道冻结后，纯表达修订不得改变 knowledge.json 的断言语义；
- 同一来源更新时保留来源身份和旧知识变化记录。

## 示例：Change-Agent 的写作分工（示意，非论文事实核查）

reading.md 可以写：“这项设计要解决的不是抽象的‘提升检测精度’，而是如何将变化观测转化为后续 Agent 可利用的信息。应解释观测结果的语义、工具调用和任务判断之间的联系，并说明论文是否实证检验该联系。”

knowledge.json 中的 purpose 应说明具体研究问题；results 只保留与该问题对应的证据；knowledge_points 分别表达可迁移机制与遥感领域条件。若论文没有解释某组件为何有效，source_notes 标记“作者未解释”，不能自行构造因果链。

review.md 则检查：是否把遥感数据上的观察泛化成所有 Agent 的结论；是否把推测写成作者观点；是否遗漏关键实验条件。

## 与其他文档的边界

阅读质量由 reading-framework.md 规定；概念层级由 knowledge-hierarchy.md 规定；证据格式与引用由 provenance.md 规定；审查门槛由 review.md 规定；仓库真实 Schema 由 pi-intel-wiki.md 说明。
