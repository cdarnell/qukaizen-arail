"""Seed Open Notebook with lab content on first boot.

Reads the PKB (research reports, experiments, docs) and creates
pre-populated notebooks so users see value immediately — not a blank
slate.  Idempotent: skips seeding if notebooks already exist.

Called from the portal after ``docker compose up`` finishes.
"""

from __future__ import annotations

import glob
import logging
import os
import platform
import time
from pathlib import Path

from arail import httpx_family

# Whichever httpx-family library is installed (httpx2 first).  A fresh install can have httpx2 and
# no httpx, and then `import httpx` here raised ModuleNotFoundError in /api/open-notebook/start
# right after `docker compose up` (TD51).  With neither installed this still fails at import, now
# with a message that names both.
httpx = httpx_family.preferred()  # noqa-airgap: localhost-only (all calls target the local surreal/open-notebook port)

log = logging.getLogger(__name__)

API_BASE = "http://127.0.0.1:{port}/api"
AUTH = ("admin", "secret")
TIMEOUT = 30.0

# Marker — we tag the first notebook's description so we can detect
# whether seeding already ran.
_SEED_MARKER = "[arail-seeded]"


def _api(port: int) -> str:
    return API_BASE.format(port=port)


def _wait_for_api(port: int, retries: int = 15, delay: float = 2.0) -> bool:
    """Poll the API until it's ready or we give up."""
    base = _api(port)
    for i in range(retries):
        try:
            r = httpx.get(f"{base}/notebooks", auth=AUTH, timeout=5.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def _already_seeded(port: int) -> bool:
    """Check if we've already created seed notebooks."""
    try:
        r = httpx.get(f"{_api(port)}/notebooks", auth=AUTH, timeout=10.0)
        if r.status_code == 200:
            notebooks = r.json()
            # If any notebook exists, we've seeded (or user created their own)
            if isinstance(notebooks, list) and len(notebooks) > 0:
                return True
            if isinstance(notebooks, dict) and notebooks.get("items"):
                return True
        return False
    except Exception:
        return False


def _create_notebook(port: int, name: str, description: str) -> str | None:
    """Create a notebook and return its ID."""
    r = httpx.post(
        f"{_api(port)}/notebooks",
        json={"name": name, "description": description},
        auth=AUTH,
        timeout=TIMEOUT,
    )
    if r.status_code in (200, 201):
        data = r.json()
        return data.get("id") or data.get("record_id")
    log.warning("Failed to create notebook %r: %s %s", name, r.status_code, r.text[:200])
    return None


def _add_text_source(port: int, notebook_id: str, title: str, content: str) -> str | None:
    """Add a text source to a notebook."""
    r = httpx.post(
        f"{_api(port)}/sources/json",
        json={
            "type": "text",
            "content": content,
            "title": title,
            "notebook_id": notebook_id,
            "async_processing": True,
        },
        auth=AUTH,
        timeout=TIMEOUT,
    )
    if r.status_code in (200, 201, 202):
        data = r.json()
        return data.get("id") or data.get("record_id")
    log.warning("Failed to add source %r: %s %s", title, r.status_code, r.text[:200])
    return None


def _add_url_source(port: int, notebook_id: str, url: str, title: str = "") -> str | None:
    """Add a URL source to a notebook."""
    payload: dict = {
        "type": "link",
        "url": url,
        "notebook_id": notebook_id,
        "async_processing": True,
    }
    if title:
        payload["title"] = title
    r = httpx.post(
        f"{_api(port)}/sources/json",
        json=payload,
        auth=AUTH,
        timeout=TIMEOUT,
    )
    if r.status_code in (200, 201, 202):
        data = r.json()
        return data.get("id") or data.get("record_id")
    log.warning("Failed to add URL source %r: %s %s", url, r.status_code, r.text[:200])
    return None


def _read_file(path: str) -> str | None:
    """Read a file and return its content, or None."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _find_lab_root() -> Path:
    """Walk up from this file to find the repo root (contains arail script)."""
    p = Path(__file__).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "arail").exists() or (parent / "lab" / "pkb").exists():
            return parent
    return Path.cwd()


def _setup_ai_provider(port: int) -> None:
    """Configure a local AI provider in Open Notebook.

    On Apple Silicon, prefers Arail's MLX-backed OpenAI-compatible
    server. Falls back to Ollama when that server is unavailable.
    Registers discovered models and sets the first language model as
    default for chat, transformation, and tools.
    """
    base = _api(port)

    # Check if credentials already exist
    try:
        r = httpx.get(f"{base}/credentials", auth=AUTH, timeout=10.0)
        if r.status_code == 200:
            creds = r.json()
            if isinstance(creds, list) and len(creds) > 0:
                log.info("AI provider already configured — skipping.")
                return
    except Exception:
        return

    # Prefer the built-in MLX-backed OpenAI-compatible server on
    # Apple Silicon. It keeps the host's primary runtime on mlx-lm
    # without requiring Ollama just to satisfy an HTTP integration.
    import shutil
    provider = "ollama"
    credential_name = "Local Ollama"
    base_url = "http://host.docker.internal:11434"
    model_names: list[str] = []

    use_mlx_server = platform.system() == "Darwin" and platform.machine() == "arm64"
    if use_mlx_server:
        mlx_port = int(os.getenv("MLX_OPENAI_PORT", "11435"))
        try:
            host_r = httpx.get(f"http://localhost:{mlx_port}/v1/models", timeout=5.0)
            if host_r.status_code == 200:
                data = host_r.json().get("data", [])
                model_names = [m.get("id") for m in data if m.get("id")]
                if model_names:
                    provider = "openai"
                    credential_name = "Local MLX"
                    base_url = f"http://host.docker.internal:{mlx_port}/v1"
                    log.info("Found MLX API models: %s", ", ".join(model_names))
        except Exception as e:
            log.info("MLX API not reachable for Open Notebook setup: %s", e)

    if not model_names:
        if not shutil.which("ollama"):
            log.info("No local OpenAI-compatible provider detected — skipping AI provider setup.")
            log.info("Start the MLX API with ./arailctl start on Apple Silicon, or install Ollama for the compatibility path.")
            return

        try:
            host_r = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
            if host_r.status_code != 200:
                log.warning("Ollama not responding — skipping AI setup.")
                return
            host_models = host_r.json().get("models", [])
            if not host_models:
                log.warning("No Ollama models found. Pull one: ollama pull qwen2.5:7b")
                return
            model_names = [m["name"] for m in host_models]
            log.info("Found Ollama models: %s", ", ".join(model_names))
        except Exception as e:
            log.warning("Cannot reach Ollama: %s", e)
            return

    # Create the credential in Open Notebook
    try:
        r = httpx.post(
            f"{base}/credentials",
            json={
                "name": credential_name,
                "provider": provider,
                "api_key": "not-needed",
                "base_url": base_url,
            },
            auth=AUTH,
            timeout=TIMEOUT,
        )
        if r.status_code not in (200, 201):
            log.warning("Failed to create %s credential: %s", provider, r.text[:200])
            return
        cred_data = r.json()
        cred_id = cred_data.get("id")
        log.info("Created %s credential: %s", provider, cred_id)
    except Exception as e:
        log.warning("Failed to create credential: %s", e)
        return

    # Register each model
    registered_models = []
    for model_name in model_names:
        try:
            r = httpx.post(
                f"{base}/models",
                json={
                    "name": model_name,
                    "provider": provider,
                    "credential_id": cred_id,
                    "type": "language",
                },
                auth=AUTH,
                timeout=TIMEOUT,
            )
            if r.status_code in (200, 201):
                model_data = r.json()
                model_id = model_data.get("id")
                registered_models.append(model_id)
                log.info("Registered model: %s → %s", model_name, model_id)
        except Exception as e:
            log.warning("Failed to register model %s: %s", model_name, e)

    # Set the first model as default for chat, transformation, tools
    if registered_models:
        default_id = registered_models[0]
        try:
            r = httpx.put(
                f"{base}/models/defaults",
                json={
                    "default_chat_model": default_id,
                    "default_transformation_model": default_id,
                    "default_tools_model": default_id,
                },
                auth=AUTH,
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                log.info("Set default model: %s", default_id)
            else:
                log.warning("Failed to set defaults: %s", r.text[:200])
        except Exception as e:
            log.warning("Failed to set defaults: %s", e)


def seed(port: int | None = None) -> dict:
    """Main entry point.  Returns a summary dict."""
    port = port or int(os.getenv("OPEN_NOTEBOOK_API_PORT", "5055"))
    lab_root = _find_lab_root()
    pkb = lab_root / "lab" / "pkb"

    result = {"seeded": False, "notebooks": 0, "sources": 0, "skipped": False}

    if not pkb.is_dir():
        log.info("No PKB found at %s — skipping seed.", pkb)
        result["skipped"] = True
        return result

    log.info("Waiting for Open Notebook API on port %d…", port)
    if not _wait_for_api(port):
        log.warning("Open Notebook API not reachable — skipping seed.")
        result["skipped"] = True
        return result

    if _already_seeded(port):
        log.info("Open Notebook already has content — skipping seed.")
        result["skipped"] = True
        return result

    # ── Configure AI provider (Ollama → local model) ───────────────
    _setup_ai_provider(port)

    sources_added = 0

    # ── Notebook 1: Lab Research ──────────────────────────────────
    nb_research = _create_notebook(
        port,
        "Lab Research",
        f"Research reports and experiment logs generated by the lab's AI agents. {_SEED_MARKER}",
    )
    if nb_research:
        result["notebooks"] += 1

        # Add research reports
        for report in sorted(glob.glob(str(pkb / "agents" / "research" / "*.md"))):
            content = _read_file(report)
            if content and len(content) > 100:
                # Clean up any duplicate content or markdown artifacts
                # (some reports have repeated sections)
                lines = content.split("\n")
                # Find if the content repeats itself
                half = len(lines) // 2
                if half > 5 and lines[:5] == lines[half:half + 5]:
                    content = "\n".join(lines[:half]).strip()

                title = Path(report).stem.replace("_", " ").title()
                # Extract a better title from the first heading
                for line in content.split("\n"):
                    if line.startswith("# ") or line.startswith("**") and line.endswith("**"):
                        title = line.lstrip("#* ").rstrip("*").strip()
                        break
                if _add_text_source(port, nb_research, title, content):
                    sources_added += 1

        # Add experiment logs
        for exp in sorted(glob.glob(str(pkb / "agents" / "experiments" / "*.md"))):
            content = _read_file(exp)
            if content and len(content) > 80:
                title = content.split("\n")[0].lstrip("# ").strip()
                if _add_text_source(port, nb_research, title, content):
                    sources_added += 1

        # Add recommendations
        for rec in sorted(glob.glob(str(pkb / "agents" / "recommendations" / "*.md"))):
            content = _read_file(rec)
            if content and len(content) > 30:
                title = content.split("\n")[0].lstrip("# ").strip()
                if _add_text_source(port, nb_research, title, content):
                    sources_added += 1

    # ── Notebook 2: Lab Documentation ─────────────────────────────
    nb_docs = _create_notebook(
        port,
        "Lab Documentation",
        "Architecture, setup guides, and module reference for the lab.",
    )
    if nb_docs:
        result["notebooks"] += 1

        # Add the main README
        readme = _read_file(str(pkb / "compiled" / "docs" / "guides" / "README.md"))
        if readme and len(readme) > 100:
            # Strip frontmatter
            if readme.startswith("---"):
                end = readme.find("---", 3)
                if end > 0:
                    readme = readme[end + 3:].strip()
            if _add_text_source(port, nb_docs, "Autoresearch AI Lab", readme):
                sources_added += 1

        # Add wiki guide
        wiki = _read_file(str(pkb / "compiled" / "docs" / "guides" / "wiki.md"))
        if wiki and len(wiki) > 100:
            if wiki.startswith("---"):
                end = wiki.find("---", 3)
                if end > 0:
                    wiki = wiki[end + 3:].strip()
            if _add_text_source(port, nb_docs, "Wiki System Guide", wiki):
                sources_added += 1

        # Add key module docs (portal, router, scheduler)
        for mod_name, title in [
            ("arail-portal-app", "Portal Web Dashboard"),
            ("arail-router", "Model Router"),
            ("arail-scheduler", "Scheduler & Work Windows"),
        ]:
            path = pkb / "compiled" / "docs" / "modules" / f"{mod_name}.md"
            content = _read_file(str(path))
            if content and len(content) > 50:
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end > 0:
                        content = content[end + 3:].strip()
                if _add_text_source(port, nb_docs, title, content):
                    sources_added += 1

    # ── Notebook 3: Getting Started ───────────────────────────────
    nb_start = _create_notebook(
        port,
        "Getting Started",
        "Welcome to your lab. Start here.",
    )
    if nb_start:
        result["notebooks"] += 1

        welcome = """# Welcome to Open Notebook

This is your lab's research curation surface — a self-hosted NotebookLM alternative.

## What you can do here

- **Organize sources**: Drop in PDFs, paste URLs, add text — Open Notebook indexes everything.
- **Chat with your research**: Ask questions grounded in your actual sources, not hallucinated.
- **Generate podcasts**: Turn your research into professional multi-speaker audio.
- **Search across everything**: Keyword and semantic search across all your materials.

## Pre-loaded content

We've seeded this instance with content from your lab's PKB:

- **Lab Research** notebook — AI-generated research reports and experiment logs from the researcher agent
- **Lab Documentation** notebook — Architecture docs, setup guides, and module reference

## Adding your own content

Click **+ New Source** in any notebook to add:
- **URLs** — web pages, YouTube videos (transcripts extracted automatically)
- **Files** — PDFs, Word docs, audio, video
- **Text** — paste anything directly

## Configuring AI providers

Go to **Settings > API Keys** to add your AI providers. Open Notebook supports 18+ providers including OpenAI, Anthropic, Ollama, and more.

If you're running Ollama or LM Studio locally, they're reachable at `host.docker.internal` from inside the container.

## Tips

- Use the **Transformation** feature to run AI prompts against your sources (summarize, extract key points, etc.)
- Create notebooks by topic to keep your research organized
- Export insights back to the lab's PKB for the wiki and knowledge base
"""
        if _add_text_source(port, nb_start, "Welcome to Open Notebook", welcome):
            sources_added += 1

        # Add the "Attention Is All You Need" paper as a URL source
        _add_url_source(
            port, nb_start,
            "https://arxiv.org/abs/1706.03762",
            "Attention Is All You Need (Vaswani et al., 2017)",
        )
        sources_added += 1

        # Add the Open Notebook project itself
        _add_url_source(
            port, nb_start,
            "https://github.com/lfnovo/open-notebook",
            "Open Notebook — GitHub Repository",
        )
        sources_added += 1

    result["sources"] = sources_added
    result["seeded"] = True
    log.info(
        "Seeded Open Notebook: %d notebooks, %d sources.",
        result["notebooks"], result["sources"],
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = seed()
    if result["seeded"]:
        print(f"Done — {result['notebooks']} notebooks, {result['sources']} sources.")
    elif result["skipped"]:
        print("Skipped (already seeded or API not available).")
