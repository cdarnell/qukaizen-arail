"""Browser Agent — wraps agent-browser CLI for supervised web research.

Uses agent-browser to visit URLs, take screenshots, extract text,
and save findings to the PKB. Respects ARAIL_MODE: fully blocked
when airgapped, consent-gated when hybrid.

Prefers credible, long-running sources (arxiv, gov, edu, established
news/tech sites) over random websites.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from arail.activity import activity_log
from arail.config import DATA_DIR

log = logging.getLogger(__name__)

# ── Credible source tiers ────────────────────────────────────────────
# Prefer these domains when generating browse suggestions.
# Tier 1: academic, government, and authoritative reference
# Tier 2: established tech/science publications
# Tier 3: reputable community-curated sites
CREDIBLE_SOURCES: dict[str, list[str]] = {
    "tier1": [
        "arxiv.org", "scholar.google.com", "pubmed.ncbi.nlm.nih.gov",
        "ieee.org", "acm.org", "nature.com", "science.org",
        "gov", "edu", "nist.gov", "nih.gov",
    ],
    "tier2": [
        "huggingface.co", "paperswithcode.com", "github.com",
        "stackoverflow.com", "wikipedia.org", "reuters.com",
        "apnews.com", "bbc.com", "npr.org",
    ],
    "tier3": [
        "slashdot.org", "arstechnica.com", "hackernews.com",
        "news.ycombinator.com", "theregister.com", "wired.com",
        "techcrunch.com",
    ],
}

SCREENSHOT_DIR = DATA_DIR / "browser" / "screenshots"
EXTRACT_DIR = DATA_DIR / "browser" / "extracts"


def _is_airgapped() -> bool:
    from arail.airgap import is_airgapped
    return is_airgapped()


def _consent_gate(url: str, *, reason: str) -> dict[str, Any] | None:
    """Per-domain consent check for a browse target.

    Returns None when the domain is on the user's allowlist (proceed).
    Otherwise creates ONE pending consent request (idempotent per domain) and
    returns an 'awaiting consent' result dict — the caller must NOT fetch.
    This makes the module's documented "consent-gated when hybrid" contract
    real: browsing a new domain waits for the user's explicit approval.
    """
    try:
        from urllib.parse import urlparse
        from arail.agents.consent import ConsentStore
        store = ConsentStore()
        if store.is_allowed(url):
            return None
        domain = urlparse(url).netloc or url
        if not any(r.get("domain") == domain for r in store.list_pending()):
            store.request_access(url, reason=reason, agent="browser")
        activity_log.emit(
            "browser",
            f"Web access to {domain} needs your approval — approve it on the "
            "Agents page to let the Browser agent continue.", "info")
        return {
            "success": False,
            "awaiting_consent": True,
            "domain": domain,
            "error": f"Awaiting your approval to reach {domain}. Approve web "
                     "access on the Agents page, then retry.",
        }
    except Exception:  # noqa: BLE001 — never fail open into a silent fetch
        return {
            "success": False,
            "awaiting_consent": True,
            "error": "Web access needs consent, which could not be recorded.",
        }


def _ab_available() -> bool:
    return shutil.which("agent-browser") is not None


def _ab_env() -> dict[str, str]:
    """Build env vars for agent-browser CLI commands."""
    return {
        **os.environ,
        "AGENT_BROWSER_SCREENSHOT_DIR": str(SCREENSHOT_DIR),
        "AGENT_BROWSER_MAX_OUTPUT": "80000",
        "AGENT_BROWSER_DEFAULT_TIMEOUT": "30000",
    }


def _ab_run(args: list[str], timeout: int = 60) -> dict[str, Any]:
    """Run an agent-browser command and return JSON result."""
    try:
        result = subprocess.run(
            ["agent-browser"] + args + ["--json"],
            capture_output=True, text=True, timeout=timeout,
            env=_ab_env(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return {"success": False, "error": result.stderr[:500] or "unknown error"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def browse_url(url: str) -> dict[str, Any]:
    """Navigate to a URL, take a screenshot, extract text.

    Returns dict with keys: success, url, title, text, screenshot_path, timestamp.
    Blocked when airgapped; consent-gated per domain when hybrid.
    """
    if _is_airgapped():
        return {
            "success": False,
            "error": "Blocked — lab is in airgapped mode. Toggle to Hybrid to enable browsing.",
            "airgapped": True,
        }
    gate = _consent_gate(url, reason=f"Browse {url} for research")
    if gate is not None:
        return gate
    if not _ab_available():
        return {
            "success": False,
            "error": "agent-browser not installed. Run: npm install -g agent-browser && agent-browser install",
        }

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    activity_log.emit("browser", f"Browsing: {url}", "info")

    # Open the page
    open_result = _ab_run(["open", url], timeout=30)
    if not open_result.get("success", True):
        return {"success": False, "error": open_result.get("error", "Failed to open URL")}

    # Get title
    title_result = _ab_run(["get", "title"])
    title = title_result.get("data", url) if title_result.get("success") else url

    # Take screenshot
    screenshot_name = f"{ts}_{_sanitize(title)}.png"
    screenshot_path = SCREENSHOT_DIR / screenshot_name
    _ab_run(["screenshot", str(screenshot_path), "--full", "--annotate"])

    # Extract page text
    text_result = _ab_run(["get", "text"], timeout=15)
    page_text = text_result.get("data", "") if text_result.get("success") else ""

    # Get interactive snapshot for structured data
    snap_result = _ab_run(["snapshot", "-i"], timeout=15)

    # Save extract to PKB
    extract_path = EXTRACT_DIR / f"{ts}_{_sanitize(title)}.md"
    extract_content = f"""# {title}

**Source:** {url}
**Captured:** {datetime.now().isoformat()}

## Extracted Text

{page_text[:10000]}
"""
    extract_path.write_text(extract_content, encoding="utf-8")

    activity_log.emit("browser", f"Captured: {title}", "info",
                      {"url": url, "screenshot": str(screenshot_path)})

    return {
        "success": True,
        "url": url,
        "title": title,
        "text": page_text[:5000],
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        "extract_path": str(extract_path),
        "snapshot": snap_result.get("data") if snap_result.get("success") else None,
        "timestamp": ts,
    }


def _get_router():
    """Resolve the fast (Tier 0) router via the model registry."""
    from arail.registry import resolve
    router = resolve("fast", tab="agents").router()
    if router is None:
        raise RuntimeError("no usable model for the 'fast' profile "
                           "(see the model status banner)")
    return router


_NAVIGATE_PROMPT = """You are a browser research agent. Given a user instruction, output a JSON object with two keys:
- "url": the URL to open first
- "search_term": the text to search for on that site (or null if just visiting)

Example: user says "Go to slashdot.org and search for karpathy"
Output: {{"url": "https://slashdot.org", "search_term": "karpathy"}}

Example: user says "Visit arxiv.org and find papers about transformers"
Output: {{"url": "https://arxiv.org", "search_term": "transformers"}}

Example: user says "Go to wikipedia.org and get an overview of RLHF"
Output: {{"url": "https://en.wikipedia.org", "search_term": "RLHF"}}

Return ONLY the JSON object, no other text.

User instruction: {instruction}"""

_INTERACT_PROMPT = """You are a browser agent. You have opened a web page and received a snapshot of its interactive elements.

Your task: {task}

Here are the interactive elements on the page:
{snapshot}

Output a JSON array of commands to perform. Available commands:
- ["fill", "<ref>", "<text>"] — type text into a form field (ref is like @e5)
- ["click", "<ref>"] — click an element (button, link, etc.)

Look at the snapshot carefully. Find the search/input field and the submit button.
If there is a search input, fill it with the search term, then click the submit button.
If there is no obvious search field, look for a link or navigation element related to the task.

Return ONLY the JSON array, no other text. Example:
[["fill", "@e3", "karpathy"], ["click", "@e4"]]"""


def chat(instruction: str) -> dict[str, Any]:
    """Run a natural-language browser task using the lab's built-in model.

    Uses a two-phase approach:
      Phase 1: Navigate to the URL
      Phase 2: Observe the page (snapshot), then plan interaction using real element refs

    Returns executed commands so the UI can show users exactly what happened.

    Example: "Go to slashdot.org, search for 'Karpathy', list the top article titles."
    """
    if _is_airgapped():
        return {
            "success": False,
            "error": "Blocked — lab is in airgapped mode. Toggle to Hybrid to enable browsing.",
            "airgapped": True,
        }
    if not _ab_available():
        return {
            "success": False,
            "error": "agent-browser not installed. Run: npm install -g agent-browser && agent-browser install",
        }

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    activity_log.emit("browser", f"Research: {instruction[:120]}", "info")

    try:
        router = _get_router()
    except Exception as e:
        return {"success": False, "error": f"Model not available: {e}"}

    # Track every command for transparency
    executed_commands: list[dict[str, Any]] = []

    def run_and_log(cmd: list[str], timeout: int = 30) -> dict[str, Any]:
        """Execute an agent-browser command, log it, and return result."""
        result = _ab_run(cmd, timeout=timeout)
        entry: dict[str, Any] = {
            "command": f"agent-browser {' '.join(cmd)}",
            "success": result.get("success", True) if "error" not in result else False,
        }
        if result.get("error"):
            entry["error"] = result["error"]
        executed_commands.append(entry)
        return result

    # ── Phase 1: Figure out where to go ──────────────────────────────
    import time as _time
    prompt = _NAVIGATE_PROMPT.format(instruction=instruction)
    try:
        from arail import agent_context
        t0 = _time.monotonic()
        # L3 (ARCHITECTURE.md): browser is a top-level module the loader
        # never touches -- each of its three model-acquisition call sites
        # (navigate/interact/summarize) gets its own explicit wrapper.
        with agent_context.agent_call("browser"):
            resp = router.complete(prompt, max_tokens=256, temperature=0.2)
        elapsed = (_time.monotonic() - t0) * 1000
        nav_text = resp.text.strip()

        # W4/V5 (ARCHITECTURE.md): activity.jsonl no longer carries prompt/
        # response bodies -- metadata only; bodies live only in the flight
        # recorder, off by default.
        activity_log.emit("browser",
                          f"Navigation plan ({int(elapsed)}ms)",
                          "info", {
                              "prompt_trace": {
                                  "max_tokens": 256,
                                  "latency_ms": round(elapsed, 1),
                              }
                          })

        # Extract JSON
        start = nav_text.find("{")
        end = nav_text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("No JSON object in model response")
        nav = json.loads(nav_text[start:end])
        url = nav.get("url", "")
        search_term = nav.get("search_term")
    except Exception as e:
        log.warning("Failed to parse navigation plan: %s", e)
        url = _extract_url(instruction)
        search_term = None
        if not url:
            return {"success": False, "error": f"Could not determine URL from instruction: {e}"}

    # Per-domain consent gate — now that the target URL is resolved (Phase 1 is
    # a local model call, no egress), require the user's approval before the
    # actual fetch reaches the network.
    gate = _consent_gate(url, reason=f"Browser task: {instruction[:80]}")
    if gate is not None:
        gate["commands_executed"] = executed_commands
        return gate

    # Open the page
    open_result = run_and_log(["open", url], timeout=30)
    if open_result.get("success") is False:
        return {
            "success": False,
            "error": f"Failed to open {url}: {open_result.get('error', 'unknown')}",
            "commands_executed": executed_commands,
        }

    all_output: list[str] = []
    screenshot_path = None

    # ── Phase 2: Observe, then interact ──────────────────────────────
    # Get a snapshot of interactive elements
    snap_result = run_and_log(["snapshot", "-i"], timeout=15)
    snapshot_text = ""
    if snap_result.get("data"):
        snapshot_text = str(snap_result["data"])

    # If we need to search, use the snapshot to find real element refs
    if search_term and snapshot_text:
        interact_prompt = _INTERACT_PROMPT.format(
            task=f'Search for "{search_term}"',
            snapshot=snapshot_text[:4000],
        )
        try:
            from arail import agent_context
            t1 = _time.monotonic()
            with agent_context.agent_call("browser"):
                interact_resp = router.complete(interact_prompt, max_tokens=256, temperature=0.2)
            elapsed2 = (_time.monotonic() - t1) * 1000
            interact_text = interact_resp.text.strip()

            activity_log.emit("browser",
                              f"Interaction plan ({int(elapsed2)}ms)",
                              "info", {
                                  "prompt_trace": {
                                      "max_tokens": 256,
                                      "latency_ms": round(elapsed2, 1),
                                  }
                              })
            start = interact_text.find("[")
            end = interact_text.rfind("]") + 1
            if start >= 0 and end > start:
                interact_cmds = json.loads(interact_text[start:end])
                for cmd in interact_cmds:
                    if isinstance(cmd, list) and cmd:
                        run_and_log(cmd, timeout=15)

                # Wait for results page, then snapshot + extract
                run_and_log(["snapshot", "-i"], timeout=15)
        except Exception as e:
            log.warning("Failed to plan interaction: %s — falling back to page text", e)

    # Extract page text
    text_result = run_and_log(["get", "text"], timeout=15)
    page_text = text_result.get("data", "") if text_result.get("success") else ""
    if page_text and isinstance(page_text, str) and len(page_text) > 50:
        all_output.append(page_text)

    # Take screenshot
    sc_name = f"{ts}_research.png"
    sc_path = SCREENSHOT_DIR / sc_name
    sc_result = _ab_run(["screenshot", str(sc_path), "--full", "--annotate"], timeout=20)
    executed_commands.append({
        "command": f"agent-browser screenshot {sc_path} --full --annotate",
        "success": sc_path.exists(),
    })
    if sc_path.exists():
        screenshot_path = sc_path

    combined = "\n\n".join(all_output)

    activity_log.emit("browser", f"Executed {len(executed_commands)} browser steps", "info")

    # ── Phase 3: Summarize findings ──────────────────────────────────
    if combined:
        summary_prompt = (
            f"The user asked: {instruction}\n\n"
            f"Here is what was found on the web page:\n\n{combined[:6000]}\n\n"
            f"Provide a clear, organized summary of the relevant information. "
            f"Include specific titles, links, and data points where available."
        )
        try:
            from arail import agent_context
            with agent_context.agent_call("browser"):
                summary_resp = router.complete(summary_prompt, max_tokens=1024, temperature=0.5)
            summary = summary_resp.text.strip()
        except Exception:
            summary = combined[:3000]
    else:
        summary = "(No text extracted from page)"

    # ── Phase 4: Save extract to PKB ─────────────────────────────────
    cmd_log = "\n".join(
        f"  {'✓' if c['success'] else '✗'} {c['command']}"
        + (f"  → {c['error']}" if c.get('error') else "")
        for c in executed_commands
    )
    extract_path = EXTRACT_DIR / f"{ts}_research_{_sanitize(instruction)}.md"
    extract_content = f"""# Research: {instruction[:100]}

**Task:** {instruction}
**Captured:** {datetime.now().isoformat()}
**Steps:** {len(executed_commands)} browser actions

## Commands Executed

```
{cmd_log}
```

## Summary

{summary}

## Raw Extract

{combined[:8000]}
"""
    extract_path.write_text(extract_content, encoding="utf-8")

    activity_log.emit("browser", f"Research complete: {instruction[:60]}", "info")

    return {
        "success": True,
        "output": summary,
        "screenshot_path": str(screenshot_path) if screenshot_path else None,
        "extract_path": str(extract_path),
        "commands_executed": executed_commands,
        "steps": len(executed_commands),
        "timestamp": ts,
    }


def _extract_url(text: str) -> str | None:
    """Try to pull a URL from an instruction string."""
    import re
    # Look for explicit URLs
    m = re.search(r'https?://\S+', text)
    if m:
        return m.group(0).rstrip('.,;)')
    # Look for domain mentions
    m = re.search(r'(?:go to|visit|open|check)\s+(\S+\.\w{2,})', text, re.IGNORECASE)
    if m:
        domain = m.group(1).rstrip('.,;)')
        return f"https://{domain}" if not domain.startswith("http") else domain
    return None


def generate_suggestions(goal_text: str, domain: str = "general") -> list[dict[str, str]]:
    """Generate browse suggestions based on the current goal.

    Returns a list of {url, title, reason, tier} dicts, preferring
    credible sources.
    """
    suggestions = []

    # Clean goal text for search queries
    query = goal_text.strip()[:100]

    # Domain-specific credible source suggestions
    domain_sources: dict[str, list[dict[str, str]]] = {
        "ml-research": [
            {"url": f"https://arxiv.org/search/?query={_urlq(query)}&searchtype=all",
             "title": "arXiv — academic papers",
             "reason": "Peer-reviewed research papers", "tier": "tier1"},
            {"url": f"https://paperswithcode.com/search?q={_urlq(query)}",
             "title": "Papers With Code",
             "reason": "Papers with reproducible implementations", "tier": "tier1"},
            {"url": f"https://huggingface.co/models?search={_urlq(query)}",
             "title": "Hugging Face Models",
             "reason": "Pre-trained models and datasets", "tier": "tier2"},
            {"url": f"https://scholar.google.com/scholar?q={_urlq(query)}",
             "title": "Google Scholar",
             "reason": "Academic citation search", "tier": "tier1"},
            {"url": f"https://news.ycombinator.com/item?id=search&q={_urlq(query)}",
             "title": "Hacker News",
             "reason": "Community discussion and links", "tier": "tier3"},
        ],
        "farming": [
            {"url": f"https://quickstats.nass.usda.gov/",
             "title": "USDA Quick Stats",
             "reason": "Official agricultural statistics", "tier": "tier1"},
            {"url": f"https://scholar.google.com/scholar?q={_urlq(query)}",
             "title": "Google Scholar",
             "reason": "Agricultural research papers", "tier": "tier1"},
            {"url": f"https://www.ncei.noaa.gov/",
             "title": "NOAA Climate Data",
             "reason": "Weather and climate data", "tier": "tier1"},
        ],
        "business": [
            {"url": f"https://scholar.google.com/scholar?q={_urlq(query)}",
             "title": "Google Scholar",
             "reason": "Business and economics research", "tier": "tier1"},
            {"url": f"https://www.reuters.com/search/news?query={_urlq(query)}",
             "title": "Reuters",
             "reason": "Authoritative business news", "tier": "tier2"},
        ],
        "trade": [
            {"url": f"https://scholar.google.com/scholar?q={_urlq(query)}",
             "title": "Google Scholar",
             "reason": "Materials science and trade technique research", "tier": "tier1"},
            {"url": f"https://www.osha.gov/search?query={_urlq(query)}",
             "title": "OSHA",
             "reason": "Occupational safety standards and best practices", "tier": "tier1"},
            {"url": f"https://duckduckgo.com/?q={_urlq(query)}+site%3Areddit.com",
             "title": "Reddit (trade community)",
             "reason": "Practitioner discussions and field experience", "tier": "tier2"},
        ],
    }

    # Always include general credible sources
    general = [
        {"url": f"https://en.wikipedia.org/w/index.php?search={_urlq(query)}",
         "title": "Wikipedia",
         "reason": "Encyclopedic overview", "tier": "tier2"},
        {"url": f"https://scholar.google.com/scholar?q={_urlq(query)}",
         "title": "Google Scholar",
         "reason": "Academic research", "tier": "tier1"},
        {"url": f"https://slashdot.org/index2.pl?fhfilter={_urlq(query)}",
         "title": "Slashdot",
         "reason": "Tech news and discussion", "tier": "tier3"},
    ]

    # Build suggestions: domain-specific first, then general (deduped)
    seen_hosts = set()
    for s in domain_sources.get(domain, []) + general:
        from urllib.parse import urlparse
        host = urlparse(s["url"]).hostname or ""
        if host not in seen_hosts:
            suggestions.append(s)
            seen_hosts.add(host)

    return suggestions


def _sanitize(text: str) -> str:
    """Sanitize a string for use in filenames."""
    import re
    return re.sub(r'[^\w\-]', '_', text.lower())[:60]


def _urlq(text: str) -> str:
    """URL-encode a query string."""
    from urllib.parse import quote_plus
    return quote_plus(text)
