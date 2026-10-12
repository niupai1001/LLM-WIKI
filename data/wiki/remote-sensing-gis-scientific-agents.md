# 遥感、GIS与自主实验资料清单

本组资料收录15项工作的完整PDF，按遥感感知、GIS工作流、领域规划、工具轨迹和自主实验组织阅读。GeoAgentic-RAG的公开PDF暂未成功获取，未用摘要代替全文编写。

## 按问题阅读

- **图像能力怎样接入执行？** [[remote-sensing-perception-to-agent|遥感感知与可执行Agent]]比较Remote Sensing ChatGPT、GeoChat和Change-Agent，区分视觉语言模型、视觉工具控制器和代码执行。
- **自然语言怎样变成GIS工作流？** [[llm-gis-workflow-generation|GIS工具与工作流生成]]比较GeoGPT、GIS Copilot和GeoFlow的数据传递、工具文档、依赖表示与执行反馈。
- **复杂任务怎样分层、失败后怎样恢复？** [[geospatial-hierarchical-planning-agents|地理空间层级规划与容错]]讨论HTAM/EarthAgent与HieraPlan/TerraLogic的机制和评测范围。
- **专业工具与经验怎样积累？** [[remote-sensing-tool-augmented-agents|遥感工具生态与轨迹学习]]连接RS-Agent、Earth-Agent和OpenEarthAgent的专家流程、接口与轨迹训练。
- **科学分析怎样组织数据、模型和验证？** [[scientific-automl-agent-workflows|专业科学分析与AutoML工作流]]比较PhenoAssistant和AutoML-Agent。
- **下一轮实验参数怎样选择、收益怎样评价？** [[agent-hyperparameter-optimization|实验反馈与超参数优化]]分别说明AgentHPO方法与AgentHPOBench评测协议。

## 原件与版本

点击资料进入PDF阅读页；作者、日期、DOI和正式记录在该页展开查看。作者预印本与正式PDF分别标注，未获取的正式版本实验不会从摘要补入。

| 工作 | 归档版本 |
| --- | --- |
| [Remote Sensing ChatGPT](/source/e80b94c99e711343) | 作者预印本 v1 |
| [GeoGPT](/source/3bdb54f327d29558) | 作者预印本 v1（2023早期稿；另记2024正式文章） |
| [GIS Copilot](/source/8cabb777c9bc6fe9) | 作者预印本 v4 |
| [RS-Agent](/source/d97a83738e867ba8) | 作者预印本 v4；已关联正式发表记录 |
| **EarthAgent / HTAM** [S:cf1e618aae1ce2d6] | 作者预印本 v1 |
| [Earth-Agent](/source/1a874e5d2226c159) | 作者预印本 v3 |
| [OpenEarthAgent](/source/a58012192462df16) | 作者预印本 v4 |
| [HieraPlan / TerraLogic](/source/8db3263052b0cab1) | 作者预印本 v1 |
| [GeoFlow](/source/19f9b059cd25c958) | 作者预印本 v1；已关联正式发表记录 |
| [GeoChat](/source/863bccdceb2db4eb) | 正式PDF |
| [Change-Agent](/source/014cdafa93ee999c) | 作者预印本 v3 |
| **PhenoAssistant** [S:f8a9212514d96b57] | 正式PDF |
| **AgentHPO** [S:94853529d326ac72] | 作者预印本 v3；已关联正式发表记录 |
| [AutoML-Agent](/source/5d666092726c8b5f) | 作者预印本 v2；已关联正式发表记录 |
| [AgentHPOBench](/source/d7eeabd9156ed311) | 作者预印本 v1 |

## 需要保留的差异

Earth-Agent与HTAM/EarthAgent是两项工作；HieraPlan是TerraLogic论文提出的Agent，作为同一来源收录。GIS Copilot归档v4的正文与Table 1成功率不一致，两处记录与冲突在GIS工作流知识页保留，没有选择一个数字作为确定结论。

GeoAgentic-RAG：DOI为`10.1016/j.jag.2026.105195`，正式文章身份已核对。当前出版社PDF请求返回403，尚无可实际获取的公开作者稿；继续保留待下载记录，不参与本轮全文知识编写。

## 相关知识

- [[agent-hyperparameter-optimization]]
- [[geospatial-hierarchical-planning-agents]]
- [[llm-gis-workflow-generation]]
- [[remote-sensing-perception-to-agent]]
- [[remote-sensing-tool-augmented-agents]]
- [[scientific-automl-agent-workflows]]

---

## 原始证据

- [Change-Agent: Towards Interactive Comprehensive Remote Sensing Change Interpretation and Analysis](/source/014cdafa93ee999c) · 来源ID `014cdafa93ee999c`
- [GeoFlow: Agentic Workflow Automation for Geospatial Tasks](/source/19f9b059cd25c958) · 来源ID `19f9b059cd25c958`
- [Earth-Agent: Unlocking the Full Landscape of Earth Observation with Agents](/source/1a874e5d2226c159) · 来源ID `1a874e5d2226c159`
- [GeoGPT: Understanding and Processing Geospatial Tasks through An Autonomous GPT](/source/3bdb54f327d29558) · 来源ID `3bdb54f327d29558`
- [AutoML-Agent: A Multi-Agent LLM Framework for Full-Pipeline AutoML](/source/5d666092726c8b5f) · 来源ID `5d666092726c8b5f`
- [GeoChat: Grounded Large Vision-Language Model for Remote Sensing](/source/863bccdceb2db4eb) · 来源ID `863bccdceb2db4eb`
- [GIS Copilot: Towards an Autonomous GIS Agent for Spatial Analysis](/source/8cabb777c9bc6fe9) · 来源ID `8cabb777c9bc6fe9`
- [TerraLogic: A Benchmark for Hierarchical Geospatial Reasoning in Earth Observation](/source/8db3263052b0cab1) · 来源ID `8db3263052b0cab1`
- [Large Language Model Agent for Hyper-Parameter Optimization](/source/94853529d326ac72) · 来源ID `94853529d326ac72`
- [OpenEarthAgent: A Unified Framework for Tool-Augmented Geospatial Agents](/source/a58012192462df16) · 来源ID `a58012192462df16`
- [Designing Domain-Specific Agents via Hierarchical Task Abstraction Mechanism](/source/cf1e618aae1ce2d6) · 来源ID `cf1e618aae1ce2d6`
- [AgentHPOBench: A Benchmark For Evaluating LLM Agents as Sequential Hyperparameter Optimizers](/source/d7eeabd9156ed311) · 来源ID `d7eeabd9156ed311`
- [RS-Agent: Automating Remote Sensing Tasks through Intelligent Agent](/source/d97a83738e867ba8) · 来源ID `d97a83738e867ba8`
- [Remote Sensing ChatGPT: Solving Remote Sensing Tasks with ChatGPT and Visual Models](/source/e80b94c99e711343) · 来源ID `e80b94c99e711343`
- [A conversational multi-agent AI system for automated plant phenotyping](/source/f8a9212514d96b57) · 来源ID `f8a9212514d96b57`
