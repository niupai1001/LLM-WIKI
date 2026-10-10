# 智能体规划与执行

把“计划如何修正”和“任务何时结束”分开设计。计划修补解决局部失败后的恢复；时间预算约束整次任务的耗时，守时并不保证结果更好。

## 从问题开始

- 执行失败后，哪些步骤需要改？ → [[agent-plan-repair|计划修补]]
- 怎样按时结束，又避免空耗预算？ → [[agent-time-budget|智能体时间预算]]

## 阅读顺序

先读计划修补理解如何定位和替换步骤，再读时间预算理解时钟反馈与执行限制。两篇研究没有共同验证一个合并系统，入口只建立阅读关系。

---

## 原始证据

- [Plan-and-Patch: Diffusion Language Models for Agentic Planning](/source/7d220dd5706f59fd) · 2026-10-07 · [原文](https://arxiv.org/abs/2610.10786)
- [On the Clock: Towards Punctual and Productive Time-Budgeted AI Agents](/source/b7c8bebd4466160c) · 2026-10-07 · [原文](https://arxiv.org/abs/2610.10833)
