"""arail.nucleus.providers.mixed — config composition only."""

from __future__ import annotations

import pytest

from arail.nucleus.errors import ProfileRefused
from arail.nucleus.providers.mixed import build_mixed


def test_build_mixed_refuses_this_sprint(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "hybrid")
    with pytest.raises(ProfileRefused, match="nucleus-sprint-2"):
        build_mixed(gateway_base_url="https://gw.example.invalid", gateway_token="t", build_id="b1")
