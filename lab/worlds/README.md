# Shipped Worlds (the catalog)

Every directory in here is a sealed **`dac.world-bundle/v1`** — the objective
knowledge dataset a lab studies: terms, categories, an association graph, and
a matching look. Whatever lives in this folder *is* the World catalog: the
portal's picker, the welcome flow, and `./arailctl world list` all scan it
directly.

## Provenance — where these come from

The default bundles (`ai`, `qukaizen`) are authored and sealed upstream in the
sibling **qukaizen-ddac** repo — the offline curation press — and their exported
bytes are **vendored into this repo and committed to git**.

That means the coupling is by vendoring, not by fetching:

- **Single-repo install, guaranteed.** Downloading ARAIL gives you every World
  dependency. qukaizen-ddac is dev-time only — it is never fetched, never a
  submodule, never imported at runtime.
- **Airgapped-friendly by construction.** An airgapped lab's fundamental
  domain terms come from these in-box bundles; the research material is
  whatever you drop into the Knowledge Base.

| Bundle | Display name | Terms | Provenance tier | Source |
|--------|--------------|-------|-----------------|--------|
| `ai` | AI & Machine Learning | 331 | sourced | qukaizen-ddac export |
| `qukaizen` | QuKaiZen | 32 | sourced | qukaizen-ddac export |
| `video-games` | Video Games | 69 | sourced | in-repo forge (`scripts/forge_video_games_world.py`) |

`video-games` is authored a third way: its inputs live in
`scripts/worlds_src/video-games/` (reviewable JSON + a persona markdown file,
committed) and its sealed bytes are produced by the same shared `dac_world`
sealer the qukaizen-ddac exporter uses — re-exported in-repo as
`arail.world_forge` — via a committed script, no vendoring step. Same format,
same seal.

Three more example bundles live in `examples/worlds/` (same format, same
provenance) — import them from the Worlds page when you want them.

## Integrity

Each bundle's `manifest.json` pins a sha256 for the six sealed files
(`terms/spec/roster/face/agenda/drift-report`) plus a `world_sha256` over
`terms.json`. Verify anytime:

```bash
./arailctl world verify-shipped            # the catalog
./arailctl world verify-shipped --examples # + examples/worlds/
```

Setup runs this automatically (step 11) and the portal re-checks at startup —
both shout, neither bricks the lab: a broken bundle is simply omitted from the
picker until restored.

## Editing rules

- **Never hand-edit the six sealed files** — that breaks the seal and the
  bundle refuses to mount. Restore with `git checkout -- lab/worlds/<slug>`.
- Term edits made **through the portal** are fine — they re-seal properly via
  `world_forge.reseal_bundle`.
- Official upstream updates arrive as whole-bundle replacements from the
  qukaizen-ddac export pipeline; this README lives outside the bundle dirs so
  re-exports never conflict with it.
- Worlds you forge in-lab are sealed by ARAIL's own `world_forge` and need no
  upstream at all.
- **`video-games` specifically**: the sealer regenerates `capabilities.json`
  and `SKILL.md` on every reseal (including a portal term edit), which would
  wipe its declared Layer-B capabilities and its authored "Research method"
  persona section — both are merged in by
  `scripts/forge_video_games_world.py` *after* sealing. If you edit its terms
  through the portal, re-run that script afterward to restore them;
  `tests/test_default_worlds_catalog.py` fails if they ever go missing.

> Naming note: new user-facing copy standardizes on **"Documentation as Code
> (DaC)"** (as the shipped `qukaizen` World defines it). qukaizen-ddac's own
> CLAUDE.md still says "Declarative-as-Code" — reconcile upstream at the next
> reseal.
