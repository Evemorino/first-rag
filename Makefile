.PHONY: up down serve sync ask log scope embed-test test redistill

# Infrastructure: the only container is Qdrant (PRD §6, constitution VI)
up:
	docker compose up -d

down:
	docker compose down

# --- M0 gate: real Ark call + dynamic-dimension collection (AC-001) ---
embed-test:
	python scripts/embed_test.py

# --- Daily pipeline (needs up + embed-test done once) ---
# Same-day idempotent: safe to re-run any number of times (FR-014).
sync:
	python -m src.sync $(if $(D),D=$(D))

# --- Retrieval ---
#   e.g. make ask Q="最近学了什么" --type error --since 7d --no-expand
ask:
	python -m src.ask Q="$(Q)" $(ARGS)

# --- Quick note -> notes/inbox.md (FR-004) ---
log:
	python -m src.log m="$(m)"

# --- Interactive scope selection (FR-005) ---
scope:
	python -m src.scope

# --- On-demand API shell (ADR-4: not a daemon) ---
serve:
	uvicorn src.api.app:app --port 8300

# --- Redistill diff / apply (US-5, FR-023) ---
#   diff only:  make redistill D=2026-09-18
#   replace:    make redistill D=2026-09-18 APPLY=1
redistill:
	python -m src.redistill $(if $(D),D=$(D)) $(if $(APPLY),--apply)

test:
	pytest
