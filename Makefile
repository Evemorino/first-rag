# All Python goes through `uv run` so the project-local .venv is used
# without manual activation (uv is pinned in mise.toml).
# --no-sync：依赖本来就是常驻的，不带它时每条命令都会重建一遍 editable 包，
# 又慢又在某些环境里失败（pre-commit 的钩子也是同样处理）。
PY = uv run --no-sync python

.PHONY: up down serve sync ask log scope embed-test test redistill
.PHONY: cov crap crap-observe mutation mutation-selfcheck hooks
.PHONY: layers layers-list size size-top

# Infrastructure: the only container is Qdrant (PRD §6, constitution VI)
up:
	docker compose up -d

down:
	docker compose down

# --- M0 gate: real Ark call + dynamic-dimension collection (AC-001) ---
embed-test:
	$(PY) scripts/embed_test.py

# --- Daily pipeline (needs up + embed-test done once) ---
# Same-day idempotent: safe to re-run any number of times (FR-014).
sync:
	$(PY) -m src.sync $(if $(D),D=$(D))

# --- Retrieval ---
#   e.g. make ask Q="最近学了什么" --type error --since 7d --no-expand
ask:
	$(PY) -m src.ask Q="$(Q)" $(ARGS)

# --- Quick note -> notes/inbox.md (FR-004) ---
log:
	$(PY) -m src.log m="$(m)"

# --- Interactive scope selection (FR-005) ---
scope:
	$(PY) -m src.scope

# --- Redistill diff / apply (US-5, FR-023) ---
#   diff only:  make redistill D=2026-09-18
#   replace:    make redistill D=2026-09-18 APPLY=1
redistill:
	$(PY) -m src.redistill $(if $(D),D=$(D)) $(if $(APPLY),--apply)

# --- On-demand API shell (ADR-4: not a daemon) ---
serve:
	uv run uvicorn src.api.app:app --port 8300

test:
	uv run pytest

# --- 质量指标：覆盖率 → CRAP → 变异 ---
# 三者共用一份 coverage 数据：cov 产出 coverage.xml，crap 吃它，
# mutation 靠它判断哪些行值得变异。
cov:
	$(PY) -m pytest -q --cov=src --cov-report=term-missing --cov-report=xml

# CRAP = 复杂度² × (1-覆盖)³ + 复杂度。超阈值就红，先补测试或先拆函数。
crap: cov
	$(PY) scripts/crap.py --top 20

# 摸基线用：只出报告不拦人
crap-observe: cov
	$(PY) scripts/crap.py --top 20 --observe

# --- 结构门禁：毫秒级，只管 src/ ---
# 分层：import 方向对不对（插件不许互相依赖、不许反向依赖编排层等）
layers:
	$(PY) scripts/lint_layers.py

# 打印各层允许 import 什么，以及唯一的例外和它的理由
layers-list:
	$(PY) scripts/lint_layers.py --list

# 规模：单文件 ≤300 SLOC、单函数 ≤80 行。用 SLOC，不罚注释写得好的文件
size:
	$(PY) scripts/size_guard.py

# 摸底用：看最长的文件与函数，不判失败
size-top:
	$(PY) scripts/size_guard.py --top 10

# 变异自检：先跑这个，再跑 mutation。
# 它塞一个已知必死的改动进去，看测试抓不抓得住 —— 抓不住说明工具或断言有问题，
# 这时候 mutation 给出的分数是假的（本项目踩过：28.8% 假 → 真实 75.8%）。
mutation-selfcheck:
	$(PY) scripts/mutation_selfcheck.py

# 变异测试：把源码改坏，看测试能不能发现。默认只打核心链路（见 pyproject）。
# 结果看 mutants/ 与 .mutmut-cache；改一行代码后重跑是增量的，很快。
mutation: mutation-selfcheck
	$(PY) -m mutmut run
	$(PY) -m mutmut results --all true

# --- 提交门禁 ---
# 装好之后，每次 git commit 会自动跑：文本/密钥检查 → pytest → CRAP。
# 想临时跳过某次提交：git commit --no-verify（别养成习惯）。
hooks:
	uv run pre-commit install

hooks-run:
	uv run pre-commit run --all-files
