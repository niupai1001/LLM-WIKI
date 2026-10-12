# 工具接口与中间产物契约

工具契约说明一个操作叫什么、接受哪些参数、返回什么，以及结果怎样被下一步使用。Agent能够生成工具名称还不够：参数需要被执行器读懂，图层、栅格、数值或图表等中间产物也需要有明确的传递方式。

## 共享接口不等于相同协议

Earth-Agent用MCP接入专业工具包。PhenoAssistant用函数schema说明名称、描述、参数和输入输出格式，帮助manager组合植物表型工作流。它们共享的是让专业模块可被控制器调用的接口思想，不能把PhenoAssistant的schema写成已经使用MCP。[S:1a874e5d2226c159][S:f8a9212514d96b57]

OpenEarthAgent使用统一JSON契约，编排器解析模型调用、检查参数、执行工具，再把返回结果追加到工作记忆；图层、栅格子集和几何等中间产物可在轨迹中继续使用。接口约束因此同时影响调用是否合法和后续步骤能否接上数据。[S:a58012192462df16]

## 格式、依赖与授权是不同检查

[[agent-task-decomposition-dependencies|任务依赖]]解决先做什么，契约解决如何交换具体参数和产物。执行前还可能需要[[agent-tool-call-gates|策略门控]]：NOMOS依据工具、参数和具有结构性来源的事实决定放行或阻断，Agent自己声称“已获批准”不能变成授权事实。格式正确的调用仍可能违反政策。[S:e0c0d198c4709cc7][S:a58012192462df16]

## 怎样使用这些联系

阅读[[remote-sensing-tool-augmented-agents|遥感工具生态]]时，可以检查专业工具输出如何进入下一步；阅读[[scientific-automl-agent-workflows|科学分析工作流]]时，可以检查manager是否得到必要的数据和模型描述。schema、协议及策略门控解决不同层次的问题，任何一层通过都不能直接替代最终结果验证。

## 相关知识

- [[agent-task-decomposition-dependencies]]
- [[agent-tool-call-gates]]
- [[remote-sensing-tool-augmented-agents]]
- [[scientific-automl-agent-workflows]]

---

## 原始证据

- [Earth-Agent: Unlocking the Full Landscape of Earth Observation with Agents](/source/1a874e5d2226c159) · 来源ID `1a874e5d2226c159`
- [OpenEarthAgent: A Unified Framework for Tool-Augmented Geospatial Agents](/source/a58012192462df16) · 来源ID `a58012192462df16`
- [NOMOS: Compiling Written Policies into Statically Verified Tool-Call Gates for LLM Agents](/source/e0c0d198c4709cc7) · 来源ID `e0c0d198c4709cc7`
- [A conversational multi-agent AI system for automated plant phenotyping](/source/f8a9212514d96b57) · 来源ID `f8a9212514d96b57`
