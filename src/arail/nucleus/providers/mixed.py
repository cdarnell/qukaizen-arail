"""`mixed` profile — gateway teacher + local judge (ARCHITECTURE.md §4.11,
brief §4.3). Config composition only; no new transport beyond
providers/gateway.py + providers/queuellm.py."""

from __future__ import annotations

from dataclasses import dataclass

from arail.nucleus.providers.gateway import GatewayClient, profile_gate


@dataclass(frozen=True)
class MixedProviders:
    """The teacher role is a GatewayClient; every other role (judge,
    student, base_student) stays local, via the same
    providers.select_local_provider() every other profile uses."""

    teacher: GatewayClient
    judge_role: str = "judge"


def build_mixed(*, gateway_base_url: str, gateway_token: str, build_id: str) -> MixedProviders:
    profile_gate("mixed")  # always raises this sprint -- see docstring below
    teacher = GatewayClient(gateway_base_url, gateway_token, build_id=build_id)
    return MixedProviders(teacher=teacher)
