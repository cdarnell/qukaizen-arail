"""One Buddy turn, as the real brain path receives it.

The persona text, header, card line format and answer grammar come from the
stamped Buddy copy; the request then goes through ``brain.parse_header`` and
``brain.flatten`` exactly as Buddy's does. How nanobot places the runtime
context block is approximated as "question, blank line, block" in one user
message (ARCHITECTURE Assumption 5)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from arail.brain_bench import vendor_data

PSEUDONYM = "p_0123456789"
ROLE = "talker"
MAX_TOKENS = {"structured": 400, "template": 120}


@dataclass(frozen=True)
class Request:
    system: str
    prompt: str
    schema: Optional[dict]
    max_tokens: int
    offered: List[str]
    id_map: Optional[Dict[str, str]] = None   # opaque cite id -> slug (SC4 blocks)


def persona(mode: str, llama: bool) -> str:
    v = vendor_data.prompt()
    body = v["structured"] if mode == "structured" else v["template"]
    return f"{body}\n{v['llama_line']}" if llama else body


def answer_schema(slugs: Sequence[str]) -> dict:
    a = vendor_data.prompt()["answer"]
    cite = ({"type": "array", "maxItems": a["max_cites"], "uniqueItems": True, "items": {"enum": list(slugs)}}
            if slugs else {"type": "array", "maxItems": 0})
    return {
        "type": "object", "additionalProperties": False, "required": ["kind", "answer", "cite", "propose"],
        "properties": {
            "kind": {"enum": list(a["kinds"]) if slugs else ["abstain"]},
            "answer": {"type": "string", "maxLength": a["answer_max_chars"]},
            "cite": cite,
            "propose": {"enum": list(a["proposals"])},
        },
    }


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("[", "\\u005b").replace("]", "\\u005d")


def context_block(world: str, cards: Sequence[dict], fields: Optional[Sequence[str]] = None) -> str:
    b = vendor_data.prompt()["block"]
    heading = b["heading"].format(world=world)
    if not cards:
        return "\n".join([heading, b["no_card"]])
    use = list(fields or b["fields"])

    def render(chars: int) -> str:
        lines = [heading]
        for c in cards:
            row = {k: c.get(k, "") for k in use}
            if "definition" in row:
                row["definition"] = row["definition"][:chars]
            lines.append(_line(row))
        return "\n".join(lines)

    chars = b["definition_max"]
    block = render(chars)
    while len(block) > b["block_max"] and chars > 40:
        chars -= 40
        block = render(chars)
    return block


def trace_id_for(task_id: str, mode: str) -> str:
    return hashlib.sha256(f"{task_id}:{mode}".encode()).hexdigest()[:16]


def build(task: dict, mode: str, cards_by_slug: Dict[str, dict], *, llama: bool,
          fields: Optional[Sequence[str]] = None, opaque: Optional[str] = None) -> Request:
    """The (system, prompt, schema, max_tokens) the edge would hand the router for this task.

    ``opaque`` ("content" or "empty", structured mode only) replaces every slug with ``t1``..``tN`` in the card
    lines and in the schema's ``cite`` enum; ``content`` keeps short and definition, ``empty`` shows the id
    alone. The reply's cites map back to slugs through ``Request.id_map`` (SC4)."""
    from arail.buddy_host import brain
    if mode == "structured":
        offered = list(task["offered"])
    else:   # template: the gold card alone stands in for code's card choice
        offered = [task["gold"]["cite"]]
    overrides = task.get("card_overrides") or {}
    cards = []
    for slug in offered:
        c = dict(cards_by_slug[slug])
        c.update(overrides.get(slug, {}))
        cards.append(c)
    id_map = None
    if opaque and mode == "structured":
        ids = [f"t{i + 1}" for i in range(len(offered))]
        id_map = dict(zip(ids, offered))
        keep = ("short", "definition") if opaque == "content" else ()
        cards = [{"slug": tid, **{k: c.get(k, "") for k in keep}} for tid, c in zip(ids, cards)]
        fields = ["slug", *keep]
        offered = ids
    block = context_block(task["world"], cards, fields)
    schema = answer_schema(offered) if mode == "structured" else None
    header = {"trace_id": trace_id_for(task["id"], mode), "channel": "telegram",
              "identity": {"pseudonym": PSEUDONYM, "role": ROLE}, "response_schema": schema}
    sysmsg = (vendor_data.prompt()["header_prefix"]
              + json.dumps(header, separators=(",", ":"), ensure_ascii=False) + "\n" + persona(mode, llama))
    messages = [{"role": "system", "content": sysmsg},
                {"role": "user", "content": f"{task['question']}\n\n{block}"}]
    body = {"messages": messages}
    brain.check_shape(body)
    brain.check_tools_and_content(body)
    brain.check_size(body)
    brain.parse_header(messages)
    system, prompt = brain.flatten(messages)
    return Request(system, prompt, schema, MAX_TOKENS[mode], offered, id_map)


def map_cites(content: str, id_map: Optional[Dict[str, str]]) -> str:
    """Opaque ids in a structured reply -> slugs, so it can be graded like any other. Anything that is not
    a JSON object with a cite list, or a cite that is not a known id, is left as it is (and fails grading)."""
    if not id_map:
        return content
    try:
        obj = json.loads((content or "").strip())
    except ValueError:
        return content
    if isinstance(obj, dict) and isinstance(obj.get("cite"), list):
        obj["cite"] = [id_map.get(c, c) if isinstance(c, str) else c for c in obj["cite"]]
        return json.dumps(obj, ensure_ascii=False)
    return content
