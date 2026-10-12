# 遥感感知输出怎样成为后续操作的输入

图像描述、目标框和变化掩膜都能表达遥感信息，但承担的作用不同。要接入后续Agent，先要说清它们表示什么、能用于哪种操作，以及哪些空间信息仍然缺失。

## 三种输出与对应动作

Remote Sensing ChatGPT用BLIP生成图像描述，给文本控制器提供视觉线索；视觉工具执行后再把结果作为新观察反馈给ChatGPT，决定后续动作。描述适合帮助理解任务，不能直接当成精确空间测量。[S:e80b94c99e711343]

GeoChat在回答中结合文本和对象位置，也接受用户给定感兴趣区域。其边界框坐标归一化到0–100，用于模型输入输出中的图像区域表达；这种图像定位不等同于GIS坐标投影或栅格空间处理。小目标与多个框的定位仍较困难。[S:863bccdceb2db4eb]

Change-Agent的多层级变化解释模型同时产生像素级变化检测与语义级变化描述，作为专门的感知工具。掩膜可进入后续着色、保存和对象计数等代码操作，描述用于解释变化内容。它与GeoChat目标框的表示单位不同，不能把二者当成同一空间产物。[S:014cdafa93ee999c][S:863bccdceb2db4eb]

## 从感知状态接到行动

[[react-reasoning-acting-loop|ReAct循环]]解释观察怎样进入下一次动作决策；[[agent-tool-interface-contracts|工具与产物契约]]说明图层、几何或栅格子集怎样被后续调用读取。OpenEarthAgent的编排器把工具输出写入工作记忆，并管理轨迹中的中间产物，展示了另一种执行侧组织。[S:a58012192462df16][S:e80b94c99e711343]

GeoChat本身的论文不是完整工具执行Agent。把它的区域理解接到某个工具控制器是组合设计建议，现有材料没有证明它已与Remote Sensing ChatGPT或Change-Agent构成并验证了一套系统。变化原因和未来发展也应区分工具测量与语言模型推测。[S:863bccdceb2db4eb][S:014cdafa93ee999c]

## 相关知识

- [[agent-tool-interface-contracts]]
- [[react-reasoning-acting-loop]]
- [[remote-sensing-perception-to-agent]]

---

## 原始证据

- [Change-Agent: Towards Interactive Comprehensive Remote Sensing Change Interpretation and Analysis](/source/014cdafa93ee999c) · 来源ID `014cdafa93ee999c`
- [GeoChat: Grounded Large Vision-Language Model for Remote Sensing](/source/863bccdceb2db4eb) · 来源ID `863bccdceb2db4eb`
- [OpenEarthAgent: A Unified Framework for Tool-Augmented Geospatial Agents](/source/a58012192462df16) · 来源ID `a58012192462df16`
- [Remote Sensing ChatGPT: Solving Remote Sensing Tasks with ChatGPT and Visual Models](/source/e80b94c99e711343) · 来源ID `e80b94c99e711343`
