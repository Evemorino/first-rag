"""Plugin registry and the unified intermediate types (F3 fix).

`SourceRef` / `RawMaterial` are defined HERE and only here; plugins import
them from this module (contracts/plugin-contract.md). Registry scans
src/plugins/ for packages exposing `PLUGIN`; import failures are silently
skipped with a log line (NFR-004, FR-001).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceRef:
    """A pointer to one piece of raw material for a given day.

    `ref` is whatever the plugin's parse() needs to find it again:
    a file path, session id, etc. Source directories stay read-only
    (constitution V).
    """

    source: str
    ref: str
    day: date


@dataclass
class RawMaterial:
    """Unified intermediate format (data-model.md), plugin parse() output."""

    source: str
    ref: str
    ts: datetime  # tz-aware, Asia/Shanghai (plugin-contract.md)
    kind: str     # message | error | commit | note | trae_record
    text: str
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Plugin:
    """Contract surface every plugin must expose via `PLUGIN`."""

    name: str
    discover: Callable[[date], list[SourceRef]]
    parse: Callable[[SourceRef], RawMaterial]


def iter_plugins(package: str | None = None) -> list[Plugin]:
    """Scan this package's submodules and return registered plugins.

    Broken or missing plugins are skipped with a warning log, never raise
    (AC-006: a renamed plugin dir must not break sync).
    """
    pkg = importlib.import_module(package or __name__)
    found: list[Plugin] = []
    for info in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
        if info.name.startswith("_"):
            continue  # _template and other private dirs
        module_name = f"{pkg.__name__}.{info.name}"
        try:
            mod = importlib.import_module(module_name)
            plugin = getattr(mod, "PLUGIN", None)
        except Exception as e:  # noqa: BLE001 — any plugin failure is non-fatal
            logger.warning("plugin '%s' failed to import, skipping: %s", info.name, e)
            continue
        if not isinstance(plugin, Plugin):
            logger.warning("module '%s' has no valid PLUGIN, skipping", module_name)
            continue
        found.append(plugin)
    return found
