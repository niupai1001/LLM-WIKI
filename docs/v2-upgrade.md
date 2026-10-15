# 分阶段编译、候选库与迁移操作

本轮将资料解析、抽取、概念对齐、编写、独立核查和发布拆成可恢复步骤。当前正式库为 `legacy`，候选库为 `candidate-v2`。候选库只在当前版本验收通过后允许手动切换；未配置语义接口和未完成真实人工评测时，系统明确阻断切换。

2026-10-15 按用户要求，两库仅保留“Agent 智能体”采集范围及其4个订阅；其余7个范围、3个非Agent订阅已移除。先将现有资料与页面的分类归入Agent并清除配置引用，再删除空范围。原配置在本地 `data/configuration-backups/agent-only-20261015T023355Z/` 留有备份，资料和元数据变更进入发布日志，原件与来源ID保留。分类调整不改变来源内容版本；解析器或选段身份变化仍按规则使相应抽取缓存失效。

人工维护的采集范围与自动知识主题分别显示在侧栏。模型从原文抽取概念、问题、断言、范围和证据，按机制对齐页面，通过 `topic_routes` 自动形成并更新知识主题；不把资料分类或论文标题直接当作知识主题。导入默认选择有效采集范围，删除“其他”后仍可正常导入；概念分配只允许使用启用的采集范围，配置改变使旧对齐计划失效。

## 启动与接口配置

```bash
make web
# 在另一终端运行；会处理正式库及候选库队列
make worker
```

也可固定 worker 到一个库：`.venv/bin/python -m station.worker candidate-v2`。Docker Compose 默认包含 Web 与 worker，采集容器仍由 `collect` profile 控制。Docker 安装 Poppler、Tesseract 及英文/简繁中文语言数据。本机需要 `pdftoppm` 和 `tesseract --list-langs` 列出的 `eng`、`chi_sim`、`chi_tra`；缺失时在解析结果中显示，并保留原件。

在本地 `.env` 设置独立的 `EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_API_KEY`；兼容接口使用 `/embeddings`。服务地址应包含所需 API 前缀，如 `/v1`。写作继续使用已有 `LLM_*` 配置。配置变化后重启进程。

也可使用某库的 `config/embedding.json` 设置 `base_url`、`model`、`api_key`，但该文件含秘密，不提交 Git。模型与服务地址共同构成向量后端身份，切换后重新生成向量。未配置时搜索仍可明确使用词法模式，不宣称语义索引已就绪。

2026-10-14 最初尝试复用知识提炼模型 `deepseek-flash`，其当前服务的 `/embeddings` 实测返回 HTTP 404。随后用户选用硅基流动并提供凭据，现已在正式库与候选库的本地 `config/embedding.json` 配置 `https://api.siliconflow.cn/v1` 和 `Qwen/Qwen3-Embedding-8B`，两条中英文短文本实测均返回有效的4096维向量。配置文件权限为仅当前用户读写，并由Git忽略；知识提炼的 `LLM_*` 配置保持原值。语义检索是否就绪以 `/api/capabilities` 的完整索引状态为准，不能只凭接口连通判定。

## 库、任务和编译命令

`--library-id` 是 CLI 全局参数，放在命令前；省略时解析注册信息中的活动库。

```bash
.venv/bin/python -m station.cli library init candidate-v2
.venv/bin/python -m station.cli --library-id candidate-v2 compile --source-id <source-id>
.venv/bin/python -m station.cli --library-id candidate-v2 jobs
.venv/bin/python -m station.cli --library-id candidate-v2 jobs --retry <job-id>
.venv/bin/python -m station.cli --library-id candidate-v2 jobs --retry <job-id> --target <blocked-slug>
.venv/bin/python -m station.cli --library-id candidate-v2 jobs --cancel <job-id>
.venv/bin/python -m station.cli --library-id candidate-v2 compile --source-id <source-id> --run
.venv/bin/python -m station.cli --library-id candidate-v2 compile --source-id <source-id> --target <existing-slug>
.venv/bin/python -m station.cli --library-id candidate-v2 index --embeddings
.venv/bin/python -m station.cli --library-id candidate-v2 search '规划与执行如何传递反馈？'
.venv/bin/python -m station.cli --library-id candidate-v2 query '规划与执行如何传递反馈？'
```

`compile` 未指定来源时选择该库归档资料；`--revisit` 可重新编译已覆盖问题。完整抽取按 18,000 字符批次执行并缓存成功批次；超过安全上限、缺页、缺少原文的部分保留明确原因。实际模型校验失败允许最多两次结构修正，编写提案允许最多两轮修正与独立复核。

每个来源的抽取结果可包含多个概念及知识问题。覆盖状态存入 `knowledge_coverage`，来源的 `approved` 不表示所有问题已经完成。部分目标失败时，已经发布的目标继续保留；原任务重试会跳过已覆盖目标，失败提案不能通过旧 approve 接口绕过独立核查。

可以只重试任务结果中的某个受阻目标；其他失败目标保留原因和覆盖状态，尚有未解决目标时整项编译仍显示失败。显式指定已有页面`--target`会重新处理该页，其他问题必须列为有原因的暂缓；更新保留旧别名、问题记录及语义变更历史。

## 页面与 API

- `/tasks` 展示当前库的持久任务及结果，提供编译、索引、取消和重试操作。
- `/libraries` 提供独立候选库建立、来源同步、验收、对照和手动切换。
- `/ask` 是只读问答，回答保留可定位证据；不会创建或更新知识页。
- 原有页面与来源 URL 继续有效；`?library_id=candidate-v2` 明确选择库。网页内链接与同源请求携带库身份，任务创建时固定绑定身份。
- `POST /api/jobs` 接受 `{kind, payload, library_id?}`，立即返回包含 `id` 的 queued 任务；`GET /api/jobs/{id}` 查询状态。
- 原文重新解析可创建`{kind:"ocr",payload:{source_id:"...",force:true}}`任务，重试暂时失败的页面；抽取任务也支持显式`force`，通常保留缓存以复用已完成批次。
- `POST /api/jobs/{id}/cancel`、`/retry` 控制任务；`GET /api/coverage` 读取知识问题处理记录。
- `/retry` 可接受`{target_slug}`，仅选择该任务已经记录的受阻目标；任务页提供对应按钮。
- `GET /api/search?q=...` 返回带能力状态的检索结果；`POST /api/query` 接受 `{question}`，返回回答、证据与能力提示。
- `GET /api/capabilities` 展示 embedding 和 OCR 就绪情况。

这些接口沿用本地同源边界，没有新增身份认证。归档网页和模型输出不会作为可执行代码进入页面。

## 存储和发布恢复

候选库位于 `data/libraries/candidate-v2/`，独立复制 raw、originals、来源索引和配置，保留来源 ID。旧正文、元数据和核查记录保存到该库 `baseline/`；新正式正文从原文重新编译。没有可写硬链接。

| 位置 | 内容 |
| --- | --- |
| `data/library-registry.json` | 活动库、库根目录与切换历史 |
| 每库 `parsed/` | 原文逐页/段落文本、实际页序、OCR 定位、质量及源版本 |
| 每库 `extractions/` | 版本化中间对象和成功批次缓存 |
| 每库 `jobs.db` | 队列、输入版本、15 分钟租约、30 秒心跳和任务结果 |
| 每库 `runs/<job-id>/` | 概念路由计划、已完成与受阻目标 |
| 每库 `search.db` | FTS5、别名、文本块和向量；可重建 |
| 每库 `state.db` | 来源、知识问题覆盖、发布日志和索引失效状态 |
| 每库 `redirects.json` | 已合并旧 slug 的导航映射 |

发布日志中的 prepared 记录先保存所有文件旧值与新值，随后替换文件；数据库业务更新和 committed 标记在同一事务提交。恢复时未提交操作回滚，已提交操作完成收尾。正式正文、元数据、主题导航和索引导航共同记录，读者需要一致快照时使用同库发布锁。

```bash
.venv/bin/python -m station.cli --library-id candidate-v2 recover
```

worker 执行前和 HTTP 访问前也进行恢复。模型与 OCR 调用不持有发布锁；发布前再次检查来源、解析结果、正文版本和任务租约。问答在模型返回后也重新核对引用来源版本。

来源同步也使用发布日志。PDF或其他原件单独变化时会同步，不依赖raw JSON同时更新；受影响来源恢复为待处理，旧解析与抽取按内容版本失效。候选库自有新增资料继续保留。

## 验收与切换

```bash
.venv/bin/python -m station.cli library sync candidate-v2
.venv/bin/python -m station.cli --library-id candidate-v2 compare
.venv/bin/python -m station.cli --library-id candidate-v2 evaluate
.venv/bin/python -m station.cli library activate candidate-v2
# 必要时回退，候选库和新增知识仍保留
.venv/bin/python -m station.cli library rollback legacy
```

固定评测集为 `tests/fixtures/v2-evaluation.json`：24 个抽取/更新案例和 12 个查询案例，绑定公开来源和旧知识问题。旧元数据预期用于查找回归，不能替代原文人工核对。

验收报告 `acceptance.json` 同时包含确定性检查、旧断言映射、问题覆盖、来源同步、语义索引、页面候选Recall@8、人工记录及问答证据定位。候选召回测24个知识案例的前8个页面；必须实际使用向量召回，才产生用于验收的`recall_at_8`。缺少配置或远程调用失败时该成绩为null，词法结果仅列为`diagnostic_recall_at_8`。12个问答案例包含多源问题和证据不足时拒答，不能用额外无依据结论替代拒答。

人工评测记录保存于每库 `human_evaluation.json`，问答输出记录保存于 `query_evaluation.json`。两者必须携带验收报告给出的五个绑定字段：`library_id`、`manifest_digest`、`legacy_manifest_digest`、`evaluation_input_digest` 和 `fixture_digest`。人工记录的`records`逐案例提供`case_id`、`reviewer_type="human"`、`by`、`rationale`、`target_slug`、`checked_source_ids`、`claim_ids`和固定评测集要求的`dimensions`布尔值。

人工文件还需独立的`claim_records`数组，逐条包含`target_slug`、`claim_id`、`reviewer_type="human"`、`by`、`rationale`、`checked_source_ids`和`support`。支持结论为`supported`、`partially_supported`、`contradicted`或`unverifiable`。核心页上的当前断言按页面与ID去重计数，缺失、重复或代理核查不计成人工记录；逐断言支持精度与案例通过率分别报告。概念误合并、范围或旧知识保留失败直接阻断。Agent记录不计入人工阈值，缺少标注时成绩为未评测。

切换要求：结构检查通过、语义索引就绪、问题覆盖或缺失原因明确、旧知识变化可追踪、来源同步、真实混合候选Recall@8至少90%、人工逐断言支持精度至少95%，并且案例维度和已保存问答输出通过检查。有原因的暂缓知识问题单列为明确缺失，无法核查的旧断言仍阻断切换。证据、评测记录、处理版本、向量内容或来源变化会使旧验收失效。运行或排队任务未结束时不允许切库。

切换与回退先恢复两库，再在`data/migration-backups/cutover-*/`保存独立文件和SQLite在线备份，失败时不修改指针。回退只允许到曾经启用的正式库，不能绕过验收启用新候选库；返回报告列出尚未同步的来源、正文和元数据，两库均保留。

该门槛是产品迁移条件，不会因程序回归全部通过而自动解除。当前工作产物和原件仍需独立备份；Git 不包含完整候选资料库。

## 当前实施验证

2026-10-15，本轮312项回归通过，包含实际进程硬中断、发布恢复、隔离库、真实HTTP与CLI调用、单目标重试和验收门槛。V2.5明确完整编写协议与旧表达改写协议的边界，并对短原文保留完整上下文、为无引文旧断言检索长原文段落；概念分配的结构错误最多修正两轮，仍失败则停止。检索候选不等同于支持证据，发布门槛保持不变。新增单一Agent范围下的导入、自动主题形成、受阻提案去重归档以及真实socket持续空白超时回归。旧回归使用独立分类夹具，不依赖用户的实时订阅配置。Python编译检查、JavaScript语法检查及Git差异空白检查通过。当前环境没有Docker，容器构建尚未实测。

初始33份来源及30页旧基线已独立保留，随后增量同步每日采集新增41份资料。两库目前各74份来源，其中68份标为全文、6份仅有摘要。候选库按解析器V2.3处理68份全文，66份完整、2份部分可读：除PDF字体映射问题外，复杂图形导致的解析内容跳过也会明确标记。CLI、网页和自动编译队列默认只选择归档全文；摘要的缺失原因进入任务结果，显式选择摘要会要求先补原文。正式候选正文目前为1个概念页及其主题入口；真实试点的其他目标仍受阻，尚未完成首个主题的质量验收，未批量重编全部来源。

全量任务`j_741100b4626742f0aca65666ad7da4fb`最初因缺少embedding配置失败。初始3,145个文本块已建立向量；当前增量索引任务`j_8cd0c645042e4e348e743268677caa4e`又完成1,720个新文本块，合计4,865个有效向量，无索引警告。字节一致的文本可复用同一库、同一文档的向量，位置或解析元数据仍刷新；后端模型变化继续要求重建。混合检索实际启用向量并命中LLMCompiler页面。此前问答smoke的3处引用在当时验证可定位，本轮解析器升级后记录需重新生成，不能继续当作当前问答验收。试点通过后才重试全量任务；旧任务仅绑定初始33份，之后还需处理新增全文。

当前验收为不通过，活动库仍为`legacy`。增量来源同步与4,865块语义索引检查通过，混合召回已实测但当前页面覆盖不足、分数未达门槛。待完成事项为试点受阻目标、其余资料重编及旧断言映射、召回达标、人工核对和12案例问答记录。人工验收尚未完成，程序回归通过或向量索引就绪均不会解除这些门槛。

写作服务当前阻塞：DeepSeek官方接口持续等待，最新试点在180秒响应期限结束；模型保持用户指定的V4.1 Flash（`deepseek-flash`）。`LLM_TIMEOUT_SECONDS`默认300秒，分段读取与运输层截止处理防止持续空白延长等待，并限制返回8MiB；建立连接阶段仍使用网络超时。相同任务、相同目标的旧受阻提案会归档，当前列表保留最新一份。

2026-10-15 进一步诊断确认[DeepSeek官方状态页](https://status.deepseek.com/)报告V4.1 Flash API服务异常，原因已识别、正在修复。新密钥已通过鉴权和模型可用列表验证，并更新本地写作配置；64-token的小请求在JSON与普通模式、代理与直连路径均迅速取得HTTP 200，但45秒内没有生成正文。保持现有模型和服务，不因这次上游事件替换模型或修改系统代理。诊断记录位于本地 `artifacts/v2-upgrade/deepseek-provider-incident.json` 及相关diagnostics文件，不包含密钥。

用户选择服务恢复后重试，已创建当前聊天的每30分钟恢复跟进（自动化ID `llm-wiki`），并根据健康记录时间避免提前重复请求：短请求验证服务，失败时保持安静；最近一次45秒验证仍超时。恢复后先重试试点，达标后才重试全量任务及增量全文并继续评测。全量任务已暂缓为可重试的取消状态，防止试点受阻时提前批量调用。12案例问答先前仅完成1条证据不足输出，保留于本地artifact；未作为已通过的问答验收。正式切换仍由用户执行。
