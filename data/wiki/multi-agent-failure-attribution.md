# 多智能体失败归因

多智能体失败归因（failure attribution）要回答的是：一次失败的多智能体执行中，哪个智能体在哪一步是决定性的错误。它区别于“检测失败”和“定位报错位置”——最终报错的那一步往往只是下游症状，真正的致因可能出现在很早的步骤，其后果经过若干次交互才显现[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)[ReCast](/source/eddf2edee3305425)。

## 工作机制

归因任务通常被形式化为：给定任务查询和一条失败轨迹，输出一个“决定性”的智能体–步骤对。决定性错误的判定依赖反事实：假设只把该步的动作替换为可行的正确动作、保持此前历史不变并让后续执行重新展开，如果任务结果从失败变为成功，则该步是决定性的；若存在多个这样的步骤，基准协议取最早的一个作为唯一目标[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)[ReCast](/source/eddf2edee3305425)。两项工作采用的都是这一“最早反事实可恢复步骤”的定义，差别在于输出形式：EMFA 同时报告智能体级与步骤级结果，ReCast 主要对步骤做排序[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)[ReCast](/source/eddf2edee3305425)。

难点在于失败很少是孤立事件。错误可能沿依赖关系级联传播：早期偏差通过信息传递影响后续决策，后续步骤只是“载体”或症状；也可能表现为持续交互循环：多个智能体反复处理同一个未解决的目标而没有实质进展[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)。因此归因需要追踪错误如何影响后续执行，而不是孤立地判断单步是否“看起来有错”。

EMFA 只需要可观察的执行轨迹：先整理依赖关系，再筛查错误传播与无进展循环，最后由模型做反事实评估，预测修正哪一步可能阻断失败。这个评估不实际重放环境，不能视为已经验证恢复成功。[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)

ReCast 需要模型内部隐状态：把每个步骤的内部信号转换成表示，再训练编码器与排序器来定位关键步骤。它的结果依赖表示提取与训练数据，属于白盒方法。[ReCast](/source/eddf2edee3305425)

## 如何使用

适用场景：调试多智能体执行、定位可修复的关键步骤、为编排策略改进提供信号。当失败轨迹很长、最终报错与致因距离远、或存在反复无效交互时，显式建模传播与循环的方法更有针对性[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)。

实践建议（基于证据的推论）：只有文本轨迹时，可考虑依赖结构与模型推理；能访问内部信号且有训练资源时，可考虑表示学习。优先根据可获得的证据和部署成本选择，不能只看排行榜。

示例（非论文实验）：一条失败轨迹在最后一步给出错误答案，但倒数第 20 步某智能体在检索时遗漏了来源约束。归因的目标不是最后一步，而是判断修正该检索步是否会让整个执行恢复成功；若是，则它是决定性错误，最后一步只是症状[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)。

## 证据与边界

两项研究在 Who&When 等基准上报告了相对所比较方法的改进。它们都以最早的反事实可恢复步骤为根因目标，但 EMFA 同时报告智能体和步骤准确率，ReCast 主要报告步骤排序指标；候选筛选和基线协议也不同，结果不宜直接横向比较。[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)[ReCast](/source/eddf2edee3305425)

反事实根因是任务定义，不代表方法已经通过真实干预证明因果关系。模型评估可能判断错误；缺失或不完整的日志也会限制归因。归因输出应作为修复与复现实验的候选线索。[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)[ReCast](/source/eddf2edee3305425)

## 常见误解

第一，把最后报错的位置当作致因。最终失败步骤只是症状暴露处，可能完全是早期错误的后果，二者不一定重合[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)。

第二，把“看起来有错的步骤”都当作候选致因。轨迹中可能包含后来被纠正的偏差、以及仅继承早期错误的下游载体；区分决定性错误与传播载体需要追踪影响而非孤立判断[Error-Propagation Modeling for Failure Attrib](/source/0b44ee6389202cb6)。

## 相关知识

- [[agent-execution-reliability|智能体执行可靠性]]
- [[agent-plan-repair|计划修补]]
- [[agent-tool-call-gates|工具调用门控]]

---

## 原始证据

- [Error-Propagation Modeling for Failure Attribution in LLM-Based Multi-Agent Systems](/source/0b44ee6389202cb6) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11600)
- [ReCast: Attribution-Oriented Step Representation Learning for LLM-Based Agent Systems](/source/eddf2edee3305425) · 2026-10-08 · [原文](https://arxiv.org/abs/2610.11334)
