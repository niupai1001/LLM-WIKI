# LLM Wiki — 从原始证据构建知识库

这个项目是本机优先的 LLM Wiki 工作台：**导入原文 → 主题分类 → LLM 综合编写 → 系统检查与发布 → Wiki 与知识图谱**。

首页围绕知识页面组织。原始资料是证据层，不把 RSS 简介当作论文原文，也不直接把每篇文章的摘要发布为 Wiki。

完整工程说明见 [项目文档与总体架构](docs/project-architecture.md)，包含模块职责、实体关系、数据流、编写发布流程、版本保护、接口与部署边界。

## 本机启动

使用 Python 3.12 和 uv，在项目根目录运行：

```bash
make setup
make test
make web
```

访问 http://127.0.0.1:8765。服务保持在当前终端运行，Ctrl+C 停止；未配置开机自启。

界面使用 Codex 风格的黑白灰工作空间，侧栏提供主题快捷入口。右上角可切换深浅色，偏好保存在当前浏览器。

`.env` 会自动加载，显式进程环境变量优先。修改模型配置后重启服务。依赖范围在 `requirements.txt`，本机与 Docker 使用 `requirements.lock.txt`。

## 从资料到 Wiki

1. 在“原始资料”页导入公开链接，或上传 PDF / Markdown / UTF-8 文本，单份最大 15 MB。为资料选择主题与类型，个人笔记与论文分开标注。
2. PDF 资料页直接展示原件，保留公式、表格和图片，支持翻页、页码跳转和缩放。阅读器使用本地 PDF.js 资源，无需 CDN。arXiv 优先归档 PDF；供 LLM 使用的提取文字另行保存，必要时补充 HTML 全文。其他网页与文本资料展示完整正文。
3. 只有 `full-text` 资料参与 Wiki 编写。`summary-only` 明确表示仅有摘要；`missing` 表示原文或可提取文字缺失。扫描 PDF 仍保留原件，但需要 OCR。获取失败的远程资料可点击“重新获取原文”。
4. 在首页点击“编写 Wiki 草稿”，按分类组合最多 8 份待编写原文。代码默认原文上下文预算为 160,000 字符，本地配置已提高至 320,000（`LLM_CONTEXT_CHARS` 可调整）。能放入预算的全文直接用于编写；超长资料会明确使用节选，完整文字继续保存在资料库。一次最多生成 3 个分类草稿。程序接口另支持按来源 ID 与具体知识问题选择材料。
5. 在“编写记录”页对照原始证据，通过系统检查后自动发布；可退回重写。更新草稿记录页面版本，原页面改动后必须重新生成，避免覆盖后续知识。
6. Wiki 页面保留原始证据引用、相关页面和反向链接；“知识图谱”展示实际 Wiki 链接、证据来源和分类，支持搜索、分类筛选、隐藏来源、拖拽和缩放。

在侧栏“主题与订阅”管理主题：新增、改名、填写关注范围或停用。标识由程序生成，改名会同步更新导航与分类，关注范围会提供给 LLM 作为编写方向。停用主题暂停关联订阅，并排除后续导入与编写；原有资料和知识仍可浏览。“其他”作为默认分类始终保留，已有资料、知识或订阅引用的主题不能直接删除。恢复主题后，需另行启用需要恢复的订阅。

### LLM 实际怎样提炼知识

系统先选有全文的原始资料，按现有页面的中英文术语和来源内容排序候选概念；候选概念可以跨分类，批次资料清单不参与候选。默认一次 LLM 调用，在原文约束下梳理方法、规划与执行步骤、信息传递、结果和限制，随后生成核心断言与解释正文。超预算长文按章节及分布选段，保留字符位置和节选标记，避免只截开头。模型选择摘录编号，程序复制原文作为证据；匹配原文不等于证实断言。来源详情可用“归入 Wiki”指定已有概念；类似的新证据优先补入旧断言，系统检查后发布。

自动采集仍按分类组合原文，没有语义聚类或向量检索。模型通过 `used_source_ids` 选择本概念实际使用的材料，未使用的原文留待下一篇；已批准原文也可按另一个知识问题复用。不存在的概念链接降为普通文字，不制造图谱节点。第二遍编辑与格式检查不能证明内容正确，仍需对照证据核查。模型使用提取文字，未进行 PDF 图片视觉解读。一次批次仍只生成一个概念提案，自动归并依赖模型判断，并不保证覆盖所有相关页面。

知识联系写进正文，并记录关系说明、范围、断言和来源。图谱区分共同机制、差异对照和实现实例，不用同属一个主题来代替知识关系。遥感、GIS和自主实验文献已按共享机制重编，见[知识联系改造记录](docs/knowledge-connections.md)。

首批 8 篇 Agent 论文现已整理为 4 个主题入口、7 个概念条目。每个概念分别可读、可引用和可更新；入口负责问题导航与阅读顺序。旧版综述保存在 `data/editorial/v2/previous/`，本轮维护记录在 `data/logs/wiki-editorial-v2.json`。

正文来源显示页内稳定编号与论文短名，例如 `[1 · Plan-and-Patch]`；悬停显示完整标题和发布日期，点击进入原始 PDF 资料页。页末编号证据列表显示完整标题、日期与原文链接。同一来源重复出现沿用编号，不用数据库 ID 作为阅读名称。

编写规范参考 [Karpathy 的 LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)（设计说明而非现成程序）、[Diátaxis](https://diataxis.fr/) 的读者任务区分和 [概念中心笔记](https://notes.andymatuschak.org/Evergreen_notes_should_be_concept-oriented)。[DeepWiki-Open](https://github.com/AsyncFuncAI/deepwiki-open) 的分层导航可参考，但它面向代码仓库文档，不直接替代论文证据 Wiki。

图谱直接在应用内提供，无需安装 Obsidian。正式 Wiki 是 Markdown，页面关系采用 `[[slug|名称]]` / `[[slug]]` 链接，可在 Obsidian 中打开 `data/wiki/` 浏览页面关系。应用内 `/source/ID` 证据链接需要本地服务运行。

## 模型配置

当前使用 DeepSeek 官方兼容接口，模型 ID `deepseek-flash` 对应 DeepSeek V4.1 Flash（[官方模型说明](https://api-docs.deepseek.com/en/)）。

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=在本地填写
LLM_JSON_MODE=true
LLM_CONTEXT_CHARS=320000
AUTO_DRAFT=true
```

密钥仅存 `.env`，不写入示例文件或版本库。其他兼容服务需确认支持 `/chat/completions` 与 `response_format=json_object`，不支持 JSON 模式时设 `LLM_JSON_MODE=false`。

当前 `AUTO_DRAFT=true`：每日采集成功后自动编写待审草稿，首页和“编写记录”可阅读，通过系统检查后自动发布。设为 false 可保持人工触发。编写会将选定原文（预算不足时为节选）和候选 Wiki 内容发送到配置的模型接口。通用示例配置在密钥未填写时默认关闭自动编写。

## 订阅源与自动采集

已配置 **Agent 相关内容，每天北京时间 08:00**：arXiv cs.AI 的 AI Agent 论文、ACL Anthology 已出版论文，以及 OpenAI 官方 Agent 资讯。原来的三个示例订阅保持禁用。网页“采集计划”显示实际任务加载状态、筛选规则和最近运行结果。

筛选采用原始标题与订阅摘要：匹配 Agent / agentic / multi-agent / tool use 等词；论文还需匹配 LLM / language model / foundation model。论文节点不限发布时间，官方资讯节点默认最近 7 天；排除 sponsored / sponsorship / 招聘，按 URL/DOI/arXiv ID 去重，每个源每轮最多 12 份新资料。关键词筛选在额度限制之前执行。RSS 只提供当前条目，放宽时间不能回溯整个文献库；历史基础参考可按官方出版链接手动导入。ACL 官方 RSS 约含最近 1000 篇出版记录。来源优先级属于人工策展，尚无自动相关性评分或质量评审；arXiv 内容不保证已经同行评审。

本机使用 macOS LaunchAgent，安装和检查：

```bash
.venv/bin/python -m station.scheduler install
.venv/bin/python -m station.scheduler status
```

安装不会立即抓取。本地配置已开启采集后自动编写待审草稿，仍需通过系统检查后自动发布。日志和最近状态保存在 `data/logs/`，锁防止重复运行。

日历时刻取决于 macOS **系统时区**，当前为 Asia/Shanghai；改变系统时区会改变触发时刻，网页会提示。Mac 需保持开机并登录，睡眠期间无法准时采集、唤醒后可能补触发；关机错过的任务不保证补跑。关闭网页终端不影响采集任务。树莓派应另行配置系统调度，macOS LaunchAgent 不能移植。

在“主题与订阅”的订阅节点区域新增、编辑、停用或删除 RSS / Atom 源，调整所属主题、名称、地址、资料类型、关键词与文章时限。关键词用逗号或换行分隔：“包含任一”和“还需匹配任一”分别至少命中一个，排除组命中即跳过；空组不限制，时限留空表示不限发布时间。关注范围不自动转换成关键词，需要在节点中明确设置筛选条件。保存后无需重启网页服务，下次采集使用新配置；删除节点只停止后续采集，原文与 Wiki 继续保留。不同页面同时编辑时，过期版本会提示刷新，避免覆盖他人的设置。配置仍保存在 `config/categories.json` 与 `config/feeds.json`。

也可手动执行：

```bash
make ingest
make collector   # 每 30 分钟发现资料并尝试获取原文；Ctrl+C 停止
```

`make collector MINUTES=1440` 是间隔约 24 小时的常驻轮询，不等于每日 08:00 日历调度；已安装每日任务时无需另开轮询进程。

站点许可、反爬、付费墙或扫描内容可能导致全文提取失败，失败状态会保留并显示，不会冒充原文。

## 命令行

```bash
.venv/bin/python -m station.cli import-url https://arxiv.org/abs/1706.03762 --category ai
.venv/bin/python -m station.cli import-file --title "资料标题" --file /path/to/paper.pdf --category ai
.venv/bin/python -m station.cli add-note --title "研究设想" --file /path/to/idea.md --category research
make prepare LIMIT=1 CATEGORY=ai
make drafts
make approve ID=<草稿来源ID>
.venv/bin/python -m station.cli reject <草稿来源ID>
make stats
```

`LIMIT` 是主题草稿批次数，不是原始资料数。单份资料详情的“生成 Wiki 草稿”按钮只使用该份原文；首页综合按钮用于同主题多来源编写。

## 数据结构

| 路径 | 内容 |
| --- | --- |
| `config/categories.json` | 主题分类 |
| `config/feeds.json` | 带启用开关与分类的订阅源 |
| `data/raw/<ID>.json` | 原文文字、来源元数据、类型、分类和获取状态 |
| `data/originals/` | 归档 PDF、HTML、Markdown、文本原件 |
| `data/state.db` | SQLite 索引、去重与编写状态 |
| `data/pending/` | 编写中或系统检查未通过的提案 |
| `data/wiki/` | 正式 Markdown 知识页面 |
| `data/wiki_meta/` | 页面分类、证据来源与知识关联 |
| `data/claim_reviews/` | 绑定正文、断言及来源版本的逐项核查历史 |
| `data/logs/` | 定时任务日志、最近运行状态与锁 |

备份整个 `data/`；SQLite 正在写入时用在线备份接口，或停机后完整复制目录。`.md.bak` 只保留最近一次更新，不是完整历史。旧 SQLite 会自动增加分类与内容状态字段，不会自动把旧摘要标成全文。

## Docker / 树莓派

```bash
cp .env.example .env   # 仅首次创建；已有配置不要覆盖
# 在本地填写 API 密钥
# 默认仅启动网页
docker compose up -d --build
# 显式启用 collector，仍只读取 enabled=true 的源
docker compose --profile collect up -d --build
```

容器网页仅绑定宿主机 `127.0.0.1:8765`。`config/` 使用可写挂载，以便网页保存主题与订阅，并与采集进程共享配置锁。树莓派使用 64-bit 系统与 Docker Compose，并迁移 `.env`、`config/` 和完整 `data/`；当前未验证树莓派实机。

## 验证与边界

`make test` 包含离线去重/分类/全文状态/原件/下载限制测试，以及临时数据目录的 HTTP 工作流：上传完整原文、同主题多来源编写、网页审批、证据图谱、扫描 PDF 提示、虚假引用拒绝、长文预算、并发修改保护。模型测试使用模拟回复；真实 API 连通另行验证。

应用只在本地监听，没有账号认证。暂不包括 OCR、网页图表复刻、全量语义检索、自动事实核验或跨主题实体消歧。知识图谱依据已存在的页面链接与来源关系构建；LLM 推断不是已证实的实体关系。

## 证据、资料质量与版本管理

知识页的“逐项事实核查”展示断言、适用范围、原文摘录及章节/页码、判断理由、核查者和时间，支持“原文支持 / 部分支持 / 与原文不符 / 无法核查”。人工记录需在页面展开“记录人工复核”，填写来源、直接原文摘录、位置和理由；摘录必须匹配所选归档全文。记录绑定正文、断言和所用来源版本，变更后显示需重新核查，历史保留。模型新增断言同时提出 evidence_passages，程序检查摘录确实存在，但仍标为待核对，不能自动变成核查结论。

当前 ReAct 与长期记忆准入有 5 条代理逐项核对记录，人工复核为 0；其他条目继续待核查。该机制检查对归档原文的忠实性，不证明实验真实有效或已经独立复现。已修正 ReAct 成功率差的单位、基线和提示选择条件，并记录外部 LLM 评审的有效性限制。“来源、编辑记录与差异记录”保留来源状态与编辑维护历史，区分单源支持、多源表述一致、范围差异和未解决冲突。作品标识去重不代表独立研究，来源数量和 Wiki 发布批准不表示知识已证实。模型新生成的证据记录明确标为提议；正文更新会失效旧内容核查标记，保存历史并使原差异重新待核查。

来源页展开“来源与出版信息”可查看作者、版本、首次提交/修订/RSS推送/采集日期、出版与预印本 DOI、获取方式、字段核对依据及入选理由。缺失机构或 DOI 不由模型猜测。正式发表与预印本分别记录作者、日期、DOI 和 PDF；来源页以实际归档版本展示身份。已补充 Toolformer、Reflexion（NeurIPS 2023）、SWE-bench、SOTOPIA（ICLR 2024）的正式 PDF。前三篇保留原来源 ID 与预印本 PDF，版本不算另一项研究。正式元数据不会被后续 arXiv 更新覆盖。同作品的正式出版页与已登记预印本链接导入时去重。

RSS 只用于发现候选材料。当前保留 AI Agent 论文订阅，已移除重复的语言模型 Agent 论文订阅；官方资讯订阅单独保留。最新预印本与基础参考分开标注。具体规则见 [来源质量规范](config/source-quality.md)；运行时编写规范见 [Wiki 编写规范](config/wiki-writing.md)，已吸收 Microsoft LLM Wiki 和社区 ingest/lint 的方法，但当前仍为单概念待审提案，尚无自动多页事务更新。

项目是独立 Git 仓库，使用 `master`。代码、配置、知识正文、知识证据元数据与公开书目快照 `data/source-catalog.json` 可版本管理。逐项核查记录也入版本管理（只保存公开论文摘录，不含密钥）。API 密钥、虚拟环境、数据库、原始全文、PDF、待审稿与运行日志忽略；Git 快照不是原件备份，完整迁移还需单独保存本地 `data` 和 `.env`。

正式公开论文页面可自动解析完整 PDF 与书目，支持 ACL Anthology、NeurIPS、ICLR proceedings 和 JMLR；原始下载仍限 15 MB。若官方全文获取失败，保留失败状态，不能用摘要页面冒充完整论文。ReAct 的 OpenReview 原件目前受 403 限制，仍展示预印本。手动补充这批基础参考可运行 `.venv/bin/python -m station.published_seed`，会保留版本并写本地备份。

界面下拉采用视口内弹层，支持键盘、Esc 和外点关闭；返回按钮跟随上一页面，直接进入详情时返回所属列表。同页保存不叠加返回步骤，设置保存恢复展开卡片与操作位置。

### 表达改写与知识更新

页面的“改写表达”使用已有 Wiki 和归档原文，默认一次 LLM 调用。勾选“额外润色（+1 次调用）”或 CLI 加 `--second-pass` 才追加一次表达编辑。JSON 格式错误可能重试一次，不把重试算成润色。断言、冲突、来源、分类、页面身份与关联记录由程序冻结，来源的采集和审批状态不变。新资料通过“编写 Wiki”生成知识更新，检查通过后发布。

在“编写记录”查看完成结果和系统异常；移除事实确认框、审核说明表单与四个审核问题。正常编写和改写自动发布；未解决的事实疑点由 Agent 核对处理，显式要求的额外润色未完成仍不能发布。需要修正事实的稿件应退回，进入知识更新，而不是借表达改写修改知识记录。发布批准不计作逐项事实认证，正文变化仍使旧核查记录失效，历史保留。

```bash
.venv/bin/python -m station.cli rewrite react-reasoning-acting-loop
.venv/bin/python -m station.cli drafts
.venv/bin/python -m station.cli prepare --draft-only  # 开发预览
.venv/bin/python -m station.cli reject <proposal-id>
```

所有新草稿使用 `p_` 开头的独立提案 ID；旧来源 ID 命名的草稿仍可审核。同一页面仅保留一个待审提案。批准/退回结果保存在 `data/proposal_history/`，发布前正文与元数据备份保存在 `data/publication_backups/`，均不提交 Git。普通发布写入失败会恢复正式文件；这不是断电恢复或完整版本管理系统。

### 单遍编写与资讯补充

官方资讯订阅取消七天限制，并新增 LangChain 官方博客 RSS。工程文章归为文章，产品发布归为资讯，均只在全文获取后参与编写；厂商预期、上线状态和未来计划分别表达。2026-10-11 已补入 Anthropic 的 Agent 构建与上下文工程文章、LangChain 规划智能体文章及 OpenAI AgentKit 发布资讯。参见 [本轮记录](docs/single-pass-writing.md)。

```bash
.venv/bin/python -m station.cli prepare --source-id <source-id> --target <existing-slug>
.venv/bin/python -m station.cli rewrite <slug> --second-pass
```

### 自动发布与跨来源知识综合

UI、CLI `prepare` / `rewrite` 及每日采集后的编写默认自动发布。`--draft-only` 仅用于开发预览；底层 `prepare()` 仍可独立生成提案供调试。系统保留完整知识对象保护、版本检查、逐字摘录验证、配对备份和写入失败恢复；异常保存在编写记录，不要求用户填写确认表。

同一概念的来源选择包含新资料，以及最相关候选条目背后的至多四份已发布归档原文，总输入共享同一预算。正文需要解释关联机制并分别引用原文。已发布比较段可以在另一方法的阅读页呈现反向联系，只有双方来源齐备的实际比较段参与，不因同一分类或裸链接生成关系。原始定义单源与跨方法综合多源分开对待，不把引用数量当作复现证明。

Agent 可通过 `compile_and_publish(target_slug=..., source_ids=[...], revisit=True)` 更新已发布知识；复用已批准来源不会预留或重置采集状态。`GET /api/pages/{slug}/claim-reviews` 提供只读证据与版本快照，普通阅读页展示记录，不展示人工复核表单。参见 [本轮说明](docs/autonomous-wiki.md)。
