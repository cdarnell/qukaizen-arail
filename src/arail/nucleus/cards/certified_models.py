"""CERTIFIED_MODELS.md / CERTIFIED_SHARDS.md appender (ARCHITECTURE.md
§4.10, §3 N6).

Default target is a LOCAL, UNTRACKED ledger (NUCLEUS_DATA/CERTIFIED_SHARDS.md).
Only ``append(..., publish_row=True)`` writes the TRACKED
docs/CERTIFIED_MODELS.md, and only inside a delimited generated section —
bytes outside the markers are never touched (T-LEDGER-2).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from arail.nucleus.errors import RefusedByPolicy

BEGIN_MARKER = "<!-- nucleus:shards:begin -->"
END_MARKER = "<!-- nucleus:shards:end -->"

_ESCAPE_MAP = {"|": "\\|", "<": "&lt;", ">": "&gt;", "`": "\\`", "\n": " "}


def _escape_cell(text: str) -> str:
    out = str(text)
    for ch, esc in _ESCAPE_MAP.items():
        out = out.replace(ch, esc)
    return out


def _row_line(card: dict) -> str:
    shard = _escape_cell(card["shard"])
    version = _escape_cell(card["version"])
    decision = _escape_cell(card["fidelity"]["decision"])
    achieved = card["fidelity"]["achieved"]
    built = _escape_cell(card["built"])
    return f"| {shard} | {version} | {decision} | {achieved:.3f} | {built} |"


_TABLE_HEADER = "| Shard | Version | Decision | Composite | Built |\n|---|---|---|---|---|"


def _default_local_path(nucleus_data: Optional[Path]) -> Path:
    if nucleus_data is None:
        from arail.nucleus.paths import nucleus_data as _nd

        nucleus_data = _nd()
    return Path(nucleus_data) / "CERTIFIED_SHARDS.md"


def _upsert_row(existing_rows: dict, card: dict) -> dict:
    key = (card["shard"], card["version"])
    existing_rows[key] = _row_line(card)
    return existing_rows


def _parse_existing_rows(table_text: str) -> dict:
    rows: dict = {}
    for line in table_text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| Shard") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2:
            rows[(cells[0], cells[1])] = line
    return rows


def _render_table(rows: dict) -> str:
    lines = [_TABLE_HEADER]
    for key in sorted(rows):
        lines.append(rows[key])
    return "\n".join(lines)


def _refuse_unless_ledgerable(card: dict, verify_result) -> None:
    if card.get("runtime") == "stub":
        raise RefusedByPolicy("stub cards are never appended to any ledger")
    if not card.get("signed"):
        raise RefusedByPolicy("unsigned cards are never appended to the ledger")
    if verify_result.key not in ("trusted",):
        raise RefusedByPolicy(f"card signed with an untrusted key ({verify_result.key}) — refused")
    if not verify_result.all_ok:
        raise RefusedByPolicy("card does not verify cleanly — refused")


def append(card: dict, *, publish_row: bool = False, verify_result=None,
          nucleus_data: Optional[Path] = None, docs_path: Optional[Path] = None) -> Path:
    if verify_result is None:
        from arail.nucleus.cards.seal import verify as seal_verify

        verify_result = seal_verify(card, fast=True)
    _refuse_unless_ledgerable(card, verify_result)

    if not publish_row:
        target = _default_local_path(nucleus_data)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing_text = target.read_text() if target.is_file() else ""
        rows = _parse_existing_rows(existing_text)
        rows = _upsert_row(rows, card)
        target.write_text(_render_table(rows) + "\n")
        return target

    target = Path(docs_path) if docs_path else Path("docs") / "CERTIFIED_MODELS.md"
    text = target.read_text() if target.is_file() else ""

    if BEGIN_MARKER not in text:
        section = f"\n\n## Model Forge shards\n\n{BEGIN_MARKER}\n{_TABLE_HEADER}\n{END_MARKER}\n"
        text = text + section

    before, _, rest = text.partition(BEGIN_MARKER)
    inside, _, after = rest.partition(END_MARKER)

    rows = _parse_existing_rows(inside)
    rows = _upsert_row(rows, card)
    new_inside = "\n" + _render_table(rows) + "\n"

    new_text = before + BEGIN_MARKER + new_inside + END_MARKER + after
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_text)
    return target
