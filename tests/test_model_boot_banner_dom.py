"""DOM/structure assertions for _model_boot_banner.html (the degraded strip).

The template is a standalone fragment (no {% extends %}, no Jinja
variables) so it renders cleanly with an empty context, same as
_airgap_modal.html's test convention (test_airgap_modal_dom.py).

Rewritten for sprints/2026-10-20-boot-model-autoresolve. Removed assertions
and replacements:
  * picker ids (mbb-a-list, mbb-b-list, mbb-confirm, mbb-not-now, mbb-fix,
    mbb-panel, mbb-error)  -> the picker is gone; test_no_picker_markup
  * esc()-before-innerHTML check -> the strip writes only via textContent;
    test_strip_writes_only_via_textcontent is strictly stronger
  * hf link rel/target check -> no links at all (test_no_links_when_airgapped)
  * fetchBoot(false) exactly-once -> test_fetch_once_no_polling
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import jinja2

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "arail" / "portal" / "templates" / "_model_boot_banner.html"
)


def _render() -> str:
    loader = jinja2.FileSystemLoader(str(TEMPLATE_PATH.parent))
    env = jinja2.Environment(loader=loader, autoescape=True)
    return env.get_template(TEMPLATE_PATH.name).render()


def _script() -> str:
    return _render().split("<script>", 1)[1].rsplit("</script>", 1)[0]


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        d = dict(attrs)
        if "id" in d:
            self.ids.add(d["id"])


def test_template_is_small_and_renders_with_empty_context():
    assert "<script>" in _render()
    assert len(TEMPLATE_PATH.read_text().splitlines()) <= 60


def test_required_ids_present_and_banner_starts_hidden():
    c = _Collector()
    c.feed(_render())
    assert {"model-boot-banner", "mbb-strip", "mbb-strip-text",
            "mbb-change", "mbb-dismiss"} <= c.ids
    tag = re.search(r'<div id="model-boot-banner"[^>]*>', _render()).group(0)
    assert "hidden" in tag


def test_no_picker_markup():
    c = _Collector()
    c.feed(_render())
    for gone in ("mbb-panel", "mbb-a-list", "mbb-b-list", "mbb-confirm",
                 "mbb-not-now", "mbb-fix", "mbb-error"):
        assert gone not in c.ids
    assert "input" not in c.tags and "select" not in c.tags
    assert "/api/models/settle" not in _render()


def test_strip_writes_only_via_textcontent():
    script = _script()
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert "textContent" in script


def test_only_shown_for_degraded_mode():
    script = _script()
    assert "p.mode !== 'degraded'" in script


def test_no_links_when_airgapped():  # FM14
    html = _render()
    assert "<a " not in html and "huggingface" not in html
    assert "target" not in _script()


def test_fetch_once_no_polling():
    script = _script()
    assert "setInterval" not in script and "EventSource" not in script
    assert len(re.findall(r"fetch\(", script)) == 1
    assert "/api/models/boot" in script


def test_change_opens_the_resident_chip_or_goes_to_chat():
    script = _script()
    assert "model-picker" in script and "/chat" in script
