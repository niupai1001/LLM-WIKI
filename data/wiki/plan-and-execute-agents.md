# Plan-and-Execute 规划与执行

Plan-and-Execute 把一项任务拆成多步计划，再由执行器按步骤使用工具。规划器负责安排要做的事；执行器接收用户查询和当前步骤，完成具体操作。这里介绍的是 LangChain 工程文章中的简单实现。[LangChain 规划智能体](/source/444b00f9850c9716)

## 计划与执行怎样衔接

1. 规划器根据任务生成多步骤计划，让执行阶段有明确的子任务。
2. 执行器结合用户查询和当前步骤，调用一个或多个工具取得结果。
3. 执行阶段返回后，系统带着重新规划提示判断任务是否完成：完成就回答，未达到预期就生成后续计划。[LangChain 规划智能体](/source/444b00f9850c9716)

这样的分工减少了每次工具调用都请求大规划模型的需要。不过，该实现仍为每个任务使用 LLM，工具调用也是串行的；费用和等待时间取决于子任务、模型和工具配置。文章的成本与速度分析属于工程设计判断。[LangChain 规划智能体](/source/444b00f9850c9716)

## 与 ReAct 的共同点和差别

[[react-reasoning-acting-loop|ReAct]] 与 Plan-and-Execute 都把语言模型的任务规划接到实际工具执行上。ReAct 的推理轨迹本身能够生成、跟踪和更新行动计划，并利用外部观察继续决策；Plan-and-Execute 将多步骤计划与执行器显式分开。因此，差别在规划与行动的组织粒度。[ReAct](/source/e504ba1ba9c3fffc) [LangChain 规划智能体](/source/444b00f9850c9716)

当下一步高度依赖刚取得的观察时，ReAct 的交互循环可以直接围绕这些信息继续决策；当任务已有可拆分的步骤时，显式计划便于组织这些步骤。还可以考虑让总体规划器安排子任务，让某个执行器用 ReAct 循环处理子任务中的工具交互。这是基于两种机制的组合设计建议，两份材料未直接验证该混合方案的效果。[ReAct](/source/e504ba1ba9c3fffc) [LangChain 规划智能体](/source/444b00f9850c9716)

## 同属规划范式，具体能力要分别看

ReWOO 允许用 `#E2` 等变量引用前序任务结果，worker 在调用时用实际结果替换变量，最后由 solver 整合答案；它减少了每次任务都重新规划的需要，但仍顺序执行。LLMCompiler 则输出带依赖关系的任务图，在依赖满足时调度可执行任务，由 Joiner 判断结束还是重新规划。变量引用、并行执行和重规划触发各属于对应实现，不能归入所有规划智能体的共同能力。[LangChain 规划智能体](/source/444b00f9850c9716)

ReAct 原论文的实验和这篇工程文章没有给出两者的直接受控比较，因此这里只比较机制与设计取舍，不据此排列准确率、速度或成本。[ReAct](/source/e504ba1ba9c3fffc) [LangChain 规划智能体](/source/444b00f9850c9716)

## 跨研究的机制联系

LLMCompiler与GeoFlow都把步骤依赖写进DAG，但节点组织的重点不同：前者按依赖调度任务并引用前序输出；GeoFlow还为子Agent明确API目标与输入上下文。这让“有计划”进一步变成“依赖怎样表达、节点究竟执行什么”的具体问题。统一解释见[[agent-task-decomposition-dependencies|任务分解与依赖约束]]。[S:444b00f9850c9716][S:19f9b059cd25c958]

简单Plan-and-Execute根据执行结果决定是否重新规划；HieraPlan则先尝试工具替换，必要时保留已验证前缀、只重规划后续步骤。反馈都参与下一决策，修订的粒度和保留对象不同，见[[agent-plan-repair|计划修补与执行恢复]]。这些材料没有给出二者的共同性能实验。[S:444b00f9850c9716][S:8db3263052b0cab1]

## 相关知识

- [[agent-execution-reliability]]
- [[agent-plan-repair]]
- [[agent-task-decomposition-dependencies]]
- [[llm-gis-workflow-generation]]
- [[react-reasoning-acting-loop]]

---

## 原始证据

- [GeoFlow: Agentic Workflow Automation for Geospatial Tasks](/source/19f9b059cd25c958) · 来源ID `19f9b059cd25c958`
- [Plan-and-Execute Agents](/source/444b00f9850c9716) · 来源ID `444b00f9850c9716`
- [TerraLogic: A Benchmark for Hierarchical Geospatial Reasoning in Earth Observation](/source/8db3263052b0cab1) · 来源ID `8db3263052b0cab1`
- [ReAct: Synergizing Reasoning and Acting in Language Models](/source/e504ba1ba9c3fffc) · 来源ID `e504ba1ba9c3fffc`
