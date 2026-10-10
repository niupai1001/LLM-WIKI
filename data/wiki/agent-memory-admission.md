# 长期记忆准入

长期记忆准入（admission）指在生成回答前，决定哪些被检索到的记忆条目或条目片段真正进入工作上下文。它与“呈现”（presentation）相对：准入改变哪些信息可用，呈现只改变同一批已准入信息的组织与措辞。把两者分开，是因为它们成本、失败模式和效果量都不同，混在一起会掩盖真正起作用的环节。[What to Admit and How to Present](/source/accd5feccfb2c025)

## 工作机制

处理一次查询时，先检索相关记忆，再判断每条信息的性质和允许用途，最后才构造生成提示。仅仅“检索相关”还不够：个人偏好可能与问题相关，却不能被当成客观事实。[What to Admit and How to Present](/source/accd5feccfb2c025)

论文研究的流程先结合当前问题给记忆打标签，例如它是谁的偏好、是否经过核实、是否仍然有效；再由确定性策略决定保留哪些内容。被拒绝的信息直接移出提示，而不是继续放进上下文、仅附一句“不要使用”。[What to Admit and How to Present](/source/accd5feccfb2c025)

论文比较两种粒度：FC 按整条记忆判断，PS 把一条记忆拆成若干信息单元再判断。例如“我喜欢品牌 A，因为它的电池最耐用”包含个人偏好和事实主张；细粒度判断可以保留偏好，同时不把未经核实的理由当作事实。[What to Admit and How to Present](/source/accd5feccfb2c025)

## 如何使用

适用场景：智能体已有跨会话记忆，希望减少对用户说法的不当迎合，或避免把一个情境下的信息错用于另一个情境。准入发生在检索之后、回答之前，与写入时审核、检索时相关性过滤互补。[What to Admit and How to Present](/source/accd5feccfb2c025)

示例（非论文实验）：记忆中有“我喜欢深色界面”。用户让助手设置主题时，可以使用这条偏好；用户询问哪种界面客观上更易读时，这条偏好不能作为证明；回答无关问题时无需加载它。

实践建议（基于证据的推论）：分别测试“哪些信息进入提示”和“同一信息怎么排列”，否则难以知道改进来自哪个环节。评测同时检查风险减少和有用记忆是否丢失。

## 证据与边界

当前依据主要来自一项预印本研究，包含多模型的开发测试与外部记忆基准。[What to Admit and How to Present](/source/accd5feccfb2c025)

在该研究的外部基准上，FC 与 PS 相对原样注入降低了合并失败率，但两种设计的直接差异不显著。它们共享若干处理环节，因此不能把全部收益单独归于“准入”这个步骤。[What to Admit and How to Present](/source/accd5feccfb2c025)

两种设计也增加了个性化缺失：一些有益信息被过滤掉，回答不能充分利用用户偏好。PS 未同时达到预注册的风险改善和个性化保持门槛；记忆与事实证据冲突时仍有较高残余失败。因此它是需要权衡和验证的治理机制，并非已经解决记忆安全。[What to Admit and How to Present](/source/accd5feccfb2c025)

## 常见误解

**只换措辞就能解决信息使用权限。** 在固定筛选结果后，研究没有发现两种被测排列方式之间的稳健差异。这只约束那两种方式，不能推广为所有呈现策略都无效。[What to Admit and How to Present](/source/accd5feccfb2c025)

**过滤越严格越好。** 过滤会同时挡掉有害信息和有用信息，应结合任务衡量两者，而非只看风险指标下降。[What to Admit and How to Present](/source/accd5feccfb2c025)

## 相关知识

- [[agent-context-memory-governance|智能体上下文与记忆]]
- [[agent-context-selection|常驻上下文选择]]
- [[agent-tool-call-gates|工具调用门控]]

---

## 原始证据

- [What to Admit and How to Present: Governing Persistent Memory in LLM Agents](/source/accd5feccfb2c025) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11188)
