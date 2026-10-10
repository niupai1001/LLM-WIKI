# 智能体执行可靠性

可靠性包含不同阶段的问题：调用前通过门控限制不允许的操作；任务失败后通过归因追踪可能的致因步骤。二者不能互相替代。

## 从问题开始

- 调用执行前，规则由谁检查？ → [[agent-tool-call-gates|工具调用门控]]
- 最终报错是否就是失败根因？ → [[multi-agent-failure-attribution|多智能体失败归因]]

## 阅读顺序

先读工具调用门控建立权限边界，再读失败归因理解诊断线索。这里的连接是工程组织方式，不代表已有论文验证了二者结合的效果。

---

## 原始证据

- [NOMOS: Compiling Written Policies into Statically Verified Tool-Call Gates for LLM Agents](/source/e0c0d198c4709cc7) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11030)
- [Error-Propagation Modeling for Failure Attribution in LLM-Based Multi-Agent Systems](/source/0b44ee6389202cb6) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11600)
- [ReCast: Attribution-Oriented Step Representation Learning for LLM-Based Agent Systems](/source/eddf2edee3305425) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11334)
