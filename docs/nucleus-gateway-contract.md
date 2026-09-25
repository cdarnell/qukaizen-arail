# Model Forge gateway contract

Status: **contract only, this sprint** (ARCHITECTURE.md §4.11 / §10 commit 23).
The live QuKaiZen gateway needs rework before it satisfies this contract —
that rework is `nucleus-sprint-2`. This sprint fixes the shape the client
(`src/arail/nucleus/providers/gateway.py`) and the eventual server must
agree on, and tests the client against a mock server.

## Brief §4.2, verbatim

> The QuKaiZen gateway exists and needs rework. This sprint fixes the
> **contract** it must satisfy; sprint 2 makes the live gateway conform.
>
> | Aspect | Requirement |
> |---|---|
> | Endpoint | `POST /v1/nucleus/teach` (rationale generation) and `POST /v1/nucleus/judge` (pairwise, length-controlled). Anthropic key held server-side only. |
> | Auth | Per-build **build token**, scoped to one `domain` + `build_id`, with a token budget and expiry. Issued by `qkz gateway token --domain linux-kernel --budget 5M`. |
> | Provenance | Every response carries `build_id`, `pipeline_hash`, upstream `model` and `request_id`. Gateway log is the audit trail for the DNA card's `teacher.provenance`. |
> | Distillation mode | `sequence` only (SCoTD chains). No logprobs — documented, not worked around. |
> | Failure | Budget exhaustion → clean `402`; Arbitrage checkpoints and pauses, never silently degrades to a weaker teacher. |
> | Egress | Single allow-listed host in `hybrid`; blocked in `airgapped`. |
> | Client | `src/arail/nucleus/providers/gateway.py` against the contract, tested with a mock server in `tests/`. |

## Concrete wire schema

### `POST /v1/nucleus/teach`

Request:

```json
{
  "build_id": "linux-kernel-20261003T142211Z-a1b2",
  "domain": "linux-kernel",
  "prompt": "...",
  "decoding": {"temperature": 0.0, "max_new_tokens": 512}
}
```

Response (200):

```json
{
  "build_id": "linux-kernel-20261003T142211Z-a1b2",
  "pipeline_hash": "sha256:...",
  "model": "claude-...",
  "request_id": "req_...",
  "text": "..."
}
```

### `POST /v1/nucleus/judge`

Request:

```json
{
  "build_id": "linux-kernel-20261003T142211Z-a1b2",
  "domain": "linux-kernel",
  "text_a": "...",
  "text_b": "..."
}
```

Response (200): same provenance envelope (`build_id`, `pipeline_hash`, `model`,
`request_id`) plus `"verdict": "A" | "B"`.

### Errors

| Status | Meaning | Client behavior |
|---|---|---|
| `401` | invalid/missing build token | `GatewayError`; refuse |
| `402` | token budget exhausted | `BudgetExhausted` — Arbitrage checkpoints and pauses, **never** falls back to another teacher |
| `403` | token valid but wrong `domain`/`build_id` scope | `GatewayError`; refuse |
| `429` | rate limited | `GatewayError`; refuse (retry policy is a sprint-2 concern) |
| `3xx` | any redirect | `GatewayError` — the client never follows a redirect (`allow_redirects=False`) |
| `200` missing a provenance field | contract violation | `ContractViolation` |

### Auth

`Authorization: Bearer <build-token>`. The token is never logged, never in
an exception message, and never in `repr(client)` — exception messages and
`repr` carry only the host and HTTP status.

### Egress

`GatewayClient` refuses to construct at all in `airgapped` mode
(`ProfileRefused`, before any socket is opened — the airgap check happens
before session construction). In `hybrid` mode it is allowed against
exactly `NUCLEUS_GATEWAY_URL` (exact host match, `https` required except
loopback for the mock server), and every call runs inside
`egress.allow_egress(f"nucleus-gateway:{build_id}")` so it is audit-logged
like every other hybrid-mode egress in this repo.
