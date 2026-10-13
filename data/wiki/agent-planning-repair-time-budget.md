# 智能体规划与执行

<!-- wiki-topic:start -->

先从步骤及其依赖理解任务组织，再比较ReAct的交互决策与Plan-and-Execute的组件分工。执行失败和存在截止时间时，继续阅读计划修补与时间预算；领域比较页提供具体实现参考。

## 按问题阅读

- 任务怎样拆成能执行的依赖步骤？ → [[agent-task-decomposition-dependencies|任务分解与依赖约束]]
- 怎样根据刚取得的观察调整下一步行动？ → [[react-reasoning-acting-loop|ReAct 推理与行动循环]]
- 怎样先组织多步计划，再交给执行器？ → [[plan-and-execute-agents|Plan-and-Execute 规划与执行]]
- 执行失败后，哪些部分保留、哪些部分重做？ → [[agent-plan-repair|计划修补]]
- 怎样在截止时间内分配与使用任务预算？ → [[agent-time-budget|智能体时间预算]]

## 研究与实现参考

- 领域依赖层级与工具层级分别怎样组织任务？ → [[geospatial-hierarchical-planning-agents|地理空间任务的层级规划与容错：HTAM/EarthAgent 与 HieraPlan/TerraLogic]]
- 任务清单怎样落实为GIS代码或依赖图？ → [[llm-gis-workflow-generation|自然语言驱动的 GIS 空间分析工作流生成与执行]]

<!-- wiki-topic:end -->

---

## 原始证据

- [Plan-and-Execute Agents](/source/444b00f9850c9716) · 来源ID `444b00f9850c9716`
- [Plan-and-Patch: Diffusion Language Models for Agentic Planning](/source/7d220dd5706f59fd) · 来源ID `7d220dd5706f59fd`
- [On the Clock: Towards Punctual and Productive Time-Budgeted AI Agents](/source/b7c8bebd4466160c) · 来源ID `b7c8bebd4466160c`
- [ReAct: Synergizing Reasoning and Acting in Language Models](/source/e504ba1ba9c3fffc) · 来源ID `e504ba1ba9c3fffc`
