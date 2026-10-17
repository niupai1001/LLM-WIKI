> 2026-10-17最新状态：用户接受单篇提炼，但否定知识索引作为Wiki。独立归纳SKILL的失败归因、反思与记忆两组实验均未通过，未发布；生成主题已移出侧栏。27篇原解读保留，不重启旧任务或新增来源。详见[Wiki归纳实验](wiki-synthesis-experiment.md)。下文旧批次、页数及迁移步骤保留为历史背景，不代表当前授权续跑。

> 2026-10-16范围变更：用户修改了知识提炼SKILL，本轮仅针对已重写并发布的30篇生成独立`skill-preview`。原42篇任务不续跑，后来新增12篇任务已取消；正式库不切换。当前状态以[知识提炼说明](knowledge-distillation.md)和`artifacts/skill-preview/scope.json`为准。

# 分阶段编译、候选库与迁移操作

本轮将资料解析、抽取、概念对齐、编写、独立核查和发布拆成可恢复步骤。当前正式库为 `legacy`，候选库为 `candidate-v2`。候选库只在当前版本验收通过后允许手动切换；未配置语义接口和未完成真实人工评测时，系统明确阻断切换。

## 当前论文全量重写（2026-10-15）

用户认可修订后的ReAct和Reflexion提炼，已授权清空旧知识并全量重写。最新准入规则为：仅保留有真实PDF原件的论文，TXT原文、缺少PDF原件的记录及资讯均移除；资讯等待专门提取流程。两库已分别备份后重置，各保留42篇PDF论文，原74条中的32条已从当前库移除。重置清单见每库`paper-reset.json`；完整备份在本地`artifacts/paper-rebuild/backups/20261015T151839Z/`。

两篇认可成果已重新入库为`react-reasoning-and-acting`和`reflexion-learning-from-feedback`。候选库任务`j_2e8a7ccc4f9146cb812927f507fd3f54`绑定42篇来源及输入版本，复用这两篇成果并重写其余40篇；当前仍在执行，不能据此宣称全量完成。任务进度以`/tasks?library_id=candidate-v2`及持久任务状态为准，重试复用有效缓存，不重复入队旧迁移任务。

知识提炼v2.2已实际接入Python编译器：完整论文理解与结果提炼→独立请求核查→论文解读页及全局知识点→跨论文概念对齐与综合→概念覆盖/语义核查→发布及主题导航。入口为`station/distillation.py`、`station/paper_rebuild.py`和`station/knowledge_synthesis.py`。正文只解释目的与动机、核心回答、实际结果及知识点；概念去向、主题组织和编辑操作保存在配套记录。用户编写记录中的“证据与可信边界”模块已去除，内部证据定位、来源版本检查及独立核查继续执行，每个目标最多两轮修正。

来源书目现支持核实后的会议/期刊类别、带目录年份的等级及带指标年份的期刊影响因子；核对URL、时间和状态随指标保存。没有适用或可核实指标时留空，不由写作模型推断。当前研究补录依据与缺失说明在本地`artifacts/paper-rebuild/publication-enrichment.json`和相邻研究记录。

活动指针仍为`legacy`，本次批量执行在`candidate-v2`，不自动切换。写作保持官方DeepSeek V4.1 Flash（`deepseek-flash`），embedding保持硅基流动`Qwen/Qwen3-Embedding-8B`。Agent采集范围及4个订阅保留；`AUTO_DRAFT=false`控制采集后自动编写，与本轮用户授权的持久重写任务分开。内容流程见[知识提炼说明](knowledge-distillation.md)。

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

启用每库`paper-workflow.json`时，`compile`进入PDF论文重建流程：仅选择真实PDF论文，复用当前版本已发布的论文解读，再综合全库知识点。长论文分批读取并缓存，完整论文理解是最终提炼依据；缓存绑定来源、解析器、模型、提示、schema和实际技能内容。旧编译模式仍保留兼容入口，不能将其旧抽取缓存冒充v2.2提炼成果。结构与内容修正均有次数限制，仍有实质问题的来源或概念保留为受阻结果。

阅读阶段的段落ID格式错误可独立修正至多两次，每次保留原始回复和错误；不会增加正文的两轮语义修正预算。已完整读完、但综合上下文过长的论文，复用全部阅读记录选择关键论证的真实原文段落，逐项记录未用于编写的材料角色与原因，保留主要负结果。选择阶段也只允许两次结构纠错；未通过时不开始编写。历史上纯引用格式失败可只归一化本来源的真实段落标记，再单独核查原正文，不重新编写。

概念编写、事实核查与知识覆盖核查的请求共用去重原文：每个来源段落只提供一次，断言用定位指向它，完整证据仍保存在发布记录中。单个概念超出预算或失败不会跳过其他目标和最终索引。任务页可分别重试受阻论文与概念；未选择的未解决目标继续保留，不能因一次单目标成功就宣称整项完成。

兼容编译模式的知识问题覆盖存入`knowledge_coverage`；论文工作流使用版本绑定的`paper_distillations`、`knowledge_points`及`concept_memberships`。两者分开核查，不把知识点数量当成已枚举问题数量，来源的`approved`也不表示所有知识已覆盖。部分目标失败时，已发布内容保留；原任务重试复用有效阶段，失败提案不能通过旧approve接口绕过独立核查。

可以只重试任务结果中的某个受阻目标；其他失败目标保留原因和覆盖状态，尚有未解决目标时整项编译仍显示失败。显式指定已有页面`--target`会重新处理该页，其他问题必须列为有原因的暂缓；更新保留旧别名、问题记录及语义变更历史。

## 页面与 API

- `/tasks` 展示当前库的持久任务及结果，提供编译、索引、取消和重试操作。
- `/libraries` 提供独立候选库建立、来源同步、验收、对照和手动切换。
- `/ask` 是只读问答，回答保留可定位证据；不会创建或更新知识页。
- 原有页面与来源 URL 继续有效；`?library_id=candidate-v2` 明确选择库。网页内链接与同源请求携带库身份，任务创建时固定绑定身份。
- `POST /api/jobs` 接受 `{kind, payload, library_id?}`，立即返回包含 `id` 的 queued 任务；`GET /api/jobs/{id}` 查询状态。
- 原文重新解析可创建`{kind:"ocr",payload:{source_id:"...",force:true}}`任务，重试暂时失败的页面；抽取任务也支持显式`force`，通常保留缓存以复用已完成批次。
- `POST /api/jobs/{id}/cancel`、`/retry`控制任务；`GET /api/coverage`在论文模式返回`coverage_mode=paper_knowledge_points`、来源版本、知识点和概念链接，兼容模式仍返回知识问题处理记录。
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
| 每库 `distillations/` | 论文理解、知识点、版本化缓存及独立请求复核结果 |
| 每库 `knowledge_synthesis/` | 绑定输入摘要的跨论文概念分配、综合与核查缓存 |
| 每库 `jobs.db` | 队列、输入版本、15 分钟租约、30 秒心跳和任务结果 |
| 每库 `runs/<job-id>/` | 概念路由计划、已完成与受阻目标 |
| 每库 `search.db` | FTS5、别名、文本块和向量；可重建 |
| 每库 `state.db` | 来源、知识问题覆盖、发布日志和索引失效状态 |
| 每库 `redirects.json` | 已合并旧 slug 的导航映射 |

发布日志中的 prepared 记录先保存所有文件旧值与新值，随后替换文件；数据库业务更新和 committed 标记在同一事务提交。恢复时未提交操作回滚，已提交操作完成收尾。正式正文、元数据、主题导航和索引导航共同记录，读者需要一致快照时使用同库发布锁。

论文重建另在`state.db`维护`paper_distillations`、`knowledge_points`、`concept_memberships`及`knowledge_point_changes`。知识点以来源ID和局部ID形成全局身份，绑定来源版本；概念归属独立存储，知识不只藏在论文正文中。概念综合核对全部有效知识点的去向和正文表达，主题由已发布概念的`topic_routes`维护。

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

## 验证状态与续跑交接

早期V2基础设施曾通过312项回归，覆盖进程硬中断、发布恢复、库隔离、HTTP/CLI和验收门槛；这是本轮论文重建接入前的历史结果，不能代替新增提炼与综合流程的验证，也不代表内容验收通过。此前74份来源、旧页面及4,865/4,869块索引均属于重置前状态，不再作为当前库覆盖率或质量成绩。

当前任务已产生经过独立请求核查的新论文解读，仍在处理其余来源。全库概念/主题综合、最终向量索引及内容验收须以该任务的实际结果为准，尚未宣称完成。出现受阻来源或概念时先查看具体内容原因，再按有效版本缓存恢复；不要恢复仅绑定初始33份资料的旧任务`j_741100b4626742f0aca65666ad7da4fb`。

下一步是完成42篇论文的有效理解与知识点记录，核对跨论文概念归属、主题阅读关系、缺失和受阻项，再更新问答与候选召回评测。旧知识已按用户要求清除，其快照只作历史对照；旧评测绑定和来源清单需要按新范围检查，不能把已移除资讯的知识要求当成当前应编写的论文目标。人工成绩继续保持未评测，正式切换仍由用户执行。

历史服务故障诊断保留于本地`artifacts/v2-upgrade/`，当前服务是否可用以真实任务结果为准。原聊天跟进`llm-wiki`已不存在，没有重建；本轮授权通过持久任务执行。
# 最新归纳实验状态（2026-10-17）

用户要求用独立归纳 SKILL 试做失败归因、反思与记忆，不再把知识点字段清单当作 Wiki。生成主题已从侧栏移除。两组真实实验各初稿加两轮修正后均未通过，没有发布或刷新旧任务预算，亦未新增来源；详见 [Wiki 归纳实验](wiki-synthesis-experiment.md)。当前阻塞是归纳正文仍退化为论文复述，以及写作与逐字绑定耦合导致无效修正。511项回归通过仅证明工程检查，不证明归纳质量。
