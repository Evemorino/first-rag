# All Python goes through `uv run` so the project-local .venv is used
# without manual activation (uv is pinned in mise.toml).
# --no-sync：依赖本来就是常驻的，不带它时每条命令都会重建一遍 editable 包，
# 又慢又在某些环境里失败（pre-commit 的钩子也是同样处理）。
PY = uv run --no-sync python

.PHONY: up down serve sync ask log scope embed-test test redistill
.PHONY: cov crap crap-observe mutation mutation-selfcheck hooks
.PHONY: baseline baseline-update orphans orphans-top
.PHONY: layers layers-list size size-top boundary boundary-list gate-selftest
.PHONY: hooks hooks-run

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
#   e.g. make log m="踩了个坑：..." t=error
# t 必须转发：漏了它快记照样写进 inbox，只是类型静默变成 reflection
# （collect 读不到 marker 时的默认值），而 README 一直写着 t=error 能用。
log:
	$(PY) -m src.log m="$(m)" $(if $(t),t=$(t))

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

# 孤儿模块：一行都没被测试跑过的 src/ 模块。CRAP 抓不到它 —— 全是简单函数的
# 模块哪怕零测试，CRAP 也只有 2，离 30 的阈值远得很。
orphans: cov
	$(PY) scripts/orphan_check.py

# 摸底用：覆盖率最低的几个模块，不判失败
orphans-top: cov
	$(PY) scripts/orphan_check.py --top 10

# --- 结构门禁：毫秒级，只管 src/ ---
# 位置与分层：src/ 下每个装着 .py 的目录都必须在 lint_layers 里登记过属于哪一层
#（没登记 = 报错），再加上 import 方向对不对（插件不许互相依赖、不许反向依赖编排层）
layers:
	$(PY) scripts/lint_layers.py

# 打印已登记的位置（含理由、每层现有文件数）、各层允许 import 什么、例外及理由
layers-list:
	$(PY) scripts/lint_layers.py --list

# 规模：单文件 ≤300 SLOC、单函数 ≤80 行。用 SLOC，不罚注释写得好的文件
size:
	$(PY) scripts/size_guard.py

# 摸底用：看最长的文件与函数，不判失败
size-top:
	$(PY) scripts/size_guard.py --top 10

# 写入边界：运行时只准写 data/ 与 notes/，产品源目录（~/.claude 等）严格只读。
# 宪法 V 里唯一此前没有机械门禁的那条，而违规不可恢复。
#   make boundary-list  看登记过的写入点各自碰到哪里、凭什么
boundary:
	$(PY) scripts/write_boundary_check.py

boundary-list:
	$(PY) scripts/write_boundary_check.py --list

# 变异自检：先跑这个，再跑 mutation。
# 它塞一个已知必死的改动进去，看测试抓不抓得住 —— 抓不住说明工具或断言有问题，
# 这时候 mutation 给出的分数是假的（本项目踩过：28.8% 假 → 真实 75.8%）。
mutation-selfcheck:
	$(PY) scripts/mutation_selfcheck.py

# 变异测试：把源码改坏，看测试能不能发现。默认只打核心链路（见 pyproject）。
# 结果看 mutants/ 与 .mutmut-cache；改一行代码后重跑是增量的，很快。
# 末尾自动核对 README 里的基线数字 —— 分数变了就报错，逼你顺手更新文档。
mutation: mutation-selfcheck
	$(PY) -m mutmut run
	$(PY) -m mutmut results --all true
	$(PY) scripts/baseline_check.py

# 只核对不重跑：改了 src/ 或 tests/ 之后，想知道 README 里那个分数还准不准。
# 它还会列出"比上次跑批还新"的文件，提醒你该重跑了。
baseline:
	$(PY) scripts/baseline_check.py

# 把 mutants/ 里的真实数字写回 README（只改那三个数字和分数，排版原样保留）
baseline-update:
	$(PY) scripts/baseline_check.py --update

# 门禁自检：给 14 个钩子各植入一个已知违规，看它到底红不红；再跑一组
# "干净仓库"对照，确认该放行的时候它也放行。约 10 秒。
# 什么时候跑：改了 .pre-commit-config.yaml 或任何一个 scripts/*_check|guard|lint 之后。
# 想确认这把自检本身还灵：把 scripts/lint_layers.py 的 main 开头加一行 `return 0`，
# 它必须报"期望红 实际绿"并退出 1 —— 报不出来说明自检瞎了，比门禁瞎了更糟。
gate-selftest:
	$(PY) scripts/gate_selftest.py

# --- 提交门禁 ---
# 装好之后，每次 git commit 会自动跑：文本/密钥检查 → pytest → CRAP。
# 想临时跳过某次提交：git commit --no-verify（别养成习惯）。
# 装两步：commit 前那 14 个钩子，外加一个 post-commit 提醒
# （提醒"这次改动落在变异覆盖范围内"，它拦不住也拦不了，只能说一声）
hooks:
	uv run pre-commit install
	uv run pre-commit install --hook-type post-commit

hooks-run:
	uv run pre-commit run --all-files
