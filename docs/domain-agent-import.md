# 遥感、GIS与自主实验 Agent 收录记录

2026-10-12。用户指定16项工作：完整PDF成功收录15项，GeoAgentic-RAG因访问限制暂未编写。新增两个可编辑分类，无新增订阅或定时任务。

阅读入口：http://127.0.0.1:8765/page/remote-sensing-gis-scientific-agents

## 来源与版本

| 工作 | 原件 | 来源ID |
| --- | --- | --- |
| Remote Sensing ChatGPT | [作者预印本 v1](https://arxiv.org/pdf/2401.09083v1) | `e80b94c99e711343` |
| GeoGPT | [作者预印本 v1（2023早期稿；另记2024正式文章）](https://arxiv.org/pdf/2307.07930v1) | `3bdb54f327d29558` |
| GIS Copilot | [作者预印本 v4](https://arxiv.org/pdf/2411.03205v4) | `8cabb777c9bc6fe9` |
| RS-Agent | [作者预印本 v4；已关联正式发表记录](https://arxiv.org/pdf/2406.07089v4) | `d97a83738e867ba8` |
| EarthAgent / HTAM | [作者预印本 v1](https://arxiv.org/pdf/2511.17198v1) | `cf1e618aae1ce2d6` |
| Earth-Agent | [作者预印本 v3](https://arxiv.org/pdf/2509.23141v3) | `1a874e5d2226c159` |
| OpenEarthAgent | [作者预印本 v4](https://arxiv.org/pdf/2602.17665v4) | `a58012192462df16` |
| HieraPlan / TerraLogic | [作者预印本 v1](https://arxiv.org/pdf/2607.12497v1) | `8db3263052b0cab1` |
| GeoFlow | [作者预印本 v1；已关联正式发表记录](https://arxiv.org/pdf/2508.04719v1) | `19f9b059cd25c958` |
| GeoChat | [正式PDF](https://openaccess.thecvf.com/content/CVPR2024/papers/Kuckreja_GeoChat_Grounded_Large_Vision-Language_Model_for_Remote_Sensing_CVPR_2024_paper.pdf) | `863bccdceb2db4eb` |
| Change-Agent | [作者预印本 v3](https://arxiv.org/pdf/2403.19646v3) | `014cdafa93ee999c` |
| PhenoAssistant | [正式PDF](https://www.nature.com/articles/s41467-026-71090-y.pdf) | `f8a9212514d96b57` |
| AgentHPO | [作者预印本 v3；已关联正式发表记录](https://arxiv.org/pdf/2402.01881v3) | `94853529d326ac72` |
| AutoML-Agent | [作者预印本 v2；已关联正式发表记录](https://arxiv.org/pdf/2410.02958v2) | `5d666092726c8b5f` |
| AgentHPOBench | [作者预印本 v1](https://arxiv.org/pdf/2607.29626v1) | `d7eeabd9156ed311` |

## 知识组织

形成6个概念/方法比较页和1个导航入口，共80项结构化断言、149段可匹配原文摘录。正文集中解释机制、信息传递和适用条件；实验细节仍保留在可折叠证据中。

- 遥感感知与可执行Agent：Remote Sensing ChatGPT、GeoChat、Change-Agent。
- GIS工作流生成：GeoGPT、GIS Copilot、GeoFlow。
- 层级规划与容错：HTAM/EarthAgent、HieraPlan/TerraLogic。
- 工具生态与轨迹学习：RS-Agent、Earth-Agent、OpenEarthAgent。
- 科学分析与AutoML工作流：PhenoAssistant、AutoML-Agent。
- 实验反馈与参数优化：AgentHPO、AgentHPOBench。

GeoGPT归档为2023年早期作者稿，另关联2024年正式书目；作者列表和题名存在版本差异，不据正式摘要补入新实验。PMLR两份正式PDF在本机遇到TLS证书错误，保留验证，改用已核对身份的作者arXiv稿并关联正式记录。Nature与CVF使用正式PDF。

GeoAgentic-RAG的DOI与正式题名经CrossRef及出版社页面核对；出版社PDF返回403，公开作者稿未找到。没有以摘要或数据共享页冒充全文。待下载记录保存在主题入口与artifacts/domain-agent-import/final-report.json。

GIS Copilot v4 PDF第20页正文与第21页Table 1的基础/中级成功率不一致。两处报告及未解决冲突均保留，不选择某个数字作为确定结论。HPO提案最初试图替换科学分析页并遗漏原断言，Agent改为独立页，原页面与16项断言保留。

原件与原文在data/originals/和data/raw/，首次入库前数据库备份、下载与编写报告在artifacts/domain-agent-import/。发布采用现有配对备份和归档；Git保存知识正文、证据元数据及公开书目，完整迁移仍需保留本地data。

补齐fonttools依赖，修复畸形PDF字体映射输出UTF-16代理字符导致JSON保存失败；有效代理对保留，无法读取的字符使用替代标记，原PDF字节不改。出版日期显示保留年/月精度，避免把CVPR2024误显示为“未提供”。

验证：164项隔离回归通过；补充字符修复后的针对性来源/引用回归通过。15份PDF逐一检查签名、可读页、全文与Wiki归属；页面引用和知识链接校验通过，实际浏览器确认主题入口与13页Nature PDF阅读器可显示。程序能核对字面出处与结构，不能独立证明实验结论或复现研究。
