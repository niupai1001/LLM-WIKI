# 智能体上下文与记忆

这两类机制都决定模型看见哪些信息，但处理对象不同：常驻上下文是会话固定加载的项目约定；长期记忆准入是在每次查询时筛选被检索到的信息。

## 从问题开始

- AGENTS.md 应保留什么，何时删除？ → [[agent-context-selection|常驻上下文选择]]
- 相关记忆是否适合用于当前回答？ → [[agent-memory-admission|长期记忆准入]]

## 阅读顺序

先理解常驻与按需信息的区别，再看记忆准入如何区分偏好、事实与允许用途。减少上下文不是目标，减少无用或不当信息才是设计方向。

---

## 原始证据

- [Curating Always-Loaded Context for LLM Agents: A Capacitated Assortment Model with Censored Feedback](/source/926ac126f2443b70) · 2026-10-07 · [原文](https://arxiv.org/abs/2610.11007)
- [What to Admit and How to Present: Governing Persistent Memory in LLM Agents](/source/accd5feccfb2c025) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11188)
