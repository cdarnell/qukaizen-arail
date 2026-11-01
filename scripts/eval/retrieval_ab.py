#!/usr/bin/env python3
"""A/B retrieval harness: ``hash_embedding`` vs ``arail.dbspec.embed`` (nomic).

See ``sprints/2026-08-08-arail2-tier1-integration/ARCHITECTURE.md`` — this
implements interface contract H1. It is a **read-only** measurement tool: it
never writes to a live ``.cache/lancedb``, never calls ``pkb.index_all``,
never imports ``pkb_index``, and makes no network call other than loopback
Ollama (for the nomic arm).

Usage:
    retrieval_ab.py --dump-corpus [--world SLUG ...] [--lab-root PATH]
    retrieval_ab.py --arm both [--lab-root PATH] [--workdir PATH]
                     [--json OUT] [--md OUT]
    retrieval_ab.py --verify-manifest [--lab-root PATH]

Definitions (fixed here, not chosen after seeing the data):
    recall@5   = fraction of queries with >=1 labelled-relevant path in the
                 top 5 hits.
    pooled     = micro-average over ALL queries (not mean of per-world means).
    delta      = nomic recall@5 - hash recall@5, in percentage points.
    rank-1 loss = an exact-token query whose expected_path is rank 1 under
                 hash and not rank 1 under nomic. Ties broken by ascending
                 path (deterministic).
    Search uses k=5 for recall@5 and k=10 for MRR@10, min_score=0.0, no
    `where` clause, approved gate off (raw corpus).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from arail import pkb  # noqa: E402
from arail.vector_index import _TOKEN_RE, hash_embedding  # noqa: E402
from arail.dbspec import embed as embed_mod  # noqa: E402
from arail.dbspec.generated.models_registry import (  # noqa: E402
    EMBEDDING_DIM, embedding_model,
)

SCHEMA = "arail.retrieval_ab/v1"
WORLDS = ["root", "ai", "video-games", "debt-finance", "qukaizen"]
EVAL_DIR = REPO_ROOT / "eval" / "retrieval"
STOPWORDS_PATH = EVAL_DIR / "stopwords.txt"
BOOTSTRAP_SEED = 20260808
BOOTSTRAP_RESAMPLES = 10_000
GATE_PP = 15.0

_DIST_EPS = 1e-9


# --------------------------------------------------------------------------
# corpus reading (read-only)
# --------------------------------------------------------------------------

def world_pkb_root(lab_root: Path, world: str) -> Path:
    if world not in WORLDS:
        raise ValueError(f"unknown world {world!r}; expected one of {WORLDS}")
    if world == "root":
        return lab_root / "pkb"
    return lab_root / "instances" / world / "pkb"


def build_embed_input(name: str, rel: str, text: str) -> str:
    """Byte-identical to ``pkb.index_all``'s row construction (pkb.py:524)."""
    return f"{name} {rel} {text[:4096]}"


@dataclass
class Row:
    world: str
    path: str            # pkb-root-relative POSIX
    name: str
    source_kind: str
    bytes: int
    text: str             # full file text — kept in memory only, never written
    embed_input: str


def iter_world_rows(lab_root: Path, world: str) -> list[Row]:
    """Read one world's corpus read-only. [] if the world's root is absent."""
    root = world_pkb_root(lab_root, world)
    if not root.exists():
        return []
    rows: list[Row] = []
    for p, text in pkb._iter_pkb_files(root):
        rel = p.relative_to(root).as_posix()
        embed_input = build_embed_input(p.name, rel, text)
        rows.append(Row(
            world=world,
            path=rel,
            name=p.name,
            source_kind=pkb._source_kind_for_rel(rel),
            bytes=len(text.encode("utf-8")),
            text=text,
            embed_input=embed_input,
        ))
    return rows


def read_all_rows(lab_root: Path, worlds: list[str]) -> dict[str, list[Row]]:
    return {w: iter_world_rows(lab_root, w) for w in worlds}


# --------------------------------------------------------------------------
# corpus manifest (H2) — no document text
# --------------------------------------------------------------------------

def build_manifest(rows_by_world: dict[str, list[Row]]) -> dict[str, Any]:
    entries = []
    for world, rows in rows_by_world.items():
        for r in rows:
            entries.append({
                "world": r.world,
                "path": r.path,
                "name": r.name,
                "source_kind": r.source_kind,
                "bytes": r.bytes,
                "sha256": hashlib.sha256(r.embed_input.encode("utf-8")).hexdigest(),
            })
    entries.sort(key=lambda e: (e["world"], e["path"]))
    return {"schema": "arail.retrieval_ab.corpus_manifest/v1", "rows": entries}


def manifest_sha256(manifest: dict[str, Any]) -> str:
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def write_manifest(lab_root: Path, worlds: list[str], out_path: Path) -> dict[str, Any]:
    rows_by_world = read_all_rows(lab_root, worlds)
    manifest = build_manifest(rows_by_world)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def verify_manifest(lab_root: Path, worlds: list[str], manifest_path: Path) -> int:
    """Compare the live corpus against a committed manifest. Returns exit code."""
    if not manifest_path.exists():
        print(f"error: manifest not found at {manifest_path}", file=sys.stderr)
        return 2
    committed = json.loads(manifest_path.read_text())
    committed_rows = {(e["world"], e["path"]): e for e in committed["rows"]}
    live_rows_by_world = read_all_rows(lab_root, worlds)
    live_manifest = build_manifest(live_rows_by_world)
    live_rows = {(e["world"], e["path"]): e for e in live_manifest["rows"]}

    added = sorted(set(live_rows) - set(committed_rows))
    removed = sorted(set(committed_rows) - set(live_rows))
    changed = sorted(
        k for k in (set(live_rows) & set(committed_rows))
        if live_rows[k]["sha256"] != committed_rows[k]["sha256"]
    )
    if not (added or removed or changed):
        print("corpus matches the committed manifest exactly.")
        return 0
    print("corpus DIFFERS from the committed manifest:")
    for k in added:
        print(f"  + added:   {k[0]}/{k[1]}")
    for k in removed:
        print(f"  - removed: {k[0]}/{k[1]}")
    for k in changed:
        print(f"  ~ changed: {k[0]}/{k[1]}")
    return 1


# --------------------------------------------------------------------------
# --dump-corpus (F1.1: ground truth comes from reading the document)
# --------------------------------------------------------------------------

def dump_corpus(lab_root: Path, worlds: list[str]) -> None:
    for world in worlds:
        rows = iter_world_rows(lab_root, world)
        for r in rows:
            preview = r.text[:800].replace("\n", "\\n")
            print(f"{world} · {r.path} · {r.name} · {preview}")


# --------------------------------------------------------------------------
# workdir safety guard (FM5)
# --------------------------------------------------------------------------

_UNSAFE_WORKDIR_RE = re.compile(r"(^|/)pkb/\.cache/lancedb(/|$)|(^|/)\.wiki-cache(/|$)")


def assert_safe_workdir(workdir: Path) -> None:
    resolved = workdir.resolve()
    if _UNSAFE_WORKDIR_RE.search(resolved.as_posix()):
        print(
            f"error: --workdir {workdir} resolves to {resolved}, which is "
            f"under a live PKB cache path (*/pkb/.cache/lancedb or "
            f"*/.wiki-cache/). Refusing to write there — use a scratch dir "
            f"such as lab/.eval-cache/.",
            file=sys.stderr,
        )
        sys.exit(2)


# --------------------------------------------------------------------------
# arm parity guard (FM6)
# --------------------------------------------------------------------------

def assert_arm_parity(rows_a: list[dict], rows_b: list[dict]) -> None:
    """Both arms must be built from one shared row list — non-vector fields
    identical row-for-row. Raises RuntimeError (harness aborts, no write)."""
    if len(rows_a) != len(rows_b):
        raise RuntimeError(
            f"arm parity violated: {len(rows_a)} vs {len(rows_b)} rows")
    for a, b in zip(rows_a, rows_b):
        for key in ("path", "name", "source_kind"):
            if a[key] != b[key]:
                raise RuntimeError(
                    f"arm parity violated at path {a.get('path')!r}: "
                    f"{key} differs ({a[key]!r} vs {b[key]!r})")


# --------------------------------------------------------------------------
# scratch index build + kNN search (own scratch indexes — C6, "Never" list)
# --------------------------------------------------------------------------

def _score_from_distance(dist: float) -> float:
    """Same transform as vector_index.VectorIndex.search (A4)."""
    return max(0.0, min(1.0, 1.0 - dist / 2.0))


def build_scratch_index(workdir: Path, arm: str, world: str,
                         rows: list[Row], vectors: list[list[float]]):
    import lancedb  # local import: optional dep, keeps --dump-corpus lancedb-free

    assert_safe_workdir(workdir)
    db_path = workdir / arm / world
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    data = []
    for r, v in zip(rows, vectors):
        data.append({
            "path": r.path,
            "name": r.name,
            "source_kind": r.source_kind,
            "vector": v,
        })
    if not data:
        return None
    try:
        db.create_table("pkb_pages", data=data, mode="overwrite")
    except TypeError:
        db.create_table("pkb_pages", data=data)
    return db


def knn_search(db, query_vector: list[float], k: int) -> list[dict[str, Any]]:
    if db is None:
        return []
    table = db.open_table("pkb_pages")
    hits = table.search(query_vector).limit(max(1, k)).to_list()
    out = []
    for h in hits:
        dist = float(h.get("_distance", 2.0))
        out.append({
            "path": h.get("path"),
            "name": h.get("name"),
            "score": round(_score_from_distance(dist), 4),
            "_distance": dist,
        })
    return out


# --------------------------------------------------------------------------
# lexical-overlap strata (F1.4)
# --------------------------------------------------------------------------

def _load_stopwords() -> set[str]:
    if not STOPWORDS_PATH.exists():
        return set()
    return {
        line.strip().lower()
        for line in STOPWORDS_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _content_tokens(text: str, stopwords: set[str]) -> set[str]:
    toks = _TOKEN_RE.findall((text or "").lower())
    return {t for t in toks if t not in stopwords}


def jaccard_overlap(query: str, doc_texts: list[str], stopwords: set[str]) -> float:
    q_toks = _content_tokens(query, stopwords)
    d_toks: set[str] = set()
    for t in doc_texts:
        d_toks |= _content_tokens(t, stopwords)
    union = q_toks | d_toks
    if not union:
        return 0.0
    return len(q_toks & d_toks) / len(union)


def overlap_stratum(overlap: float) -> str:
    if overlap == 0.0:
        return "zero"
    if overlap < 0.05:
        return "low"
    return "high"


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def recall_at_k(hits: list[dict[str, Any]], relevant_paths: set[str], k: int) -> bool:
    top = {h["path"] for h in hits[:k]}
    return bool(top & relevant_paths)


def reciprocal_rank(hits: list[dict[str, Any]], relevant_paths: set[str], k: int) -> float:
    for i, h in enumerate(hits[:k], start=1):
        if h["path"] in relevant_paths:
            return 1.0 / i
    return 0.0


def rank1_path(hits: list[dict[str, Any]]) -> str | None:
    if not hits:
        return None
    # Deterministic tie-break: ascending path among ties at the top score.
    top_score = hits[0]["score"]
    tied = [h for h in hits if h["score"] == top_score]
    tied.sort(key=lambda h: h["path"])
    return tied[0]["path"]


def paired_bootstrap_ci(per_query_hash: list[bool], per_query_nomic: list[bool],
                         *, resamples: int = BOOTSTRAP_RESAMPLES,
                         seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """95% CI on Delta = nomic_recall - hash_recall via paired bootstrap
    over queries. Deterministic from the committed seed."""
    n = len(per_query_hash)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(seed)
    deltas = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        h = sum(per_query_hash[i] for i in idx) / n
        m = sum(per_query_nomic[i] for i in idx) / n
        deltas.append((m - h) * 100.0)
    deltas.sort()
    lo = deltas[int(0.025 * resamples)]
    hi = deltas[min(resamples - 1, int(0.975 * resamples))]
    return (lo, hi)


# --------------------------------------------------------------------------
# fixture loading
# --------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    import yaml  # PyYAML — already a dep (models_catalog.yaml etc.)
    return yaml.safe_load(path.read_text())


def load_queries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = _load_yaml(path)
    return data if isinstance(data, list) else []


def load_exact_tokens(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = _load_yaml(path)
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------
# git / environment stamps
# --------------------------------------------------------------------------

def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip()
    except Exception:
        return "unknown"


def lancedb_version() -> str:
    try:
        import lancedb
        return getattr(lancedb, "__version__", "unknown")
    except Exception:
        return "not-installed"


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def embed_arm_vectors(arm: str, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
    """Returns (vectors, throughput_stats). Raises EmbeddingError for nomic
    on a provider outage — caller must write no partial results (FM7)."""
    stats: dict[str, Any] = {"rows": len(texts), "batch_latencies_sec": []}
    if not texts:
        stats["rows_per_sec"] = 0.0
        return [], stats

    start = time.monotonic()
    if arm == "hash":
        vectors = [hash_embedding(t) for t in texts]
    elif arm == "nomic":
        batch = embed_mod._BATCH
        vectors = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            t0 = time.monotonic()
            vectors.extend(embed_mod.embed_documents(chunk))
            stats["batch_latencies_sec"].append(time.monotonic() - t0)
    else:
        raise ValueError(f"unknown arm {arm!r}")
    elapsed = time.monotonic() - start
    stats["wall_clock_sec"] = elapsed
    stats["rows_per_sec"] = (len(texts) / elapsed) if elapsed > 0 else float("inf")
    if stats["batch_latencies_sec"]:
        lat = sorted(stats["batch_latencies_sec"])
        stats["batch_p50_sec"] = lat[len(lat) // 2]
        stats["batch_p95_sec"] = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
    return vectors, stats


def run(args: argparse.Namespace) -> int:
    lab_root = Path(args.lab_root)
    worlds = args.world or WORLDS
    workdir = Path(args.workdir) if args.workdir else Path(
        __import__("os").environ.get("ARAIL_EVAL_WORKDIR", str(REPO_ROOT / "lab" / ".eval-cache")))
    assert_safe_workdir(workdir)

    stopwords = _load_stopwords()
    rows_by_world = read_all_rows(lab_root, worlds)
    manifest = build_manifest(rows_by_world)
    manifest_hash = manifest_sha256(manifest)

    queries_path = Path(args.queries) if getattr(args, "queries", None) else EVAL_DIR / "queries.yaml"
    exact_path = Path(args.exact_tokens) if getattr(args, "exact_tokens", None) else EVAL_DIR / "exact_tokens.yaml"
    queries = load_queries(queries_path)
    exact_tokens = load_exact_tokens(exact_path)
    queries_sha = hashlib.sha256(queries_path.read_bytes()).hexdigest() if queries_path.exists() else None
    exact_sha = hashlib.sha256(exact_path.read_bytes()).hexdigest() if exact_path.exists() else None

    # Index rows by (world, path) for evidence/overlap lookups.
    rows_index: dict[tuple[str, str], Row] = {}
    for w, rows in rows_by_world.items():
        for r in rows:
            rows_index[(w, r.path)] = r

    arms = ["hash", "nomic"] if args.arm == "both" else [args.arm]

    # Build both arms' scratch indexes from ONE shared row list per world (FM6).
    dbs: dict[str, dict[str, Any]] = {arm: {} for arm in arms}
    throughput: dict[str, dict[str, Any]] = {arm: {} for arm in arms}
    world_row_lists: dict[str, list[dict[str, Any]]] = {}

    for world in worlds:
        rows = rows_by_world.get(world, [])
        world_row_lists[world] = [
            {"path": r.path, "name": r.name, "source_kind": r.source_kind}
            for r in rows
        ]
        texts = [r.embed_input for r in rows]
        for arm in arms:
            try:
                vectors, stats = embed_arm_vectors(arm, texts)
            except embed_mod.EmbeddingError as exc:
                print(f"error: {arm} embedding failed for world {world!r}: {exc}",
                      file=sys.stderr)
                return 1  # write NOTHING (FM7)
            throughput[arm][world] = stats
            dbs[arm][world] = build_scratch_index(workdir, arm, world, rows, vectors)

    # Arm parity (FM6) is structural here: both arms are built from the same
    # `rows`/`texts` per world in the loop above (only the vectors differ),
    # so there is no separate per-arm row list to diverge. assert_arm_parity
    # is exercised directly in tests/eval/test_retrieval_ab.py against
    # synthetic inputs to pin the invariant it encodes.

    # ---- score NL queries ----
    per_query_results: list[dict[str, Any]] = []
    per_arm_per_query_hit: dict[str, list[bool]] = {arm: [] for arm in arms}
    for q in queries:
        world = q["world"]
        relevant_paths = {r["path"] for r in q["relevant"]}
        doc_texts = [
            rows_index[(world, r["path"])].embed_input
            for r in q["relevant"] if (world, r["path"]) in rows_index
        ]
        overlap = jaccard_overlap(q["query"], doc_texts, stopwords)
        stratum = overlap_stratum(overlap)
        entry: dict[str, Any] = {
            "id": q["id"], "world": world, "overlap": round(overlap, 4),
            "stratum": stratum,
        }
        for arm in arms:
            db = dbs[arm].get(world)
            if db is None:
                entry[f"{arm}_recall5"] = False
                entry[f"{arm}_rr10"] = 0.0
                per_arm_per_query_hit[arm].append(False)
                continue
            if arm == "hash":
                qvec = hash_embedding(q["query"])
            else:
                qvec = embed_mod.embed_query(q["query"])
            hits = knn_search(db, qvec, k=10)
            hit5 = recall_at_k(hits, relevant_paths, 5)
            rr10 = reciprocal_rank(hits, relevant_paths, 10)
            entry[f"{arm}_recall5"] = hit5
            entry[f"{arm}_rr10"] = rr10
            per_arm_per_query_hit[arm].append(hit5)
        per_query_results.append(entry)

    # ---- score exact-token queries (rank-1 only) ----
    exact_results: list[dict[str, Any]] = []
    for q in exact_tokens:
        world = q["world"]
        expected = q["expected_path"]
        entry = {"id": q["id"], "world": world, "expected_path": expected}
        for arm in arms:
            db = dbs[arm].get(world)
            if db is None:
                entry[f"{arm}_rank1"] = None
                continue
            if arm == "hash":
                qvec = hash_embedding(q["query"])
            else:
                qvec = embed_mod.embed_query(q["query"])
            hits = knn_search(db, qvec, k=1)
            entry[f"{arm}_rank1"] = rank1_path(hits)
        exact_results.append(entry)

    # ---- aggregate ----
    def pooled_recall(arm: str) -> float:
        vals = per_arm_per_query_hit[arm]
        return (sum(vals) / len(vals)) if vals else 0.0

    def per_world_recall(arm: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for world in worlds:
            hits = [e[f"{arm}_recall5"] for e in per_query_results if e["world"] == world]
            out[world] = (sum(hits) / len(hits)) if hits else 0.0
        return out

    def pooled_mrr(arm: str) -> float:
        vals = [e[f"{arm}_rr10"] for e in per_query_results]
        return (sum(vals) / len(vals)) if vals else 0.0

    strata_counts: dict[str, int] = {}
    strata_recall: dict[str, dict[str, float]] = {}
    for stratum in ("zero", "low", "high"):
        entries = [e for e in per_query_results if e["stratum"] == stratum]
        strata_counts[stratum] = len(entries)
        strata_recall[stratum] = {}
        for arm in arms:
            hits = [e[f"{arm}_recall5"] for e in entries]
            strata_recall[stratum][arm] = (sum(hits) / len(hits)) if hits else 0.0

    exact_rank1_summary: dict[str, Any] = {}
    for arm in arms:
        correct = sum(1 for e in exact_results if e.get(f"{arm}_rank1") == e["expected_path"])
        exact_rank1_summary[arm] = {"correct": correct, "total": len(exact_results)}

    rank1_losses = []
    if "hash" in arms and "nomic" in arms:
        for e in exact_results:
            hash_ok = e.get("hash_rank1") == e["expected_path"]
            nomic_ok = e.get("nomic_rank1") == e["expected_path"]
            if hash_ok and not nomic_ok:
                rank1_losses.append(e["id"])

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "git_sha": git_sha(),
        "lancedb_version": lancedb_version(),
        "embedding_model": embedding_model().name,
        "embedding_dim": EMBEDDING_DIM,
        "corpus_manifest_sha256": manifest_hash,
        "queries_sha256": queries_sha,
        "exact_tokens_sha256": exact_sha,
        "worlds": worlds,
        "row_counts": {w: len(rows_by_world.get(w, [])) for w in worlds},
        "arms": arms,
        "per_world_recall5": {arm: per_world_recall(arm) for arm in arms},
        "pooled_recall5": {arm: pooled_recall(arm) for arm in arms},
        "pooled_mrr10": {arm: pooled_mrr(arm) for arm in arms},
        "overlap_strata": {
            "counts": strata_counts,
            "recall5": strata_recall,
        },
        "exact_token_rank1": exact_rank1_summary,
        "rank1_losses": rank1_losses,
        "per_query": per_query_results,
        "exact_token_detail": exact_results,
        "throughput": throughput,
        "n_queries": len(queries),
        "n_exact_tokens": len(exact_tokens),
    }

    delta_pp = None
    ci = None
    verdict = None
    if "hash" in arms and "nomic" in arms:
        delta_pp = (pooled_recall("nomic") - pooled_recall("hash")) * 100.0
        ci = paired_bootstrap_ci(per_arm_per_query_hit["hash"], per_arm_per_query_hit["nomic"])
        gate_recall_ok = delta_pp >= GATE_PP
        gate_rank1_ok = len(rank1_losses) == 0
        if gate_recall_ok and gate_rank1_ok:
            verdict = "PASS" if ci[0] > 0 else "PASS_INCONCLUSIVE"
        else:
            verdict = "FAIL"
        result["delta_pp"] = delta_pp
        result["bootstrap_ci_95"] = {"lo": ci[0], "hi": ci[1], "seed": BOOTSTRAP_SEED,
                                      "resamples": BOOTSTRAP_RESAMPLES}
        result["verdict"] = verdict

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    if args.md:
        out_path = Path(args.md)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(result))

    print_summary(result)
    return 0


def render_markdown(result: dict[str, Any]) -> str:
    lines = ["# Retrieval A/B results", ""]
    lines.append(f"- git sha: `{result['git_sha']}`")
    lines.append(f"- embedding model: `{result['embedding_model']}` "
                 f"({result['embedding_dim']}d)")
    lines.append(f"- LanceDB: `{result['lancedb_version']}`")
    lines.append(f"- corpus manifest sha256: `{result['corpus_manifest_sha256']}`")
    lines.append(f"- queries.yaml sha256: `{result['queries_sha256']}`")
    lines.append(f"- exact_tokens.yaml sha256: `{result['exact_tokens_sha256']}`")
    lines.append(f"- row counts: {result['row_counts']}")
    lines.append("")
    lines.append("## Pooled recall@5")
    for arm, val in result["pooled_recall5"].items():
        lines.append(f"- {arm}: {val * 100:.1f}%")
    if "delta_pp" in result:
        lines.append(f"- **delta (nomic - hash): {result['delta_pp']:.1f}pp**")
        ci = result["bootstrap_ci_95"]
        lines.append(f"- 95% bootstrap CI on delta: [{ci['lo']:.1f}, {ci['hi']:.1f}]pp "
                     f"(seed {ci['seed']}, {ci['resamples']} resamples)")
        lines.append(f"- **verdict: {result['verdict']}**")
    lines.append("")
    lines.append("## Per-world recall@5")
    for arm, per_world in result["per_world_recall5"].items():
        lines.append(f"### {arm}")
        for world, val in per_world.items():
            lines.append(f"- {world}: {val * 100:.1f}%")
    lines.append("")
    lines.append("## Pooled MRR@10")
    for arm, val in result["pooled_mrr10"].items():
        lines.append(f"- {arm}: {val:.3f}")
    lines.append("")
    lines.append("## Overlap strata")
    lines.append(f"counts: {result['overlap_strata']['counts']}")
    for stratum, per_arm in result["overlap_strata"]["recall5"].items():
        lines.append(f"- {stratum}: " + ", ".join(
            f"{arm}={val * 100:.1f}%" for arm, val in per_arm.items()))
    lines.append("")
    lines.append("## Exact-token rank-1")
    for arm, s in result["exact_token_rank1"].items():
        lines.append(f"- {arm}: {s['correct']}/{s['total']}")
    if result.get("rank1_losses"):
        lines.append(f"- rank-1 losses (hash correct, nomic wrong): "
                     f"{result['rank1_losses']}")
    lines.append("")
    lines.append("## Throughput")
    for arm, per_world in result["throughput"].items():
        for world, stats in per_world.items():
            lines.append(f"- {arm}/{world}: {stats.get('rows', 0)} rows, "
                         f"{stats.get('rows_per_sec', 0):.1f} rows/s, "
                         f"wall {stats.get('wall_clock_sec', 0):.2f}s, "
                         f"batch p50={stats.get('batch_p50_sec', 0):.2f}s "
                         f"p95={stats.get('batch_p95_sec', 0):.2f}s")
    return "\n".join(lines) + "\n"


def print_summary(result: dict[str, Any]) -> None:
    print(f"schema: {result['schema']}  git: {result['git_sha']}")
    print(f"corpus manifest sha256: {result['corpus_manifest_sha256']}")
    print(f"row counts: {result['row_counts']}")
    for arm, val in result["pooled_recall5"].items():
        print(f"pooled recall@5 [{arm}]: {val * 100:.1f}%")
    if "delta_pp" in result:
        ci = result["bootstrap_ci_95"]
        print(f"delta (nomic - hash): {result['delta_pp']:.1f}pp  "
             f"95% CI [{ci['lo']:.1f}, {ci['hi']:.1f}]pp")
        print(f"exact-token rank-1 losses: {result['rank1_losses']}")
        print(f"VERDICT: {result['verdict']}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lab-root", default="lab",
                   help="Root containing pkb/ and instances/ (default: ./lab)")
    p.add_argument("--world", action="append", choices=WORLDS,
                   help="Restrict to one world; repeatable. Default: all 5.")
    p.add_argument("--arm", choices=["hash", "nomic", "both"], default="both")
    p.add_argument("--workdir", default=None,
                   help="Scratch dir for scratch LanceDB tables "
                        "(default: $ARAIL_EVAL_WORKDIR or lab/.eval-cache/)")
    p.add_argument("--dump-corpus", action="store_true",
                   help="Print path/name/preview for every row, then exit. "
                        "Mutates nothing.")
    p.add_argument("--verify-manifest", action="store_true",
                   help="Compare the live corpus against the committed "
                        "corpus_manifest.json and report differing rows.")
    p.add_argument("--write-manifest", action="store_true",
                   help="(Re)write eval/retrieval/corpus_manifest.json from "
                        "the live corpus.")
    p.add_argument("--json", default=None, help="Write results.json here.")
    p.add_argument("--md", default=None, help="Write a RESULTS.md block here.")
    p.add_argument("--queries", default=None,
                   help="Override eval/retrieval/queries.yaml (testing only).")
    p.add_argument("--exact-tokens", default=None,
                   help="Override eval/retrieval/exact_tokens.yaml (testing only).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lab_root = Path(args.lab_root)
    worlds = args.world or WORLDS

    if args.dump_corpus:
        dump_corpus(lab_root, worlds)
        return 0

    if args.write_manifest:
        manifest_path = EVAL_DIR / "corpus_manifest.json"
        write_manifest(lab_root, worlds, manifest_path)
        print(f"wrote {manifest_path}")
        return 0

    if args.verify_manifest:
        manifest_path = EVAL_DIR / "corpus_manifest.json"
        return verify_manifest(lab_root, worlds, manifest_path)

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
