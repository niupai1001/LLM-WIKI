# 知识维护改造交付记录

2026-10-17。基线 `236cd971386b88d7ef17e350dd5f8847486d4854`，交付分支 `codex/knowledge-maintenance`。代码及离线验证已完成；按用户确认的检查点，真实模型内容验收等待人工审阅参考决策集。

## 分阶段交付

| 阶段 | 变更文件 | 设计与结果 |
| --- | --- | --- |
| A/B 决策与迁移 | `station/knowledge_maintenance.py`、`skills/knowledge-maintenance/SKILL.md`、`tests/test_knowledge_maintenance.py` | 独立维护契约；每点一条处理结果、多概念角色/理由、持久暂缓；增量兼容迁移；局部候选及去重证据；决策输入、原始输出、错误、预算和待审状态可审计。核心首批提交 `09773e3`。 |
| C 综合与审核 | `station/knowledge_synthesis.py`、`station/model_attempts.py`、`station/wiki_experiment.py`、综合 Skill | 生产与实验共用维护—写作—绑定—独立审核流程；实验只保存草稿；格式错误不重写正文；写作按概念相关内容保存预算，请求前持久预占，超时和重启不会刷新。 |
| C/D 发布与服务 | `station/paper_rebuild.py`、`station/relations.py`、`station/knowledge.py`、`station/v2web.py`、`station/web.py` | 点与来源版本、概念正文/元数据及归属版本校验；共享点/关系依赖按组原子发布；合并共享主题和首页；无变化依赖也参加并发校验。关系必须获维护决策批准，编辑性关系在图谱明确标示。 |
| D 验收兼容 | `station/evaluation.py`、相关测试 | 多归属逐对核对；有效暂缓计入处理完整性；背景点豁免依赖真实已执行决策，不能靠角色标签逃过审核；保留人工验收门槛。实现与集成提交 `d3da284`。 |
| E 参考材料 | `docs/knowledge-maintenance-reference.md`、`tests/fixtures/knowledge-maintenance-reference.json`、参考集测试 | 四篇已接受论文共 45 点；4 个真实材料案例、1 个独立合成冲突案例；10 条短引文有段落、偏移和哈希。全部标为待人工确认。 |

完整架构、运行与回滚边界见 [维护说明](knowledge-maintenance.md)。

新增集成与兼容测试主要位于 `tests/test_maintenance_pipeline.py`、`tests/test_maintenance_publication.py`、`tests/test_maintenance_evaluation.py`；原综合、实验、关系、覆盖率及论文验收测试已同步新契约，没有删除原文/版本/事务/人工核查保护。

## 实际验证

- 修改前：`.venv/bin/python -m unittest discover -s tests -q`，511 项通过。
- 决策与维护验收：`.venv/bin/python -m unittest tests.test_knowledge_maintenance tests.test_maintenance_evaluation -q`，32 项通过。
- 独立发布集成：`.venv/bin/python -m unittest tests.test_maintenance_pipeline -q`，12 项通过。涵盖真实事务下双归属、背景无变化、并发版本冲突、超时预算、未批准关系、预审稿发布及中途故障回滚。
- 最终：`make test`，**574 项全部通过**，14.820 秒。原始日志位于本地 `artifacts/knowledge-maintenance-implementation/tests.log`，未提交运行日志。
- 两个 Skill：`python3 /Users/Lemon/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill目录>`，均返回 `Skill is valid!`。项目虚拟环境未装验证器所需 PyYAML，因此使用已有系统环境运行此独立工具，未更改项目依赖。
- `git diff --check` 通过。

集成过程中曾发现并修复：新建概念空归属快照的比较问题；全局批次变化可能刷新失败概念预算；无变化后台归属未被旧读取/验收路径识别；共享无变化概念的并发修改可能导致部分提交；旧 `align` 调用输出预算兼容问题。以上均有通过的回归或专项测试。当前离线测试无未通过项。

## 现有数据保护

检查涵盖正式库及已有候选库的原始记录、PDF 原件、Wiki、元数据、状态数据库，以及注册文件。修改前后 **534 个文件**逐一 SHA-256 比较：内容变化 0、新增 0、删除 0；未切换活动库，未重提炼或批量重写正式内容。

本地证据：`artifacts/knowledge-maintenance-implementation/before.json` 与 `data-integrity.json`。正式数据库本次没有运行迁移；自动化测试全部使用独立临时 Workspace。上述证明范围为列明的知识与状态文件，不把可再生缓存或锁文件当成知识内容。

## 待审、风险与下一步

1. 先审阅 [候选参考决策集](knowledge-maintenance-reference.md)：确认多归属作用、暂缓理由、条件差异以及可接受替代答案。Agent 候选不充当人工金标。
2. 人工确认后，才在隔离副本运行现有配置模型；本轮真实模型调用为 **0**。需分别报告决策正确性、正文质量、语义支持与真实发布一致性，不能以 574 项程序测试替代。
3. 四篇材料尚无可确认的真实直接冲突。合成冲突只能验证程序保护；真实冲突内容验收仍缺样本。
4. 候选检索目前复用既有归属和轻量词法相关性，可能漏召回；未引入新图数据库或全面改造检索/UI。未经证实的合并、拆分、撤回仍留待审，不提供绕过版本或语义核查的强制发布入口。
5. 语义审核仍是独立模型任务，不保证消除所有模型错误；质量阈值须在人工作出的参考答案及分歧固定后确定。

代码回滚顺序为先 `git revert d3da284`，再 `git revert 09773e3`。当前运行库未迁移，可直接回到基线；将来部署后若已迁移，需恢复迁移前的一致备份或保留显式列名写入的兼容修正。基线使用三值位置写入，不能直接配合新增列后的归属表。不要清库或单独覆盖 Markdown/SQLite 来回滚已发布知识。
