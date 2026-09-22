# AGENTS.md

本文件是给 AI 助手（和未来的你）的速查卡。原则与需求的完整定义不在这里——
见 [.specify/memory/constitution.md](.specify/memory/constitution.md)（宪法）
与 [PRD.md](PRD.md)（唯一需求事实来源）；两者冲突时：原则问题以宪法为准，
范围与验收以 PRD 为准。

## 常用命令

Makefile 的每条命令都通过 `uv run` 走项目内 `.venv`，无需手动激活。
**Windows 机器上没有 make 时**，直接用等价命令（效果相同）：

```sh
uv run pytest                                # = make test
uv run python -m src.sync [D=2026-09-18]     # = make sync
uv run python -m src.log m="想法" [t=idea]   # = make log
uv run python -m src.ask Q="…" --type error --since 7d   # = make ask
uv run python -m src.scope                   # = make scope
uv run python -m src.redistill D=2026-09-18 [--apply]    # = make redistill
uv run python scripts/embed_test.py          # = make embed-test
uv run uvicorn src.api.app:app --port 8300   # = make serve
```

make 可用时的入口：

```sh
make up            # 启动 Qdrant（唯一常驻容器）
make embed-test    # 首次：真调 Ark 验证嵌入模型与维度，建集合
make sync          # 当日采集→蒸馏→入库（幂等，可重复跑）
make sync D=2026-09-18   # 补跑历史日期（AC-009）
make log m="想法" t=idea  # 手动快记 → notes/inbox.md
make ask Q="最近学了什么" --type error --since 7d   # 检索问答
make scope         # 交互式选择采集范围（写 config/scope.json）
make redistill D=2026-09-18        # 重蒸馏对照（只看 diff）
make redistill D=2026-09-18 APPLY=1  # 确认后整组替换
make serve         # FastAPI 薄壳（:8300，health/log/sync/ask）
make test          # pytest（unit + integration）
make cov           # 行覆盖率 → coverage.xml
make crap          # CRAP 指标（复杂度 × 未覆盖度），≥30 视为 crappy
make layers        # 分层依赖：src/ 的 import 方向（--list 看规则）
make size          # 规模：src/ 单文件 ≤300 SLOC、单函数 ≤80 行
make mutation-selfcheck  # 已知必死改动的自检：抓不住就别信变异分数
make mutation      # 变异测试（mutmut），默认只打核心链路（内部先跑自检）
make hooks         # 装 pre-commit：每次 commit 自动跑 12 个钩子
```

## 目录速查

```text
src/
  config.py        # 环境变量/路径/时区/schema.json 校验
  ark_client.py    # Ark OpenAI 兼容薄客户端（embed/chat）★
  ids.py           # uuid5 幂等条目 ID ★
  similarity.py    # 检索/新颖度去重（T027 关联边/T037 对齐规则 ★ 用户手写）
  distill_prompt.py# 蒸馏 prompt 运行时拼装 ★
  collect.py       # 汇聚插件 + git + 快记 → data/raw/YYYY-MM-DD.json
  distill.py       # 蒸馏编排：脱敏→LLM→校验→熔断
  sanitize.py      # 脱敏：进 LLM 前抹掉密钥/令牌（宪法 V 的边界）
  distill_candidates.py  # 候选条目校验：类型/标签/来源引用 → 可用与待重试两堆
  distill_messages.py    # 追问 LLM 的话术（非法 JSON、未知类型各一次）
  ingest.py        # 嵌入→新颖度去重→Qdrant upsert（幂等）
  ask.py           # 过滤检索 + 引用式回答 + 关联扩展
  sync.py          # 串联主链路 + .sync.lock + retention 清理
  redistill.py     # 重蒸馏对照编排（diff 先行，确认后替换）
  log.py / scope.py# 快记与范围选择入口
  plugins/         # 采集插件：claude_code / codex / kimi_code / trae / _template
  api/app.py       # FastAPI 薄壳（路由只做校验与调用）
scripts/crap.py    # CRAP 计算器：radon 复杂度 × coverage 覆盖率
scripts/lint_layers.py   # 分层依赖门禁：AST 查 src/ 的 import 方向
scripts/size_guard.py    # 规模门禁：src/ 文件 SLOC 与函数行数上限
scripts/mutation_selfcheck.py  # 变异自检 canary（改坏源码看测试红不红）
tests/mutmut_compat.py  # mutmut 3.x 对 `src.` 包名的兼容补丁（见文件头）
config/            # schema.json（类型/rubric/检索/trae 映射/保留期）、repos.txt、scope.json
data/              # qdrant/ 与 raw/（gitignore；备份=复制本目录）
notes/             # inbox.md 手动快记（gitignore）
tests/unit|integration/  # 单测 / 需 Qdrant 或内嵌向量库的集成测试
specs/001-learning-memory-rag/  # spec/plan/data-model/contracts/tasks
```

## 硬约束（违者即错，出处见宪法）

- **写入边界（NON-NEGOTIABLE）**：运行时只写 `data/`、`notes/`；临时产物用系统 tmp；
  产品源目录（`~/.claude`、`~/.codex`、`~/.kimi-code`、`~/.trae-cn`）严格只读。
- **密钥**：只从 `.env`（gitignore）读，不进代码/配置模板/提交物；蒸馏前正则脱敏。
  提交前由 pre-commit 的 `detect-private-key` 自动再拦一道。
- **幂等**：条目 ID = uuid5(source|date|content_hash)；任何重跑不产生重复数据/边。
- **扩展走配置或插件**：新类型改 `config/schema.json`；新采集源复制 `src/plugins/_template/`。
  两者都不许改核心代码。
- **★ 模块**：`similarity.py` 的关联边（T027）与对齐规则（T037）默认用户手写，
  AI 仅 review；已有逐任务授权记录见 tasks.md 头部说明。
- **Git**：不主动 commit/push，时机由用户决定。
