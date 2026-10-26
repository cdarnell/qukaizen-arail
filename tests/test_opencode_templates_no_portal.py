"""Addendum A.3: no OpenCode command template can reach the portal (the model's shell holds no credential)."""
from __future__ import annotations

from arail.portal.services import opencode


def _cfg():
    return opencode._render_opencode_config(provider="my_machine", model="m", portal_port=8080,
                                            tier="maximus", models_list=None)


def test_no_command_template_reaches_the_portal():
    cfg = _cfg()
    for name, cmd in cfg["command"].items():
        t = cmd["template"]
        for bad in ("/api/", "127.0.0.1:", "localhost:", "curl"):
            assert bad not in t, f"{name} template contains {bad!r}"
    assert "rg " in cfg["command"]["kb-search"]["template"]
