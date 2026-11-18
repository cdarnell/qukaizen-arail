"""arail.model_pins: pin record loader + digest verification (no network)."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from arail import model_pins as mp

FAMILY = "qkz-aiml-qwen3-1.7b"
GOOD = "ab" * 32          # pinned weights
OTHER = "d67e0abf" + "1" * 56   # what control-merge serves
TEMPLATE = "{{ .Prompt }}"
TAG = f"{FAMILY}:1.0.0"
CONTROL = f"{FAMILY}:control-merge"


def _pinned_doc(**over):
    doc = json.loads(mp.PINS_PATH.read_text())
    rec = doc["pins"][0]
    rec.update(status="pinned", ollama_tag=TAG, weights_sha256=GOOD)
    rec["serving"]["parameters"]["num_ctx"] = 8192
    rec["release"]["source_commit"] = "c" * 40
    rec.update(over)
    return doc


@pytest.fixture
def pinfile(tmp_path, monkeypatch):
    def _w(doc):
        p = tmp_path / "pins.json"
        p.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
        monkeypatch.setenv("ARAIL_MODEL_PINS_FILE", str(p))
        mp.clear_caches()
    return _w


def _show(weights, template=TEMPLATE, params="num_ctx 8192"):
    return {"modelfile": f"# Modelfile\nFROM /Users/someone/.ollama/models/blobs/sha256-{weights}\n",
            "template": template, "parameters": params}


@pytest.fixture
def ollama(monkeypatch):
    st = {"tags": {TAG: "m1"}, "show": {TAG: _show(GOOD)}, "calls": []}
    monkeypatch.setattr(mp, "_fetch_tags", lambda: st["tags"])

    def show(tag):
        st["calls"].append(tag)
        return st["show"].get(tag)
    monkeypatch.setattr(mp, "_fetch_show", show)
    mp.clear_caches()
    return st


# T-P1
def test_shipped_record_is_pending_with_empty_identity():
    recs = mp.load()
    assert list(recs) == [FAMILY]
    r = recs[FAMILY]
    assert r.state == "pending"
    assert r.ollama_tag is None and r.weights_sha256 is None and r.manifest_digest is None
    assert mp.verify(r).state == "pending"


# T-P2
@pytest.mark.parametrize("mutate", [
    lambda d: d["pins"][0].update(bogus=1),
    lambda d: d["pins"][0].update(ollama_tag=f"{FAMILY}:latest"),
    lambda d: d["pins"][0].update(ollama_tag=CONTROL),
    lambda d: d["pins"][0].update(weights_sha256="xyz"),
    lambda d: d["pins"][0]["serving"]["parameters"].update(num_ctx=None),
    lambda d: d["pins"][0].update(status="maybe"),
    lambda d: d["pins"].append(copy.deepcopy(d["pins"][0])),
    lambda d: d.update(schema="other/v1"),
])
def test_invalid_records_never_raise_and_are_unusable(pinfile, mutate):
    doc = _pinned_doc()
    mutate(doc)
    pinfile(doc)
    r = mp.load()[FAMILY]
    assert r.state == "invalid"
    assert mp.verify(r, TAG).state == "invalid"


def test_malformed_json_is_invalid(pinfile):
    pinfile("{not json")
    assert mp.load()[FAMILY].state == "invalid"
    assert mp.family_of(CONTROL) is not None   # still guarded


def test_pinned_without_commit_is_invalid(pinfile):
    doc = _pinned_doc()
    doc["pins"][0]["release"]["source_commit"] = None
    pinfile(doc)
    assert mp.load()[FAMILY].state == "invalid"


def test_family_of_matches_bare_name():
    assert mp.family_of(CONTROL).id == FAMILY
    assert mp.family_of(FAMILY).id == FAMILY
    assert mp.family_of("llama-ai-eng:latest") is None
    assert mp.family_of(None) is None


# T-P3
def test_control_merge_mismatch_reports_short_digests_without_paths(pinfile, ollama):
    pinfile(_pinned_doc())
    ollama["tags"][CONTROL] = "m2"
    ollama["show"][CONTROL] = _show(OTHER)
    chk = mp.verify(mp.load()[FAMILY], CONTROL)
    assert chk.state == "mismatch"
    assert OTHER[:8] in chk.reason and GOOD[:8] in chk.reason
    assert "/Users/" not in chk.reason and OTHER not in chk.reason
    assert "/Users/" not in json.dumps(chk.to_dict())


def test_retagged_control_under_the_pinned_tag_still_mismatches(pinfile, ollama):
    pinfile(_pinned_doc())
    ollama["show"][TAG] = _show(OTHER)
    assert mp.verify(mp.load()[FAMILY]).state == "mismatch"


# T-P4
def test_show_without_from_digest_is_unverifiable(pinfile, ollama):
    pinfile(_pinned_doc())
    ollama["show"][TAG] = {"modelfile": "FROM qwen3:1.7b\n", "template": ""}
    assert mp.verify(mp.load()[FAMILY]).state == "unverifiable"


def test_ollama_down_is_unverifiable(pinfile, monkeypatch):
    pinfile(_pinned_doc())
    monkeypatch.setattr(mp, "_fetch_tags", lambda: None)
    assert mp.verify(mp.load()[FAMILY]).state == "unverifiable"


def test_not_installed(pinfile, ollama):
    pinfile(_pinned_doc())
    ollama["tags"].clear()
    chk = mp.verify(mp.load()[FAMILY])
    assert chk.state == "not_installed" and TAG in chk.reason


# T-P5
def test_non_loopback_host_makes_no_request(pinfile, monkeypatch):
    pinfile(_pinned_doc())
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5")

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert mp.verify(mp.load()[FAMILY]).state == "unverifiable"


# T-P6
def test_template_and_parameter_mismatch_name_the_field(pinfile, ollama):
    doc = _pinned_doc()
    doc["pins"][0]["serving"]["template_sha256"] = hashlib.sha256(TEMPLATE.encode()).hexdigest()
    pinfile(doc)
    assert mp.verify(mp.load()[FAMILY]).state == "match"
    ollama["show"][TAG] = _show(GOOD, template="different")
    mp.clear_caches()
    chk = mp.verify(mp.load()[FAMILY])
    assert chk.state == "mismatch" and "template" in chk.reason
    ollama["show"][TAG] = _show(GOOD, params="num_ctx 4096")
    mp.clear_caches()
    chk = mp.verify(mp.load()[FAMILY])
    assert chk.state == "mismatch" and "num_ctx" in chk.reason


# T-P7
def test_match_and_show_cache_follows_manifest_digest(pinfile, ollama):
    pinfile(_pinned_doc())
    rec = mp.load()[FAMILY]
    assert mp.verify(rec).state == "match"
    assert mp.verify(rec).state == "match"
    assert len(ollama["calls"]) == 1            # cached while manifest unchanged
    ollama["tags"][TAG] = "m2"                  # re-tagged
    ollama["show"][TAG] = _show(OTHER)
    assert mp.verify(rec).state == "mismatch"
    assert len(ollama["calls"]) == 2


def test_verify_cached_ttl(pinfile, ollama):
    pinfile(_pinned_doc())
    rec = mp.load()[FAMILY]
    assert mp.verify_cached(rec).state == "match"
    ollama["show"][TAG] = _show(OTHER)
    ollama["tags"][TAG] = "m2"
    assert mp.verify_cached(rec).state == "match"        # cached
    assert mp.verify_cached(rec, ttl=0).state == "mismatch"


# T-P8
def test_cli_exit_codes(pinfile, ollama, capsys):
    assert mp.main([]) == 3                         # shipped pending record
    assert "pending" in capsys.readouterr().out
    pinfile(_pinned_doc())
    assert mp.main(["--json"]) == 0
    json.loads(capsys.readouterr().out)
    ollama["tags"].clear()
    assert mp.main([]) == 4
    ollama["tags"][TAG] = "m"
    ollama["show"][TAG] = _show(OTHER)
    mp.clear_caches()
    assert mp.main([]) == 5
    assert mp.main(["--bogus"]) == 2
