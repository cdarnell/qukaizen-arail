"""GatewayClient — the `gateway` profile client (ARCHITECTURE.md §4.11).

Against docs/nucleus-gateway-contract.md, tested with a mock server (this
sprint fixes the contract; the live gateway conforming to it is
nucleus-sprint-2). Sequence-mode distillation only — no logprobs.
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

from arail.nucleus.errors import BudgetExhausted, ContractViolation, GatewayError, ProfileRefused

_REQUIRED_PROVENANCE = ("build_id", "pipeline_hash", "model", "request_id")
_TOKEN_ENV_VAR = "NUCLEUS_GATEWAY_TOKEN"


def _read_secrets_token() -> Optional[str]:
    """Mirrors portal/app.py's `_read_secrets()` convention: a plain
    KEY=value secrets.env file under DATA_DIR, never logged."""
    try:
        from arail.config import DATA_DIR

        path = DATA_DIR / "secrets.env"
        if not path.is_file():
            return None
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == _TOKEN_ENV_VAR:
                return v.strip()
    except Exception:  # noqa: BLE001 — token lookup must never crash the caller
        pass
    return None


class GatewayClient:
    def __init__(self, base_url: str, token: Optional[str] = None, *, build_id: str = ""):
        from arail.airgap import is_airgapped

        # Airgap check happens BEFORE anything else -- no socket, no
        # requests.Session(), no egress.jsonl line, in airgapped mode.
        if is_airgapped():
            from arail.airgap import AIRGAPPED_NOTICE

            raise ProfileRefused(AIRGAPPED_NOTICE)

        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        is_loopback = host in ("127.0.0.1", "::1", "localhost")
        if parsed.scheme != "https" and not is_loopback:
            raise GatewayError(f"gateway base_url must be https (got {parsed.scheme!r} for host {host!r})")

        expected_host = os.getenv("NUCLEUS_GATEWAY_URL", "")
        if expected_host:
            expected = urlparse(expected_host).hostname or expected_host
            if host != expected:
                raise GatewayError(f"gateway host {host!r} does not exact-match NUCLEUS_GATEWAY_URL {expected!r}")

        self._base_url = base_url.rstrip("/")
        self._token = token or _read_secrets_token() or ""
        self._build_id = build_id
        if not self._token:
            raise GatewayError("no gateway build token (pass token= or set NUCLEUS_GATEWAY_TOKEN in secrets.env)")

    def __repr__(self) -> str:
        return f"GatewayClient(base_url={self._base_url!r}, token=<redacted>)"

    def _post(self, path: str, payload: dict) -> dict:
        import requests

        from arail import egress

        url = f"{self._base_url}{path}"
        with egress.allow_egress(f"nucleus-gateway:{self._build_id}"):
            try:
                resp = requests.post(
                    url, json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                    allow_redirects=False, timeout=60,
                )
            except requests.RequestException as exc:
                raise GatewayError(f"gateway request to {urlparse(url).hostname} failed") from exc

        if 300 <= resp.status_code < 400:
            raise GatewayError(f"gateway at {urlparse(url).hostname} returned a redirect ({resp.status_code})")
        if resp.status_code == 402:
            raise BudgetExhausted(f"gateway token budget exhausted (build {self._build_id})")
        if resp.status_code in (401, 403, 429):
            raise GatewayError(f"gateway at {urlparse(url).hostname} returned {resp.status_code}")
        if resp.status_code != 200:
            raise GatewayError(f"gateway at {urlparse(url).hostname} returned {resp.status_code}")

        data = resp.json()
        missing = [f for f in _REQUIRED_PROVENANCE if f not in data]
        if missing:
            raise ContractViolation(f"gateway response missing provenance field(s): {missing}")
        return data

    def teach(self, *, domain: str, prompt: str, decoding: Optional[dict] = None) -> dict:
        return self._post("/v1/nucleus/teach", {
            "build_id": self._build_id, "domain": domain, "prompt": prompt,
            "decoding": decoding or {},
        })

    def judge(self, *, domain: str, text_a: str, text_b: str) -> dict:
        return self._post("/v1/nucleus/judge", {
            "build_id": self._build_id, "domain": domain, "text_a": text_a, "text_b": text_b,
        })


def profile_gate(teacher_profile: str) -> None:
    """domain.teacher.profile in {gateway, mixed} refuses per-mode
    (ARCHITECTURE.md §4.11 "Profile gate")."""
    if teacher_profile not in ("gateway", "mixed"):
        return

    from arail.airgap import AIRGAPPED_NOTICE, is_airgapped

    if is_airgapped():
        raise ProfileRefused(AIRGAPPED_NOTICE)
    raise ProfileRefused("the live gateway lands in nucleus-sprint-2")
