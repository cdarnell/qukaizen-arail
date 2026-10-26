# Buddy on your phone: the host edge

Buddy is a small QA expert you can reach from your phone. He is a separate app
(`qukaizen-buddy`, built on a pinned copy of [nanobot](https://github.com/HKUDS/nanobot))
that runs on your Mac and talks to Telegram. ARAIL is his only brain: every model call
he makes goes through ARAIL, which can hold it, trace it and budget it. This page
describes ARAIL's half: the **host edge** (`buddy.host/v1`), how you link a Buddy to
your lab, and what leaves the box.

Status: sprint `2026-10-20-buddy-always-on` (S1). Design record:
`sprints/2026-10-20-buddy-always-on/` (VISION, ARCHITECTURE, BUILD_LOG).

## What the edge is

Six routes under `/api/host/v1/`. Since S4 they are served twice, by the same code:

- by a **box-only edge app** on `127.0.0.1:$ARAIL_EDGE_PORT` (default **8089**; `0` turns it off). It
  is a second app inside the portal process, bound to the literal loopback address (never
  `BIND_ADDR`), with no portal routes behind it: every other path is 404, and portal cookies and
  tokens mean nothing there. `./arailctl buddy link` writes this URL into both link files.
  A World instance never starts it.
- by the portal's own port (default 8080) for links made before S4. S5 removes this mount.

The portal itself now requires a login (`./arailctl auth` for programs); the edge keeps its own
`bhc1_` credential and is the one thing the portal's auth defers to.

| Route | What it does |
|---|---|
| `POST handshake` | Tells Buddy the lab's mode, tier, whether you consented to Telegram, the hold state, which brain would answer, the day's budget and the research status. Read-only. |
| `POST brain/chat/completions` (and `GET brain/models`) | One model call, in the OpenAI shape. Fast brain only, local only, halt-gated, traced, budget-capped. |
| `POST trace/record` | Buddy writes his own steps (message accepted, reply sent, confirm attempted) into the lab's trace. No message bodies. |
| `POST hold_all` | Hold every agent in the lab (`/stop` from the phone). Reductive. |
| `POST pause_research` | Pause the research run, once per approval, after a confirmer's tap. The only action. Reductive. |

The edge is **loopback only**. It refuses unless the portal is bound to loopback
(`BIND_ADDR=127.0.0.1`), the caller's address is loopback, the lab is the root lab (not a
World instance), the request carries the capability credential, and the request carries the
sha256 of the contract. Every refusal is a fixed message with `X-Should-Retry: false`;
nothing from the request is ever echoed back.

What it cannot do: it cannot start a run, change a goal, resume anything, install, ingest,
read your files, reach the internet, or use a cloud model. Resume is at the desk.

## Link a Buddy to this lab

Run these at the desk, on the root lab.

1. Make Buddy's home: a real directory, yours, mode `0700` (Buddy's `python -m buddy setup`
   does this; by hand: `mkdir -m 700 ~/.qukaizen-buddy`).
2. Link:

   ```bash
   ./arailctl buddy link --buddy-home ~/.qukaizen-buddy
   ```

   It prints the consent text below and asks you to type `yes`.
3. Put the lab in hybrid mode when you want Buddy to listen (Admin, or `LAB_MODE=hybrid`).
   Linking does not change the mode. Buddy listens only while the lab is `hybrid`; flip back to
   airgapped and he stops within a minute.

### The consent text

> Buddy on your phone needs the internet. Your lab will run in hybrid mode. In hybrid mode, the lab may also
> use cloud providers you set up. Buddy himself uses only your local model.
>
> Your messages to Buddy and his replies pass through Telegram's servers. Telegram can read them. Bot chats
> are not end-to-end encrypted.
>
> You can switch back to airgapped at any time. Buddy stops listening within one minute.

Answering `yes` records one consent for `api.telegram.org` in the lab's consent store (the
same allowlist other agents use; see debt D11).

### What link writes

| Where | File | Mode | Holds |
|---|---|---|---|
| ARAIL | `lab/data/buddy/link.json` | 0600 | the sha256 of the credential, its 8-character id, the Buddy home, the edge URL, the contract hash, the consent record id. **Never the credential.** |
| Buddy's home | `secrets.env` | 0600 | `BUDDY_EDGE_CREDENTIAL=bhc1_...` (the only copy of the credential) |
| Buddy's home | `link.json` | 0600 | the edge URL, the credential id, the contract hash, the link time |
| ARAIL | `lab/data/buddy/approvals.sqlite3` | 0600 | the ledger that makes `pause_research` run once per approval |
| ARAIL | `lab/data/buddy/budget.json` | 0600 | today's turn and token counts |

The credential is `bhc1_` plus 32 random bytes (48 characters). `link` never prints it.

### Commands

```bash
./arailctl buddy link --buddy-home PATH [--yes]     # first link; --yes skips the typed answer
./arailctl buddy link --rotate --buddy-home PATH    # new credential; the old one dies at once. Restart Buddy.
./arailctl buddy unlink [--buddy-home PATH]         # delete ARAIL's link, revoke consent; with PATH also Buddy's files
./arailctl buddy status [--json]                    # linked? which credential id? never the credential
```

Exit codes: `0` ok, `2` usage, `3` refused by policy. A refusal writes nothing and records
no consent. `link` is refused on a World instance, when the portal is not bound to
loopback, when the Buddy home is missing, a symlink, not yours or not `0700`, when
`secrets.env` or Buddy's `link.json` is a symlink or not `0600`, when a link already exists
(use `--rotate`), and without a terminal unless you pass `--yes`.

## What leaves the box

| Path | What crosses | Where |
|---|---|---|
| Buddy to Telegram | The text of messages from the Telegram ids you listed, and Buddy's replies. | `api.telegram.org`, by Buddy's process only, only while the lab is hybrid and you consented. |
| ARAIL to anywhere | **Nothing new.** The edge makes no outbound connection. The model is local (Ollama on loopback). | n/a |
| Phone traffic to a cloud LLM | **Never.** The edge refuses a cloud, remote or non-Ollama brain. | n/a |

Telegram can read bot chats; they are not end-to-end encrypted. Do not put secrets in a
message to Buddy.

## What you can see

**Admin, Agent lanes, "Buddy on the phone - last 50 steps"** shows each step with time, step,
channel, who (a salted pseudonym plus role, never the raw Telegram id), brain, time to first
token (or `n/a`), tokens, cost (`$0.00 cash` plus the simulated cloud-equivalent figure) and
outcome. Click a row for the record and the other records of the same turn. The lanes view
counts model calls only; Buddy's non-model steps are in the list, not in the call count.

The trace schema is `arail.agent_trace/v2` (v1 keys unchanged; 3D.EVAL reads v1 by name, so its
adapter must accept both).

## What the edge requires of a request, and where it differs from OpenAI

- **The carrier.** `messages[0]` must have role `system`; its first line must be `BUDDY-TURN/1 ` and one-line JSON
  that matches the contract's header schema; a persona must follow that line (not empty after stripping whitespace);
  and no later message may be a `system` message. Any failure is `turn_header_invalid`. The edge cannot tell Buddy's
  persona from anyone else's; Buddy proves that on his side.
- **The persona is the model's system message.** Ollama receives the text after the header line as a real `system` message and
  the later messages, flattened (`### <role>` blocks), as one `user` message. A request with no system message makes Ollama use
  the Modelfile's own `SYSTEM`, which for `llama-ai-eng` is an "AI engineering assistant" persona (about 50 prompt tokens), so
  Buddy's rules would arrive as user text underneath it. Only the edge asks for this (`system_as_message`); every other
  caller's request is unchanged (debt D7 is paid for the system message only; history roles and `temperature` are still
  flattened and ignored).
- **`finish_reason` is always `stop`.** A completed answer ends with `stop` and carries usage on that chunk; the edge never
  sends `length`, even when the answer used all of `max_tokens`. nanobot (Buddy's client) reads `length` as "continue" and
  would call again on a prompt it wrote, turning one phone question into several admitted model calls. A capped answer stays
  visible in the model-call record (`tokens_out` equals `max_tokens`). This is a deliberate deviation from OpenAI (debt D26).

## Limits

- **300 turns and 600,000 tokens per day** for Buddy, reset at local midnight. A person is also
  limited to 20 admitted calls a minute. Over a cap, Buddy sends a fixed reply. The caps are checked when a
  request arrives, and checked again, together with the lab mode, in one atomic step once the call holds the inference slot (the wait can
  be 8 seconds): a call that cannot be counted is refused as `budget_exhausted` and costs nothing.
- One inference slot at a time, shared with chat; a phone question waits at most 8 seconds for it,
  then Buddy says the lab's model is busy.
- Bodies are 16 KiB (256 KiB for chat); a chat prompt is at most 24,000 characters, an answer at
  most 400 tokens.

## Keeping the two repos in step

`buddy.host.v1.json` and its conformance cases are **owned by qukaizen-buddy**. ARAIL carries a
byte-identical copy in `src/arail/buddy_host/contract/` with a `PROVENANCE.json` stamp (source
commit, per-file sha256, tree digest). Every request carries the contract's sha256; a mismatch is
HTTP 412 and Buddy closes his gate.

```bash
python scripts/sync-buddy-contract.py --from "$QKZ_BUDDY_PATH"   # re-vendor after Buddy changes the contract
python scripts/sync-buddy-contract.py --check                    # verify (sibling-free; compares bytes too if the sibling exists)
```

If the vendored copy does not match its stamp, the portal starts **without** the edge (Buddy then
gets a 404 and stays closed) rather than failing to boot.

## Troubleshooting

| Buddy says / you see | Meaning | Fix |
|---|---|---|
| "The lab is on hold." / `held` | Hold all agents is on (from the desk or from `/stop`). | Admin: turn off "Hold all agents". Also `python -m buddy hold off` in Buddy's repo. |
| `hold_unreadable` | `lab/data/halt.json` exists but is not a hold record. The edge treats the lab as held. | Fix or delete the file, then hold/resume once in Admin. |
| `mode_airgapped` | The lab is airgapped. | Flip to hybrid if you want Buddy to answer. |
| `brain_not_local` / `brain_unavailable` | The `fast` model is a cloud or remote entry, or Ollama is down. | Bind `fast` to the local `llama-ai-eng`; start Ollama. |
| `brain_busy` | Chat or an agent held the inference slot for more than 8 s. | Ask again in a minute. |
| `contract_mismatch` (412) | ARAIL and Buddy disagree on the contract. | Update the one that is behind; re-run the sync. |
| `not_linked` / `credential_invalid` | No link, or the credential was rotated. | `./arailctl buddy status`; `link --rotate`; restart Buddy. |
| `lab_not_onboarded` (401) | The lab has no passphrase yet. This is not a contract error, so Buddy cannot tell it from an unreachable lab and says "The lab is not reachable" (debt D28). | Open the portal and finish onboarding. |

## Known limits (tech debt, see `sprints/BACKLOG.md`)

Buddy runs as you; the model has no tools, but Buddy's own code is not sandboxed (D1). The portal's
other routes are still unauthenticated on loopback, so the edge's credential protects only the edge
(D3). `api.telegram.org` joins the shared consent allowlist (D11).
