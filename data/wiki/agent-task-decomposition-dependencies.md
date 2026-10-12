# 任务分解与依赖约束

把任务拆成步骤后，还要确定步骤需要什么输入、依赖哪一步，以及什么时候可以执行。任务分解回答“有哪些工作”；依赖约束回答“这些工作怎样组成能运行的过程”。多步骤任务可以使用顺序清单、任务图或领域层级，具体表示取决于要控制的依赖。

## 计划表示改变了什么

[[react-reasoning-acting-loop|ReAct]]在交互轨迹中生成和更新计划，读取观察后决定后续行动。[[plan-and-execute-agents|Plan-and-Execute]]把多步计划显式交给planner，再由executor完成步骤；其中LLMCompiler进一步输出任务DAG，让满足依赖的步骤得到调度。区别在计划表达和组件分工，不能把ReAct说成没有规划。[S:e504ba1ba9c3fffc][S:444b00f9850c9716]

HTAM先分析领域的前置关系，用任务依赖图组织系统层级；每次用户查询自顶向下选择子智能体，再自底向上处理数据。[[geospatial-hierarchical-planning-agents|EarthAgent的三层实例]]把数据预处理、分析和应用综合分开，这是一种领域先验。[S:cf1e618aae1ce2d6]

GeoFlow为具体请求生成DAG，节点写清子任务、负责的Agent及API目标，边表达该次任务的数据依赖。它与HTAM都重视先决关系，但约束的位置不同：HTAM先组织系统功能层，GeoFlow再组织一次运行的工作流。领域分层和运行时任务图可以互补，组合收益尚无这两篇材料的共同实验。[S:cf1e618aae1ce2d6][S:19f9b059cd25c958]

## 层级与候选方案需要分别理解

HieraPlan按功能、模态和类型组织工具，再检索相关子集。感知、空间关系、SAR和红外等分类帮助缩小选择空间；它们不等于五个必须依次执行的任务层。HTAM层级来自前置依赖，HieraPlan层级来自工具组织，两者不能只凭“hierarchical”合并为同一种规划。[S:8db3263052b0cab1][S:cf1e618aae1ce2d6]

AutoML-Agent的检索增强规划还会生成多个候选端到端方案。这增加的是方案搜索维度，候选计划与预测指标仍需后续代码实现验证。可以结合[[scientific-automl-agent-workflows|科学分析工作流]]理解计划、候选设计与真实运行的区别。[S:5d666092726c8b5f]

## 与后续机制的衔接

依赖表示告诉执行器如何接续结果，[[agent-tool-interface-contracts|工具接口与中间产物契约]]说明结果究竟是什么类型；发生失败时，[[agent-plan-repair|计划修补与执行恢复]]决定哪些部分保留、哪些部分重做。设计时应分别规定这些边界。上述材料支持机制对照，未支持把某一种表示判为所有任务的最优解。

## 相关知识

- [[agent-plan-repair]]
- [[agent-tool-interface-contracts]]
- [[geospatial-hierarchical-planning-agents]]
- [[llm-gis-workflow-generation]]
- [[plan-and-execute-agents]]
- [[react-reasoning-acting-loop]]
- [[scientific-automl-agent-workflows]]

---

## 原始证据

- [GeoFlow: Agentic Workflow Automation for Geospatial Tasks](/source/19f9b059cd25c958) · 来源ID `19f9b059cd25c958`
- [Plan-and-Execute Agents](/source/444b00f9850c9716) · 来源ID `444b00f9850c9716`
- [AutoML-Agent: A Multi-Agent LLM Framework for Full-Pipeline AutoML](/source/5d666092726c8b5f) · 来源ID `5d666092726c8b5f`
- [TerraLogic: A Benchmark for Hierarchical Geospatial Reasoning in Earth Observation](/source/8db3263052b0cab1) · 来源ID `8db3263052b0cab1`
- [Designing Domain-Specific Agents via Hierarchical Task Abstraction Mechanism](/source/cf1e618aae1ce2d6) · 来源ID `cf1e618aae1ce2d6`
- [ReAct: Synergizing Reasoning and Acting in Language Models](/source/e504ba1ba9c3fffc) · 来源ID `e504ba1ba9c3fffc`
