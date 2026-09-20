"""Copy this directory to add a new product source (US-6 / AC-011).

Contract (contracts/plugin-contract.md):
  1. discover(date) -> list[SourceRef]  — return [] when the day has nothing
     or the product dir is missing; never raise.
  2. parse(ref) -> RawMaterial          — ref is your own pointer from discover.

Must obey (constitution layer):
  - READ-ONLY towards the product's source directories (constitution V)
  - import SourceRef/RawMaterial only from src.plugins, never core modules
  - thresholds / type mappings come from config, never hardcoded

Drop the finished directory into src/plugins/ and the registry picks it
up automatically — zero core changes.
"""

from __future__ import annotations

from datetime import date

from src.plugins import Plugin, RawMaterial, SourceRef


def discover(day: date) -> list[SourceRef]:
    """Find this product's material for `day`. Return [] if none."""
    # e.g. scan ~/.<product>/sessions/<day>/ and build one SourceRef per file
    raise NotImplementedError("implement discover() in your plugin copy")


def parse(ref: SourceRef) -> RawMaterial:
    """Turn one SourceRef into the unified intermediate format."""
    # e.g. read the file ref.ref points to, extract text + meta, build:
    # RawMaterial(source="<product>", ref=ref.ref, ts=<tz-aware>,
    #             kind="message", text="...", meta={...})
    raise NotImplementedError("implement parse() in your plugin copy")


PLUGIN = Plugin(name="<product>", discover=discover, parse=parse)
