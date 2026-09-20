.PHONY: up down serve sync ask log scope embed-test test

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

test:
	pytest
