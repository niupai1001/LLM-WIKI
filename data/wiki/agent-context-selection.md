# 常驻上下文选择

常驻上下文选择（always-loaded context selection）要回答的是：会话开始时，智能体应把哪些指令固定带进上下文。具体来说，它要决定把哪些指令写进一份固定加载的上下文文件（例如 AGENTS.md），以及从里面删掉哪些。这份文件在整段会话中一直留在提示里，和按需检索的上下文不同：每条被选中的指令都要用持续占用的空间，换取它带来的行为改变。所以真正要回答的不是「还能加什么」，而是「哪些指令值得一直带着」。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

## 为什么不能一直往里加

论文把这份文件看成一个有成本的指令集合。每条指令占用 token；被任务用到并被遵循时，可能带来收益，也可能造成伤害。做选择时要同时看相关性、实际收益和占用成本，不能只看模型是否照做。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

在论文的有限容量与共享稀释模型中，指令之间会竞争被遵循的机会。即使每条指令单独看都有价值，把它们全部追加进文件也可能让总收益下降；模型给出最优文件规模的上界。这些结论依赖假设，不等于所有智能体都会出现同样的退化。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

## 反馈为什么是不对称的

反馈是「删失」的（censored feedback）：已加载指令的遵循情况与是否造成伤害可以观察到，未加载指令的价值通常不可见，只有当它的缺失导致失败时才留下痕迹。因此从会话记录里能学到的东西是不对称的。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

## 什么时候适用

适用场景：维护一份跨会话复用的指令文件，且候选指令来自历史会话摘要、会持续增长。不适用场景：指令只对单次任务有效（应走检索或任务内提示）。

实践建议（基于证据的推论）：

1. 常驻文件保留长期有效的项目约定；一次性的任务细节放在本次提示或按需检索中。

2. 给文件设定预算，新增内容时也检查过时、重复和矛盾的条目。

3. 用代表性任务比较增删前后的效果。不要只因一条指令常被忽略就删除它：被遵循的频率与实际价值不是同一件事。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

解释性场景（非论文实验）：项目已从 conda 迁移到 uv。此时应更新旧环境约定，而不是把两套相互冲突的要求都留在 AGENTS.md；某次调试的临时路径也无需永久加入。

## 证据与边界

当前依据主要来自一项研究（arXiv 预印本），它同时给出模型、理论结果与实验。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

- 论文在两个模型、不同规模的真实仓库指令集合上测试，观察到全部加载时，被遵循的相关指令数下降；但这类指令遵循指标不等于整个任务的质量。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

边界与矛盾：论文引用的其他研究报告，在真实配置文件的小尺寸范围内未观察到遵循率随文件增大而下降；这与高指令密度下遵循率下降的结论并存，说明稀释效应在什么规模上显现尚无一致结论。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

成本口径：本页按模型设定解释为「每条常驻指令在会话开始时计一次 setup 成本，后续轮次命中提示缓存」，不宣称每轮都重新计费。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

## 两个常见误解

- 「指令越多越保险」。在共享注意力假设下，加入指令不会提升已有指令的遵循率，只会分摊注意力并增加成本；把全部候选加载进来可能比只选一小部分更差。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

- 「遵循率低就该删」。遵循率与价值是两个不同的量：遵循数据无法识别收益大小，也无法在一般情况下识别收益正负。删除决策需要价值侧的证据（如伤害标记或收益测量），而不是只看是否被遵循。[Curating Always-Loaded Context for LLM Agents](/source/926ac126f2443b70)

## 相关知识

- [[agent-context-memory-governance]]
- [[agent-memory-admission]]

---

## 原始证据

- [Curating Always-Loaded Context for LLM Agents: A Capacitated Assortment Model with Censored Feedback](/source/926ac126f2443b70) · 来源ID `926ac126f2443b70`
