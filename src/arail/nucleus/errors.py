"""Shared exception types for arail.nucleus.

A small, dependency-free home for error types multiple modules need before
their "natural" owning module exists in build order (ARCHITECTURE.md §10):
``DomainConfigError`` is raised by ``runtime_names.py`` (commit 4) for an
unknown runtime name, and by ``domain.py`` (commit 5) for every other
domain-config validation failure — one error type, one message shape, no
forward import from runtime_names into domain or vice versa.

Not listed in ARCHITECTURE.md §4.0's file layout; noted as a build-time
deviation in BUILD_LOG.md (infrastructure, not scope drift — no behavior
described in the architecture changes because of this file's existence).
"""

from __future__ import annotations


class NucleusError(Exception):
    """Base class for arail.nucleus domain errors.

    Every message is a single plain-language paragraph — cli.py prints
    ``str(exc)`` directly and never a traceback unless ``--debug`` is set
    (T-CLI-3).
    """


class DomainConfigError(NucleusError):
    """A domain.yaml (or a runtime name inside one) fails validation."""


class RefusedByPolicy(NucleusError):
    """Preflight/tier/airgap/capability/lock/contamination refusal.

    cli.py maps this to exit code 3.
    """


class CapabilityMissing(NucleusError):
    """A required runtime capability (e.g. logprobs) is absent."""
