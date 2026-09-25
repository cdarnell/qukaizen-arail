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


# ── B5 (2026-09-23 review): the routing prompt must not leak the gold
# label, and the parser must not misparse a negation as its positive ──

def test_subsystem_routing_prompt_never_contains_the_gold_label():
    items = [_item(0, "netdev"), _item(1, "blockdev")]
    prompts, gold = subsystem_routing_task(items)
    for prompt, label in zip(prompts, gold):
        assert label.lower() not in prompt.text.lower()


def test_parse_closed_answer_negation_before_label_parses_as_negation():
    # A substring match in `valid` order ("cve" before "not") would find
    # "cve" first even though "not" is what the sentence actually says.
    assert parse_closed_answer("This is not a CVE fix.", valid=("cve", "not")) == "not"
    assert parse_closed_answer("not a cve", valid=("cve", "not")) == "not"


def test_parse_closed_answer_whole_word_not_substring():
    # "notimportant"/"nothing" must never be parsed as the label "not".
    assert parse_closed_answer("nothing to see here, this is a cve", valid=("cve", "not")) == "cve"
