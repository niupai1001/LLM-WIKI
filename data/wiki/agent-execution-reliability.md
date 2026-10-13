# 智能体执行可靠性

<!-- wiki-topic:start -->

沿执行前、失败后和结果评价三个位置阅读：先了解调用门控，再了解失败归因，最后区分计划、执行轨迹和最终结果的检查。领域案例用于对照具体执行与恢复方式，不把这些机制的组合当成已有共同实验结论。

## 按问题阅读

- 调用执行之前，怎样检查规则与授权条件？ → [[agent-tool-call-gates|工具调用门控]]
- 最终报错是否就是失败的根因？ → [[multi-agent-failure-attribution|多智能体失败归因]]
- 怎样分别判断计划、调用和最终结果？ → [[agent-evaluation-layers|计划、执行轨迹与最终结果的分层评价]]

## 研究与实现参考

- GIS代码与工作流的执行错误怎样处理？ → [[llm-gis-workflow-generation|自然语言驱动的 GIS 空间分析工作流生成与执行]]
- 领域规划在失败后怎样保留有效步骤？ → [[geospatial-hierarchical-planning-agents|地理空间任务的层级规划与容错：HTAM/EarthAgent 与 HieraPlan/TerraLogic]]

<!-- wiki-topic:end -->

---

## 原始证据

- [Error-Propagation Modeling for Failure Attribution in LLM-Based Multi-Agent Systems](/source/0b44ee6389202cb6) · 来源ID `0b44ee6389202cb6`
- [NOMOS: Compiling Written Policies into Statically Verified Tool-Call Gates for LLM Agents](/source/e0c0d198c4709cc7) · 来源ID `e0c0d198c4709cc7`
- [ReCast: Attribution-Oriented Step Representation Learning for LLM-Based Agent Systems](/source/eddf2edee3305425) · 来源ID `eddf2edee3305425`
