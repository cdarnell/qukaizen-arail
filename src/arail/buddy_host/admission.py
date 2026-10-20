"""Admission for every ``/api/host/v1/*`` request (ARCHITECTURE.md §5.1, §5.4).

Order, fixed by the contract (``transport.admission_order``):

1. root lab only         (``ARAIL_INSTANCE`` unset)
2. loopback bind         (``BIND_ADDR``)
3. loopback peer         (the socket's peer address)
4. credential            (``Authorization: Bearer bhc1_...`` against the linked sha256)
5. contract hash         (``X-Buddy-Contract: sha256:<hex>`` equals this lab's)
6. body and JSON Schema  (size cap, JSON, the method's request schema)

A refusal is an :class:`EdgeError`; :func:`error_response` turns it into the
OpenAI-shaped, fixed-message body the contract defines, with ``X-Should-Retry: false``
so nanobot never retries a refusal. Messages are fixed strings: nothing from the
request is ever echoed back.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator

from arail.buddy_host import CONTRACT, CONTRACT_SHA256, link

_ERRORS = CONTRACT["errors"]["codes"]
_ERROR_HEADERS = dict(CONTRACT["errors"]["response_headers"])
_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

_VALIDATORS: dict[str, Draft202012Validator] = {}


class EdgeError(Exception):
    """A contract refusal: ``code`` is a key of ``errors.codes``; ``extra`` goes in the error object."""

    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        assert code in _ERRORS, code
        self.code = code
        self.extra = extra

    @property
    def status(self) -> int:
        return int(_ERRORS[self.code]["http"])


def error_response(code: str, **extra: Any) -> JSONResponse:
    entry = _ERRORS[code]
    body = {"error": {"type": "buddy_host_error", "code": code, "message": entry["message"], **extra}}
    return JSONResponse(status_code=int(entry["http"]), content=body,
                        headers={**_ERROR_HEADERS, "Cache-Control": "no-store"})


@dataclass
class Admitted:
    body: Any
    link: dict
    method: str


def validator_for(method: str) -> Draft202012Validator:
    v = _VALIDATORS.get(method)
    if v is None:
        v = _VALIDATORS[method] = Draft202012Validator(CONTRACT["methods"][method]["request"])
    return v


def max_body_bytes(method: str) -> int:
    caps = CONTRACT["limits"]["max_body_bytes"]
    return int(caps.get(method, caps["default"]))


def check_placement(request: Request) -> None:
    if os.getenv("ARAIL_INSTANCE", "").strip():
        raise EdgeError("root_lab_only")
    from arail import config
    if not config.bind_is_loopback():
        raise EdgeError("bind_not_loopback")
    peer = request.client.host if request.client else None
    if peer not in _LOOPBACK_PEERS:
        raise EdgeError("peer_not_loopback")


def check_credential(request: Request) -> dict:
    header = request.headers.get("authorization")
    if not header or not header.strip():
        raise EdgeError("credential_missing")
    scheme, _, token = header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise EdgeError("credential_invalid")
    if link.load_link() is None:
        raise EdgeError("not_linked")
    rec = link.verify_credential(token.strip())
    if rec is None:
        raise EdgeError("credential_invalid")
    return rec


def check_contract(request: Request) -> None:
    raw = request.headers.get("x-buddy-contract")
    if raw is None or not raw.strip():
        raise EdgeError("contract_missing")
    if raw.strip() != "sha256:" + CONTRACT_SHA256:
        raise EdgeError("contract_mismatch", lab_contract_sha256=CONTRACT_SHA256)


async def read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise EdgeError("schema_invalid")
        except ValueError:
            raise EdgeError("schema_invalid") from None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise EdgeError("schema_invalid")
        chunks.append(chunk)
    return b"".join(chunks)


def first_error_path(method: str, body: Any) -> Optional[list]:
    """The instance path of the first schema violation, or None if the body is valid."""
    errors = sorted(validator_for(method).iter_errors(body), key=lambda e: [str(p) for p in e.absolute_path])
    return list(errors[0].absolute_path) if errors else None


def validate_body(method: str, body: Any) -> None:
    path = first_error_path(method, body)
    if path is None:
        return
    extra: dict[str, Any] = {}
    if method == "trace.record" and len(path) >= 2 and path[0] == "records" and isinstance(path[1], int):
        extra["record_index"] = path[1]
    raise EdgeError("schema_invalid", **extra)


async def admit(request: Request, method: str) -> Admitted:
    """Run admission steps 1 to 6 for ``method`` (a key of the contract's ``methods``)."""
    check_placement(request)
    rec = check_credential(request)
    check_contract(request)
    spec = CONTRACT["methods"][method]
    if "request" not in spec:  # GET with no body
        return Admitted(body=None, link=rec, method=method)
    ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype != CONTRACT["transport"]["content_type"]:
        raise EdgeError("schema_invalid")
    raw = await read_body(request, max_body_bytes(method))
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EdgeError("schema_invalid") from None
    validate_body(method, body)
    return Admitted(body=body, link=rec, method=method)


# --------------------------------------------------------------------------- refusal events
_last_logged: dict[str, tuple[float, int]] = {}
_COALESCE_S = 5.0
_ADMISSION_CODES = frozenset({
    "credential_missing", "credential_invalid", "not_linked", "root_lab_only", "bind_not_loopback",
    "peer_not_loopback", "contract_missing", "contract_mismatch",
})


def note_refusal(code: str) -> None:
    """Record an ``edge_refused`` event for a refusal that has no trusted identity.

    Admission-level refusals can come from anything on the loopback, so they are
    coalesced: at most one event per code per 5 s, carrying a count. Never raises.
    """
    try:
        from arail.buddy_host import events
        now = time.monotonic()
        if code in _ADMISSION_CODES:
            last, pending = _last_logged.get(code, (0.0, 0))
            if now - last < _COALESCE_S:
                _last_logged[code] = (last, pending + 1)
                return
            _last_logged[code] = (now, 0)
            events.write_edge_refused(code, count=pending + 1)
        else:
            events.write_edge_refused(code, count=1)
    except Exception:  # noqa: BLE001 - a trace failure must not change the refusal
        pass


def _reset_for_tests() -> None:
    _last_logged.clear()
