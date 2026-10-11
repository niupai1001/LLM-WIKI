# ReAct 推理与行动循环

ReAct 是一种提示范式：让语言模型在解决任务时交替生成「推理轨迹」（thought）与「任务动作」（action），并把环境返回的观察（observation）接回上下文，形成 thought→action→observation 的循环。它要解决的问题是：只做推理（如思维链）不与外部世界接触，容易产生事实幻觉；只生成动作又缺少对目标的高层推理与进度跟踪。ReAct 把两者放进同一条轨迹，让推理指导行动、行动为推理补充信息[ReAct](/source/e504ba1ba9c3fffc)。

## 工作机制

ReAct 把智能体的动作空间扩展为原动作集合与语言空间的并集。语言空间中的动作称为 thought：它不改变外部环境、不产生观察，只用于在当前上下文上做推理，并把结果写回上下文，供后续推理或行动使用[ReAct](/source/e504ba1ba9c3fffc)。

一次循环的因果顺序是：模型根据问题与已有上下文生成一段 thought（分解目标、注入常识、从观察中抽取要点、跟踪进度、处理异常等）；接着生成一个任务动作；环境执行该动作并返回 observation；observation 被追加进上下文，进入下一轮。在知识密集型任务中，论文让 thought 与 action 密集交替；在动作数量可能很多的决策任务中，thought 稀疏出现，由模型自行决定何时插入[ReAct](/source/e504ba1ba9c3fffc)。

论文的主要设置是冻结的大模型（PaLM-540B）加少量人工编写的 in-context 示例，不修改权重；作者也报告了在更小模型上用正确轨迹做微调的结果[ReAct](/source/e504ba1ba9c3fffc)。

## 如何使用

适用场景：任务需要多步推理，且需要外部信息或环境交互，例如多跳问答、事实核查、文本游戏、网页导航。不适用场景：答案完全依赖模型内部知识、不需要外部事实校验时，纯思维链可能更简单。

选择条件可以归纳为：任务是否存在可定义的动作空间（检索、点击、移动等）；失败是否常来自事实错误或信息缺失；是否需要对中间步骤做人工检查。若动作空间很大而示例很少，论文指出这类任务需要更多示例，可能超出上下文长度限制[ReAct](/source/e504ba1ba9c3fffc)。

示例（非论文实验）：回答「某乐队主唱参演的电影由谁执导」时，先 thought 分解为「先查乐队、再查主唱、再查电影」，再 search 乐队名，读取 observation 后修正检索词，最后 finish 给出答案。

实践建议（基于证据的推论）：把 thought 与 action 的格式固定下来，便于人工检查与编辑；论文报告人类可以通过编辑 thought 改变智能体行为，而只改动作难以达到同样效果[ReAct](/source/e504ba1ba9c3fffc)。

## 证据与边界

当前依据主要来自一项研究（ICLR 2023 会议论文，arXiv 预印本 v3）[ReAct](/source/e504ba1ba9c3fffc)。

- 在 HotpotQA 与 FEVER 上，使用 PaLM-540B 与一个简单的 Wikipedia API，ReAct 优于只生成动作的 Act 基线，并与思维链（CoT）互有胜负；论文报告的最佳提示方法是在两者之间按启发式切换（ReAct→CoT-SC 或 CoT-SC→ReAct）[ReAct](/source/e504ba1ba9c3fffc)。
- 在 ALFWorld 上，ReAct 六组提示中的最佳结果为 71%，BUTLER 八组设置中的最佳结果为 37%，相差 34 个百分点；ReAct 六组提示的平均成功率为 57%。在 WebShop 上，ReAct 成功率为 40.0%，IL 基线为 29.1%，相差 10.9 个百分点[ReAct](/source/e504ba1ba9c3fffc)。这些结果采用 PaLM-540B 和一到两个 in-context 示例。最佳提示结果不等于平均表现；不同方法的设置和解码方式也有差异，不能由此推断一般任务上的提升。
- 论文对 HotpotQA 轨迹的人工分析显示，CoT 的主要失败模式是幻觉，而 ReAct 的失败更多来自推理错误与检索结果无效；作者把这种取舍解释为「事实性」与「推理灵活性」之间的权衡[ReAct](/source/e504ba1ba9c3fffc)。
- 论文也报告 ReAct 在 PaLM-8/62B 上直接提示表现最差，但用约 3000 条正确轨迹微调后成为四种方法中最好的[ReAct](/source/e504ba1ba9c3fffc)。

边界：以上结果都来自该论文的模型、提示与基准设置；论文未证明 ReAct 在任意任务或任意模型上都优于纯推理或纯行动。

## 常见误解

**把 thought 当成对外部世界的操作。** thought 不改变环境、不产生观察，只有 action 才会；把两者混为一谈会导致对轨迹状态的误判[ReAct](/source/e504ba1ba9c3fffc)。

**认为 ReAct 一定优于思维链。** 论文中 ReAct 在 FEVER 上优于 CoT，但在 HotpotQA 上略低于 CoT；作者据此提出组合两种方法，而不是用其一替代另一个[ReAct](/source/e504ba1ba9c3fffc)。

## 相关知识

- [[agent-context-memory-governance|智能体上下文与记忆]]
- [[agent-tool-call-gates|工具调用门控]]
- [[agent-plan-repair|计划修补]]

---

## 原始证据

- [ReAct: Synergizing Reasoning and Acting in Language Models](/source/e504ba1ba9c3fffc) · arXiv 2210.03629v3 · [原文](https://arxiv.org/abs/2210.03629)
