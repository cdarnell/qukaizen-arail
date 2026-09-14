---
title: Portal Design Spec
category: Design
order: 10
tags:
  - design
  - architecture
  - philosophy
audience: architect
related:
  - agents-explained
  - api-conventions
buddy_prompt: Walk me through the ARAIL portal design spec — tokens, personalities, and component patterns.
---
# ARAIL — Design Spec (v2 · "Warm Observatory")

> A night-observatory lab: warm indigo surfaces, amber instrument-light for
> actions, cool cyan starlight for live data. Server-rendered, no JS
> frameworks. This doc is the single source of truth for visual decisions;
> if the CSS drifts from this doc, the doc is wrong — fix it.
> Full binding component spec: `sprints/2026-07-07-portal-design-v2/design-panel/STYLE-SPEC.md`.
> World-shipped themes: `docs/world-theme-contract.md`.

---

**Sections:** §1 brand · §2 tokens · §3 themes & personalities · §4 typography · §5 components · §6 rules · §7 how to extend.

## 1. Brand anchors

- **Name:** ARAIL — Autoresearch AI Labs (configurable per fork via `LAB_NAME`; see [`brand.py`](../src/arail/brand.py)).
- **Tagline:** *A rail gun for AI* — fast, precise, single-shot. The signature 1px gradient **rail** under the nav carries that identity.
- **Voice:** human sentences in sans; terse mono only for measured things. Errors and hints teach — no telemetry-speak at people.
- **Logo:** `⟨Autoresearch⟩` mono chip on `--surface2`, `--text-strong`, no glow. Forks override via `LAB_LOGO`.
- **Heritage:** the original terminal-hacker skin is not gone — it lives on as the shipped **AI & Machine Learning World's** theme (`lab/worlds/ai`, personality `hacker`). Mount it and the lab flips 1337.

## 2. Token contract

Tokens live in two coordinated places:

1. **`static/style.css` `:root`** — real default values (the shipped
   Warm Observatory theme), so pages render correctly even without the
   middleware injection.
2. **`src/arail/ui_theme.py`** — the theme definitions. The
   `inject_ui_theme` middleware injects `<style id="ui-theme-vars">` after
   the stylesheet on every HTML page; its `:root` block wins by source
   order and repaints the lab per theme / mounted World, live.

**Scheme tokens** (12 slots, themeable — this is exactly what a World's
`theme.dark` block carries): `--bg --surface --surface2 --border --text
--text-muted --accent --accent2 --positive --warn --danger --info`.
Derived neutrals (computed, never World-supplied): `--surface3
--border-strong --text-strong --positive-dim`. Every hex token has a
`--x-rgb` channel companion for `rgba(var(--x-rgb), a)` washes, plus
`a08/a16/a28` alpha tiers.

**The duotone rule:** `--accent` (amber by default) appears ONLY on primary
actions, the mission, and selection emphasis. `--accent2` (cyan) owns links,
live data, run ids, focus rings, sparklines. A surface where both fight is
a bug.

**Personality scalars** (themeable via the closed personality table):
`--radius-s/m/l/pill`, `--motif-scanline-alpha`, `--glow-mix` (the glow
dial), `--dur-1/2/3`, `--ease-accent`, `--heading-font`,
`--label-transform`, `--label-tracking`, `--rail-from/to`.

**Derived layer** (computed in CSS from primitives — Worlds can never set
these): `--rail`, `--glow-a`, `--glow-b`, `--shadow-1`.

**Structural tokens** (never themed): spacing `--s-1..7` (4px rhythm), type
scale `--fs-xs..2xl`, `--lh-*`, `--elev-1/2`, `--font-sans`, `--font-mono`.
Fonts are **self-hosted** (`static/fonts/`, OFL) — an airgapped lab paints
identically offline.

Templates and per-page CSS **must** reference tokens, never raw hex/rgba
(black-alpha elevation shadows excepted). Enforced by
`tests/portal/test_token_compliance.py` (a ratchet: counts only go down)
and the literal-ban in `tests/test_world_recolor.py`.

## 3. Themes & personalities

A theme = 12 color slots per scheme + a personality. Sources, in resolution
order (per request, no restart): mounted World's validated `face.json`
`theme` block → `palette_hint` preset match → `LAB_UI_THEME` env → default.

Personalities (closed table in `ui_theme.py::_PERSONALITY`):

| | `technical` (default) | `scholarly` | `playful` | `hacker` (legacy skin) |
|---|---|---|---|---|
| radii s/m/l | 6/10/16px | 5/8/12px | 12/18/26px | 4/6/10px |
| `--glow-mix` | 10% | 0% (dead flat) | 26% | 30% |
| scanlines | 0.03 | 0 | 0 | 0.03 |
| headings | sans | sans | sans | mono |
| motion | crisp | calm | springy | crisp |

Scholarly at `--glow-mix: 0%` is the smoke test: any surface that breaks
when glows go flat is misbuilt.

Light mode: token names are scheme-neutral and `ThemeColors` has a `light`
slot (accepted, stored, not yet emitted) — adding light is an emission
change, not a redesign.

## 4. Typography

- **Sans for everything a human reads** (`--font-sans`: Inter → system-ui).
  Body 15px/1.6; headings weight 650, tracking −0.015em.
- **Mono only for measured things** (`--font-mono`: JetBrains Mono →
  ui-monospace) with `tabular-nums`: metrics, timestamps, run ids, table
  cells, paths, keys. Mono in a sentence of prose is a bug.
- **Micro-labels** — the one uppercase voice: mono 10–11px/500,
  `letter-spacing: var(--label-tracking)`, `text-transform:
  var(--label-transform)`, `--text-muted`. Labels ≤ 3 words only.

## 5. Components (summary — STYLE-SPEC §e is binding)

Cards (`--surface` + hairline + `--radius-l` + `--shadow-1`, hover lift),
buttons (primary = solid `--accent` with **dark ink**, ghost, danger wash;
never gradient fills), inputs (`--surface2`, cyan focus ring — a focus
affordance, exempt from the glow budget), tables (mono 12.5px, micro-label
thead, no vertical rules), pills (state color + 12% bg + 28% border mixes),
modals (`--surface2` + `--border-strong`, 62%-mix backdrop + blur(3px) — the
only backdrop-filter), chat bubbles (assistant avatar carries one of the
three permitted gradients: rail, avatar, world swatches).

**Glow budget: exactly one glow per view** — the live status dot
(`0 0 8px currentColor` + pulse). Everything else goes through
`--glow-a/--glow-b`.

## 6. Rules that block review

The "do NOT" list in STYLE-SPEC §f is enforceable verbatim; highlights:
no hardcoded hex in component CSS · no gradient-clipped text or gradient
buttons · no glass beyond modal backdrops · don't spend `--accent` on data
or `--accent2` on actions · no uppercase mono on prose · animate only
transform/opacity/box-shadow, ≤ `--dur-3`, behind `prefers-reduced-motion`
· never drop below the contrast gates (text:bg ≥ 4.5, muted/accent:bg ≥ 3.0)
· never remove a loading affordance without replacing it.

## 7. How to extend

- **New component**: build from tokens; check it under a mounted World
  (playful AND scholarly) before shipping — if it only looks right in the
  default theme, it's wrong.
- **New theme preset**: add a `UITheme` to `ui_theme.py::_THEMES` (12 slots
  + personality + pinned derived neutrals). Validate contrast via
  `world_theme.contrast_ratio`.
- **World-shipped theme**: see `docs/world-theme-contract.md` — Worlds carry
  the 12 slots + a personality id in `face.json`, validated fail-closed on
  both sides of the DaC contract.
- **New personality**: closed table by design — add to
  `ui_theme.PERSONALITIES` + `_PERSONALITY` + the DaC mirror
  (`qukaizen-ddac/src/arail-export/theme.ts`), with a schema bump per
  ADR-0004.
