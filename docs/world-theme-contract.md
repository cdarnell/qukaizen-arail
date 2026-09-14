# World Theme Contract — `dac.world-theme/v1`

> The cross-repo contract for World-shipped UI themes. Producer:
> `qukaizen-ddac` (`scripts/export-bundle.mts` + `src/arail-export/theme.ts`).
> Consumer: ARAIL (`src/arail/world_theme.py` → `src/arail/identity.py` →
> the `inject_ui_theme` middleware). Sibling of the mount contract in
> qukaizen-ddac's `docs/adr/0004-dac-arail-mount-contract.md`; per that ADR,
> **DaC owns the format, ARAIL only reads.**

## What it is

A World's sealed `face.json` may carry an optional top-level `theme` block.
When present and valid, mounting the World swaps the portal's entire look —
palette *and* personality (radii, glow temperature, motion, label style) —
alongside its knowledge. When absent or invalid, resolution falls back to
`palette_hint` (a preset id) and then the shipped default. **A bad theme
never blocks a mount** — the World mounts with the fallback look.

```json
"theme": {
  "schema": "dac.world-theme/v1",
  "personality": "hacker",
  "dark": {
    "bg": "#0a0a0f",  "surface": "#0f1118", "surface2": "#151821",
    "border": "#1e2230", "text": "#c8cdd8",  "muted": "#78839a",
    "accent": "#00ff41", "accent2": "#00d4ff", "positive": "#00ff41",
    "warn": "#ffb000",  "danger": "#ff3355",  "info": "#b48eff"
  },
  "light": null
}
```

## Validation rules (enforced twice, identically)

Enforced at **export time** by qukaizen-ddac (`validateWorldTheme` — a hard
export failure with an author-actionable message) and at **mount/request
time** by ARAIL (`parse_world_theme` — fail-closed to fallback):

1. `schema` must be exactly `"dac.world-theme/v1"`.
2. `personality` ∈ `technical | scholarly | playful | hacker` (closed enum —
   personalities map to scalar tables that live in ARAIL; Worlds *select*
   one, never supply scalar values).
3. `dark` is required and carries **exactly** the 12 slots above; `light` is
   optional with the same shape (accepted and stored now, emitted when ARAIL
   ships light schemes). No unknown keys anywhere.
4. Every color is a full-string `#rrggbb` (regex-anchored; no shorthand, no
   named colors, no functions, no trailing bytes). Normalized to lowercase.
5. Serialized block ≤ 4 KB.
6. WCAG contrast floors: `text:bg ≥ 4.5`, `muted:bg ≥ 3.0`, `accent:bg ≥ 3.0`
   (WCAG 2.x relative luminance).

## Security model

`face.json` is seal-verified, but a hostile author can seal anything — the
theme block is treated as **untrusted input** on the ARAIL side regardless.
XSS-safety is by construction: the only World-controlled values that reach
the emitted CSS are regex-validated hex strings and a closed-enum
personality id. Derived tokens (`--rail`, `--glow-*`, `--shadow-1`, radii,
easings, `--surface3`/`--border-strong`/`--text-strong`) are computed by
ARAIL from the 12 slots and the personality — Worlds can never set them.
Adversarial coverage: ARAIL `tests/test_world_theme_adversarial.py`
(seal-valid hostile bundles; payload-leak and fallback assertions), DaC
`tests/arail-theme.test.ts`.

## Authoring (DaC side)

Hand-author `data/worlds/<slug>/face.json` with the `theme` key; the
exporter's authored-override allow-list carries it into the sealed face
(integrity fields are still force-derived). Then:

```
node --experimental-strip-types scripts/assemble-world.mts --world=<slug>
node --experimental-strip-types scripts/export-bundle.mts --world=<slug>
```

Reference palettes (validated): the shipped default "Warm Observatory", the
pink-playful and scholarly demos in ARAIL's
`sprints/2026-07-07-portal-design-v2/design-panel/STYLE-SPEC.md` §(b), and
the terminal-hacker palette carried by the shipped `ai` World
(`lab/worlds/ai/face.json`) — the legacy 1337 skin preserved as a World.

## Producers

ARAIL is now a **second producer** of `dac.world-bundle/v1`: the in-portal
World Forge (`src/arail/world_forge.py`) drafts and seals bundles locally
(including theme blocks selected from the lab's presets), and the term
editor re-seals after edits. The schema is unchanged and byte-parity with
DaC's exporter is a non-goal — both producers' output round-trips the same
`verify_seal`. Flagged to DaC per ADR-0004 ("DaC owns the format"): any
format change still starts on the DaC side.

## Versioning

Additive within `dac.world-face/v1` (older ARAIL ignores the unknown key and
uses `palette_hint`). Any change to the block's shape requires a DaC-side
schema bump (`dac.world-theme/v2`) plus a consumer-contract test in both
repos, per ADR-0004's "DaC owns the format" rule. Keep `palette_hint`
populated as graceful degradation.
