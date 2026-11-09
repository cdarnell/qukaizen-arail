"""The cloud SDKs' default clients are refused by the egress guard (TD50).

anthropic 2.x and openai 3.x build their HTTP client on ``httpx2``.  Before the guard covered that
library, ``anthropic.Anthropic().messages.create(...)`` in an airgapped lab went straight to the
network layer.  The SDKs are optional installs (the ``maximus`` tier), so these tests are gated:

- ``ARAIL_TEST_EXPECT_SDKS`` (comma separated, e.g. ``anthropic,openai``) names the SDKs the
  environment is DECLARED to have.  A declared SDK that is missing FAILS; an undeclared one that is
  missing skips, because the SDKs really are optional.  CI's SDK step declares both and forbids any
  skip, so a step that silently lost an SDK is red.
- Each probe runs in a fresh interpreter (tests.egress_family_support.run_fresh) against
  ``http://egress-probe.invalid``, a name that never resolves, with a dummy key.  Nothing real is
  contacted: if the guard failed, the result would be a fast ConnectError.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from tests.egress_family_support import run_fresh

SDKS = ("anthropic", "openai")

# What an unguarded default client ends with: the request reached the network layer.
NETWORK_ERRORS = ("ConnectError", "ConnectTimeout", "gaierror", "NameResolutionError")


def _declared():
    return {part.strip() for part in os.environ.get("ARAIL_TEST_EXPECT_SDKS", "").split(",") if part.strip()}


def _audit(tmp_path):
    path = tmp_path / "data" / "egress.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


_SDK = r'''
import importlib, json, os, sys, time

sdk = sys.argv[1]
endpoint = "http://egress-probe.invalid"
lib = importlib.import_module(sdk)
log = os.path.join(os.environ["ARAIL_DATA_DIR"], "egress.jsonl")
messages = [{"role": "user", "content": "hi"}]


def build():
    if sdk == "anthropic":
        return lib.Anthropic(api_key="sk-dummy", base_url=endpoint)
    return lib.OpenAI(api_key="sk-dummy", base_url=endpoint + "/v1")


def call(client):
    if sdk == "anthropic":
        return client.messages.create(model="x", max_tokens=1, messages=messages)
    return client.chat.completions.create(model="x", messages=messages)


before_install = build()  # built BEFORE the guard exists: the SDK-at-import-time pattern
import arail.egress as egress
from arail.airgap import EgressBlocked

egress.install_guard()
after_install = build()


def lines():
    if not os.path.exists(log):
        return 0
    with open(log) as handle:
        return sum(1 for _ in handle)


def chain_of(exc):
    names, seen, node = [], set(), exc
    while node is not None and id(node) not in seen and len(names) < 10:
        names.append(type(node).__name__)
        seen.add(id(node))
        node = node.__cause__ or node.__context__
    return names


def run(label, client):
    before, started = lines(), time.monotonic()
    try:
        call(client)
        final, chain = "NO EXCEPTION", []
    except BaseException as exc:
        final, chain = type(exc).__name__, chain_of(exc)
    return {"label": label, "final": final, "chain": chain, "refused": lines() - before,
            "seconds": round(time.monotonic() - started, 3)}


try:
    transport = type(after_install._client._transport)
    transport = transport.__module__.split(".")[0] + "." + transport.__name__
except Exception:
    transport = "unknown"

rows = [
    run("built before install, max_retries=0", before_install.with_options(max_retries=0)),
    run("built after install, max_retries=0", after_install.with_options(max_retries=0)),
    run("built before install, default retries", before_install),
    run("built after install, default retries", after_install),
]
print(json.dumps({"egress_file": egress.__file__, "sdk_version": lib.__version__, "transport": transport, "rows": rows}))
'''


def test_the_sdk_declaration_names_only_sdks_this_file_knows():
    """A typo in ARAIL_TEST_EXPECT_SDKS would silently stop declaring an SDK, which is the TD50 shape
    again (a check that quietly checks less).  Never skips."""
    unknown = sorted(_declared() - set(SDKS))
    assert not unknown, f"ARAIL_TEST_EXPECT_SDKS names {unknown}; the choices are {', '.join(SDKS)}"


@pytest.mark.parametrize("sdk", SDKS)
def test_sdk_default_clients_are_refused_before_any_network_error(sdk, tmp_path):
    """SDK-1.  A default client built before and one built after ``install_guard()``, each called with
    ``max_retries=0`` and with the SDK's default retries.

    Means: anthropic wraps the refusal in APIConnectionError and retries it (measured: 3 refusals in
    about 1.4 s with defaults); openai lets EgressBlocked through (1 refusal).  Bar: EgressBlocked in the
    exception chain, none of ConnectError, ConnectTimeout, gaierror or NameResolutionError in it, and
    EXACTLY one audit line at ``max_retries=0``, because that is proof no attempt left the process.  With
    default retries only "EgressBlocked in the chain and at least one audit line" is asserted: the retry
    count is the SDK's own business, and is reported.  Out of range: a network error in the chain means
    the SDK's default client left the guarded transports (for example an aiohttp default), which calls for
    an architect decision about extending the guard or documenting it, not for editing this test.  Where:
    the SDK's ``_base_client``; ``arail.egress``.
    """
    if importlib.util.find_spec(sdk) is None:
        if sdk in _declared():
            pytest.fail(
                f"{sdk} is declared in ARAIL_TEST_EXPECT_SDKS but is not installed: this environment lost an SDK "
                "it was built to have, so what it checks is less than it claims"
            )
        pytest.skip(f"{sdk} is not installed (the SDKs are optional) and ARAIL_TEST_EXPECT_SDKS does not name it")

    result = run_fresh(_SDK, sdk, tmp_path=tmp_path, timeout=180)
    rows = result["rows"]
    print(f"\nEVIDENCE SDK-1 {sdk} {result['sdk_version']} on {result['transport']}: case | final | chain | refused | seconds")
    for row in rows:
        print(f"  {row['label']} | {row['final']} | {' <- '.join(row['chain'])} | {row['refused']} | {row['seconds']}")

    for row in rows:
        label = f"{sdk} {row['label']}"
        assert "EgressBlocked" in row["chain"], f"{label}: chain {row['chain']} has no EgressBlocked (final {row['final']})"
        leaked = [name for name in row["chain"] if name in NETWORK_ERRORS]
        assert not leaked, (
            f"{label}: {leaked} in the chain means the request reached the network layer; the SDK's default "
            f"client is not on a guarded transport (chain {row['chain']})"
        )
        if "max_retries=0" in row["label"]:
            assert row["refused"] == 1, f"{label}: {row['refused']} refusals; one attempt must leave exactly one audit line"
        else:
            assert row["refused"] >= 1, f"{label}: no audit line at all"
    assert {line["url_host"] for line in _audit(tmp_path)} == {"egress-probe.invalid"}
