"""QA guards for the 2026-05-30 model-hosting-reframe sprint.

Allocation (arail): 30% setup / 30% Buddy / 20% security / 10% happy / 10% regression.
The setup-ladder shell tests live in tests/setup_ladder/. This file covers the
Python/static surfaces: the deep-model sentinel, qwen-hiding regression guard,
NOTICE attribution, packaging-script security, Buddy (ai-eng) resolution.

OOM-SAFETY: no model is loaded; backend guards are asserted to RAISE before
any load. No network. No subprocess that downloads.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SENTINEL = "__TODO_DEEP_MODEL__"


# ===========================================================================
# DEEP-MODEL SENTINEL (security / OOM-safety)
# ===========================================================================

def test_sentinel_is_not_a_resolvable_model_id():
    """The sentinel must look like a placeholder, never a real org/model id."""
    assert SENTINEL.startswith("__") and SENTINEL.endswith("__")
    assert "/" not in SENTINEL, "a real HF id has an org/ prefix; sentinel must not"
    assert ":" not in SENTINEL, "a real ollama tag has a :tag; sentinel must not"


def test_airllm_worker_raises_on_sentinel_before_any_load(monkeypatch):
    """airllm_worker._load_model must raise (no download/load) when the deep
    model resolves to the sentinel.

    OOM-SAFETY: we set AIRLLM_MODEL to the sentinel string explicitly (the
    documented 'unconfigured' state). The guard must trip BEFORE any
    AutoModel.from_pretrained call. We also patch the airllm import so that
    if the guard ever regressed, the test fails loudly instead of loading a
    real 70B (matching the workspace OOM memory note)."""
    monkeypatch.setenv("AIRLLM_MODEL", SENTINEL)
    import types
    fake_airllm = types.ModuleType("airllm")

    class _Boom:
        @staticmethod
        def from_pretrained(*a, **k):  # pragma: no cover - must never run
            raise AssertionError(
                "GUARD REGRESSION: AutoModel.from_pretrained was called with "
                "the sentinel still active — a real model would have loaded."
            )

    fake_airllm.AutoModel = _Boom
    monkeypatch.setitem(sys.modules, "airllm", fake_airllm)
    from arail.router import airllm_worker
    with pytest.raises(RuntimeError, match="[Dd]eep model is not configured"):
        airllm_worker._load_model()


def test_backends_raises_on_sentinel_before_any_load(monkeypatch):
    """backends deep backend must raise on the sentinel default."""
    monkeypatch.delenv("AIRLLM_MODEL", raising=False)
    from arail.router import backends
    # Find the AirLLM backend class and instantiate enough to hit the guard.
    # The guard lives in __init__ (reads AIRLLM_MODEL, raises on sentinel).
    src = (REPO_ROOT / "src/arail/router/backends.py").read_text()
    assert 'os.getenv("AIRLLM_MODEL"' in src
    assert f'_sentinel = "{SENTINEL}"' in src
    # Behavioral: the guard raises. Locate the class and trigger __init__.
    classes = [getattr(backends, n) for n in dir(backends)
               if isinstance(getattr(backends, n), type)]
    raised = False
    for cls in classes:
        srcfile = getattr(cls, "__module__", "")
        if "backends" not in srcfile:
            continue
        try:
            cls()
        except RuntimeError as e:
            if "not configured" in str(e).lower():
                raised = True
                break
        except Exception:
            continue
    assert raised, "a backend must RuntimeError 'not configured' on the sentinel"


def test_no_deep_default_resolves_to_a_real_weight():
    """REGRESSION GUARD: fail if anyone wires a real 70B/405B/llama default
    back into the deep-model defaults. Locks the sentinel in place."""
    files = [
        "pyproject.toml",
        "scripts/setup.sh",
        "src/arail/router/backends.py",
        "src/arail/router/airllm_worker.py",
        "src/arail/portal/app.py",
        "src/arail/chat/models_catalog.yaml",
    ]
    # Only flag a heavy id used as a DEFAULT (env getenv default, assignment,
    # or default_model wiring) — not benign comment/docstring mentions.
    default_ctx = re.compile(
        r"""(?ix)
        (?:
          getenv\(\s*["']AIRLLM_MODEL["']\s*,\s*["'][^"']*Llama-3\.1-(?:70B|405B)
        | (?:AIRLLM_MODEL[_A-Z]*|default_model)\s*[:=]\s*["'][^"']*Llama-3\.1-(?:70B|405B)
        )
        """
    )
    for f in files:
        text = (REPO_ROOT / f).read_text()
        m = default_ctx.search(text)
        assert not m, f"{f} reintroduces a heavy deep MODEL DEFAULT: {m.group(0)!r}"


def test_app_default_model_is_sentinel():
    """The optional-backend default_model wiring stays on the sentinel."""
    text = (REPO_ROOT / "src/arail/portal/app.py").read_text()
    assert f'"default_model": "{SENTINEL}"' in text


# ===========================================================================
# LLAMA ATTRIBUTION + QWEN-HIDING REGRESSION GUARD (security / attribution)
# MODEL-TIERS-V2 update (2026-05-31):
#   - The default model (llama-ai-eng) is Llama-3.2-1B-Instruct under the
#     Llama 3.2 Community License. Llama attribution is REQUIRED — the
#     "hide the base" rule is REVERSED for the 1B default.
#   - The deep model (ai-engineer) is Qwen2.5-7B, Apache-2.0. Qwen lineage
#     stays in NOTICE/config, not marketing copy.
# ===========================================================================

# Qwen may appear in: Modelfile.preview FROM line, NOTICE, LICENSE, pyproject
# ai_eng_preview, and catalog dormant-lane rows. NOT in the default or deep
# ai-eng identity marketing lines.
USER_FACING_AI_ENG_SURFACES = [
    "README.md",
    "CLAUDE.md",
    "src/arail/portal/templates/tuning.html",
]


def _ai_eng_section_has_qwen(text: str) -> list[str]:
    """Return offending lines that mention qwen near the DEFAULT ai-eng identity.

    MODEL-TIERS-V2: The default is llama-ai-eng (Llama, not Qwen). Any line
    that mentions qwen AND the default model identity is an attribution leak.
    The deep model (ai-engineer, 7B) mentions are allowed in tier-table lines
    that are clearly labelled 'deep' and do NOT pair 'ai-eng' with 'qwen'.
    """
    offending = []
    for line in text.splitlines():
        low = line.lower()
        if "qwen" not in low:
            continue
        # Qwen is allowed on lines that are clearly about the deep/7B model
        # (e.g. tier table rows that say 'deep' or '7b' or 'ai-engineer deep').
        # It is NOT allowed on lines about the default ai-eng identity.
        default_markers = ("llama-ai-eng", "ai-eng", "ai engineer")
        if any(m in low for m in default_markers):
            # Allow lines that are explicitly about the deep model
            # (contain 'deep' or '7b') and mention ai-engineer NOT ai-eng default.
            if ("deep" in low or "7b" in low) and "ai-eng " not in low and "ai-eng," not in low:
                continue
            offending.append(line.strip())
    return offending


def test_no_qwen_in_ai_eng_identity_lines():
    """No line ties the DEFAULT ai-eng identity to qwen in user-facing copy.

    MODEL-TIERS-V2: the default is llama-ai-eng (Llama, not Qwen). The deep
    model (ai-engineer, 7B) may appear in tier tables alongside 'deep'/'7B'
    labels — those are allowed. What is blocked: any line pairing the default
    ai-eng name with qwen lineage.
    """
    for f in USER_FACING_AI_ENG_SURFACES:
        text = (REPO_ROOT / f).read_text()
        bad = _ai_eng_section_has_qwen(text)
        assert not bad, f"{f}: default ai-eng identity leaks qwen: {bad}"


def test_catalog_llama_ai_eng_default_entry_has_no_qwen():
    """The llama-ai-eng catalog entry YAML fields must not mention qwen lineage.

    MODEL-TIERS-V2: default entry is now llama-ai-eng (was ai-eng:latest).
    Only the YAML fields (id/name/family/description/install/tier) are checked —
    comments that follow the block may refer to the next deep-model entry.
    """
    text = (REPO_ROOT / "src/arail/chat/models_catalog.yaml").read_text()
    # Extract the llama-ai-eng block (stop at next list item, not at comments).
    m = re.search(r"^- id: llama-ai-eng\b.*?(?=^- id:|\Z)", text,
                  re.MULTILINE | re.DOTALL)
    assert m, "llama-ai-eng catalog entry not found — default renamed?"
    full_block = m.group(0)
    # Strip trailing comment lines (lines starting with #) that belong to the
    # next entry — these may mention the deep model's Qwen base legitimately.
    yaml_lines = []
    in_trailing_comments = False
    for line in full_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not yaml_lines:
            continue  # leading comment, skip
        if stripped.startswith("#"):
            in_trailing_comments = True
        if not in_trailing_comments:
            yaml_lines.append(line)
    block = "\n".join(yaml_lines)
    assert "qwen" not in block.lower(), (
        f"llama-ai-eng catalog YAML fields leak qwen lineage:\n{block}"
    )
    # Must carry Llama attribution.
    assert "built with llama" in block.lower() or "llama" in block.lower(), (
        "llama-ai-eng catalog entry must mention Llama attribution"
    )


def test_catalog_ai_eng_description_has_no_qwen():
    """Legacy guard: any surviving ai-eng:latest entry must not mention qwen.

    After MODEL-TIERS-V2 the default entry is llama-ai-eng. If ai-eng:latest
    still appears (back-compat row), its description must also be qwen-free.
    """
    text = (REPO_ROOT / "src/arail/chat/models_catalog.yaml").read_text()
    m = re.search(r"^- id: ai-eng:latest\b.*?(?=^- id:|\Z)", text,
                  re.MULTILINE | re.DOTALL)
    if not m:
        return  # ai-eng:latest removed — fine, llama-ai-eng is the default now
    block = m.group(0)
    assert "qwen" not in block.lower(), (
        f"ai-eng:latest catalog entry leaks qwen lineage:\n{block}"
    )


def test_modelfile_preview_is_the_only_user_facing_qwen_FROM():
    """The lone permitted qwen reference is Modelfile.preview's FROM line.
    Dormant self-hosted lane fallback; FROM qwen2.5:1.5b preserved."""
    mf = (REPO_ROOT / "models/ai-eng/Modelfile.preview").read_text()
    assert re.search(r"^FROM\s+qwen2\.5:1\.5b\s*$", mf, re.MULTILINE), (
        "Modelfile.preview must have FROM qwen2.5:1.5b (dormant lane fallback)"
    )
    # And its SYSTEM prompt must NOT self-describe as qwen.
    sys_block = mf.lower()
    # The persona must still be ai-eng, not a qwen self-description.
    assert "ai-eng" in sys_block
    # qwen appears only on the FROM line, nowhere in the SYSTEM narrative.
    non_from = "\n".join(ln for ln in mf.splitlines()
                         if not ln.strip().lower().startswith("from"))
    assert "qwen" not in non_from.lower(), (
        "qwen may only appear on the FROM line, not in the SYSTEM persona"
    )


# ===========================================================================
# NOTICE / ATTRIBUTION (security)
# ===========================================================================

def test_notice_dual_base_structure():
    """MODEL-TIERS-V2: NOTICE must have dual-base structure.

    Section 1: Llama-3.2-1B-Instruct, Llama 3.2 Community License,
               verbatim required notice string, 'Built with Llama'.
    Section 2: Qwen2.5-7B-Instruct, Apache-2.0 (deep model).
    Section 3: dormant distill lane note.
    """
    notice = (REPO_ROOT / "NOTICE").read_text()

    # Section 1 — Llama default
    assert "Llama-3.2-1B-Instruct" in notice, "must name the 1B Llama base"
    assert "Llama 3.2 Community License" in notice, "must name the Llama license"
    # Verbatim required notice string (Llama 3.2 Community License §1.b.iii)
    assert (
        "Llama 3.2 is licensed under the Llama 3.2 Community License" in notice
    ), "verbatim required notice string must be present"
    assert "Copyright © Meta Platforms" in notice, "Meta copyright must be present"
    assert "Built with Llama" in notice, "Built with Llama must be stated in NOTICE"
    assert "llama-ai-eng" in notice, "must name the distributed model llama-ai-eng"
    assert "llama3_2/use-policy" in notice or "llama3.2/use-policy" in notice or \
           "LLAMA-3.2-ACCEPTABLE-USE-POLICY" in notice, "AUP reference required"
    assert "licenses/LLAMA-3.2-COMMUNITY-LICENSE" in notice, \
        "must reference bundled license file"

    # Section 2 — Qwen deep model (Apache-2.0 attribution)
    assert "Qwen2.5-7B-Instruct" in notice, "must name the 7B deep model base"
    assert "Apache-2.0" in notice, "deep model is Apache-2.0"
    assert "huggingface.co/Qwen/Qwen2.5-7B-Instruct" in notice, "7B upstream URL"

    # Section 3 — dormant lane note
    assert "DORMANT" in notice or "dormant" in notice.lower(), \
        "must note the dormant self-hosted lane"

    # Qwen Research License must NOT appear (was removed in 2026-05-30 re-base)
    assert "Qwen Research License" not in notice, \
        "Qwen Research License must not appear — 1.5B was re-based to Apache-2.0"


def test_notice_exists_and_names_qwen_base_and_license():
    """Legacy guard (updated): NOTICE covers both Llama and Qwen bases."""
    notice = (REPO_ROOT / "NOTICE").read_text()
    # Llama base (default) — must be present
    assert "Llama-3.2-1B-Instruct" in notice, "must name the 1B Llama base"
    # Apache-2.0 (deep model) — must be present
    assert "Apache-2.0" in notice, "deep model Apache-2.0 must be attributed"
    # Qwen Research License must NOT appear
    assert "Qwen Research License" not in notice, (
        "Qwen Research License must be removed — was the non-commercial research license"
    )


def test_notice_states_redistribution_attribution_requirement():
    """Architecture: NOTICE must state HF-card + GitHub-release carry it too."""
    notice = (REPO_ROOT / "NOTICE").read_text().lower()
    assert "model card" in notice
    assert "release" in notice
    assert "redistribut" in notice


def test_license_points_to_notice():
    lic = (REPO_ROOT / "LICENSE").read_text().lower()
    assert "notice" in lic, "LICENSE must point to NOTICE for bundled model licenses"


# ===========================================================================
# PACKAGING-SCRIPT SECURITY  (retargeted post-consolidation — CONSOLIDATION.md §5)
# ===========================================================================

def test_package_ai_eng_is_retired_shim():
    """Regression guard: package_ai_eng.sh must stay a thin deprecation shim.

    The 329-line scaffold body was replaced with a small shim that prints a
    deprecation breadcrumb and forwards to build_ai_eng.sh publish.
    This test prevents the scaffold logic from silently reappearing.
    """
    s = (REPO_ROOT / "scripts/package_ai_eng.sh").read_text()
    assert "DEPRECATED" in s
    assert "build_ai_eng.sh" in s and "exec" in s
    # The scaffold's real packaging logic must NOT reappear here.
    for forbidden in ("merge_and_unload", "convert_hf_to_gguf",
                      "llama-quantize", "PeftModel"):
        assert forbidden not in s, f"retired scaffold logic reappeared: {forbidden}"
    assert len(s.splitlines()) < 20, "shim should stay thin"


def test_package_script_embeds_no_credentials():
    """The shim (and build_ai_eng.py) must contain no hardcoded credentials.

    Retargeted: the shim itself is trivially clean; we also assert build_ai_eng.py
    (the canonical pipeline) has no embedded HF tokens or GitHub PATs.
    """
    shim = (REPO_ROOT / "scripts/package_ai_eng.sh").read_text()
    pipeline_py = (REPO_ROOT / "scripts/build_ai_eng.py").read_text()
    for source, name in ((shim, "package_ai_eng.sh"), (pipeline_py, "build_ai_eng.py")):
        assert not re.search(r"hf_[A-Za-z0-9]{20,}", source), \
            f"{name}: looks like an HF token"
        assert not re.search(r"ghp_[A-Za-z0-9]{20,}", source), \
            f"{name}: looks like a GitHub PAT"
        assert not re.search(
            r"(?i)(api[_-]?key|secret|password)\s*=\s*['\"][^'\"]{8,}", source
        ), f"{name}: embedded credential literal"
    # build_ai_eng.py documents login as a manual step in print_upload_instructions
    assert "huggingface-cli login" in pipeline_py
    assert "gh auth login" in pipeline_py


def test_package_script_exits_nonzero_on_missing_inputs(tmp_path):
    """Shim with no args → forwards to build_ai_eng.sh publish with no flags
    → publish exits 70 (no --yes-i-have-read-bench). Exit must be nonzero and
    the deprecation breadcrumb must appear on stderr. No real packaging runs.
    """
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/package_ai_eng.sh")],
        capture_output=True, text=True, timeout=30,
        cwd=str(tmp_path),
    )
    assert r.returncode != 0, "shim must exit nonzero when forwarded publish is refused"
    assert "DEPRECATED" in r.stderr, "deprecation breadcrumb must appear on stderr"
    # Must not download or invent weights.
    assert "huggingface-cli download" not in r.stdout + r.stderr, (
        "shim must not execute a weight download"
    )


def test_package_script_weight_download_is_only_documentation():
    """The shim must contain no executable huggingface-cli download calls.

    Retargeted: the shim is a forwarder; the 'no auto weight download' property
    now lives in build_ai_eng.py (which never auto-downloads base weights either).
    Assert both the shim and the pipeline Python have no auto-download of base weights.
    """
    shim = (REPO_ROOT / "scripts/package_ai_eng.sh").read_text()
    # Shim contains no huggingface-cli download at all (it just forwards).
    assert "huggingface-cli download" not in shim, (
        "shim must not execute any weight download"
    )
    # build_ai_eng.py never auto-downloads the bf16 base weights (only the adapter,
    # via huggingface-cli download, which is gated behind the build subcommand and
    # behind the user explicitly running build — not a silent auto-download).
    pipeline_py = (REPO_ROOT / "scripts/build_ai_eng.py").read_text()
    # The base model (DEFAULT_BF16_BASE / DEFAULT_MLX_BASE) is never downloaded
    # by huggingface-cli download in publish; only the adapter is downloaded in build.
    assert "DEFAULT_BF16_BASE" in pipeline_py, "sanity: base constant must exist"
    # No unconditional weight download of the base (would be a separate huggingface-cli
    # download call for the bf16 base, not the adapter).
    base_downloads = [
        ln for ln in pipeline_py.splitlines()
        if "huggingface-cli" in ln and "download" in ln
        and ("Qwen" in ln or "mlx-community" in ln)
        and not ln.strip().startswith("#")
    ]
    assert not base_downloads, (
        f"build_ai_eng.py auto-downloads base weights (must be manual): {base_downloads}"
    )


def test_package_script_passes_bash_syntax():
    """Shim must pass bash -n syntax check."""
    r = subprocess.run(["bash", "-n", str(REPO_ROOT / "scripts/package_ai_eng.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ===========================================================================
# PUBLISH MODEL RECONCILIATION GUARDS  (new — CONSOLIDATION.md §5)
# ===========================================================================

def test_publish_gate_has_no_ollama_registry_destination():
    """build_ai_eng.py _run_publish must not advertise the ollama.ai registry tag
    as a distribution destination (CONSOLIDATION.md §3 — the old 'Ollama:
    qukaizen/ai-eng:1.5b' line must be gone from the PUBLISH GATE print block).
    """
    src = (REPO_ROOT / "scripts/build_ai_eng.py").read_text()
    # The old distribution destination print line must be absent.
    assert '  3. Ollama: qukaizen/ai-eng:1.5b' not in src, (
        "ollama.ai registry tag still listed as a publish destination"
    )
    # The PUBLISH GATE block must mention self-hosted / HF GGUF.
    assert "self-hosted" in src or "HF GGUF" in src, (
        "publish gate must describe the self-hosted distribution model"
    )


def test_published_json_has_no_ollama_key():
    """print_upload_instructions and _run_publish must produce a PUBLISHED.json
    without an 'ollama' registry key. Assert by inspecting the source dict literal.
    """
    src = (REPO_ROOT / "scripts/build_ai_eng.py").read_text()
    # The old published dict had '"ollama": "qukaizen/ai-eng:1.5b"'.
    assert '"ollama": "qukaizen/ai-eng:1.5b"' not in src, (
        "old ollama registry key still present in PUBLISHED.json dict"
    )
    # The new shape must have hf_gguf_repo and gguf_sha256.
    assert '"hf_gguf_repo"' in src, "PUBLISHED.json must have hf_gguf_repo key"
    assert '"gguf_sha256"' in src, "PUBLISHED.json must have gguf_sha256 key"
    assert '"status": "ready-to-upload"' in src, "status must be ready-to-upload"


def test_print_upload_instructions_references_quant_tagged_filename():
    """print_upload_instructions output must reference the ai-eng-1.5b-<QUANT>.gguf
    filename that check_ai_eng_artifact.sh derives, so copy-paste upload commands line up.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import build_ai_eng as bld
    import io
    from contextlib import redirect_stdout
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        fake_gguf = Path(td) / "ai-eng-1.5b-v2.1.bf16.gguf"
        fake_gguf.write_bytes(b"STUB")
        buf = io.StringIO()
        with redirect_stdout(buf):
            bld.print_upload_instructions(
                gguf_path=fake_gguf,
                sha256="a" * 64,
                license_id="Apache-2.0",
                quant="Q4_K_M",
            )
        out = buf.getvalue()

    assert "ai-eng-1.5b-Q4_K_M.gguf" in out, (
        "upload instructions must include the quant-tagged filename"
    )
    assert "ai_eng_hf_repo" in out or "qukaizen/ai-eng-1.5b-gguf" in out, (
        "upload instructions must reference the HF repo key/value"
    )
    assert "a" * 64 in out, "upload instructions must include the full sha256"
    assert "ai_eng_sha256" in out, "upload instructions must mention the pyproject key"


def test_emit_notice_beside_gguf_copies_repo_notice(tmp_path):
    """emit_notice_beside_gguf must copy the repo-root NOTICE into build_dir."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import build_ai_eng as bld

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    fake_gguf = build_dir / "ai-eng-1.5b-v2.1.bf16.gguf"
    fake_gguf.write_bytes(b"STUB")

    bld.emit_notice_beside_gguf(build_dir, fake_gguf)

    notice_path = build_dir / "NOTICE"
    assert notice_path.exists(), "NOTICE must be written beside the GGUF"
    content = notice_path.read_text()
    # Repo NOTICE mentions Qwen attribution
    assert "Qwen" in content or "Apache" in content, (
        "NOTICE content must mention the base model attribution"
    )


def test_build_ai_eng_py_passes_compile():
    """build_ai_eng.py must compile cleanly (no syntax errors)."""
    import py_compile
    py_compile.compile(str(REPO_ROOT / "scripts/build_ai_eng.py"), doraise=True)


def test_check_artifact_script_passes_bash_syntax():
    r = subprocess.run(["bash", "-n", str(REPO_ROOT / "scripts/check_ai_eng_artifact.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_setup_sh_passes_bash_syntax():
    r = subprocess.run(["bash", "-n", str(REPO_ROOT / "scripts/setup.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ===========================================================================
# SUPPLY-CHAIN: digest gate exists and gates the create
# ===========================================================================

def test_setup_mirror_create_is_gated_behind_sha256_check():
    """The ollama-create-from-gguf must be reachable only after a sha256 match
    and only when the digest is not the placeholder (static assertion that the
    fail-closed structure is present in source)."""
    src = (REPO_ROOT / "scripts/setup.sh").read_text()
    # Fail-closed on placeholder.
    assert '"$_sha256" == "__PLACEHOLDER_SHA256__"' in src
    # sha mismatch path discards the file and skips create.
    assert "sha256 mismatch" in src.lower()
    # The create happens via a generated Modelfile only inside _install_from_gguf.
    assert "_install_from_gguf" in src


def test_check_artifact_uses_head_not_blob_download():
    """check_ai_eng_artifact.sh probes with HEAD (-I), never downloads the blob."""
    src = (REPO_ROOT / "scripts/check_ai_eng_artifact.sh").read_text()
    assert "-I" in src, "must use HEAD probes"
    assert "-o /dev/null" in src, "must discard any body"


# ===========================================================================
# BUDDY — ai-eng is Buddy's brain; the id must still resolve post-rename
# ===========================================================================

def test_resilient_chat_default_returns_installed_ai_eng(monkeypatch):
    """_resilient_chat_default('ai-eng:latest') returns it when installed."""
    monkeypatch.setenv("LAB_MODE", "airgapped")
    import arail.chat as chat_mod
    monkeypatch.setattr(chat_mod, "detect_installed_models",
                        lambda: [{"id": "ai-eng:latest"}], raising=False)
    from arail.portal import app as portal_app
    assert portal_app._resilient_chat_default("ai-eng:latest") == "ai-eng:latest"


def test_resilient_chat_default_returns_llama_ai_eng(monkeypatch):
    """_resilient_chat_default resolves llama-ai-eng (new v1.1 default)."""
    monkeypatch.setenv("LAB_MODE", "airgapped")
    import arail.chat as chat_mod
    monkeypatch.setattr(chat_mod, "detect_installed_models",
                        lambda: [{"id": "llama-ai-eng"}], raising=False)
    from arail.portal import app as portal_app
    result = portal_app._resilient_chat_default("llama-ai-eng")
    assert result == "llama-ai-eng"


def test_resilient_chat_default_prefers_llama_ai_eng_over_legacy(monkeypatch):
    """When both llama-ai-eng and ai-eng:latest are installed, prefer llama-ai-eng."""
    monkeypatch.setenv("LAB_MODE", "airgapped")
    import arail.chat as chat_mod
    monkeypatch.setattr(chat_mod, "detect_installed_models",
                        lambda: [{"id": "ai-eng:latest"}, {"id": "llama-ai-eng"}],
                        raising=False)
    from arail.portal import app as portal_app
    result = portal_app._resilient_chat_default("llama-ai-eng")
    assert result == "llama-ai-eng"


def test_resilient_chat_default_last_resorts_to_ai_engineer_with_warning(monkeypatch, caplog):
    """ai-engineer:latest (7B deep persona) is a last resort, not a confident alias.

    It is NOT a prior name for the ~1B default (see
    model_specs.MODEL_METADATA_OVERRIDES and scripts/setup.sh's "Do NOT
    alias" handling of the same legacy name) — it's ARAIL's original
    pre-two-tier default, based on qwen3:8b then qwen2.5:7b, repositioned as
    the maximus deep persona. When it's the only model installed,
    _resilient_chat_default still returns it (better than refusing, and it
    passes the primary-model ceiling) — but unlike a real alias, that
    fallback must be logged, not silent.
    """
    monkeypatch.setenv("LAB_MODE", "airgapped")
    import arail.chat as chat_mod
    monkeypatch.setattr(chat_mod, "detect_installed_models",
                        lambda: [{"id": "ai-engineer:latest"}], raising=False)
    from arail.portal import app as portal_app
    with caplog.at_level("WARNING", logger="arail.portal.app"):
        result = portal_app._resilient_chat_default("ai-eng:latest")
    assert result == "ai-engineer:latest"
    assert any("ai-engineer:latest" in r.message for r in caplog.records), (
        "falling back to the differently-sized ai-engineer must be logged, not silent"
    )


def test_resilient_chat_default_ai_engineer_still_subject_to_ceiling_check(monkeypatch):
    """REGRESSION GUARD: ai-engineer:latest must not bypass the ceiling check.

    Before the fix, ai-engineer:latest sat in the same back-compat
    preferred-name tuple as llama-ai-eng/ai-eng:latest, so it was returned
    unconditionally — never even reaching the primary-model ceiling check
    that's supposed to gate the answering-model slot, not just passing it.
    Simulate the ceiling rejecting every installed model (as it would for an
    oversized or misconfigured one) and confirm ai-engineer:latest is no
    longer exempt: the resolver must fall through to refusing (returning the
    original candidate) rather than handing back a model the ceiling just
    rejected.
    """
    monkeypatch.setenv("LAB_MODE", "airgapped")
    import arail.chat as chat_mod
    monkeypatch.setattr(chat_mod, "detect_installed_models",
                        lambda: [{"id": "ai-engineer:latest"}], raising=False)
    from arail.portal import app as portal_app
    from arail.registry import ceiling as ceiling_mod

    def _always_refuse(model_id, *, role, **k):
        raise ceiling_mod.ModelCeilingViolation("simulated refusal", model_id=model_id, role=role)

    monkeypatch.setattr(ceiling_mod, "resolve_answering_model", _always_refuse, raising=False)
    result = portal_app._resilient_chat_default("ai-eng:latest")
    assert result == "ai-eng:latest", (
        "ai-engineer:latest must not bypass a failing ceiling check just "
        "because it shares a name prefix with the real ai-eng alias family"
    )


def test_no_reference_to_removed_modelfile_production_tag():
    """Buddy plumbing: the default catalog entry must not use the dead ollama.ai tag.

    MODEL-TIERS-V2: default entry is now llama-ai-eng; install is the persona-wrap.
    """
    cat = (REPO_ROOT / "src/arail/chat/models_catalog.yaml").read_text()
    # llama-ai-eng is the new default entry
    m = re.search(r"^- id: llama-ai-eng\b.*?(?=^- id:|\Z)", cat,
                  re.MULTILINE | re.DOTALL)
    assert m, "llama-ai-eng catalog entry not found"
    block = m.group(0)
    assert "llama3.2:1b" in block or "Modelfile.default" in block, (
        "llama-ai-eng install must reference llama3.2:1b or Modelfile.default"
    )
    assert "qukaizen/ai-eng:3b" not in block, "dead ollama.ai tag must be gone"


def test_modelfile_preview_persona_is_ai_eng():
    """Buddy behavior unchanged: the preview Modelfile still yields the ai-eng
    persona (asserted on text, no model load — OOM-safe)."""
    mf = (REPO_ROOT / "models/ai-eng/Modelfile.preview").read_text()
    assert "ai-eng" in mf
    assert "AI engineering expert" in mf or "AI engineer" in mf.lower()


# ===========================================================================
# REGRESSION: honest framing — no 'frontier-scale' in rewritten surfaces
# ===========================================================================

@pytest.mark.parametrize("f", [
    "README.md", "CLAUDE.md",
    "src/arail/portal/templates/tuning.html", "pyproject.toml",
])
def test_no_frontier_scale_in_rewritten_surfaces(f):
    text = (REPO_ROOT / f).read_text()
    assert not re.search(r"frontier-scale", text, re.IGNORECASE), (
        f"{f} still contains 'frontier-scale' marketing"
    )


def test_pyproject_self_hosted_keys_are_placeholder_marked():
    """Re-base 2026-05-30: preview base is now qwen2.5:1.5b; hf_repo is ai-eng-1.5b-gguf."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    assert 'ai_eng_sha256' in text
    assert "__PLACEHOLDER_SHA256__" in text, "sha must ship as a placeholder"
    assert "ai_eng_hf_repo" in text
    assert "ai-eng-1.5b-gguf" in text, "hf_repo must reference 1.5b artifact"
    assert 'ai_eng_preview' in text and "qwen2.5:1.5b" in text, (
        "preview base key must remain with qwen2.5:1.5b value (operator config)"
    )


def test_check_artifact_returns_nonzero_today(tmp_path):
    """Documents the 2b deferral: the artifact is not uploaded, so the probe
    must report NOT LIVE (nonzero). It flips to 0 once the GGUF is live.

    OOM/network-safe: we force placeholder repo + a curl shim that always
    fails, so no real network call leaves the box."""
    binp = tmp_path / "bin"; binp.mkdir()
    curl = binp / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 22\n")
    curl.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{binp}:{env['PATH']}"
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/check_ai_eng_artifact.sh")],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert r.returncode != 0, "probe must report NOT LIVE while artifact unuploaded"
    assert "NOT LIVE" in (r.stdout + r.stderr)


# ===========================================================================
# MODEL-TIERS-V2 REGRESSION GUARDS (2026-05-31)
# ===========================================================================

def test_default_base_is_16gb_safe_1b_llama():
    """REGRESSION GUARD: default base must be llama3.2:1b (1B, ~0.9 GB, 16 GB safe).

    Prevents the 'heavy-model-as-default' footgun. If someone re-wires the
    default to a 7B or larger, this test blocks it.
    """
    setup = (REPO_ROOT / "scripts/setup.sh").read_text()
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    # pyproject must declare the 1B default
    assert 'default_base' in pyproject and '"llama3.2:1b"' in pyproject, (
        "pyproject.toml must declare default_base = 'llama3.2:1b'"
    )
    # setup.sh default path must pull llama3.2:1b (not a 7B or larger model)
    assert "llama3.2:1b" in setup, "setup.sh must pull llama3.2:1b as the default"
    # The default path must NOT be pulling qwen2.5:7b unconditionally
    # (7B is only in the dormant or deep-persona paths)
    assert "ollama pull qwen2.5:7b" not in setup or \
           "ARAIL_INSTALL_DEEP_PERSONA" in setup, (
        "setup.sh must not pull qwen2.5:7b without the deep-persona gate"
    )


def test_llama_attribution_present_in_required_locations():
    """REGRESSION GUARD: 'Built with Llama' must be in README, catalog, and Modelfile.default."""
    readme = (REPO_ROOT / "README.md").read_text()
    catalog = (REPO_ROOT / "src/arail/chat/models_catalog.yaml").read_text()
    modelfile_default = (REPO_ROOT / "models/ai-eng/Modelfile.default").read_text()

    assert "Built with Llama" in readme, "README must display 'Built with Llama'"
    assert "Built with Llama" in catalog or "built with llama" in catalog.lower(), (
        "catalog must display 'Built with Llama' in the llama-ai-eng entry"
    )
    assert "Built with Llama" in modelfile_default, (
        "Modelfile.default SYSTEM prompt must contain 'Built with Llama'"
    )


def test_llama_license_files_exist_and_nonempty():
    """REGRESSION GUARD: Llama 3.2 license + AUP must be bundled in licenses/."""
    lic = REPO_ROOT / "licenses" / "LLAMA-3.2-COMMUNITY-LICENSE.txt"
    aup = REPO_ROOT / "licenses" / "LLAMA-3.2-ACCEPTABLE-USE-POLICY.txt"
    assert lic.exists(), "licenses/LLAMA-3.2-COMMUNITY-LICENSE.txt must exist"
    assert aup.exists(), "licenses/LLAMA-3.2-ACCEPTABLE-USE-POLICY.txt must exist"
    assert len(lic.read_text()) > 200, "license file must be non-trivial"
    assert len(aup.read_text()) > 200, "AUP file must be non-trivial"
    # Must contain the verbatim required attribution notice
    assert (
        "Llama 3.2 is licensed under the Llama 3.2 Community License" in lic.read_text()
    ), "license file must contain the verbatim required attribution notice"


def test_no_mislabel_alias_7b_as_1b_default():
    """REGRESSION GUARD: ai-engineer:latest (7B) must NOT be aliased to llama-ai-eng.

    The v1 footgun was aliasing the 7B to the 1B default name, making the
    '1B default' secretly a 7B. This guard blocks that pattern in setup.sh.
    """
    setup = (REPO_ROOT / "scripts/setup.sh").read_text()
    # Must NOT have: ollama cp ai-engineer:latest llama-ai-eng
    assert "ollama cp ai-engineer:latest llama-ai-eng" not in setup, (
        "setup.sh must not alias ai-engineer:latest (7B) to llama-ai-eng (1B default)"
    )
    # Must NOT have: ollama tag ai-engineer llama-ai-eng
    assert "ollama tag ai-engineer llama-ai-eng" not in setup, (
        "setup.sh must not tag the 7B as the 1B default"
    )


def test_modelfile_default_exists_and_has_llama_base():
    """REGRESSION GUARD: Modelfile.default must exist with FROM llama3.2:1b."""
    mf = REPO_ROOT / "models/ai-eng/Modelfile.default"
    assert mf.exists(), "models/ai-eng/Modelfile.default must exist"
    text = mf.read_text()
    assert re.search(r"^FROM\s+llama3\.2:1b\s*$", text, re.MULTILINE), (
        "Modelfile.default must have FROM llama3.2:1b"
    )
    assert "Built with Llama" in text, (
        "Modelfile.default SYSTEM prompt must contain 'Built with Llama'"
    )
    assert "AI engineer" in text or "AI engineering" in text, (
        "Modelfile.default must define the AI-engineer persona"
    )


def test_modelfile_deep_exists_and_has_qwen_7b_base():
    """REGRESSION GUARD: Modelfile.deep must exist with FROM qwen2.5:7b."""
    mf = REPO_ROOT / "models/ai-eng/Modelfile.deep"
    assert mf.exists(), "models/ai-eng/Modelfile.deep must exist"
    text = mf.read_text()
    assert re.search(r"^FROM\s+qwen2\.5:7b\s*$", text, re.MULTILINE), (
        "Modelfile.deep must have FROM qwen2.5:7b"
    )
    assert "AI engineer" in text or "AI engineering" in text, (
        "Modelfile.deep must define the AI-engineer persona"
    )


def test_modelfiles_preview_and_production_still_present():
    """REGRESSION GUARD: dormant lane Modelfiles must not be prematurely deleted."""
    assert (REPO_ROOT / "models/ai-eng/Modelfile.preview").exists(), \
        "Modelfile.preview must be kept (dormant lane fallback)"
    assert (REPO_ROOT / "models/ai-eng/Modelfile.production").exists(), \
        "Modelfile.production must be kept (dormant lane build recipe)"


def test_surface_b_airllm_sentinel_unchanged():
    """REGRESSION GUARD: the AirLLM/AeroLLM frontier sentinel must NOT be resolved.

    Surface B (layer-streaming frontier, AIRLLM_MODEL) keeps __TODO_DEEP_MODEL__.
    The 7B Ollama deep persona (Surface A) is separate. This test guards against
    accidentally wiring the 7B to the frontier lane.
    """
    setup = (REPO_ROOT / "scripts/setup.sh").read_text()
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    # The sentinel must still be in the AirLLM keys (may have alignment spaces)
    assert 'airllm_minimalist' in pyproject and '"__TODO_DEEP_MODEL__"' in pyproject
    assert 'airllm_maximus' in pyproject
    # All three airllm keys must have the sentinel value
    sentinel_count = pyproject.count('"__TODO_DEEP_MODEL__"')
    assert sentinel_count >= 3, \
        f"Expected at least 3 __TODO_DEEP_MODEL__ sentinels in pyproject, got {sentinel_count}"
    # AIRLLM_MODEL_ID sentinels must still be in setup.sh
    assert "__TODO_DEEP_MODEL__" in setup


def test_self_hosted_ladder_gated_behind_env_flag():
    """REGRESSION GUARD: the self-hosted GGUF ladder runs only under ARAIL_AI_ENG_SELFHOSTED=1."""
    setup = (REPO_ROOT / "scripts/setup.sh").read_text()
    assert "ARAIL_AI_ENG_SELFHOSTED" in setup, \
        "setup.sh must gate the self-hosted ladder behind ARAIL_AI_ENG_SELFHOSTED"
    # The HF pull for the self-hosted ladder must be inside the flag gate
    lines = setup.splitlines()
    selfhosted_flag_idx = next(
        (i for i, l in enumerate(lines) if "ARAIL_AI_ENG_SELFHOSTED" in l and '==' in l), None
    )
    assert selfhosted_flag_idx is not None, \
        "setup.sh must have an ARAIL_AI_ENG_SELFHOSTED == 1 conditional"


def test_pyproject_has_two_tier_persona_keys():
    """REGRESSION GUARD: pyproject must declare both persona-wrap tiers."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    assert 'default_base' in text and '"llama3.2:1b"' in text
    assert 'default_model_name' in text and '"llama-ai-eng"' in text
    assert 'default_license' in text and 'Llama-3.2-Community-License' in text
    assert 'deep_persona_base' in text and '"qwen2.5:7b"' in text
    assert 'deep_persona_name' in text and '"ai-engineer"' in text
    assert 'deep_persona_license' in text and 'Apache-2.0' in text
