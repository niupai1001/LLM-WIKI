# 遥感 Agent 的工具生态与轨迹学习：RS-Agent、Earth-Agent 与 OpenEarthAgent

遥感Agent需要把用户问题转成可运行的专业操作，并把输出继续交给后续步骤。RS-Agent以专家流程和领域知识指导工具选择；Earth-Agent提供较大规模的遥感工具生态和定量任务评测；OpenEarthAgent进一步利用可重放的工具轨迹训练模型。它们处理的是工具与执行知识如何进入Agent的不同环节。

## RS-Agent：任务匹配与知识检索

RS-Agent先推断用户请求属于哪类预定义任务，再从Solution Space检索专家编写的解题流程，按流程组织工具调用。任务分类决定先查哪种流程；流程再帮助控制器选择和排列工具，减少仅凭工具名称猜测执行顺序的负担。[S:d97a83738e867ba8]

Knowledge Space提供相关领域知识。DualRAG结合整体查询的语义检索与关键词分解检索，旨在保留传感器类型、分辨率等容易被通用描述稀释的约束。专家流程和文档检索都依赖预先准备的知识空间，不能因此推断系统已经覆盖任意遥感任务。[S:d97a83738e867ba8]

## Earth-Agent：工具接口与任务轨迹

Earth-Agent在MCP接口之上接入104个专家工具，分为指数、反演、感知、分析和统计工具包。ReAct式的循环让模型读取工具结果后继续决定动作，工具范围也从RGB图像感知扩展到光谱和遥感产品上的分析。[S:1a874e5d2226c159]

它配套Earth-Bench，既评价最终答案，也评价工具序列和参数。评价轨迹有助于辨别“碰巧给出答案”与按适当工具链完成分析。论文消融还显示，调用更多工具并不持续提高效果：过长轨迹会增加基座模型处理依赖和反馈的负担，这一观察受实验模型与任务条件限制。[S:1a874e5d2226c159]

Earth-Agent与HTAM论文的EarthAgent是不同研究：前者关注工具生态和多步定量任务，后者把遥感任务依赖组织为三层。可结合[[geospatial-hierarchical-planning-agents|层级规划与容错]]阅读，分别考察工具接口和领域任务结构，避免按相似名称合并来源。[S:1a874e5d2226c159][S:cf1e618aae1ce2d6]

## OpenEarthAgent：从可验证轨迹到模型训练

OpenEarthAgent为视觉、光谱、GIS和地理参考栅格操作提供统一的工具调用结构。编排器解析并校验工具名与参数，执行工具，把结果写入工作记忆；图层、栅格子集和几何等中间产物缓存下来，供后续调用复用。[S:a58012192462df16]

训练材料是带推理、动作和观测的结构化轨迹。入库前先检查调用格式与文件引用，再重放工具链，核对每步是否可执行、观测是否匹配，最后检查坐标、几何、距离、面积或指数等空间结果。失败实例被丢弃或重新生成。验证后的轨迹用于监督微调，让模型学习工具调用的组织方式。[S:a58012192462df16]

论文报告14538个训练实例与1169个评测实例，但轨迹数量不能代替对新地域、数据格式和任务的验证。该工作的高层规划、上下文示例和工具顺序消融，属于其模型、工具集与数据条件下的结果。[S:a58012192462df16]

## 可复用的联系

三者都要把自然语言请求连接到专业工具和中间结果，但知识的进入位置不同：RS-Agent在运行时检索流程与文档，Earth-Agent扩展接口和工具选择空间，OpenEarthAgent还把执行轨迹用于训练。按任务需要可以研究这些环节如何互补；现有材料没有给出它们在同一协议下的直接比较，也没有验证任意组合一定改善效果。[S:d97a83738e867ba8][S:1a874e5d2226c159][S:a58012192462df16]

## 跨研究的机制联系

RS-Agent的任务匹配和专家流程进入[[agent-tool-knowledge-selection|工具知识与流程复用]]；Earth-Agent和OpenEarthAgent的接口组织进入[[agent-tool-interface-contracts|工具契约]]；OpenEarthAgent的重放与微调进入[[agent-tool-experience-learning|工具经验学习]]。同一来源支持多个机制，各机制仍保留自身证据范围。

Toolformer按后续token预测损失筛API调用，OpenEarthAgent重放检查完整操作轨迹后训练；学习的数据单位与验证目标不同。PhenoAssistant还提供另一条经验积累路径：把实际成功执行的代码抽为可复用函数，属于程序产物保存，不能自动解释为权重学习。[S:4a3710e0ff43d563][S:a58012192462df16][S:f8a9212514d96b57]

## 相关知识

- [[agent-tool-experience-learning]]
- [[agent-tool-interface-contracts]]
- [[agent-tool-knowledge-selection]]
- [[geospatial-hierarchical-planning-agents]]
- [[toolformer-self-supervised-tool-use-training]]

---

## 原始证据

- [Earth-Agent: Unlocking the Full Landscape of Earth Observation with Agents](/source/1a874e5d2226c159) · 来源ID `1a874e5d2226c159`
- [Toolformer: Language Models Can Teach Themselves to Use Tools](/source/4a3710e0ff43d563) · 来源ID `4a3710e0ff43d563`
- [OpenEarthAgent: A Unified Framework for Tool-Augmented Geospatial Agents](/source/a58012192462df16) · 来源ID `a58012192462df16`
- [Designing Domain-Specific Agents via Hierarchical Task Abstraction Mechanism](/source/cf1e618aae1ce2d6) · 来源ID `cf1e618aae1ce2d6`
- [RS-Agent: Automating Remote Sensing Tasks through Intelligent Agent](/source/d97a83738e867ba8) · 来源ID `d97a83738e867ba8`
- [A conversational multi-agent AI system for automated plant phenotyping](/source/f8a9212514d96b57) · 来源ID `f8a9212514d96b57`
