"""arail.nucleus.evals.tasks.linux_kernel — task adapters."""

from __future__ import annotations

from arail.nucleus.evals.tasks.linux_kernel import (
    cve_detection_task, parse_closed_answer, subsystem_routing_task,
)


def _item(i, subsystem, *, cve=False):
    return {"id": f"it-{i}", "subject": f"{subsystem}: fix thing {i}",
           "text": "body text", "labels": {"cve": f"CVE-2026-{i}"} if cve else {}}


def test_cve_detection_task_gold_labels():
    items = [_item(0, "netdev", cve=True), _item(1, "blockdev", cve=False)]
    prompts, gold = cve_detection_task(items)
    assert gold == ["cve", "not"]
    assert prompts[0].item_id == "it-0"
    assert "cve" in prompts[0].text.lower()


def test_subsystem_routing_task_gold_labels():
    items = [_item(0, "netdev"), _item(1, "blockdev")]
    prompts, gold = subsystem_routing_task(items)
    assert gold == ["netdev", "blockdev"]


def test_parse_closed_answer_constrained():
    assert parse_closed_answer("I think this is a CVE fix.", valid=("cve", "not")) == "cve"
    assert parse_closed_answer("not a security issue", valid=("cve", "not")) == "not"
    assert parse_closed_answer("who knows", valid=("cve", "not")) == "invalid"
