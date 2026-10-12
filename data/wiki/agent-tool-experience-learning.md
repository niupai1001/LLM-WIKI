# 工具使用经验怎样进入模型

工具经验可以作为运行时材料被检索，也可以整理成训练数据改变模型。Toolformer与OpenEarthAgent都先筛选执行经验再微调，但筛选对象和学习目标不同；工具创建则主要扩展运行时可执行操作，不能直接当成同一种训练方法。

## API调用的筛选

[[toolformer-self-supervised-tool-use-training|Toolformer]]在文本中采样候选API调用，执行后判断调用及结果是否降低后续token预测损失，再用保留的样本微调模型。这个筛选衡量调用对语言建模的帮助，不能直接证明地理计算或科学解释正确。原论文还明确限制了链式和交互式工具使用。[S:4a3710e0ff43d563]

## 多步轨迹的验证与训练

OpenEarthAgent的训练对象是包含多步调用与观测的轨迹。入库前通过工具控制器重放，检查参数格式、坐标、几何和整链可执行性，再用验证后的轨迹做监督微调。它检查整条操作过程，学习目标和Toolformer插入单个API调用的筛选不同，不能统称为相同自监督方案。[S:a58012192462df16][S:4a3710e0ff43d563]

重放通过仍只覆盖既定工具、数据和检查规则；更换地域、图层语义或任务后还要检查适用性。两份研究的模型、任务和指标不同，不能用各自成绩证明哪种训练普遍更优。[S:a58012192462df16][S:4a3710e0ff43d563]

## 训练、流程保存与工具创建

PhenoAssistant的流程复现模块把成功执行代码整理成函数，保存的是可复用程序；[[agent-tool-knowledge-selection|运行时工具知识]]可能读取这种材料，是否训练权重需要另看实现。[S:f8a9212514d96b57]

[[tool-creation-agent|OpenEarth-Agent工具创建]]按DAG节点生成并检查脚本，扩展的是当前任务可执行的操作。它与OpenEarthAgent轨迹训练是不同论文。现场生成脚本、保存程序和更新模型权重，应分别记录其输入、更新对象与验证范围。[S:84d839e63c28ec29][S:a58012192462df16]

## 相关知识

- [[agent-tool-knowledge-selection]]
- [[tool-creation-agent]]
- [[toolformer-self-supervised-tool-use-training]]

---

## 原始证据

- [Toolformer: Language Models Can Teach Themselves to Use Tools](/source/4a3710e0ff43d563) · 来源ID `4a3710e0ff43d563`
- [OpenEarth-Agent: From Tool Calling to Tool Creation for Open-Environment Earth Observation](/source/84d839e63c28ec29) · 来源ID `84d839e63c28ec29`
- [OpenEarthAgent: A Unified Framework for Tool-Augmented Geospatial Agents](/source/a58012192462df16) · 来源ID `a58012192462df16`
- [A conversational multi-agent AI system for automated plant phenotyping](/source/f8a9212514d96b57) · 来源ID `f8a9212514d96b57`
