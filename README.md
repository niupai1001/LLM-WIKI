# LLM Wiki

自托管的知识库生成系统：把论文、工程博客与官方资讯的**原文**归档为证据，由大语言模型在原文约束下综合成带引用的 Markdown Wiki。

LLM Wiki 面向需要长期积累、且要求每条结论都能回溯到原始出处的技术研究场景。它把「原始资料」与「知识页面」分成两层：原始资料是证据，Wiki 页面是结论。系统只让取得完整全文的资料参与编写，不把订阅摘要当作论文原文，也不把单篇摘要直接发布成页面——每个断言都要求能定位到归档原文的对应位置。

## 核心特性

- **原文驱动的知识生成**：只有取得完整全文的资料才参与编写，超长原文按预算明确标记为节选；模型只能在原文约束下归纳，不能引入无出处的结论。
- **可溯源的引用体系**：正文中的来源以页内稳定编号与论文短名呈现（如 `[1 · Plan-and-Patch]`），页末列出完整标题、发布日期与原文链接，同一来源重复出现沿用同一编号。
- **逐项事实核查**：每条断言绑定适用范围、原文摘录与章节/页码，支持「原文支持 / 部分支持 / 与原文不符 / 无法核查」的逐项人工复核；正文变更会使旧核查结论失效，历史完整保留。
- **自动化采集与编写**：按主题订阅 RSS / Atom 源，用关键词组与时限筛选候选资料，可配置为采集完成后自动生成草稿并通过系统检查后发布。
- **知识图谱**：依据实际存在的页面链接、证据来源与主题关系构建，区分共同机制、差异对照与实现实例，不使用模型推断的实体关系。
- **可恢复的编译流程**：解析、抽取、概念对齐、编写、独立核查与发布拆分为可恢复步骤，中间结果按批次缓存；任务持久化保存，支持取消与按受阻目标重试。
- **检索与问答**：内置词法检索；配置向量服务后启用语义混合检索与只读问答，回答附带可定位证据，不创建或修改页面。
- **容器化部署**：提供 Docker Compose 编排，可自托管在任意支持 Docker 的主机上。

## 快速开始

### 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)：用于创建虚拟环境并同步锁定依赖
- 可选：Poppler（`pdftoppm`）与 Tesseract（需含 `eng`、`chi_sim`、`chi_tra` 语言数据），用于扫描件 OCR
- 可选：Docker 与 Docker Compose，用于容器化部署

缺少 Poppler 或 Tesseract 不会阻止启动：解析结果会明确记录缺失原因，原件仍然保留。

### 安装

```bash
git clone <repository-url>
cd <project-directory>
make setup
```

`make setup` 会从 `.env.example` 生成 `.env`（已有配置不会被覆盖），创建 `.venv` 并安装锁定依赖。

### 启动

```bash
# 终端 1：网页服务
make web

# 终端 2：后台任务 worker
make worker
```

浏览器访问 <http://127.0.0.1:8765>。服务随终端运行，`Ctrl+C` 停止。

- 只使用同步编写流程时，启动 `make web` 即可。
- 使用分阶段编译、检索索引或任务队列时，需同时运行 `make worker`；worker 可固定到某个知识库：`.venv/bin/python -m station.worker <library-id>`。
- 网页服务默认不随开机自启；定时采集需另行安装，见[定时采集](#定时采集)。

### 首次配置

编辑项目根目录的 `.env`，至少填写模型服务密钥：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<your-api-key>
LLM_MODEL=deepseek-flash
```

配置在进程启动时读取，修改后需重启服务。

## 配置

### 模型服务

所有模型相关配置集中在 `.env`（由 `make setup` 从 `.env.example` 生成）。`.env` 在模块加载时读取，显式进程环境变量优先。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 兼容 `/chat/completions` 的服务地址；留空则不做 AI 处理 |
| `LLM_API_KEY` | 空 | 模型服务密钥，仅保存在 `.env`，不进入版本库 |
| `LLM_MODEL` | `deepseek-flash` | 模型 ID |
| `LLM_JSON_MODE` | `true` | 请求 `response_format=json_object`；服务不支持时设为 `false` |
| `LLM_TIMEOUT_SECONDS` | `300` | 单次请求截止时间，含服务端排队时的等待 |
| `LLM_CONTEXT_CHARS` | `160000` | 原文证据字符预算，超预算的原文会明确标记为节选 |
| `AUTO_DRAFT` | `false` | 为 `true` 时采集完成后自动编写并尝试发布 |
| `MAX_PER_FEED` | `12` | 单个订阅源每轮新增资料上限（1–100） |
| `TZ` | `Asia/Shanghai` | 进程时区，影响调度时刻与容器行为 |
| `STATION_DATA` | `data` | 运行数据根目录，需在进程启动前设置 |

任意兼容服务均可替换默认值，但需确认支持 `/chat/completions` 与 `response_format=json_object`。启用编写后，选中的原文（或节选）、书目信息与候选页面内容会发送到所配置的模型服务，**数据并非始终只在本地处理**。

### 主题与订阅

主题是资料的分类与编写方向，订阅源指向 RSS / Atom 地址。两者可在网页「主题与订阅」页管理，也可直接编辑配置文件：`config/categories.json`（主题）与 `config/feeds.json`（订阅源）。

订阅筛选支持三组关键词：`include_keywords`（包含任一）与 `require_keywords`（还需匹配任一）分别至少命中一个，`exclude_keywords` 命中即跳过，空组表示不限制；`max_age_days` 留空表示不限发布时间。资料类型须为 `paper`、`article`、`news`、`note` 或 `document`。

配置保存后无需重启网页服务，下次采集即生效。停用主题会暂停关联订阅并排除后续导入与编写，历史资料与知识仍可浏览；恢复主题不会自动重新启用订阅。被资料、知识页或订阅引用的主题不能直接删除。

### 语义检索

配置独立的向量服务后启用语义检索；未配置时系统以词法模式工作，并明确提示语义索引未就绪。

```dotenv
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_API_KEY=
```

`EMBEDDING_BASE_URL` 需包含 API 前缀（例如 `https://api.example.com/v1`），接口兼容 `/embeddings`。也可改用某个知识库的 `config/embedding.json` 配置 `base_url`、`model`、`api_key`；该文件含密钥，已被 `.gitignore` 忽略，请勿提交。模型与服务地址共同构成向量后端身份，切换后必须重建索引。

## 使用

### 网页界面

| 路径 | 用途 |
| --- | --- |
| `/` | 知识首页：页面列表、主题入口与编写入口 |
| `/page/{slug}` | 页面阅读：正文、证据引用、相关页面与逐项事实核查 |
| `/sources` | 原始资料列表与导入 |
| `/source/{sid}` | 资料详情：原件阅读、书目信息、获取状态 |
| `/drafts` | 编写记录：待审提案、与证据对照、发布结果 |
| `/graph` | 知识图谱 |
| `/collection` | 采集观测：任务状态、筛选规则与最近运行结果 |
| `/settings` | 主题与订阅管理 |
| `/tasks` | 任务队列：编译、索引、取消与重试 |
| `/ask` | 只读问答 |
| `/libraries` | 知识库：建立、来源同步、验收与切换 |

上传限制：单份文件最大 15 MB（请求体上限 22 MB）。PDF 资料页直接展示原件并保留公式、表格与图片，阅读器使用本地 PDF.js 资源，无需 CDN。

### 命令行

在项目根目录运行（`make` 目标内部使用 `.venv/bin/python`）：

| 命令 | 说明 |
| --- | --- |
| `make ingest` | 抓取所有已启用订阅源的新资料 |
| `make collector` | 常驻轮询，默认每 30 分钟一轮；`make collector MINUTES=1440` 约 24 小时一轮 |
| `make stats` | 按阶段统计资料数量 |
| `make drafts` | 列出待审提案 |
| `make prepare LIMIT=3 CATEGORY=<topic-id>` | 按主题组合多份原文生成草稿并发布 |
| `make approve ID=<proposal-id>` | 批准指定提案 |
| `make rewrite SLUG=<slug>` | 为已有页面生成仅改写表达的草稿 |
| `make test` | 运行测试套件 |

更细粒度的入口：

```bash
# 导入资料
.venv/bin/python -m station.cli import-url https://arxiv.org/abs/1706.03762 --category <topic-id>
.venv/bin/python -m station.cli import-file --title "资料标题" --file ./paper.pdf --category <topic-id>
.venv/bin/python -m station.cli add-note --title "研究设想" --file ./idea.md --category <topic-id>

# 编写与审核
.venv/bin/python -m station.cli prepare --draft-only          # 仅生成提案，不发布
.venv/bin/python -m station.cli prepare --source-id <source-id> --target <existing-slug>
.venv/bin/python -m station.cli rewrite <slug> --second-pass  # 追加一次仅表达层的润色
.venv/bin/python -m station.cli drafts
.venv/bin/python -m station.cli reject <proposal-id>

# 编译任务、检索与问答
.venv/bin/python -m station.cli compile --source-id <source-id> [--run]
.venv/bin/python -m station.cli jobs [--retry <job-id> [--target <slug>]] [--cancel <job-id>]
.venv/bin/python -m station.cli index --embeddings
.venv/bin/python -m station.cli search '规划与执行如何传递反馈？'
.venv/bin/python -m station.cli query '规划与执行如何传递反馈？'
```

`--category` 需使用实际存在的主题标识，取值见 `config/categories.json` 或「主题与订阅」页。`LIMIT` 是主题草稿批次数的上限，不是资料条数或模型调用次数。`--library-id` 是全局参数，须放在子命令之前；省略时使用注册信息中的活动知识库。

> `prepare` 与 `rewrite` 默认**自动发布**，名称中的 "prepare" 不表示只做预览。需要只生成提案时加 `--draft-only`。

### 知识编写流程

1. 在「原始资料」页导入公开链接，或上传 PDF / Markdown / UTF-8 文本，并选择主题与资料类型（个人笔记与论文分开标注）。
2. 系统尝试获取并归档完整原文；获取失败的远程资料可点击「重新获取原文」，扫描版 PDF 保留原件但需要 OCR。
3. 只有取得完整全文的资料参与编写。在首页按主题组合待编写原文，或对单份资料使用「生成 Wiki 草稿」（仅使用该份原文）。
4. 在「编写记录」页对照原始证据审阅提案，通过系统检查后自动发布，也可退回重写。更新草稿会记录页面版本，原页面改动后必须重新生成，避免覆盖后续知识。
5. 发布后的页面保留证据引用、相关页面与反向链接；「知识图谱」按实际链接与来源关系展示。

**表达改写**只改善已有页面的行文，断言、来源、分类、页面身份与关联记录由程序冻结，来源的采集与审批状态不变。需要修正事实的稿件应退回后进入知识更新，而不是借表达改写修改知识记录。发布批准不计作逐项事实认证。

### 知识库与验收切换

系统支持在同一实例内维护多个相互隔离的知识库（如正式库与候选库），用于在不影响正式内容的前提下试点新流程：

```bash
.venv/bin/python -m station.cli library init <library-id>
.venv/bin/python -m station.cli library sync <library-id>
.venv/bin/python -m station.cli --library-id <library-id> compare    # 与正式库对照
.venv/bin/python -m station.cli --library-id <library-id> evaluate   # 生成验收报告
.venv/bin/python -m station.cli library activate <library-id>        # 验收达标后手动切换
.venv/bin/python -m station.cli library rollback <library-id>        # 必要时回退
```

切换是手动操作，只有在结构检查、语义索引、问题覆盖、来源同步、召回率与人工逐断言支持精度等门槛全部满足时才应执行；运行或排队中的任务未结束时不允许切换。切换与回退会先做独立备份，失败时不修改指针，两个知识库的内容均保留。评测集与完整门槛见 [分阶段编译与迁移说明](docs/v2-upgrade.md)。

## 部署

### Docker Compose

```bash
cp .env.example .env    # 仅首次创建；已有配置不要覆盖
# 编辑 .env 填入模型服务密钥
docker compose up -d --build                      # 默认启动 web 与 worker
docker compose --profile collect up -d --build    # 额外启用 collector（每 30 分钟采集）
```

容器内网页监听 `0.0.0.0`，宿主端口映射限制为 `127.0.0.1:8765`。`data/` 与 `config/` 以可写方式挂载，供网页保存配置并与采集进程共享锁。镜像基于 `python:3.12-slim`，预装 Poppler 与 Tesseract（`eng`、`chi_sim`、`chi_tra`）。

应用所有运行数据（资料、页面、索引与日志）都在 `data/` 目录下，迁移或备份时需完整复制该目录，并同时保留 `.env` 与 `config/`。

### 定时采集

三种方式互不等价，按需选择：

| 方式 | 行为 |
| --- | --- |
| macOS LaunchAgent | 每天系统时区 08:00 触发一次；需开机并保持用户登录 |
| Docker collector | 以 `collect` profile 启动容器，每 30 分钟循环采集 |
| 手动 | `make ingest` 单次抓取，或 `make collector` 常驻轮询 |

macOS 定时任务：

```bash
.venv/bin/python -m station.scheduler install   # 安装，不会立即抓取
.venv/bin/python -m station.scheduler status    # 查看安装与最近运行状态
```

launchd 使用系统时区决定日历时刻，安装时会校验时区偏移，与 `Asia/Shanghai` 不一致会拒绝安装；进程环境中的 `TZ` 不改变 launchd 的日历时刻。休眠期间无法准时触发，关机错过的任务不保证补跑。该机制仅适用于 macOS，其他系统请使用 Docker collector 或自行配置系统调度。

## 测试

```bash
make test
```

测试覆盖离线去重、分类、全文状态、原件处理与下载限制，以及基于临时数据目录的完整 HTTP 工作流：上传原文、同主题多来源编写、网页审批、证据图谱、扫描 PDF 提示、虚假引用拒绝、长文预算与并发修改保护；另含编译、任务队列、检索、问答、知识库与验收门槛的测试。模型调用使用模拟回复，**真实 API 连通性需另行验证**。测试需在项目根目录运行。

## 已知限制

- 应用默认只监听本地地址，没有账号认证，请勿直接暴露到公网。
- 模型使用提取出的文字，未做 PDF 图片的视觉解读，图表内容不会被理解。
- 知识图谱依据已存在的页面链接与来源关系构建，模型推断不构成已证实的实体关系。
- 引用数量、来源数量与发布批准都不表示知识已被独立证实；事实核查只检查对归档原文的忠实性，不证明实验有效或可复现。
- 自动归并依赖模型判断，不保证覆盖所有相关页面。

## 文档

| 文档 | 内容 |
| --- | --- |
| [项目说明与架构设计](docs/project-architecture.md) | 项目定位、模块职责、实体关系、数据流、接口与部署边界 |
| [分阶段编译与迁移说明](docs/v2-upgrade.md) | 分阶段编译、知识库隔离、任务队列、语义检索与验收门槛 |
| [自主 Wiki 更新](docs/autonomous-wiki.md) | 自动发布与跨来源知识综合 |
| [知识联系改造记录](docs/knowledge-connections.md) | 知识关系与图谱构建 |
| [主题导航](docs/topic-navigation.md) | 主题入口与概念页的同步维护 |
| [单遍编写与资讯补充](docs/single-pass-writing.md) | 单遍编写流程与资讯类资料的表达约定 |
| [领域 Agent 资料导入](docs/domain-agent-import.md) | 领域资料的导入与整理 |
| [修改计划核对与校正版实施路线](docs/modification-plan-review.md) | 对既有质量改进方案的评估与校正 |
| [Wiki 编写规范](config/wiki-writing.md) | 运行时写作约束（由程序读取） |
| [来源质量规范](config/source-quality.md) | 来源策展与质量维护约定 |

## 贡献

欢迎通过 Issue 反馈缺陷与改进建议，或提交 Pull Request。参与开发前请先运行 `make test` 确认本地测试通过；提交前请保持 `config/` 中的示例配置可用，并避免提交 `.env`、密钥、原始全文与运行数据。

## 许可证

仓库当前未包含许可证文件。如需在组织内或对外分发使用，请先补充明确的授权条款。
