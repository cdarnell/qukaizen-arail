# Mastra (opt-in, not bundled)

ARAIL's optional Mastra Studio surface (`integrations/mastra-studio/`, `./arailctl mastra install`) installs these
packages from the public npm registry with `npm ci --ignore-scripts`. They are **not** vendored into the
release bundle and not redistributed by ARAIL.

| Package | Version | Licence |
|---|---|---|
| mastra | 1.34.0 | Apache-2.0 |
| @mastra/core | 1.76.0 | Apache-2.0 (+ separate EE licence for `ee/` directories, see below) |
| @mastra/mcp | 2.3.0 | Apache-2.0 |
| @mastra/libsql | 1.26.0 | Apache-2.0 |
| @mastra/observability | 1.19.0 | Apache-2.0 |

The full transitive tree is checked by `scripts/mastra/license-census.mjs` (allowlist: MIT, ISC, Apache-2.0,
BSD-2/3-Clause, 0BSD, BlueOak-1.0.0, CC-BY-4.0 for the data-only `caniuse-lite`). Each package's own `LICENSE.md`
is copied here as `LICENSE-<name>.md`.

## Enterprise (EE) code: not used, not redistributed

Mastra licenses everything under directories named `ee/` (for example `@mastra/core/auth/ee`,
`@mastra/core/agent-builder/ee`) under a separate enterprise licence. ARAIL uses **no EE feature**: no auth/RBAC/FGA,
no agent builder, no editor, no licence key is ever passed to Mastra (`arail.mastra_bridge.mastra_env()` drops
`MASTRA_LICENSE_KEY` and `MASTRA_EE_LICENSE`, and `run-once.mjs` exits 3 if either is set).

One bundled chunk, `@mastra/core/dist/ee-<hash>.js`, is loaded by any `Agent` import. It is inert without a licence
key. The owner accepted this on 2026-10-21 on condition that it is pinned: `scripts/mastra/check-ee-loads.mjs`
fails if any module URL contains `/ee/`, if any other ee-named module loads, or if the `@mastra/core` version or
chunk name changes (so an upgrade forces a new review).
