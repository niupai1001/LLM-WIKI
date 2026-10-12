# 计划、执行轨迹与最终结果的分层评价

Agent的计划合理、工具调用成功和最终答案正确是三个不同判断。知识页引用成绩时，需要先说明指标对应哪一层，否则容易把规划分数当成真实执行能力，或把一次无报错运行当成正确结论。

## 计划检查与真实执行

Remote Sensing ChatGPT的138条查询主要评价必要任务是否规划正确；HTAM/EarthAgent的GeoPlan-bench实验评价生成的工具序列和规划质量。这些证据不能直接替代完整执行成功率。[S:e80b94c99e711343][S:cf1e618aae1ce2d6]

AutoML-Agent先用提示技术产生候选数据/模型方案，随后由Operation Agent运行实现代码。前期预期性能和后期运行验证应分开记录；[[scientific-automl-agent-workflows|科学分析工作流]]详细解释它们怎样衔接。[S:5d666092726c8b5f]

## 轨迹与最终产物

Earth-Agent的Earth-Bench同时检查答案与工具顺序、参数。TerraLogic的数据构建要求工具链可执行、产物满足基本有效性，并让最终量值来自工具输出。这些检查为追踪错误提供依据，但其协议和指标不同，不能直接比较分数。[S:1a874e5d2226c159][S:8db3263052b0cab1]

[[agent-plan-repair|执行恢复]]可能修复代码、工具选择或计划的一段；恢复后仍需检查结果。GIS Copilot v4的正文与Table 1还存在成功率报告不一致，已经保留两处记录；没有明确口径时不选择其中一组作为确定成绩。[S:8cabb777c9bc6fe9]

## 对照条件也属于证据

[[agent-hyperparameter-optimization|AgentHPOBench]]在受限干预空间内固定数据、划分、指标和评估代码，并控制训练预算。预算和反馈内容改变，成绩可能随之变化；不能把改了评分规则、数据或训练资源的结果直接归因于智能体更会决策。[S:d7eeabd9156ed311]

引用任何一项工作时，应明确：检查对象是计划、调用还是结果；任务与指标是什么；运行和反馈预算是什么；是否包含人工反馈或评估器判断。这些是证据范围，多个来源出现相似观点仍不等于独立复现。

## 相关知识

- [[agent-hyperparameter-optimization]]
- [[agent-plan-repair]]
- [[geospatial-hierarchical-planning-agents]]
- [[scientific-automl-agent-workflows]]

---

## 原始证据

- [Earth-Agent: Unlocking the Full Landscape of Earth Observation with Agents](/source/1a874e5d2226c159) · 来源ID `1a874e5d2226c159`
- [AutoML-Agent: A Multi-Agent LLM Framework for Full-Pipeline AutoML](/source/5d666092726c8b5f) · 来源ID `5d666092726c8b5f`
- [GIS Copilot: Towards an Autonomous GIS Agent for Spatial Analysis](/source/8cabb777c9bc6fe9) · 来源ID `8cabb777c9bc6fe9`
- [TerraLogic: A Benchmark for Hierarchical Geospatial Reasoning in Earth Observation](/source/8db3263052b0cab1) · 来源ID `8db3263052b0cab1`
- [Designing Domain-Specific Agents via Hierarchical Task Abstraction Mechanism](/source/cf1e618aae1ce2d6) · 来源ID `cf1e618aae1ce2d6`
- [AgentHPOBench: A Benchmark For Evaluating LLM Agents as Sequential Hyperparameter Optimizers](/source/d7eeabd9156ed311) · 来源ID `d7eeabd9156ed311`
- [Remote Sensing ChatGPT: Solving Remote Sensing Tasks with ChatGPT and Visual Models](/source/e80b94c99e711343) · 来源ID `e80b94c99e711343`
