/* World Terms — structured editor over /api/worlds/terms.
   Rendered inside #world-terms-view on the Knowledge page.
   All dynamic strings go through textContent/createElement (F8). */
(function () {
  'use strict';

  const view = document.getElementById('world-terms-view');
  const toggle = document.getElementById('wt-viewtoggle');
  const tabFiles = document.getElementById('wt-tab-files');
  const tabTerms = document.getElementById('wt-tab-terms');
  if (!view || !toggle) return;

  const CAPS = { short: 200, definition: 600, example: 300 };
  const MAX_RELATED = 12;

  const S = {
    data: null,          // /api/worlds/terms payload
    flags: {},           // slug -> flag object from /api/worlds/review
    reviewState: 'idle',
    reviewNote: '',      // the API's message when a review is partial or failed
    reviewTimer: null,
    growState: 'idle',   // world growth engine; 'not_applied' is terminal (a failed call)
    growNote: '',        // the API's message when a growth pass was not applied
    growTimer: null,
    growPasses: [],
    query: '',
    drawer: null,        // {backdrop, panel}
  };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  async function api(url, opts) {
    const r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
    let body = null;
    try { body = await r.json(); } catch (e) { /* no body */ }
    return { ok: r.ok, status: r.status, body: body };
  }

  function jsonOpts(method, payload) {
    return {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    };
  }

  /* ── view switching ─────────────────────────────────────────── */

  function setMode(terms) {
    document.body.classList.toggle('wt-mode', terms);
    view.hidden = !terms;
    tabFiles.setAttribute('aria-selected', String(!terms));
    tabTerms.setAttribute('aria-selected', String(terms));
  }

  tabFiles.addEventListener('click', () => setMode(false));
  tabTerms.addEventListener('click', () => setMode(true));

  /* ── provenance helpers ─────────────────────────────────────── */

  function provOf(t) {
    if (t.tier_of_source === 'model-asserted') return 'dreamed';
    if (typeof t.source === 'string' && t.source.indexOf('operator:') === 0) return 'edited';
    return 'sourced';
  }

  function provChip(kind) {
    if (kind === 'dreamed') return el('span', 'wt-chip wt-chip--dreamed', 'dreamed');
    if (kind === 'edited') return el('span', 'wt-chip wt-chip--edited', 'edited by you');
    return el('span', 'wt-chip wt-chip--sourced', 'sourced');
  }

  /* ── curator review ─────────────────────────────────────────── */

  async function pollReview() {
    const r = await api('/api/worlds/review');
    if (r.ok && r.body) {
      S.reviewState = r.body.state || 'idle';
      // A review that judged fewer terms than it planned says so; its silence is not "All clear".
      S.reviewNote = (r.body.outcome === 'partial' || r.body.outcome === 'failed') ? (r.body.message || '') : '';
      S.flags = {};
      for (const f of (r.body.flags || [])) S.flags[f.slug] = f;
    }
    if (S.reviewState === 'running') {
      S.reviewTimer = setTimeout(pollReview, 3000);
    } else {
      S.reviewTimer = null;
    }
    render();
  }

  async function startReview() {
    const r = await api('/api/worlds/review', { method: 'POST' });
    if (r.status === 202 || r.ok) {
      S.reviewState = 'running';
      render();
      if (!S.reviewTimer) S.reviewTimer = setTimeout(pollReview, 3000);
    }
  }

  /* ── growth engine (organic evolution) ──────────────────────── */

  async function pollGrow() {
    const r = await api('/api/worlds/grow');
    if (r.ok) {
      S.growState = r.body.state || 'idle';
      S.growPasses = r.body.passes || [];
      // 'not_applied' is terminal: a model call failed, so nothing was resealed or swapped.
      S.growNote = S.growState === 'not_applied' ? (r.body.message || '') : '';
      if (S.growState === 'running') {
        S.growTimer = setTimeout(pollGrow, 3000);
      } else {
        S.growTimer = null;
        if (S.growState === 'done') loadTerms();  // pull the newly-grown terms
      }
      render();
    }
  }

  async function startGrow() {
    const brain = (document.getElementById('wt-brain') || {}).value || 'auto';
    const r = await api('/api/worlds/grow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brain }),
    });
    if (r.status === 202 || r.ok) {
      S.growState = 'running';
      render();
      if (!S.growTimer) S.growTimer = setTimeout(pollGrow, 3000);
    } else if (r.body && r.body.error === 'grow_busy') {
      S.growState = 'running';
      render();
    }
  }

  /* ── World knowledge graph ──────────────────────────────────────
     The graph lives on the page itself now (the "lab's brain" section,
     one shared canvas — see _brain_graph.html / window.arailGraph); the
     terms header offers a chip that scrolls to it. Every term is still a
     wiki page tagged world-<slug> (world_mount._write_term_pages), so
     the graph stays truthful as terms are added, edited, or grown. */
  function focusGraphOnWorld() {
    const target = document.getElementById('brain-graph');
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth' });
  }

  /* ── main render ────────────────────────────────────────────── */

  function render() {
    if (!S.data) return;
    const scrollY = view.scrollTop;
    view.textContent = '';

    // Header — two deliberate rows (main's stable layout, so it doesn't
    // reshuffle at narrow widths), adapted to the restructured page: World
    // identity (name + tier) now lives in the page hero above, so row 1
    // carries the "view in graph" chip + the one primary action (Add term)
    // in place of the old worldname/tier badge; row 2 is the secondary
    // curation tools.
    const head = el('div', 'wt-head');

    const headTop = el('div', 'wt-head-top');
    const graphChip = el('button', 'wt-btn wt-btn--ghost', '🕸 View this World in the graph');
    graphChip.type = 'button';
    graphChip.title = 'Scroll to the knowledge graph above';
    graphChip.addEventListener('click', focusGraphOnWorld);
    headTop.appendChild(graphChip);
    headTop.appendChild(el('div', 'wt-head-spacer'));
    const addBtn = el('button', 'wt-btn wt-btn--primary', '＋ Add term');
    addBtn.type = 'button';
    addBtn.addEventListener('click', () => openDrawer(null));
    headTop.appendChild(addBtn);
    head.appendChild(headTop);

    const headActions = el('div', 'wt-head-actions');
    const reviewBtn = el('button', 'wt-btn wt-btn--curator',
      S.reviewState === 'running' ? 'Curator reviewing…' : 'Ask the Curator to review');
    reviewBtn.type = 'button';
    reviewBtn.disabled = S.reviewState === 'running';
    reviewBtn.addEventListener('click', startReview);
    headActions.appendChild(reviewBtn);

    // Growth engine: pick the curation brain, then let agents evolve the World.
    const growWrap = el('div', 'wt-grow');
    const brain = el('select', 'wt-brain');
    brain.id = 'wt-brain';
    brain.title = 'Which brain curates and grows this World';
    [['auto', 'Best local brain'], ['claude', 'Claude (cloud)'],
     ['openrouter', 'OpenRouter (cloud)'], ['local', 'On-GPU model']]
      .forEach(([v, label]) => {
        const o = el('option', null, label); o.value = v; brain.appendChild(o);
      });
    if (S._brain) brain.value = S._brain;
    brain.addEventListener('change', () => { S._brain = brain.value; });
    const growBtn = el('button', 'wt-btn wt-btn--curator',
      S.growState === 'running' ? 'Growing…' : '✦ Grow this World');
    growBtn.type = 'button';
    growBtn.disabled = S.growState === 'running';
    growBtn.title = 'Agents correct existing terms and add new ones — reversible, labeled';
    growBtn.addEventListener('click', startGrow);
    growWrap.appendChild(brain);
    growWrap.appendChild(growBtn);
    headActions.appendChild(growWrap);
    head.appendChild(headActions);

    view.appendChild(head);

    // The Curator's honest notices: the API's own sentence, inserted as text (never HTML).
    [S.reviewNote, S.growNote].forEach((note) => {
      if (!note) return;
      const n = el('div', 'wt-notice wt-notice--warn', note);
      n.setAttribute('role', 'status');
      view.appendChild(n);
    });

    // Evolution summary — the World's growth history (transparency).
    if (S.growState === 'running' || (S.growPasses && S.growPasses.length)) {
      const evo = el('div', 'wt-evo');
      if (S.growState === 'running') {
        evo.appendChild(el('span', 'wt-evo-live', '✦ Agents are growing this World…'));
      } else {
        const last = S.growPasses[S.growPasses.length - 1];
        const added = (last.added || []).length;
        const corr = (last.corrections || []).length;
        evo.appendChild(el('span', null,
          `✦ Last evolved via ${last.model || 'a model'}: +${added} term${added === 1 ? '' : 's'}, `
          + `${corr} correction${corr === 1 ? '' : 's'}`
          + ` · ${S.growPasses.length} pass${S.growPasses.length === 1 ? '' : 'es'} total`));
      }
      view.appendChild(evo);
    }

    // (The sourced-world edit warning moved into the hero's ⓘ provenance
    // disclosure — knowledge-page.js opens it once per World, same
    // localStorage key, and it now *explains* sealing instead of just
    // warning about it.)

    // Search
    const search = el('input', 'wt-search');
    search.type = 'search';
    search.placeholder = 'filter terms…';
    search.value = S.query;
    search.addEventListener('input', () => {
      S.query = search.value;
      renderSections(sectionsHost);
    });
    view.appendChild(search);

    const sectionsHost = el('div');
    view.appendChild(sectionsHost);
    renderSections(sectionsHost);
    view.scrollTop = scrollY;
  }

  function matches(t, q) {
    return (t.term || '').toLowerCase().includes(q) ||
           (t.slug || '').toLowerCase().includes(q) ||
           (t.short || '').toLowerCase().includes(q);
  }

  function renderSections(host) {
    host.textContent = '';
    const q = S.query.trim().toLowerCase();
    let shown = 0;
    for (const cat of S.data.categories) {
      const terms = S.data.terms.filter(t => t.category === cat.id && (!q || matches(t, q)));
      if (!terms.length) continue;
      shown += terms.length;
      const sec = el('section', 'wt-cat');
      const h = el('h3', 'wt-cat-head');
      h.appendChild(el('span', null, cat.label));
      h.appendChild(el('span', 'wt-cat-count', String(terms.length)));
      sec.appendChild(h);
      for (const t of terms) sec.appendChild(termRow(t));
      host.appendChild(sec);
    }
    // Terms in unknown categories still get shown
    const known = new Set(S.data.categories.map(c => c.id));
    const orphans = S.data.terms.filter(t => !known.has(t.category) && (!q || matches(t, q)));
    if (orphans.length) {
      shown += orphans.length;
      const sec = el('section', 'wt-cat');
      const h = el('h3', 'wt-cat-head');
      h.appendChild(el('span', null, 'uncategorized'));
      h.appendChild(el('span', 'wt-cat-count', String(orphans.length)));
      sec.appendChild(h);
      for (const t of orphans) sec.appendChild(termRow(t));
      host.appendChild(sec);
    }
    if (!shown) host.appendChild(el('div', 'wt-empty',
      q ? 'No terms match “' + S.query.trim() + '”.' : 'No terms in this World yet.'));
  }

  function termRow(t) {
    const row = el('button', 'wt-row');
    row.type = 'button';
    row.appendChild(el('span', 'wt-row-term', t.term));
    row.appendChild(el('span', 'wt-row-short', t.short || ''));
    row.appendChild(provChip(provOf(t)));
    const flag = S.flags[t.slug];
    if (flag) {
      const f = el('span', 'wt-chip wt-chip--dreamed wt-flag', '⚑ flagged');
      f.title = flag.note || 'Flagged by the Curator';
      row.appendChild(f);
    }
    row.addEventListener('click', () => openDrawer(t));
    return row;
  }

  /* ── drawer ─────────────────────────────────────────────────── */

  function closeDrawer() {
    if (!S.drawer) return;
    S.drawer.backdrop.remove();
    S.drawer.panel.remove();
    S.drawer = null;
  }

  function field(labelText, control, cap) {
    const wrap = el('div', 'wt-field');
    const lab = el('label');
    lab.appendChild(el('span', null, labelText));
    let counter = null;
    if (cap) {
      counter = el('span', 'wt-count');
      const upd = () => {
        counter.textContent = control.value.length + '/' + cap;
        counter.classList.toggle('wt-count--over', control.value.length > cap);
      };
      control.addEventListener('input', upd);
      upd();
      lab.appendChild(counter);
    }
    wrap.appendChild(lab);
    wrap.appendChild(control);
    const err = el('div', 'wt-field-err');
    err.hidden = true;
    wrap.appendChild(err);
    wrap._err = err;
    wrap._update = () => { if (cap) control.dispatchEvent(new Event('input')); };
    return wrap;
  }

  function showFieldError(fields, name, message) {
    const f = fields[name];
    if (f) {
      f._err.textContent = message;
      f._err.hidden = false;
      return;
    }
    // unknown field → surface in the actions row
    fields._msg.textContent = message;
  }

  function openDrawer(term) {
    closeDrawer();
    const isNew = !term;

    const backdrop = el('div', 'wt-backdrop');
    backdrop.addEventListener('click', closeDrawer);
    const panel = el('aside', 'wt-drawer');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');

    panel.appendChild(el('h3', 'wt-drawer-title', isNew ? 'Add term' : 'Edit “' + term.term + '”'));

    if (!isNew && term.slug) {
      const wikiLink = el('a', 'wt-btn wt-btn--ghost', '🕸 View in graph');
      wikiLink.href = '/wiki/' + encodeURIComponent(term.slug);
      wikiLink.title = 'Open this term’s wiki page — shows its graph neighborhood and backlinks';
      panel.appendChild(wikiLink);
    }

    // Curator flag note + apply suggestion
    let related = (term && Array.isArray(term.related)) ? term.related.slice() : [];
    const flag = term ? S.flags[term.slug] : null;
    let catSelect; // forward ref for Apply suggestion

    if (flag && !isNew) {
      const fbox = el('div', 'wt-drawer-flag');
      fbox.appendChild(el('div', null, '⚑ ' + (flag.note || 'Flagged by the Curator')));
      const canApply = flag.better_category || (flag.bad_edges && flag.bad_edges.length);
      if (canApply) {
        const apply = el('button', 'wt-btn wt-btn--ghost', 'Apply suggestion');
        apply.type = 'button';
        apply.addEventListener('click', () => {
          if (flag.better_category && catSelect) catSelect.value = flag.better_category;
          if (flag.bad_edges && flag.bad_edges.length) {
            related = related.filter(s => flag.bad_edges.indexOf(s) === -1);
            renderRelChips();
          }
          apply.disabled = true;
          apply.textContent = 'Applied — review and Save';
        });
        fbox.appendChild(apply);
      }
      panel.appendChild(fbox);
    }

    // Fields
    const fields = {};

    const termInput = el('input');
    termInput.type = 'text';
    termInput.value = term ? (term.term || '') : '';
    fields.term = field('term', termInput);
    panel.appendChild(fields.term);

    const shortTa = el('textarea');
    shortTa.rows = 2;
    shortTa.value = term ? (term.short || '') : '';
    fields.short = field('short', shortTa, CAPS.short);
    panel.appendChild(fields.short);

    const defTa = el('textarea');
    defTa.rows = 5;
    defTa.value = term ? (term.definition || '') : '';
    fields.definition = field('definition', defTa, CAPS.definition);
    panel.appendChild(fields.definition);

    const exTa = el('textarea');
    exTa.rows = 3;
    exTa.value = term ? (term.example || '') : '';
    fields.example = field('example', exTa, CAPS.example);
    panel.appendChild(fields.example);

    catSelect = el('select');
    for (const c of S.data.categories) {
      const o = el('option', null, c.label);
      o.value = c.id;
      catSelect.appendChild(o);
    }
    if (term && term.category) catSelect.value = term.category;
    fields.category = field('category', catSelect);
    panel.appendChild(fields.category);

    // Related slug-picker
    const relWrap = el('div', 'wt-field');
    const relLab = el('label');
    relLab.appendChild(el('span', null, 'related'));
    const relCount = el('span', 'wt-count');
    relLab.appendChild(relCount);
    relWrap.appendChild(relLab);
    const chips = el('div', 'wt-related-chips');
    relWrap.appendChild(chips);
    const relSearch = el('input');
    relSearch.type = 'search';
    relSearch.placeholder = 'add related term…';
    relWrap.appendChild(relSearch);
    const relResults = el('div', 'wt-rel-results');
    relWrap.appendChild(relResults);
    const relErr = el('div', 'wt-field-err');
    relErr.hidden = true;
    relWrap.appendChild(relErr);
    relWrap._err = relErr;
    fields.related = relWrap;
    panel.appendChild(relWrap);

    function renderRelChips() {
      chips.textContent = '';
      relCount.textContent = related.length + '/' + MAX_RELATED;
      relCount.classList.toggle('wt-count--over', related.length > MAX_RELATED);
      for (const slug of related) {
        const chip = el('span', 'wt-rel-chip');
        chip.appendChild(el('span', null, slug));
        const x = el('button', null, '✕');
        x.type = 'button';
        x.setAttribute('aria-label', 'Remove ' + slug);
        x.addEventListener('click', () => {
          related = related.filter(s => s !== slug);
          renderRelChips();
          renderRelResults();
        });
        chip.appendChild(x);
        chips.appendChild(chip);
      }
    }

    function renderRelResults() {
      relResults.textContent = '';
      const q = relSearch.value.trim().toLowerCase();
      if (!q) return;
      const self = term ? term.slug : null;
      const opts = S.data.terms.filter(t =>
        t.slug !== self &&
        related.indexOf(t.slug) === -1 &&
        ((t.term || '').toLowerCase().includes(q) || (t.slug || '').toLowerCase().includes(q))
      ).slice(0, 20);
      for (const t of opts) {
        const b = el('button', 'wt-rel-opt', t.slug + ' — ' + t.term);
        b.type = 'button';
        b.addEventListener('click', () => {
          if (related.length >= MAX_RELATED) {
            relErr.textContent = 'Max ' + MAX_RELATED + ' related terms.';
            relErr.hidden = false;
            return;
          }
          relErr.hidden = true;
          related.push(t.slug);
          relSearch.value = '';
          renderRelChips();
          renderRelResults();
        });
        relResults.appendChild(b);
      }
    }
    relSearch.addEventListener('input', renderRelResults);
    renderRelChips();

    const akaInput = el('input');
    akaInput.type = 'text';
    akaInput.placeholder = 'comma-separated aliases';
    akaInput.value = term && Array.isArray(term.aka) ? term.aka.join(', ') : '';
    fields.aka = field('aka', akaInput);
    panel.appendChild(fields.aka);

    // Actions
    const actions = el('div', 'wt-drawer-actions');
    const msg = el('span', 'wt-drawer-msg');
    fields._msg = msg;
    let draftSource = null;

    if (isNew) {
      const draftBtn = el('button', 'wt-btn wt-btn--ghost', 'Draft with model');
      draftBtn.type = 'button';
      draftBtn.addEventListener('click', async () => {
        const name = termInput.value.trim();
        if (!name) {
          showFieldError(fields, 'term', 'Type a term name first.');
          return;
        }
        fields.term._err.hidden = true;
        draftBtn.disabled = true;
        draftBtn.textContent = 'Drafting…';
        const r = await api('/api/worlds/terms/draft', jsonOpts('POST', { term: name }));
        draftBtn.disabled = false;
        draftBtn.textContent = 'Draft with model';
        if (r.ok && r.body && r.body.proposal) {
          const p = r.body.proposal;
          shortTa.value = p.short || '';
          defTa.value = p.definition || '';
          exTa.value = p.example || '';
          related = Array.isArray(p.related) ? p.related.slice(0, MAX_RELATED) : [];
          draftSource = p.source || null;
          fields.short._update(); fields.definition._update(); fields.example._update();
          renderRelChips();
        } else {
          msg.textContent = (r.body && (r.body.message || r.body.error)) || 'Draft failed.';
        }
      });
      actions.appendChild(draftBtn);
    }

    const saveBtn = el('button', 'wt-btn wt-btn--primary', 'Save');
    saveBtn.type = 'button';
    saveBtn.addEventListener('click', async () => {
      for (const k of ['term', 'short', 'definition', 'example', 'category', 'aka']) {
        fields[k]._err.hidden = true;
      }
      relErr.hidden = true;
      msg.textContent = '';
      const payload = {
        term: termInput.value.trim(),
        short: shortTa.value,
        definition: defTa.value,
        example: exTa.value,
        category: catSelect.value,
        related: related,
        aka: akaInput.value.split(',').map(s => s.trim()).filter(Boolean),
      };
      saveBtn.disabled = true;
      let r;
      if (isNew) {
        if (draftSource) payload._draft_source = draftSource;
        r = await api('/api/worlds/terms', jsonOpts('POST', payload));
      } else {
        r = await api('/api/worlds/terms/' + encodeURIComponent(term.slug), jsonOpts('PUT', payload));
      }
      saveBtn.disabled = false;
      if (r.ok) {
        closeDrawer();
        await loadTerms();
        render();
        return;
      }
      const b = r.body || {};
      if (r.status === 400 && b.field) {
        showFieldError(fields, b.field, b.message || 'Invalid value.');
      } else if (r.status === 409) {
        showFieldError(fields, 'term', b.message || 'A term with that name already exists.');
      } else {
        msg.textContent = b.message || b.error || ('Save failed (' + r.status + ').');
      }
    });
    actions.appendChild(saveBtn);

    if (!isNew) {
      const delBtn = el('button', 'wt-btn wt-btn--danger-ghost', 'Delete');
      delBtn.type = 'button';
      delBtn.addEventListener('click', async () => {
        if (!confirm('Delete “' + term.term + '” from this World?')) return;
        const r = await api('/api/worlds/terms/' + encodeURIComponent(term.slug), { method: 'DELETE' });
        if (r.ok) {
          closeDrawer();
          await loadTerms();
          render();
        } else if (r.status === 400) {
          msg.textContent = (r.body && r.body.message) || 'Cannot delete the last term of a World.';
        } else {
          msg.textContent = 'Delete failed (' + r.status + ').';
        }
      });
      actions.appendChild(delBtn);
    }

    const cancelBtn = el('button', 'wt-btn wt-btn--ghost', 'Cancel');
    cancelBtn.type = 'button';
    cancelBtn.addEventListener('click', closeDrawer);
    actions.appendChild(cancelBtn);
    actions.appendChild(msg);
    panel.appendChild(actions);

    panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

    document.body.appendChild(backdrop);
    document.body.appendChild(panel);
    S.drawer = { backdrop: backdrop, panel: panel };
    termInput.focus();
  }

  /* ── boot ───────────────────────────────────────────────────── */

  async function loadTerms() {
    const r = await api('/api/worlds/terms');
    if (!r.ok) return false;
    S.data = r.body;
    // Broadcast for page-level consumers (the World hero) so they can
    // hydrate without a second /api/worlds/terms fetch. Re-fires after
    // every save/delete/grow reload, keeping counts live.
    window.dispatchEvent(new CustomEvent('arail:world-terms-loaded', {
      detail: {
        world: S.data.world || '',
        display_name: S.data.display_name || S.data.world || '',
        tier: S.data.tier || '',
        term_count: (S.data.terms || []).length,
        category_count: (S.data.categories || []).length,
      },
    }));
    return true;
  }

  (async function boot() {
    const ok = await loadTerms();
    if (!ok) return; // 409 no_world_mounted → tab stays hidden, Files view untouched
    toggle.hidden = false;
    // pick up any existing curator review results (and resume polling if running)
    await new Promise((res) => {
      api('/api/worlds/review').then((r) => {
        if (r.ok && r.body) {
          S.reviewState = r.body.state || 'idle';
          S.flags = {};
          for (const f of (r.body.flags || [])) S.flags[f.slug] = f;
          if (S.reviewState === 'running') S.reviewTimer = setTimeout(pollReview, 3000);
        }
        res();
      }).catch(res);
    });
    // pick up growth history + resume if a pass is running
    try {
      const g = await api('/api/worlds/grow');
      if (g.ok && g.body) {
        S.growState = g.body.state || 'idle';
        S.growPasses = g.body.passes || [];
        if (S.growState === 'running' && !S.growTimer) S.growTimer = setTimeout(pollGrow, 3000);
      }
    } catch (e) { /* ignore */ }
    setMode(true); // world mounted → land on World Terms
    render();
  })();
})();
