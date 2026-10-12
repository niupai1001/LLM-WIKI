# 工具知识、选择与流程复用

Agent选择工具需要知道工具的能力、输入条件、输出和适合的组合顺序。工具知识可以是接口文档、专家流程，也可以是已经成功执行的程序。三者分别回答“工具怎样调用”“任务通常怎样完成”“哪些步骤确实运行过”，不应混成一个笼统的知识库。

## 工具文档与任务流程

GeoGPT在工具池中说明操作的功能及输入输出，让模型据此选工具；GIS Copilot进一步使用QGIS帮助文档中的参数与代码示例，帮助生成具体PyQGIS代码。工具文档缩小函数和参数的猜测范围，但文档存在仍不能保证选型、投影或运行结果正确。[S:3bdb54f327d29558][S:8cabb777c9bc6fe9]

RS-Agent先识别任务类型，再检索专家编写的Solution Space方案。方案包含任务条件、推荐工具链和预期输出，比单个API说明多了一层程序性知识。Knowledge Space的DualRAG则结合整体语义和关键词分解，保留传感器、分辨率等约束。流程检索和领域文档检索承担不同作用。[S:d97a83738e867ba8]

Remote Sensing ChatGPT把任务库的功能、输入输出、依赖信息和示例放进提示，让模型规划和调用视觉工具。它与RS-Agent都给控制器提供执行知识，区别是提示内提供任务描述，与运行时按任务检索专家流程；不能据此说两者已经拥有同一种检索模块。[S:e80b94c99e711343][S:d97a83738e867ba8]

## 成功历史怎样变成可复用程序

PhenoAssistant的Pipeline Reproducer抽取聊天历史里实际执行过的函数与代码，排除失败片段，整理成新的Python函数。它保存的是已发生的执行流程；RS-Agent的方案库主要是专家事先设计的任务流程。两者都能承载“怎样做”的知识，产生途径与后续使用能力不同。[S:f8a9212514d96b57][S:d97a83738e867ba8]

这不能证明PhenoAssistant已经自动检索这些函数来规划新任务，也不能把RS-Agent的检索理解为模型权重学习。与[[agent-tool-experience-learning|工具使用经验的训练]]相比，本页主要讨论运行时可读取的知识和可复用程序。[S:f8a9212514d96b57][S:d97a83738e867ba8]

## 选择、调用和创建的边界

流程中选定工具后，[[agent-tool-interface-contracts|接口与中间产物契约]]约束参数及结果的传递；如果现有操作无法覆盖任务，[[tool-creation-agent|工具创建]]处理生成新脚本的问题。已有工具多、描述完整或检索成功，都不能直接证明已经具备现场创建新工具的能力。

## 相关知识

- [[agent-tool-experience-learning]]
- [[agent-tool-interface-contracts]]
- [[remote-sensing-tool-augmented-agents]]
- [[scientific-automl-agent-workflows]]
- [[tool-creation-agent]]

---

## 原始证据

- [GeoGPT: Understanding and Processing Geospatial Tasks through An Autonomous GPT](/source/3bdb54f327d29558) · 来源ID `3bdb54f327d29558`
- [GIS Copilot: Towards an Autonomous GIS Agent for Spatial Analysis](/source/8cabb777c9bc6fe9) · 来源ID `8cabb777c9bc6fe9`
- [RS-Agent: Automating Remote Sensing Tasks through Intelligent Agent](/source/d97a83738e867ba8) · 来源ID `d97a83738e867ba8`
- [Remote Sensing ChatGPT: Solving Remote Sensing Tasks with ChatGPT and Visual Models](/source/e80b94c99e711343) · 来源ID `e80b94c99e711343`
- [A conversational multi-agent AI system for automated plant phenotyping](/source/f8a9212514d96b57) · 来源ID `f8a9212514d96b57`
