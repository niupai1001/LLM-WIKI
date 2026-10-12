# 遥感感知如何接入可执行 Agent：视觉工具交互、变化解释与视觉定位

遥感视觉模型提供图像描述、定位或变化掩膜；可执行Agent还需要把用户要求转换成工具或代码操作。Remote Sensing ChatGPT、GeoChat与Change-Agent分别展示了视觉描述接入工具控制器、遥感视觉语言模型、专用变化模型接入代码执行三种路径。

## Remote Sensing ChatGPT：视觉线索与工具循环

Remote Sensing ChatGPT使用BLIP生成遥感图像描述，把视觉线索提供给当时作为控制器的文本ChatGPT。提示还包含任务库中的功能、支持类别、输入输出与依赖信息，帮助模型决定调用什么工具。工具处理图像后返回新的观测，模型继续安排操作，最终根据已执行任务的输出作答。[S:e80b94c99e711343]

这份研究展示了规划与工具交互，但其138条查询实验主要检查任务规划正确性，不能把该分数当成图像解释精度或整个分析流程的成功率。视觉工具不支持目标类别时会失败；工具或信息不足时，语言模型还可能生成没有依据的回答。[S:e80b94c99e711343]

## GeoChat：图像与空间位置进入同一模型

GeoChat使用视觉编码器、跨模态适配器与语言模型把图像信息接入对话。训练材料覆盖遥感描述、问答、场景分类和区域定位；不同任务使用相应标识，空间位置以文本坐标表示，可用于输入感兴趣区域或输出定位结果。[S:863bccdceb2db4eb]

GeoChat是遥感视觉语言模型，原论文并未将它构建为调用GIS软件的完整执行器。它输出的描述和位置可以作为后续系统的输入，这属于组合设计的可能性，不能据此认为它已完成工具编排。论文中的小目标与多目标定位仍较困难。[S:863bccdceb2db4eb]

## Change-Agent：变化感知与可运行代码

Change-Agent同时读取双时相图像。多层级变化解释模型包含像素级变化检测和语言描述两个分支：一个产生变化掩膜，一个说明变化内容。两分支通过时相交互与多任务训练学习相关特征，为后续操作提供可处理的结果。[S:014cdafa93ee999c]

语言模型依据用户指令规划并编写Python代码，由解释器调用视觉分支和相关库。例如检测建筑或道路变化后，可以保存掩膜、着色和统计变化对象。代码执行与专用感知模型负责这些可检查的产物。[S:014cdafa93ee999c]

论文的交互案例也让模型推测变化原因和可能的未来发展；这些回答是基于图像变化及语言模型知识生成的解释，不能视为已确认的因果关系或可靠预测结果。其训练与实验主要使用带检测掩膜和变化描述的遥感数据，小变化建筑的检测仍存在明显困难。[S:014cdafa93ee999c]

## 从感知结果走向多步分析

这些路径解决不同连接问题：Remote Sensing ChatGPT让视觉描述与工具观测进入控制循环，GeoChat把视觉与位置学习进模型，Change-Agent让专用变化结果进入可执行代码。它们的任务和指标不同，不能按各自论文分数判断通用优劣；将视觉模型与执行器组合也需要单独验证。[S:e80b94c99e711343][S:863bccdceb2db4eb][S:014cdafa93ee999c]

[[remote-sensing-tool-augmented-agents|工具生态与轨迹学习]]继续讨论RS-Agent的专家流程、Earth-Agent的MCP工具和OpenEarthAgent的轨迹训练，适合接着理解感知产物怎样进入更长的工具链。[S:d97a83738e867ba8][S:1a874e5d2226c159][S:a58012192462df16]

[[geospatial-hierarchical-planning-agents|层级规划与容错]]则讨论任务前置关系、工具组织及失败后的恢复。HTAM的领域任务分层与HieraPlan的工具层级属于不同设计；需要结合任务依赖、输入输出和实际执行条件考察它们与感知模块的衔接。[S:cf1e618aae1ce2d6][S:8db3263052b0cab1]

## 跨研究的机制联系

本组图像描述、对象框和变化掩膜的不同作用，集中解释在[[remote-sensing-perception-outputs|感知输出与后续输入]]。Remote Sensing ChatGPT把视觉工具输出反馈为新观察，与[[react-reasoning-acting-loop|ReAct]]的交互协议相接；GeoChat原文是一项VLM能力，未证明它已经是完整工具执行Agent。[S:e80b94c99e711343][S:e504ba1ba9c3fffc][S:863bccdceb2db4eb]

变化掩膜后续计数、保存或其他操作，还需要[[agent-tool-interface-contracts|产物契约]]；规划和感知的分数必须按[[agent-evaluation-layers|评价层次]]解释。页面保留不同训练和实验条件，共享概念不使这些分数变得可直接比较。

## 相关知识

- [[agent-evaluation-layers]]
- [[agent-tool-interface-contracts]]
- [[geospatial-hierarchical-planning-agents]]
- [[react-reasoning-acting-loop]]
- [[remote-sensing-perception-outputs]]
- [[remote-sensing-tool-augmented-agents]]

---

## 原始证据

- [Change-Agent: Towards Interactive Comprehensive Remote Sensing Change Interpretation and Analysis](/source/014cdafa93ee999c) · 来源ID `014cdafa93ee999c`
- [Earth-Agent: Unlocking the Full Landscape of Earth Observation with Agents](/source/1a874e5d2226c159) · 来源ID `1a874e5d2226c159`
- [GeoChat: Grounded Large Vision-Language Model for Remote Sensing](/source/863bccdceb2db4eb) · 来源ID `863bccdceb2db4eb`
- [TerraLogic: A Benchmark for Hierarchical Geospatial Reasoning in Earth Observation](/source/8db3263052b0cab1) · 来源ID `8db3263052b0cab1`
- [OpenEarthAgent: A Unified Framework for Tool-Augmented Geospatial Agents](/source/a58012192462df16) · 来源ID `a58012192462df16`
- [Designing Domain-Specific Agents via Hierarchical Task Abstraction Mechanism](/source/cf1e618aae1ce2d6) · 来源ID `cf1e618aae1ce2d6`
- [RS-Agent: Automating Remote Sensing Tasks through Intelligent Agent](/source/d97a83738e867ba8) · 来源ID `d97a83738e867ba8`
- [ReAct: Synergizing Reasoning and Acting in Language Models](/source/e504ba1ba9c3fffc) · 来源ID `e504ba1ba9c3fffc`
- [Remote Sensing ChatGPT: Solving Remote Sensing Tasks with ChatGPT and Visual Models](/source/e80b94c99e711343) · 来源ID `e80b94c99e711343`
