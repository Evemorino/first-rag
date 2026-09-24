# Tasks: first-rag v0.1

**Input**: plan.md / spec.md / data-model.md / contracts/plugin-contract.md
**组织方式**: 按 plan 纵切里程碑分 Phase（宪法 VII / LG-002），任务标签 `[USx]` 映射 spec 用户故事
**★ 标注**: 默认用户手写任务（宪法 VII v2.0.0），AI 仅 review 答疑；T004/T012/T015/T017 已获用户 2026-09-20 逐任务明确授权由 AI 实现，其余可 AI 生成、用户抽查

## Phase 1: Setup（M0 脚手架）

- [x] T001 [P][US1] 项目脚手架：docker-compose.yml（仅 qdrant，卷 `./data/qdrant/`）、`.env.example`、`.gitignore`（**初版必含 `data/` `.env` `notes/`**，PRD §6 规则 2）、`pyproject.toml`、`Makefile` 骨架。验证：`make up` 后 Qdrant dashboard 可访问（AC-001 前半）——*代码就位，`make up` 待与 T005 一并真跑*
- [x] T002 [P][US1] `src/config.py`：环境变量加载（.env）、路径常量、时区 Asia/Shanghai。验证：单测读 fixture .env（FR-015、§6）——*单测过（.env fixture 读取 + 缺失报错）*
- [x] T003 [P][US1] `config/schema.json` 初版 + 加载校验：types / distill / retrieval / trae_type_map / raw_retention_days，结构按 data-model.md。验证：改坏结构时报错明确（FR-007/016/019/020）——*含 5 个负向单测（缺字段/空 types/坏 mode 等）*
- [x] T004 ★[US1] `src/ark_client.py`：`embed(texts)` / `chat(messages, json_mode)`，openai SDK 指向 Ark base_url（FR-011 密钥走环境）——*用户于 2026-09-20 明确授权 AI 实现（宪法 v2.0.0）；5 个 fake-client 单测覆盖环境配置、空输入、批量顺序、数量校验、JSON mode*
- [x] T005 [P][US1] `make embed-test`：真调验证候选嵌入模型 ID → 确认维度 → 写回 `.env` → 动态维度建集合脚本（AC-001；PRD §9 假设验证点）——*`scripts/embed_test.py` 就位（含 F1 维度冲突检测）；T004 已完成，真跑依赖 `.env` 与 Qdrant*
- [x] T006 [P][US1] `tests/` 骨架 + pytest 配置（unit/integration 分层，fixture 全用系统 tmp——宪法 V）——*conftest tmp_data_dir + test_config.py 7 用例*

**Checkpoint**: `make up` + `make embed-test` 全绿，基础设施就绪。

## Phase 2: Foundation（M1 前置）

- [x] T007 [US1] `src/plugins/__init__.py`：registry（扫描含 `PLUGIN` 的包注册；导入失败/目录缺失 → 静默跳过 + 日志）+ `SourceRef`/`RawMaterial` 类型定义（FR-001；F3 修复）
- [x] T008 [P][US1] `src/plugins/_template/`：插件模板（discover/parse 两函数 + 说明）（US-6；AC-011 插件面）
- [x] T009 [P][US1] 单测：registry 静默跳过行为（NFR-004；AC-006 前置）——*5 用例：正常注册/坏导入不炸/缺 PLUGIN 记日志/_template 被忽略*

**Checkpoint**: 插件契约可用，registry 行为有测试兜底。

## Phase 3: US1 每日自动沉淀（M1 采集直插 → M2 蒸馏 → M6 插件扩展）

**Independent Test**: 当日真实素材 `make sync` 后库中可查（AC-002）

- [x] T010 [US1] `src/plugins/claude_code/`：按 research.md 格式解析 `~/.claude/projects/**.jsonl`，提取消息/工具报错/struggle 轮次 meta（FR-002；源目录只读——宪法 V）——*6 用例全过（含 struggle 连续计数、跨日过滤）*——*v2 增强：~/.claude 存在但 projects/ 缺失时输出 layout drift 警告（本机实测触发）；summary 行忽略锁定为回归测试*
- [x] T011 [US1] `src/collect.py`：汇聚各插件 discover/parse + git（`config/repos.txt`，FR-003）+ 快记 → `data/raw/YYYY-MM-DD.json`（含蒸馏运行元信息，FR-006）；空日写空快照（§8）。验证：repos.txt 缺失/路径失效 → no-op + 日志（G3 补）——*7 用例全过*
- [x] T012 ★[US1] `src/ids.py`：uuid5(source|date|content_hash)，含 content_hash 定义（FR-014；NFR-003）——*用户于 2026-09-20 明确授权 AI 实现（宪法 v2.0.0）；5 个单测覆盖 sha256 前 16 位、UUIDv5 公式、确定性、身份变化、空输入拒绝*
- [x] T013 [US1] `src/ingest.py` 直插路径：embed → upsert（payload 按 data-model.md 全字段）（依赖 T004/T012）——*5 个 fake-client/fake-embed 单测覆盖 Entry 全字段、空输入 no-op、批量 embedding 顺序、确定性 ID、完整 payload、同批去重；真实 Qdrant 集成待 T014*
- [x] T014 [US1] integration 测试：同日重跑幂等——points 数不变（AC-003；FR-014）——*真实 Qdrant 测试通过：fake embedding 下同日重跑保持 2 个 points，ID 命中 UUIDv5 期望值；测试客户端以 trust_env=False 隔离宿主 SOCKS 代理*
- [x] T015 ★[US1] `src/distill_prompt.py`：**用户手写（本次授权 AI 实现）**——rubric → prompt 运行时拼装（类型枚举来自 config；include/exclude 信号、keep/drop 示例注入）（FR-007）——*用户于 2026-09-20 明确授权 AI 实现；6 个单测覆盖配置 rubric/type 注入、严格 JSON 契约、untrusted data 边界、素材溯源、system+user 顺序、空素材；好/坏例子见 example/*
- [x] T016 [US1] `src/distill.py` 编排：脱敏正则（FR-011）→ LLM → JSON 解析 → 未知类型重试一次后丢弃（FR-012）→ 熔断（FR-010）→ pre-summarized 快记/trae 直并入（FR-013）——*10 个核心单测覆盖直接路径、脱敏+快照、合法 JSON、非法 JSON 重试、未知类型重试/丢弃、熔断、空素材；好/坏例子见 example/*
- [x] T017 ★[US1] `src/similarity.py` 新颖度去重：**用户手写（本次授权 AI 实现）**——候选嵌入 → 检索已有 → 超阈值跳过/并入（FR-009）——*12 个 similarity 单测 + 2 个 ingest 接线测试覆盖 collection 缺失、阈值边界、同 ID 重跑、跳过语义重复；好/坏例子见 example/*
- [x] T018 [US1] 单测：脱敏（伪造密钥 fixture，AC-012）、未知类型（AC-013）、熔断（临时上限 3，AC-014）——*6 个 AC 专项用例（tests/unit/test_distill_ac.py）：AC-012 双路径（快记直并入 + LLM）8 类伪造密钥全链路不泄漏、AC-013 重试一次后丢弃其余保留/可纠正恢复、AC-014 恰好上限无熔断日志/超限截断留日志正常结束*
- [x] T019 [US1] `src/sync.py`：串联 collect→distill→ingest + `data/.sync.lock` 文件锁 + 到期 raw 清理（FR-022；F2 修复）+ `make sync [D=]` 入口（FR-025）——*18 个单测（tests/unit/test_sync.py）：串联顺序/默认今日、跨平台文件锁（fcntl/msvcrt）运行期互斥+崩溃后释放、retention 90/0/null 三态、scope.json 透传给 gather、CLI D=/裸日期/非法日期/失败非零码；`make sync` 入口就位*
- [x] T021 [P][US1] `src/plugins/codex/`：按 research.md 解析 rollout-*.jsonl（FR-002）——*7 个单测（tests/unit/test_codex.py）覆盖本地日判定（UTC 目录名≠归属日）、developer 角色丢弃、task_complete/turn_aborted 报错与 struggle 计数、AGENTS.md/<skill> 注入文本过滤；已用本机真实 ~/.codex 数据只读冒烟验证（8 会话/日，转写以真实用户发言开头）；**v2 增强（同日）**：工具级报错提取（function/custom_tool_call_output 的 str 与内容块列表两种形态，Exit code: N≠0 / execution error / Script failed，成功输出不进转写只用于挣扎归零）、struggle 语义修正（assistant 叙述不打断失败连击，仅工具成功归零）、agent_message 实测为子代理通信锁定跳过；真实数据复测：2026-08-21 单日提取 79 个错误（旧版仅任务级 8/14 全天 8 个）*
- [x] T022 [P][US1] `src/plugins/kimi_code/`：解析 wd_*/session_*/wire.jsonl（FR-002）——*7 个单测（tests/unit/test_kimi_code.py）覆盖三种 wire 事件形态（flat role/claude 块/typed）、state.json 缺失容错、报错与连续 struggle 计数、本地日判定；⚠️ 本机无 kimi-code 真实数据，wire 形态为容错假设，装了 kimi-code 的机器上需跑一次真实数据冒烟（schema_check）再信任解析*
  - **2026-09-24 更正：上面那句 ⚠️ 是错的，本机 `~/.kimi-code/sessions` 一直有 11 个真实会话。** 对真实数据探测的结论：`_classify` 对 39206 个事件认出 **0 条**，且 `time` 是 int 毫秒（原实现只接受 ISO 串）→ 归属日永不命中 → `discover` 每一天都返回 0 refs。FR-002 的 kimi 源自建库起就在静默空转，而 7 条单测全绿——它们测的是猜出来的形状。已按实测形态重写解析并换掉夹具（11 条，先红后绿），另加 `test_real_sessions_are_recognised` 当格式漂移闸口（无真实数据的机器 skip）。实测形态记在 research.md 的 kimi 行。
  - **连带更正 struggle 语义**：原来"人一开口即归零"的理由是"wire 里没有 tool 结果事件"，同样被证伪——真实数据有 3669 条 `tool.result`，其中 108 条带 `isError`。现在与 claude_code / codex 一致：只有工具真成功才打断失败连击。两规则在本机真实数据上的差别：达到 struggle_rounds≥3 的会话 4/11（旧）vs 1/11（新）。
- [x] T023 [P][US1] `src/plugins/trae/`：pre-summarized 轻转换 + `trae_type_map` 映射，未匹配默认 reflection（FR-002/013）——*7 个单测（tests/unit/test_trae.py）：每条记录一条素材（discover 按行返回 SourceRef）、text 仅由 learned/outcome 拼装、类型按 map 键序首匹配/未匹配落默认、project 编码不可无歧义还原宁缺毋错；已用本机真实 ~/.trae-cn 数据只读冒烟验证（4 条记录/日，中文内容正确提取）*
- [x] T024 [P][US1] 各插件 fixture 单测 + integration：四源真实数据混跑（AC-002 多源）——*单测见 test_codex/test_kimi_code/test_trae；integration（test_sync_four_sources.py）用内嵌 Qdrant（:memory:，同 upsert/query 代码路径）跑通六源（四插件+git+manual）端到端 sync：6 条 upsert、source 集合齐全、同日重跑幂等；**并抓出 T011 真实 bug：git log 未加 --date=iso-strict 导致 %ad 非 ISO 解析崩溃，已修**；真实服务器验证保留在 test_ingest_qdrant.py（Qdrant 不可达时 skip，make up 后自动启用）*
- [x] T025 [US1] integration：插件目录改名 → sync 成功 + 日志（AC-006）——*test_sync_four_sources.py::test_missing_source_dir_sync_still_succeeds：素材目录指到不存在路径 → sync 成功、claude_source 缺席、日志含 missing no-op、其余五源正常入库；插件包目录层面的导入失败跳过已由 test_registry.py 单测覆盖*

**Checkpoint**: US1 完整——每日沉淀端到端可用，幂等、脱敏、熔断全有测试。

## Phase 4: US2 带过滤的语义检索（M3 → M4 关联边）

**Independent Test**: `make ask Q="…" --type error --since 7d` 带引用回答（AC-004）

- [x] T026 [US2] `src/ask.py`：嵌入问题 → 向量检索 + payload 过滤（type/date/project，FR-018）→ 引用式回答（`[date] type: 摘要`，FR-019）——*9 个单测（tests/unit/test_ask.py）：相对/绝对日期解析（7d/ISO）、Qdrant Filter 构建（type/project/DatetimeRange）、top_k 走配置、引用上下文格式 `[YYYY-MM-DD] type:`、无命中给 make sync 引导且不调 LLM、CLI Q=/--type/--since/--no-expand、失败非零码*
- [x] T029 [US2] `ask.py` 扩展：三模式（off/all/阈值，`retrieval.expand.mode`）+ "关联补充"上下文标记 + `--no-expand`（FR-020/021）——*6 个单测（test_ask_expand.py）：all 模式去重+关联补充标记、off 空扩展、数值阈值按与问题的 cosine 过滤、neighbor_limit_per_hit/context_cap 约束、--no-expand、上下文含（关联补充）；取数用 qdrant retrieve by id（similarity.py 保持 ★ 未动，扩展失败降级不拖垮主回答）*
- [ ] T027 ★[US2] `src/similarity.py` 关联边构建：**用户手写**——top-5 中 >0.75 建边、单条上限 5、对方已满跳过回填、确定性（FR-017；AC-005 前置）
- [ ] T028 [US2] `ingest.py` 接入关联边构建 + 重跑不产重复边（AC-003 关联面）——*⏸ 阻塞于 T027（用户手写），边构建就位后接线*
- [x] T030 [US2] integration：过滤生效（AC-004）、扩展开关与标记（AC-005）；ask 单次耗时 ≤10 秒实测记录（plan Technical Context，G2 补）——*test_ask_filters.py（内嵌 Qdrant + fake embed/chat）：type+date 过滤仅命中窗口内 error、DatetimeRange 对 "YYYY-MM-DD" payload 实证有效、默认扩展带（关联补充）标记、--no-expand 后消失；**ask ≤10s 耗时实测待 .env + make up 后记录***
- [x] T031 [US2] 性能检查：典型日 sync ≤5 分钟实测记录（NFR-006 / SC-011）——*两次真跑（Qdrant 本地容器、Ark plan 通道）：**2026-09-18 = 188.06s**（8 条素材 / 234,021 字符 / 2 次 chat 往返 / 27 条入库）；**2026-09-23 = 291s**（26 条素材 / 1,332,282 字符 / 12 条入库）。均 ≤300s，但 09-23 那天已用到预算的 ~97%，素材再翻一倍就不是"慢一点"而是违约*
  - 实测过程中炸出来一个真缺陷并已修：单次 chat 的**输出**被服务端 completion 上限掐断（实测 `finish_reason='length'`、6894 字符 / 6865 tokens 处断），`DistillError: LLM returned invalid JSON`，而 `_parse_or_retry` 的重试是原样再发一遍 → 同一处再断一次，整天 0 条入库。修法是按 `distill.batch_max_chars`（新配置项，默认 120000）把一天切批蒸馏，见 `src/distill_batches.py`；决定成败的是输出长度不是输入长度（输入大 5.7 倍的 09-23 反而一次就过）。
- [ ] T020 [US1] **Dogfood ①**（M5，按 plan 顺序置于检索/关联边之后）：用本项目开发会话跑通全链路，记录首印象（LG-001）——*⏸ 待 .env 后真跑（建议 T027 关联边就位后一并做，LG-001 首印象更完整）*

## Phase 5: US3 手动快记（M8 入口，随 T019 可先行）

- [x] T032 [P][US3] `make log m="…"`：追加时间戳行到 `notes/inbox.md`（FR-004）；未标类型默认 reflection、轻处理不改类型（§8 v0.4）——*src/log.py：`- [ISO时间戳 #type] 文本` 行格式与 collect.NOTE_LINE 对齐，t= 可选标注；CLI 与 API 壳共用 log()*
- [x] T033 [P][US3] 单测：快记入库走轻路径、不进 LLM（FR-013）——*6 个用例（tests/unit/test_log.py）：时间戳+类型标注、无标注无 marker、行可被 collect 解析且默认 reflection、log→collect→distill 全链 chat 零调用、CLI m=/t= 解析*

## Phase 6: US4 范围选择（M8）

- [x] T034 [US4] `make scope`：交互式 工具×项目 矩阵（最近会话日期、估算条数）→ 写 `config/scope.json`；纯文本配置是唯一事实来源（FR-005）——*src/scope.py + 7 个单测：矩阵估算仅用插件契约（discover 近 7 天窗口，不触插件内部）、repos.txt 项目行、t/a/n/s/q 命令、保存 JSON*
- [x] T035 [US4] `collect.py` 尊重 scope.json 过滤。验证：取消勾选后 sync 跳过该工具（AC-007）——*sync 透传 scope.json（坏 JSON 降级不过滤）+ gather 过滤工具与项目；单测：取消勾选的插件 discover 零调用、其余正常入库；取消勾选的仓库不跑 git log*

## Phase 7: US5 校准与运维（M7）

- [x] T036 [US5] 重蒸馏对照：指定日重跑 → diff（共享 source_refs 且相似度 ≥0.85 配对=改写；仅新=新增；仅旧=消失，FR-023）→ 确认后整组替换（先删后插）——*src/redistill.py + make redistill D= [APPLY=1]；7 个单测：快照加载/缺失报错、diff 编排（对齐器注入）、先删后插（date filter delete）、无 --apply 只打印 diff、diff 与替换共用同一批新条目（蒸馏非确定不重跑）；**对齐规则本体是 ★ T037 接缝（similarity.align_redistill），未就位时明确指引***
- [ ] T037 ★[US5] 对齐规则实现在 `src/similarity.py`（T027 同文件，用户手写）+ diff 单测——*⏸ 留用户手写；T036 已留好接缝与注入式单测*
- [x] T038 [P][US5] 历史日期补跑验证：`make sync D=<过去日>`（AC-009）——*test_sync_four_sources 以固定过去日 2026-09-18 跑通六源全链（行为与当日一致：快照/蒸馏/入库/幂等）；真实数据补跑待 .env + make up 后记录*
- [x] T039 [US5] retention：`raw_retention_days=0` 时清理过期 raw、库内条目不动（AC-008）——*unit 三态（90/0/null）+ integration test_retention.py：过期快照清理、今日快照保留、库内 2 条目数不变；retention=0 边界=清理所有早于今日的快照*

## Phase 8: 入口完备 + 收尾（M8–M9）

- [x] T040 [US1] `src/api/app.py` FastAPI 薄壳：`/health`、`POST /log`、`POST /sync`(后台)+`/sync/status`、`GET /ask`；路由仅参数校验（FR-024；宪法 II）——*后台线程跑 sync.run（互斥靠 data/.sync.lock，运行中 409）；/health 报 Qdrant+四环境变量状态；/ask 失败转 502 附引导*
- [x] T041 [US1] API 冒烟：四端点 curl 通过（AC-001 后半）——*7 个 TestClient 用例（tests/unit/test_api.py）：health 组件状态、/log 空文本 422+核心调用、/sync 202+状态轮询+409 互斥、/ask 引用返回+缺 q 422+失败 502；真实 curl 冒烟待 make up 后*
- [x] T042 [P] AGENTS.md（命令、目录速查、指向 constitution/PRD 不复制）+ README（含隐私数据流向说明，PRD §9）（M9）——*两文件已建；README 含源目录只读/写入边界/脱敏/蒸馏数据流向（与方舟使用一致）/备份迁移四段隐私说明*
- [x] T043 [P][US6] 用 `_template` 写一个测试插件验证自动注册（US-6 / AC-011 插件面）——*tests/unit/test_plugin_extension.py：按契约在临时父包内建 plugdemo → iter_plugins 自动发现 → gather 采集到素材，核心零改动*
- [x] T044 [US1] 配置新增类型零迁移验证：schema.json 加类型 → sync 识别（AC-011）——*同一测试文件：新增类型 insight 在 LLM 路径与直并入路径均被接受，无代码迁移*
- [x] T045 [US1] 全量 AC 回归（14 条逐条过）+ **Dogfood ②**：ask 本项目开发史（LG-001；M9）——*AC 回归结论（2026-09-20，169 单测+集成全绿）：AC-002/003/004/006/007/008/009/011/012/013/014 ✅ 测试实证；AC-001/005/010 部分 ✅（/health 冒烟过、扩展机制过、重蒸馏编排过——dashboard 真访问/真实边数据/对齐规则待环境或 T027/T037）；**Dogfood ①②、sync≤5min 与 ask≤10s 实测、embed-test 真调、服务器版集成与 curl 冒烟：待 .env + make up 后补记***

## Dependencies & Execution Order

- **Phase 1 → 2 → 3 严格串行**（基础设施 → 契约 → 主链路）；T021/T022/T023 三个插件彼此 [P] 可并行
- **Phase 4 依赖 T013**（ingest 先在）；Phase 5/6 依赖 T019（sync 先在）
- ★ 任务（T015/T017/T027/T037）**排期按用户手写速度**，不受 AI 生成速度绑架（PRD §12.3）；每个 ★ 任务前用户先写预期行为（LG-003，记入 notes/）。T004/T012/T015/T017 为已获用户逐任务明确授权的例外，不得扩大到其他核心模块
- 同 phase 内 [P] 任务可并行；跨 phase 禁止（纵切完整性优先）

## Notes

- 每任务完成即更新本文件勾选状态；测试先写并确认失败再实现（TDD 仅适用于非 ★ 任务，★ 任务用户自定节奏）
- 提交时机由用户决定（宪法 Development Workflow）
