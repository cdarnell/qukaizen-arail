---
title: Security Policy
category: Operating
order: 1
tags:
  - security
  - privacy
  - operating
audience: operator
related:
  - PRIVACY
  - PUBLISH
---
# Security Policy

## Scope

Arail is a **local-first AI lab blueprint**. The default posture is:

- Every service (portal, terminal, notebook, IDE) binds to `127.0.0.1`.
- Local model weights, local PKB, local agents. No outbound network without explicit per-domain consent.
- The unified `ARAIL_PASSWORD` set during `./arailctl setup` gates every login surface.

The security model assumes **the loopback interface is trusted** and the user is the only operator on the machine. Moving any service off `127.0.0.1` — even to a LAN address, even behind a VPN — is **explicit opt-out** of that model and requires the operator to put their own auth layer in front. See `README.md` for the exposure warning.

## Supported versions

Arail is pre-1.0 and ships from `main`. Only the latest commit on `main` receives security fixes. There is no LTS branch.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

- Preferred: GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository (Security tab → *Report a vulnerability*).
- Alternative: open a minimal public issue asking for a private contact, and a maintainer will reach out.

Please include:

- A description of the vulnerability and the affected file/component.
- Steps to reproduce (a minimal PoC is ideal, but not required).
- The impact you observed or can reasonably expect.
- Any mitigations you're aware of.

## Disclosure window

We aim to:

- Acknowledge the report within **7 days**.
- Share a preliminary assessment (confirm / dispute / need more info) within **14 days**.
- Ship a fix or document a mitigation within **90 days** of acknowledgment.

If you need to disclose publicly before a fix is available (e.g. the vulnerability is being actively exploited), please tell us — we will not object to coordinated disclosure on a shortened timeline when warranted.

## Out of scope

The following are **known trade-offs**, not vulnerabilities, and will be closed without action:

- **Portal, terminal, notebook, or IDE bound to `127.0.0.1` being reachable from other processes on the same machine.** The blueprint assumes a single-user workstation. If you need multi-tenant isolation, this is not the right tool. **Do not deploy on a shared school lab machine without adding your own auth proxy in front of the portal** — the unified passphrase authenticates the IDE but not the dashboard. See [docs/PRIVACY.md](docs/PRIVACY.md) for the recommended deployment posture on shared hardware (run per-user under each student's Unix account).
- **Plugin install cloning arbitrary GitHub repos and running `pip install -r requirements.txt`.** The plugin system is explicitly a "bring your own code" surface; the auth layer exists to make sure only the operator can trigger it, not to sandbox what the operator installs. Treat every plugin the same way you'd treat `pip install <untrusted-package>`.
- **Consent approvals persisting across runs.** If you approve `arxiv.org` today, it remains approved until you revoke it from the dashboard. This is a UX choice, not a bug.
- **Local LLM backends leaking prompt content to the model weights on disk.** By design — that's where the model lives.
- **The bundled AeroLLM channel (`./arailctl deep install`) executing unsigned native code on import.** `aerollm_api.abi3.so` is a prebuilt Python extension fetched from ARAIL's own GitHub Releases and imported directly into the interpreter — importing a native extension is always code execution. The installer verifies a sha256 chain (tarball → in-tarball manifest → extracted `.so`) and a cheap Mach-O-arm64 format check before copying it into site-packages, but this is an **integrity** control, not an **authenticity** one: anyone who can serve a malicious tarball from that GitHub Release can also serve a matching digest. There is no codesign or notarization check — the binary is unsigned. The trust boundary is GitHub's TLS + this repo's own access control, one hop, same-origin. See [docs/cli.md](docs/cli.md) for the full breakdown; treat the bundled channel with the same trust you'd extend to any other binary you download from this repo's releases.

## In scope

Reports in these areas are welcome:

- Authentication or authorization bypass on any `/api/*` route.
- Remote code execution, command injection, or path traversal reachable from the portal.
- CSRF or same-origin policy bypass against the dashboard.
- Credential leakage (tokens, passwords, encryption keys) into logs, git history, or world-readable files.
- Supply-chain or dependency issues that materially change the above.

Thanks for helping keep Arail honest.
