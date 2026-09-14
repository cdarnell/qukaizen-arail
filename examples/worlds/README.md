# Example Worlds

These are **example** WorldBundles — fully valid, sealed worlds you can study,
fork, or learn the bundle format from. They are **not** shipped as catalog
defaults (the defaults are the `ai` and `qukaizen` Worlds under `lab/worlds/`),
so a fresh lab starts focused rather than cluttered.

| World | What it is |
|-------|------------|
| `art-history` | A humanities World — movements, media, and canonical works. |
| `horticulture` | A botany / plant-care World. |
| `physics` | A physics World — the reference bundle used across the design docs. |

## Using one

Import any of these into your lab from the **Worlds** page (Import → by path),
or via the API:

```bash
curl -X POST http://127.0.0.1:8080/api/worlds/import \
  -H 'content-type: application/json' \
  -d '{"path": "examples/worlds/physics"}'
```

Importing mounts the bundle and **adopts** it into your catalog
(`lab/worlds/`), after which it appears alongside the defaults. Nothing here is
special-cased — they are ordinary sealed bundles that simply live outside the
scanned catalog directory.

## Provenance

Like the catalog defaults, these are sealed exports from the sibling
**qukaizen-ddac** repo (the offline authoring press), vendored into ARAIL and
committed to git. Nothing is fetched at install time — a single ARAIL download
contains every World dependency; qukaizen-ddac is dev-time only. Check their
seals anytime with `./arailctl world verify-shipped --examples`. See
`lab/worlds/README.md` for the full vendoring contract.

## The format

Each directory is a `dac.world-bundle/v1`: six sha256-sealed JSON files
(`terms/spec/roster/face/agenda/drift-report`) pinned by `manifest.json`, plus
seal-exempt siblings (`SKILL.md`, `capabilities.json`, `arail-plugin.json`).
See `docs/world-forge.md` for how bundles are authored and sealed.
