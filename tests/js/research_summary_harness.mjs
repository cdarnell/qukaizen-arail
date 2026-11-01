// JS-render harness for research.html's goal-scoped experiment summary.
//
// Pins the bug: GET /api/experiments returns the WHOLE corpus — every
// experiment ever created, across every goal and every World — sorted
// newest-first, with no filter. Before this fix, renderSummary() rendered
// the raw top-5 of that corpus under the header "Experiments testing your
// goal," so a brand-new goal that hadn't run yet could show five OTHER
// goals' experiments, marked ✓/✗ from THEIR runs, as if they belonged to
// the current one.
//
// The fix intersects RESEARCH.experiments against goal.experiments — the
// linked-ID list goal_store.link_experiment() has always populated but the
// template never read. Extracts the REAL goalScopedExperiments()/
// renderSummary() out of the live src/arail/portal/templates/research.html
// (not a reimplementation) and runs them through a minimal DOM shim.
//
// Run: node tests/js/research_summary_harness.mjs   (exit 0 = pass)

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const TPL = join(REPO_ROOT, "src/arail/portal/templates/research.html");
const tplText = readFileSync(TPL, "utf8");

function extractFunction(signature) {
  const idx = tplText.indexOf(signature);
  if (idx === -1) {
    console.error("FAIL: could not locate " + JSON.stringify(signature) + " in research.html");
    process.exit(2);
  }
  let i = tplText.indexOf("{", idx);
  let depth = 0, j = i;
  for (; j < tplText.length; j++) {
    if (tplText[j] === "{") depth++;
    else if (tplText[j] === "}") { depth--; if (depth === 0) break; }
  }
  return tplText.slice(idx, j + 1);
}

const escSrc = extractFunction("function esc(");
const goalScopedSrc = extractFunction("function goalScopedExperiments(");
const renderSummarySrc = extractFunction("function renderSummary(");

// ── Minimal DOM shim — just enough for renderSummary's element reads ──────
class ShimNode {
  constructor() {
    this.hidden = false;
    this._html = "";
  }
  set innerHTML(v) { this._html = v; }
  get innerHTML() { return this._html; }
  set textContent(v) {
    // esc() (the real function under test) does exactly this round-trip
    // via a real <div> to get browser-correct HTML-entity escaping — this
    // mirrors that so esc() behaves identically inside the shim instead of
    // silently discarding every string (which made earlier substring
    // assertions here pass for the wrong reason: nothing was ever there).
    this._html = String(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  get textContent() { return this._html; }
  // renderSummary() wires click handlers on the rendered list after setting
  // innerHTML — this harness asserts on the rendered HTML/text, not on
  // click behavior, so an empty NodeList satisfies the real code's
  // subsequent .forEach() without needing a full DOM parse here.
  querySelectorAll() { return []; }
}

function makeSandbox() {
  const nodes = {
    "rx-summary": new ShimNode(),
    "rx-summary-meta": new ShimNode(),
    "rx-summary-body": new ShimNode(),
    "rx-summary-foot-meta": new ShimNode(),
  };
  const document = {
    getElementById: (id) => nodes[id] || null,
    createElement: () => new ShimNode(),
  };
  const sandbox = {
    document,
    console,
    RESEARCH: { goal: null, experiments: [] },
    // renderSummary()'s last line refreshes the separate "why these
    // hypotheses" alternatives panel — a different concern (Phase 3 part
    // 2) that this harness doesn't exercise. Stubbed so the REAL
    // renderSummary/goalScopedExperiments run unmodified.
    refreshPlanningTrace: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(escSrc, sandbox);
  vm.runInContext(goalScopedSrc, sandbox);
  vm.runInContext(renderSummarySrc, sandbox);
  return { sandbox, nodes };
}

let failures = 0;
function assert(cond, msg) {
  if (!cond) { console.error("FAIL:", msg); failures++; }
}

function exp(id, hyp, status, supported) {
  return { id, hypothesis: hyp, status, hypothesis_supported: supported };
}

// ── T1: unlinked experiments from OTHER goals never render as "yours" ─────
// This is the exact field failure: a fresh goal with experiments: [] must
// not surface the corpus's 5 newest entries as if they tested this goal.
{
  const { sandbox, nodes } = makeSandbox();
  sandbox.RESEARCH.goal = {
    goal_text: "Find the best rates on loans to consolidate my debt",
    parsed: { domain: "Finance and Loan Consolidation" },
    experiments: [],  // nothing designed yet for THIS goal
  };
  sandbox.RESEARCH.experiments = [
    exp("44e91e0a", "Optimizing 'performant inference engine.' will contribute to: aeroLLM", "completed", true),
    exp("eb8be84e", "Optimizing 'performant inference engine.' will contribute to: aeroLLM", "completed", true),
    exp("6289ec94", "Focusing on learn optimization is key to: learn math", "completed", true),
  ];
  vm.runInContext("renderSummary()", sandbox);
  assert(!nodes["rx-summary"].hidden, "T1: summary section should be visible (goal is set)");
  assert(nodes["rx-summary-body"].innerHTML.includes("No experiments designed yet"),
    "T1: with zero linked experiments, body must say none are designed yet — got:\n" + nodes["rx-summary-body"].innerHTML);
  assert(!nodes["rx-summary-body"].innerHTML.includes("aeroLLM"),
    "T1 (the field bug): another goal's aeroLLM experiment must NOT render under this goal");
  assert(!nodes["rx-summary-body"].innerHTML.includes("learn math"),
    "T1 (the field bug): another goal's math experiment must NOT render under this goal");
}

// ── T2: linked experiments for the CURRENT goal do render, and only those ─
{
  const { sandbox, nodes } = makeSandbox();
  sandbox.RESEARCH.goal = {
    goal_text: "Find the best rates on loans to consolidate my debt",
    parsed: { domain: "Finance and Loan Consolidation" },
    experiments: ["aaa111", "bbb222"],
  };
  sandbox.RESEARCH.experiments = [
    // Newest-first, as the real /api/experiments endpoint returns —
    // includes two unrelated experiments from other goals interleaved.
    exp("zzz999", "unrelated goal's hypothesis", "completed", true),
    exp("bbb222", "Debt consolidation hypothesis B", "in_progress", null),
    exp("yyy888", "another unrelated hypothesis", "completed", false),
    exp("aaa111", "Debt consolidation hypothesis A", "completed", true),
  ];
  vm.runInContext("renderSummary()", sandbox);
  const body = nodes["rx-summary-body"].innerHTML;
  assert(body.includes("Debt consolidation hypothesis A"), "T2: linked experiment A must render");
  assert(body.includes("Debt consolidation hypothesis B"), "T2: linked experiment B must render");
  assert(!body.includes("unrelated goal's hypothesis"), "T2: unlinked zzz999 must not render");
  assert(!body.includes("another unrelated hypothesis"), "T2: unlinked yyy888 must not render");
  const foot = nodes["rx-summary-foot-meta"].textContent;
  assert(foot.includes("2 experiment"), "T2: footer count must reflect the SCOPED total (2), not the full list (4) — got: " + foot);
}

// ── T3: no goal at all -> section stays hidden, no crash ──────────────────
{
  const { sandbox, nodes } = makeSandbox();
  sandbox.RESEARCH.goal = null;
  sandbox.RESEARCH.experiments = [exp("x", "h", "completed", true)];
  vm.runInContext("renderSummary()", sandbox);
  assert(nodes["rx-summary"].hidden, "T3: no goal -> summary must stay hidden");
}

// ── T4: goalScopedExperiments is a pure function — direct unit coverage ───
{
  const { sandbox } = makeSandbox();
  const r1 = vm.runInContext(
    'goalScopedExperiments({experiments: ["a","b"]}, [{id:"a"},{id:"b"},{id:"c"}])',
    sandbox
  );
  assert(r1.length === 2 && r1[0].id === "a" && r1[1].id === "b",
    "T4: must return exactly the linked experiments, in corpus order");
  const r2 = vm.runInContext('goalScopedExperiments(null, [{id:"a"}])', sandbox);
  assert(Array.isArray(r2) && r2.length === 0, "T4: a null goal must yield an empty list, not throw");
  const r3 = vm.runInContext('goalScopedExperiments({experiments: []}, [{id:"a"}])', sandbox);
  assert(Array.isArray(r3) && r3.length === 0, "T4: an empty-but-present experiments array must yield empty");
}

if (failures > 0) {
  console.error(`${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("research-summary JS-render assertions passed");
