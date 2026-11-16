"""TD61: Recent-activity rows must not turn stored strings into markup.

The audit log's url_host and caller can hold any text a guarded process passed to
urllib. nav.js must write them with textContent, never innerHTML.
"""
import re
from pathlib import Path

NAV = Path(__file__).resolve().parents[1] / "src/arail/portal/static/nav.js"


def _row_block() -> str:
    src = NAV.read_text()
    a = src.index("items.forEach(function (item)")
    b = src.index("list.appendChild(row);", a)
    return src[a:b]


def test_row_block_never_uses_innerhtml():
    assert "innerHTML" not in _row_block()


def test_host_caller_and_time_use_textcontent():
    block = _row_block()
    for field in ("item.url_host", "item.caller"):
        assert re.search(r"\.textContent\s*=\s*" + re.escape(field), block), field
    assert "createTextNode" in block
