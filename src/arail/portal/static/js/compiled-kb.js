/* Compiled KB review queue — the human-approved gate.
 *
 * Raw corpus (world terms, forged/grown drafts, agent research/experiments,
 * notes) is a candidate POOL. This panel is where the human approves what
 * agents may experiment/develop against. Agents propose; you approve.
 *
 * All user-derived text is set via textContent (never innerHTML) — the F8
 * injection discipline. Writes ride the same-origin fetch (Sec-Fetch-Site:
 * same-origin) that the /api/pkb/* CSRF envelope requires.
 */
(function () {
  "use strict";
  const panel = document.getElementById("compiled-kb-panel");
  if (!panel) return;

  const KIND_LABEL = {
    world_term: "world term",
    agent_research: "research",
    agent_experiment: "experiment",
    agent_synthesis: "synthesis",
    agent_recommendation: "recommendation",
    agent_dream: "dream",
    note: "note",
    source: "source",
  };

  // `scope` is the World the queue is showing (e.g. "world-debt-finance"),
  // or null for the root lab's cross-World view. The server decides it from
  // the mount; the page only reports it.
  const state = { pending: [], approved: [], gate: true, selected: new Set(),
                  showApproved: false, scope: null };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  async function api(path, body) {
    const opt = { headers: { "content-type": "application/json" } };
    if (body) { opt.method = "POST"; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    if (!r.ok) throw new Error(path + " → " + r.status);
    return r.json();
  }

  async function load() {
    let data;
    try { data = await api("/api/pkb/review"); }
    catch (e) { return; }
    state.pending = data.pending || [];
    state.approved = data.approved || [];
    state.gate = !!data.gate_enabled;
    state.scope = data.scope || null;
    state.selected.clear();
    render();
    // Broadcast counts for page-level consumers (the World hero's
    // approved/pending stats) — saves them a duplicate review fetch.
    window.dispatchEvent(new CustomEvent("arail:kb-review-loaded", {
      detail: {
        pending: state.pending.length,
        approved: state.approved.length,
        gate: state.gate,
      },
    }));
  }

  function chip(kind, provenance, world) {
    const wrap = el("span", "ckb-chips");
    wrap.appendChild(el("span", "ckb-chip ckb-chip--kind", KIND_LABEL[kind] || kind));
    if (world) wrap.appendChild(el("span", "ckb-chip ckb-chip--world", world.replace(/^world-/, "")));
    wrap.appendChild(el("span", "ckb-chip ckb-chip--prov", provenance || "—"));
    return wrap;
  }

  function render() {
    panel.replaceChildren();
    // Hide entirely when there is nothing to review and nothing approved yet.
    if (!state.pending.length && !state.approved.length) { panel.hidden = true; return; }
    panel.hidden = false;

    // Header
    const head = el("div", "ckb-head");
    const title = el("div", "ckb-title");
    title.appendChild(el("span", "ckb-title-main", "Compiled Knowledge Base"));
    title.appendChild(el("span", "ckb-title-sub",
      "The approved layer your agents build on — raw is a candidate pool until you approve it."));
    // Scope line: what this queue is and is not. Without it, a mounted World
    // showing 0 candidates and the root lab showing 53 look like the same
    // surface disagreeing with itself.
    title.appendChild(el("span", "ckb-title-scope",
      state.scope
        ? "Showing " + state.scope.replace(/^world-/, "") + " only — knowledge from other Worlds is not listed here."
        : "Root lab · every World in this lab. Mount a World to narrow it to that World's knowledge."));
    head.appendChild(title);
    const gateBadge = el("span", "ckb-gate " + (state.gate ? "ckb-gate--on" : "ckb-gate--off"),
      state.gate ? "gate on · agents use approved only" : "gate off · agents use raw corpus");
    head.appendChild(gateBadge);
    panel.appendChild(head);

    // Pending queue
    if (state.pending.length) {
      const bar = el("div", "ckb-bar");
      bar.appendChild(el("span", "ckb-count", state.pending.length + " awaiting review"));
      const sel = el("button", "btn btn-sm btn-ghost", "Select all");
      sel.addEventListener("click", () => {
        if (state.selected.size === state.pending.length) state.selected.clear();
        else state.pending.forEach((p) => state.selected.add(p.path));
        render();
      });
      bar.appendChild(sel);
      const approveBtn = el("button", "btn btn-sm ckb-approve",
        "✓ Approve selected" + (state.selected.size ? " (" + state.selected.size + ")" : ""));
      approveBtn.disabled = !state.selected.size;
      approveBtn.addEventListener("click", () => act("/api/pkb/promote"));
      bar.appendChild(approveBtn);
      const rejectBtn = el("button", "btn btn-sm btn-ghost ckb-reject", "Dismiss selected");
      rejectBtn.disabled = !state.selected.size;
      rejectBtn.addEventListener("click", () => act("/api/pkb/reject"));
      bar.appendChild(rejectBtn);
      // One-tap: bring a freshly forged/mounted world alive without hunting
      // through mixed candidates. Approves only the world-term pages.
      const worldTerms = state.pending.filter((p) => p.kind === "world_term");
      if (worldTerms.length) {
        const wBtn = el("button", "btn btn-sm ckb-approve-world",
          "✦ Approve all " + worldTerms.length + " world terms");
        wBtn.addEventListener("click", async () => {
          try {
            await api("/api/pkb/promote", { paths: worldTerms.map((p) => p.path) });
            await load();
            announceChange("approve", worldTerms.length);
          } catch (e) {}
        });
        bar.appendChild(wBtn);
      }
      panel.appendChild(bar);

      const list = el("ul", "ckb-list");
      state.pending.forEach((item) => list.appendChild(row(item)));
      panel.appendChild(list);
    } else {
      panel.appendChild(el("div", "ckb-empty", "Nothing awaiting review — the Compiled KB is up to date."));
    }

    // Approved (collapsible)
    if (state.approved.length) {
      const toggle = el("button", "ckb-approved-toggle",
        (state.showApproved ? "▾ " : "▸ ") + state.approved.length + " approved");
      toggle.addEventListener("click", () => { state.showApproved = !state.showApproved; render(); });
      panel.appendChild(toggle);
      if (state.showApproved) {
        const list = el("ul", "ckb-list ckb-list--approved");
        state.approved.forEach((item) => list.appendChild(approvedRow(item)));
        panel.appendChild(list);
      }
    }
  }

  function row(item) {
    const li = el("li", "ckb-row");
    const cb = el("input", "ckb-cb");
    cb.type = "checkbox";
    cb.checked = state.selected.has(item.path);
    cb.addEventListener("change", () => {
      if (cb.checked) state.selected.add(item.path); else state.selected.delete(item.path);
      render();
    });
    li.appendChild(cb);
    const main = el("div", "ckb-row-main");
    main.appendChild(el("div", "ckb-row-title", item.title || item.path));
    if (item.preview) main.appendChild(el("div", "ckb-row-preview", item.preview));
    main.appendChild(chip(item.kind, item.provenance, item.world));
    li.appendChild(main);
    return li;
  }

  function approvedRow(item) {
    const li = el("li", "ckb-row ckb-row--approved");
    const main = el("div", "ckb-row-main");
    main.appendChild(el("div", "ckb-row-title", item.title || item.path));
    main.appendChild(chip(item.kind, item.provenance, item.world));
    li.appendChild(main);
    const revoke = el("button", "btn btn-sm btn-ghost", "Revoke");
    revoke.addEventListener("click", async () => {
      try {
        await api("/api/pkb/revoke", { paths: [item.path] });
        await load();
        announceChange("revoke", 1);
      } catch (e) {}
    });
    li.appendChild(revoke);
    return li;
  }

  // Tell the page a human decision landed — the brain graph solidifies
  // (or drops) ghosts and the hero counts refresh off the load() rebroadcast.
  function announceChange(action, count) {
    window.dispatchEvent(new CustomEvent("arail:kb-review-changed", {
      detail: { action: action, count: count },
    }));
  }

  async function act(path) {
    const paths = Array.from(state.selected);
    if (!paths.length) return;
    try {
      await api(path, { paths });
      await load();
      announceChange(path.indexOf("promote") >= 0 ? "approve" : "reject", paths.length);
    } catch (e) {}
  }

  document.addEventListener("DOMContentLoaded", load);
  // Refresh when growth/forge/ingest events land (agents propose new candidates).
  window.addEventListener("focus", load);
  // Cross-tab / agent-driven review changes arrive via SSE (knowledge-page.js
  // routes data.kb_review here).
  window.arailReviewReload = load;
})();
