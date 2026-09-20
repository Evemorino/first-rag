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

- [x] T010 [US1] `src/plugins/claude_code/`：按 research.md 格式解析 `~/.claude/projects/**.jsonl`，提取消息/工具报错/struggle 轮次 meta（FR-002；源目录只读——宪法 V）——*6 用例全过（含 struggle 连续计数、跨日过滤）*
- [x] T011 [US1] `src/collect.py`：汇聚各插件 discover/parse + git（`config/repos.txt`，FR-003）+ 快记 → `data/raw/YYYY-MM-DD.json`（含蒸馏运行元信息，FR-006）；空日写空快照（§8）。验证：repos.txt 缺失/路径失效 → no-op + 日志（G3 补）——*7 用例全过*
- [x] T012 ★[US1] `src/ids.py`：uuid5(source|date|content_hash)，含 content_hash 定义（FR-014；NFR-003）——*用户于 2026-09-20 明确授权 AI 实现（宪法 v2.0.0）；5 个单测覆盖 sha256 前 16 位、UUIDv5 公式、确定性、身份变化、空输入拒绝*
- [x] T013 [US1] `src/ingest.py` 直插路径：embed → upsert（payload 按 data-model.md 全字段）（依赖 T004/T012）——*5 个 fake-client/fake-embed 单测覆盖 Entry 全字段、空输入 no-op、批量 embedding 顺序、确定性 ID、完整 payload、同批去重；真实 Qdrant 集成待 T014*
- [x] T014 [US1] integration 测试：同日重跑幂等——points 数不变（AC-003；FR-014）——*真实 Qdrant 测试通过：fake embedding 下同日重跑保持 2 个 points，ID 命中 UUIDv5 期望值；测试客户端以 trust_env=False 隔离宿主 SOCKS 代理*
- [x] T015 ★[US1] `src/distill_prompt.py`：**用户手写（本次授权 AI 实现）**——rubric → prompt 运行时拼装（类型枚举来自 config；include/exclude 信号、keep/drop 示例注入）（FR-007）——*用户于 2026-09-20 明确授权 AI 实现；6 个单测覆盖配置 rubric/type 注入、严格 JSON 契约、untrusted data 边界、素材溯源、system+user 顺序、空素材；好/坏例子见 example/*
- [x] T016 [US1] `src/distill.py` 编排：脱敏正则（FR-011）→ LLM → JSON 解析 → 未知类型重试一次后丢弃（FR-012）→ 熔断（FR-010）→ pre-summarized 快记/trae 直并入（FR-013）——*10 个核心单测覆盖直接路径、脱敏+快照、合法 JSON、非法 JSON 重试、未知类型重试/丢弃、熔断、空素材；好/坏例子见 example/*
- [x] T017 ★[US1] `src/similarity.py` 新颖度去重：**用户手写（本次授权 AI 实现）**——候选嵌入 → 检索已有 → 超阈值跳过/并入（FR-009）——*12 个 similarity 单测 + 2 个 ingest 接线测试覆盖 collection 缺失、阈值边界、同 ID 重跑、跳过语义重复；好/坏例子见 example/*
- [ ] T018 [US1] 单测：脱敏（伪造密钥 fixture，AC-012）、未知类型（AC-013）、熔断（临时上限 3，AC-014）
- [ ] T019 [US1] `src/sync.py`：串联 collect→distill→ingest + `data/.sync.lock` 文件锁 + 到期 raw 清理（FR-022；F2 修复）+ `make sync [D=]` 入口（FR-025）
- [ ] T021 [P][US1] `src/plugins/codex/`：按 research.md 解析 rollout-*.jsonl（FR-002）
- [ ] T022 [P][US1] `src/plugins/kimi_code/`：解析 wd_*/session_*/wire.jsonl（FR-002）
- [ ] T023 [P][US1] `src/plugins/trae/`：pre-summarized 轻转换 + `trae_type_map` 映射，未匹配默认 reflection（FR-002/013）
- [ ] T024 [P][US1] 各插件 fixture 单测 + integration：四源真实数据混跑（AC-002 多源）
- [ ] T025 [US1] integration：插件目录改名 → sync 成功 + 日志（AC-006）

**Checkpoint**: US1 完整——每日沉淀端到端可用，幂等、脱敏、熔断全有测试。

## Phase 4: US2 带过滤的语义检索（M3 → M4 关联边）

**Independent Test**: `make ask Q="…" --type error --since 7d` 带引用回答（AC-004）

- [ ] T026 [US2] `src/ask.py`：嵌入问题 → 向量检索 + payload 过滤（type/date/project，FR-018）→ 引用式回答（`[date] type: 摘要`，FR-019）
- [ ] T027 ★[US2] `src/similarity.py` 关联边构建：**用户手写**——top-5 中 >0.75 建边、单条上限 5、对方已满跳过回填、确定性（FR-017；AC-005 前置）
- [ ] T028 [US2] `ingest.py` 接入关联边构建 + 重跑不产重复边（AC-003 关联面）
- [ ] T029 [US2] `ask.py` 扩展：三模式（off/all/阈值，`retrieval.expand.mode`）+ "关联补充"上下文标记 + `--no-expand`（FR-020/021）
- [ ] T030 [US2] integration：过滤生效（AC-004）、扩展开关与标记（AC-005）；ask 单次耗时 ≤10 秒实测记录（plan Technical Context，G2 补）
- [ ] T031 [US2] 性能检查：典型日 sync ≤5 分钟实测记录（NFR-006 / SC-011）
- [ ] T020 [US1] **Dogfood ①**（M5，按 plan 顺序置于检索/关联边之后）：用本项目开发会话跑通全链路，记录首印象（LG-001）

## Phase 5: US3 手动快记（M8 入口，随 T019 可先行）

- [ ] T032 [P][US3] `make log m="…"`：追加时间戳行到 `notes/inbox.md`（FR-004）；未标类型默认 reflection、轻处理不改类型（§8 v0.4）
- [ ] T033 [P][US3] 单测：快记入库走轻路径、不进 LLM（FR-013）

## Phase 6: US4 范围选择（M8）

- [ ] T034 [US4] `make scope`：交互式 工具×项目 矩阵（最近会话日期、估算条数）→ 写 `config/scope.json`；纯文本配置是唯一事实来源（FR-005）
- [ ] T035 [US4] `collect.py` 尊重 scope.json 过滤。验证：取消勾选后 sync 跳过该工具（AC-007）

## Phase 7: US5 校准与运维（M7）

- [ ] T036 [US5] 重蒸馏对照：指定日重跑 → diff（共享 source_refs 且相似度 ≥0.85 配对=改写；仅新=新增；仅旧=消失，FR-023）→ 确认后整组替换（先删后插）
- [ ] T037 [US5] ★对齐规则实现在 `src/similarity.py`（T027 同文件，用户手写）+ diff 单测
- [ ] T038 [P][US5] 历史日期补跑验证：`make sync D=<过去日>`（AC-009）
- [ ] T039 [US5] retention：`raw_retention_days=0` 时清理过期 raw、库内条目不动（AC-008）

## Phase 8: 入口完备 + 收尾（M8–M9）

- [ ] T040 [US1] `src/api/app.py` FastAPI 薄壳：`/health`、`POST /log`、`POST /sync`(后台)+`/sync/status`、`GET /ask`；路由仅参数校验（FR-024；宪法 II）
- [ ] T041 [US1] API 冒烟：四端点 curl 通过（AC-001 后半）
- [ ] T042 [P] AGENTS.md（命令、目录速查、指向 constitution/PRD 不复制）+ README（含隐私数据流向说明，PRD §9）（M9）
- [ ] T043 [P][US6] 用 `_template` 写一个测试插件验证自动注册（US-6 / AC-011 插件面）
- [ ] T044 [US1] 配置新增类型零迁移验证：schema.json 加类型 → sync 识别（AC-011）
- [ ] T045 [US1] 全量 AC 回归（14 条逐条过）+ **Dogfood ②**：ask 本项目开发史（LG-001；M9）

## Dependencies & Execution Order

- **Phase 1 → 2 → 3 严格串行**（基础设施 → 契约 → 主链路）；T021/T022/T023 三个插件彼此 [P] 可并行
- **Phase 4 依赖 T013**（ingest 先在）；Phase 5/6 依赖 T019（sync 先在）
- ★ 任务（T015/T017/T027/T037）**排期按用户手写速度**，不受 AI 生成速度绑架（PRD §12.3）；每个 ★ 任务前用户先写预期行为（LG-003，记入 notes/）。T004/T012/T015/T017 为已获用户逐任务明确授权的例外，不得扩大到其他核心模块
- 同 phase 内 [P] 任务可并行；跨 phase 禁止（纵切完整性优先）

## Notes

- 每任务完成即更新本文件勾选状态；测试先写并确认失败再实现（TDD 仅适用于非 ★ 任务，★ 任务用户自定节奏）
- 提交时机由用户决定（宪法 Development Workflow）
