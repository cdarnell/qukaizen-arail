"""Writing the edge's own records into ``agent_trace`` (v2, ``record_type="event"``).

Buddy's half of a turn arrives through ``trace.record``; the edge adds a few records
of its own (``edge_link``, ``edge_refused``, ``hold_all``, ``action_executed``). All of
them share the lane of agent ``buddy`` and ``origin="edge"``. No free text: ``reason``
and ``attrs`` come from the contract's allowlists or fixed edge vocabulary.
"""

from __future__ import annotations

from typing import Any, Optional

from arail import agent_trace

CHANNEL = "telegram"


def _record(event: str, *, trace_id: Optional[str], channel: Optional[str], identity: Optional[dict],
            outcome: Optional[str] = None, reason: Optional[str] = None, attrs: Optional[dict] = None) -> None:
    agent_trace.record(
        record_type="event", kind="agent", agent_id="buddy", label="buddy", attribution="agent:buddy",
        origin="edge", event=event, channel=channel, identity=identity, outcome=outcome, reason=reason,
        attrs=attrs or {}, trace_id=trace_id,
    )


def identity_dict(who: Optional[dict]) -> Optional[dict]:
    if not who:
        return None
    return {"pseudonym": who.get("pseudonym"), "role": who.get("role")}


def write_buddy_record(rec: dict) -> None:
    """One validated ``trace.record`` entry from Buddy."""
    attrs = dict(rec.get("attrs") or {})
    attrs["client_ts"] = rec.get("client_ts")
    _record(rec["event"], trace_id=rec.get("trace_id"), channel=rec.get("channel"),
            identity=identity_dict(rec.get("identity")), outcome=rec.get("outcome"),
            reason=rec.get("reason"), attrs=attrs)


def write_edge_link(instance_id: str, buddy: dict, world: dict) -> None:
    _record("edge_link", trace_id=None, channel=None, identity={"pseudonym": None, "role": "system"},
            attrs={"instance_id": instance_id, "buddy_version": buddy.get("version"),
                   "nanobot_pin": buddy.get("nanobot_pin"), "world": world.get("slug"),
                   "world_sha8": (world.get("world_sha256") or "")[:8]})


def write_edge_refused(code: str, *, count: int = 1) -> None:
    _record("edge_refused", trace_id=None, channel=None, identity=None, outcome="refused", reason=code,
            attrs={"count": count})


def write_hold_all(*, trace_id: str, by: dict, already_held: bool) -> None:
    _record("hold_all", trace_id=trace_id, channel=CHANNEL, identity=identity_dict(by), outcome="ok",
            reason="telegram_stop", attrs={"already_held": already_held})


def write_action_executed(*, trace_id: str, by: dict, outcome: str, approval_id: str, replayed: bool = False) -> None:
    _record("action_executed", trace_id=trace_id, channel=CHANNEL, identity=identity_dict(by), outcome="ok",
            reason=outcome, attrs={"approval_id": approval_id, "action": "arail.pause_research", "replayed": replayed})


def write_refused_model_call(code: str, *, trace_id: str, channel: str, pseudonym: str, role: str) -> None:
    """One ``model_call`` record for a ``brain.chat`` the edge refused after its turn header was valid.

    Before the header is valid there is no trusted identity, so those refusals are
    ``edge_refused`` events instead. No model was called, so there is no cost.
    """
    agent_trace.record(
        record_type="model_call", kind="agent", agent_id="buddy", label="buddy", attribution="agent:buddy",
        origin="edge", channel=channel, identity={"pseudonym": pseudonym, "role": role}, trace_id=trace_id,
        brain="reflex", foreground=True, outcome="refused_" + code, cost=None,
    )
