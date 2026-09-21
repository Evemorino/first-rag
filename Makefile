# All Python goes through `uv run` so the project-local .venv is used
# without manual activation (uv is pinned in mise.toml).
PY = uv run python

.PHONY: up down serve sync ask log scope embed-test test redistill

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
