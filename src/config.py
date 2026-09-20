"""Environment variables, paths, timezone, and config schema loading.

PRD FR-015 (env), FR-007/016/019/020 (schema), §6 (data locations).
Secrets come from .env only; never hardcoded (constitution V).
"""

import json
import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# --- Paths (constitution V: runtime writes limited to data/ and notes/) ---

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
QDRANT_DIR = DATA_DIR / "qdrant"
SYNC_LOCK_PATH = DATA_DIR / ".sync.lock"

NOTES_DIR = ROOT / "notes"
INBOX_PATH = NOTES_DIR / "inbox.md"

CONFIG_DIR = ROOT / "config"
SCHEMA_PATH = CONFIG_DIR / "schema.json"

# Single Qdrant collection; dimension decided by first real embed call (M0).
QDRANT_URL = "http://localhost:6333"
COLLECTION = "learning_memory"

# --- Time ---

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai, no external tzdata dep


class ConfigError(Exception):
    """Raised when .env or schema.json is missing required values/structure."""


def load_env() -> None:
    """Load .env from project root into os.environ (idempotent)."""
    load_dotenv(ROOT / ".env")


def env(name: str) -> str:
    """Read a required env var, with an actionable error message."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


# --- config/schema.json (structure is the contract, data-model.md) ---

_DISTILL_KEYS = ("include_signals", "exclude_signals", "examples",
                 "novelty_threshold", "max_entries_per_day",
                 "struggle_rounds", "max_raw_chars")
_EXPAND_KEYS = ("mode", "neighbor_limit_per_hit", "context_cap")


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    """Load and structurally validate config/schema.json.

    Raises ConfigError naming the exact broken field so the user can fix
    schema.json directly (FR-016: new types need zero code migration).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"schema not found at {path}") from None
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"schema.json is not valid JSON: {e}") from None

    _validate(schema)
    return schema


def _validate(schema: dict) -> None:
    types = schema.get("types")
    if not isinstance(types, list) or not types:
        raise ConfigError("schema.types must be a non-empty list")
    for i, t in enumerate(types):
        if not isinstance(t, dict) or not t.get("name") or not t.get("desc"):
            raise ConfigError(
                f"schema.types[{i}] must be an object with 'name' and 'desc'"
            )

    distill = schema.get("distill")
    if not isinstance(distill, dict):
        raise ConfigError("schema.distill must be an object")
    for key in _DISTILL_KEYS:
        if key not in distill:
            raise ConfigError(f"schema.distill is missing '{key}'")
    if not (0 < distill["novelty_threshold"] <= 1):
        raise ConfigError("schema.distill.novelty_threshold must be in (0, 1]")
    if distill["max_entries_per_day"] < 1:
        raise ConfigError("schema.distill.max_entries_per_day must be >= 1")

    retrieval = schema.get("retrieval")
    if not isinstance(retrieval, dict) or "top_k" not in retrieval:
        raise ConfigError("schema.retrieval must contain 'top_k'")
    expand = retrieval.get("expand")
    if not isinstance(expand, dict):
        raise ConfigError("schema.retrieval.expand must be an object")
    for key in _EXPAND_KEYS:
        if key not in expand:
            raise ConfigError(f"schema.retrieval.expand is missing '{key}'")
    mode = expand["mode"]
    # "off"/"all" or a numeric score threshold (FR-020 three modes)
    if mode not in ("off", "all") and not isinstance(mode, (int, float)):
        raise ConfigError(
            "schema.retrieval.expand.mode must be 'off', 'all', or a number"
        )

    if not isinstance(schema.get("trae_type_map", {}), dict):
        raise ConfigError("schema.trae_type_map must be an object")

    retention = schema.get("raw_retention_days")
    if retention is not None and (not isinstance(retention, int) or retention < 0):
        raise ConfigError("schema.raw_retention_days must be a non-negative int or null")


def type_names(schema: dict) -> list[str]:
    return [t["name"] for t in schema["types"]]
