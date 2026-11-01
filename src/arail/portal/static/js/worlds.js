/* Worlds page — catalog + forge state machine. All dynamic strings via textContent. */
(function () {
  'use strict';

  var HEX_RE = /^#[0-9a-fA-F]{6}$/;
  var STAGES_DREAM = ['Spec', 'Seed', 'Discover', 'Link', 'Define', 'Gate'];
  var STAGES_FETCH = ['Resolve', 'Harvest', 'Define', 'Link', 'Gate'];
  var STAGES = STAGES_DREAM; // switched per active forge source

  var $ = function (id) { return document.getElementById(id); };
  var panels = {
    form: $('forge-form'),
    progress: $('forge-progress'),
    preview: $('forge-preview'),
    terminal: $('forge-terminal'),
  };

  var pollTimer = null;
  var lastForgeParams = null;

  function showPanel(name) {
    Object.keys(panels).forEach(function (k) { panels[k].hidden = (k !== name); });
  }

  function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin' };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        return { ok: r.ok, status: r.status, data: j };
      });
    });
  }

  /* ══ Forge form ══ */
  var subjectInput = $('forge-subject');
  var errEl = $('forge-err');
  var selectedTerms = 50;
  var selectedPalette = null;
  var selectedSource = 'dream';
  var selectedBrain = 'local';

  // Fetch mode: no model calls, so ETAs are network-bound and the big sizes open up.
  var FETCH_ETAS = { 25: '≈1 min', 50: '≈1 min', 100: '≈2 min', 250: '≈3 min', 512: '≈6 min' };
  var DREAM_ETAS = { 25: '≈4 min', 50: '≈8 min', 100: '≈15 min' };

  function applySource(source) {
    selectedSource = source;
    var isFetch = source === 'fetch';
    document.querySelectorAll('#forge-source .source-opt').forEach(function (b) {
      var on = b.dataset.source === source;
      b.classList.toggle('selected', on);
      if (on) b.setAttribute('aria-pressed', 'true'); else b.removeAttribute('aria-pressed');
    });
    document.querySelectorAll('#forge-size .size-opt').forEach(function (b) {
      var n = parseInt(b.dataset.terms, 10);
      if (b.classList.contains('fetch-only')) b.hidden = !isFetch;
      var eta = b.querySelector('.size-eta');
      if (eta) eta.textContent = (isFetch ? FETCH_ETAS : DREAM_ETAS)[n] || '';
    });
    // A fetch-only size can't survive a switch back to dream.
    if (!isFetch && selectedTerms > 100) {
      var fallback = document.querySelector('#forge-size .size-opt[data-terms="100"]');
      if (fallback) fallback.click();
    }
    $('forge-brain').hidden = isFetch;
    updateBanners();
    STAGES = isFetch ? STAGES_FETCH : STAGES_DREAM;
  }

  function updateBanners() {
    var isFetch = selectedSource === 'fetch';
    var isFrontier = !isFetch && selectedBrain !== 'local';
    $('banner-dream').hidden = isFetch || isFrontier;
    $('banner-fetch').hidden = !isFetch;
    $('banner-frontier').hidden = !isFrontier;
    $('note-dream').hidden = isFetch || isFrontier;
    $('note-fetch').hidden = !isFetch;
    $('note-frontier').hidden = !isFrontier;
  }

  function applyBrain(brain) {
    selectedBrain = brain;
    document.querySelectorAll('#forge-brain .source-opt').forEach(function (b) {
      var on = b.dataset.brain === brain;
      b.classList.toggle('selected', on);
      if (on) b.setAttribute('aria-pressed', 'true'); else b.removeAttribute('aria-pressed');
    });
    updateBanners();
  }

  document.querySelectorAll('#forge-source .source-opt').forEach(function (btn) {
    btn.addEventListener('click', function () { applySource(btn.dataset.source); });
  });

  document.querySelectorAll('#forge-brain .source-opt').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      applyBrain(btn.dataset.brain);
    });
  });

  // The frontier option is only offered when the lab can actually reach a
  // cloud gateway: hybrid mode + a saved Claude key. Otherwise it stays
  // visible-but-disabled so the capability is discoverable. When the only
  // thing missing is the key (lab is already hybrid), the inline
  // frontier-key-row lets the operator paste one right here instead of
  // hunting for Chat → ⚙ Connections.
  function loadBrainAvailability() {
    api('GET', '/api/providers/status').then(function (res) {
      if (!res.ok) return;
      var d = res.data || {};
      var frontier = $('brain-frontier');
      var keyRow = $('frontier-key-row');
      if (!frontier) return;
      var hasKey = !!(d.available && d.available.claude);
      if (!d.cloud_enabled) {
        frontier.disabled = true;
        frontier.title = d.airgapped_notice ||
          'Airgapped — set LAB_MODE=hybrid to enable the frontier API.';
        if (keyRow) keyRow.hidden = true;
        if (selectedBrain !== 'local') applyBrain('local');
      } else if (!hasKey) {
        frontier.disabled = true;
        frontier.title = 'No Claude key saved — paste one below, or add it under Chat → ⚙ Connections.';
        if (keyRow) keyRow.hidden = false;
        if (selectedBrain !== 'local') applyBrain('local');
      } else {
        frontier.disabled = false;
        frontier.title = '';
        if (keyRow) keyRow.hidden = true;
      }
    }).catch(function () {});
  }

  (function initFrontierKeyRow() {
    var saveBtn = $('frontier-key-save');
    var input = $('frontier-key-input');
    var errEl2 = $('frontier-key-err');
    if (!saveBtn || !input) return;
    function submit() {
      var token = input.value.trim();
      errEl2.textContent = '';
      if (!token) { errEl2.textContent = 'Paste a key first.'; return; }
      saveBtn.disabled = true;
      api('POST', '/api/providers/save', { provider: 'claude', token: token }).then(function (res) {
        saveBtn.disabled = false;
        if (res.ok && res.data && res.data.ok) {
          input.value = '';
          loadBrainAvailability();
        } else {
          errEl2.textContent = (res.data && res.data.error) || 'Save failed (' + res.status + ').';
        }
      }).catch(function (e) {
        saveBtn.disabled = false;
        errEl2.textContent = 'Save failed: ' + e.message;
      });
    }
    saveBtn.addEventListener('click', submit);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
  })();

  document.querySelectorAll('#forge-chips .chip-ghost').forEach(function (btn) {
    btn.addEventListener('click', function () {
      subjectInput.value = btn.textContent;
      subjectInput.focus();
    });
  });

  document.querySelectorAll('#forge-size .size-opt').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('#forge-size .size-opt').forEach(function (b) {
        b.classList.remove('selected');
        b.removeAttribute('aria-pressed');
      });
      btn.classList.add('selected');
      btn.setAttribute('aria-pressed', 'true');
      selectedTerms = parseInt(btn.dataset.terms, 10);
    });
  });

  function loadPalettes() {
    api('GET', '/api/system/theme').then(function (res) {
      if (!res.ok || !res.data.themes) return;
      var row = $('palette-row');
      res.data.themes.slice(0, 4).forEach(function (t) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'swatch';
        b.title = String(t.name || t.id || '');
        if (HEX_RE.test(t.preview_start || '') && HEX_RE.test(t.preview_end || '')) {
          b.style.background = 'linear-gradient(135deg, ' + t.preview_start + ', ' + t.preview_end + ')';
        }
        b.addEventListener('click', function () {
          var was = b.classList.contains('selected');
          row.querySelectorAll('.swatch').forEach(function (s) { s.classList.remove('selected'); });
          if (was) { selectedPalette = null; return; } // toggle off
          b.classList.add('selected');
          selectedPalette = String(t.id || t.name || '');
        });
        row.appendChild(b);
      });
    }).catch(function () {});
  }

  function startForge(params) {
    errEl.textContent = '';
    api('POST', '/api/worlds/forge', params).then(function (res) {
      if (res.status === 202 || res.ok) {
        lastForgeParams = params;
        beginPolling();
      } else {
        var e = res.data && res.data.error;
        errEl.textContent =
          e === 'bad_subject' ? 'That subject can’t be forged — try a more concrete topic.' :
          e === 'forge_busy' ? 'A forge is already running.' :
          e === 'slug_exists' ? 'A world with that name already exists. Delete it first, or pick a different subject.' :
          e === 'airgapped' ? 'The lab is airgapped — the frontier API can’t be reached. Flip the Airgapped pill, or forge with the local model.' :
          'Forge failed (' + res.status + ').';
      }
    }).catch(function () { errEl.textContent = 'Network error — is the lab running?'; });
  }

  $('forge-go').addEventListener('click', function () {
    var subject = subjectInput.value.trim();
    if (!subject) { errEl.textContent = 'Enter a subject to forge.'; subjectInput.focus(); return; }
    var params = { subject: subject, max_terms: selectedTerms, source: selectedSource };
    if (selectedSource === 'dream') params.brain = selectedBrain;
    if (selectedPalette) params.palette_hint = selectedPalette;
    var pers = $('forge-personality').value;
    if (pers) params.personality = pers;
    startForge(params);
  });
  subjectInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') $('forge-go').click();
  });

  /* ══ Progress ══ */
  function fmtClock(s) {
    s = Math.max(0, Math.floor(s || 0));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  function renderProgress(st) {
    // Recover the right stage list after a mid-forge page reload: the status
    // payload's source (or its stage count) says which pipeline is running.
    if (st.source === 'fetch' || st.stages_total === STAGES_FETCH.length) {
      STAGES = STAGES_FETCH;
    } else if (st.source === 'dream' || st.stages_total === STAGES_DREAM.length) {
      STAGES = STAGES_DREAM;
    }
    $('prog-subject').textContent = st.subject || '';
    var list = $('stage-list');
    list.textContent = '';
    STAGES.forEach(function (label, i) {
      var li = document.createElement('li');
      var dot = document.createElement('span');
      dot.className = 'stage-dot';
      if (i < st.stage_index) { li.className = 'done'; dot.textContent = '✓'; }
      else if (i === st.stage_index) { li.className = 'active'; dot.textContent = '●'; }
      else { dot.textContent = '·'; }
      li.appendChild(dot);
      li.appendChild(document.createTextNode(label));
      list.appendChild(li);
    });
    $('prog-terms').textContent = 'terms ' + (st.terms_found || 0) + '/' + (st.max_terms || 0);
    $('prog-clock').textContent = fmtClock(st.elapsed_s);
    $('prog-message').textContent = st.message || '';
  }

  function beginPolling() {
    showPanel('progress');
    stopPolling();
    pollOnce();
    pollTimer = setInterval(pollOnce, 2000);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function pollOnce() {
    api('GET', '/api/worlds/forge/status').then(function (res) {
      var st = res.data || {};
      if (st.state === 'running') {
        renderProgress(st);
      } else if (st.state === 'done') {
        stopPolling();
        loadPreview();
      } else if (st.state === 'error' || st.state === 'cancelled') {
        stopPolling();
        $('terminal-msg').textContent = (st.state === 'cancelled' ? 'Forge cancelled. ' : 'Forge failed. ') + (st.message || '');
        showPanel('terminal');
      } else {
        stopPolling();
        showPanel('form');
      }
    }).catch(function () {});
  }

  $('forge-cancel').addEventListener('click', function () {
    api('POST', '/api/worlds/forge/cancel').then(function () { pollOnce(); });
  });
  $('terminal-back').addEventListener('click', function () { showPanel('form'); });

  /* ══ Preview ══ */
  function loadPreview() {
    api('GET', '/api/worlds/forge/preview').then(function (res) {
      if (!res.ok) { showPanel('form'); return; }
      var p = res.data;
      $('prev-name').textContent = p.display_name || p.slug || '';
      var counts = p.counts || {};
      $('prev-tier').textContent = 'dreamed · model-asserted · ' + (counts.model || 0) + '/' + (counts.total || 0) + ' model';
      $('prev-stats').textContent = (counts.total || 0) + ' terms · avg ' + (p.avg_edges != null ? p.avg_edges : '?') + ' edges';

      var warnBox = $('prev-warnings');
      warnBox.textContent = '';
      (p.warnings || []).forEach(function (w) {
        var d = document.createElement('div');
        d.className = 'prev-warning';
        d.textContent = String(w);
        warnBox.appendChild(d);
      });

      var cats = $('prev-cats');
      cats.textContent = '';
      var byCat = {};
      (p.terms || []).forEach(function (t) {
        (byCat[t.category] = byCat[t.category] || []).push(t);
      });
      (p.categories || []).forEach(function (c) {
        var det = document.createElement('details');
        var sum = document.createElement('summary');
        var lab = document.createElement('span');
        lab.textContent = c.label || c.id;
        var cnt = document.createElement('span');
        cnt.className = 'cat-count';
        cnt.textContent = String(c.term_count != null ? c.term_count : (byCat[c.id] || []).length);
        sum.appendChild(lab);
        sum.appendChild(cnt);
        det.appendChild(sum);
        (byCat[c.id] || []).forEach(function (t) {
          var row = document.createElement('div');
          row.className = 'term-row';
          var name = document.createElement('span');
          name.className = 'term-name';
          name.textContent = t.term || '';
          var slug = document.createElement('span');
          slug.className = 'term-slug';
          slug.textContent = t.slug || '';
          var short = document.createElement('div');
          short.className = 'term-short';
          short.textContent = t.short || '';
          row.appendChild(name);
          row.appendChild(slug);
          row.appendChild(short);
          det.appendChild(row);
        });
        cats.appendChild(det);
      });
      $('prev-err').textContent = '';
      buildPreviewGraph(p);
      showPanel('preview');
    }).catch(function () { showPanel('form'); });
  }

  /* ══ Preview graph — categories + terms depicted as a graph the moment
     a World is forged, before the operator ever commits to mounting it. ══ */
  var previewView = 'graph';
  var graphState = null; // { nodes, edges, byId } built once per preview load

  $('prev-view-toggle').addEventListener('click', function (e) {
    var btn = e.target.closest('.prev-view-btn');
    if (!btn) return;
    previewView = btn.dataset.view;
    document.querySelectorAll('#prev-view-toggle .prev-view-btn').forEach(function (b) {
      var on = b === btn;
      b.classList.toggle('selected', on);
      b.setAttribute('aria-selected', String(on));
    });
    $('prev-graph-wrap').hidden = previewView !== 'graph';
    $('prev-cats').hidden = previewView !== 'list';
    if (previewView === 'graph') drawPreviewGraph();
  });

  var GRAPH_PALETTE = ['--accent', '--accent2', '--info', '--warn', '--positive', '--danger'];
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function colorForCategory(index) {
    return cssVar(GRAPH_PALETTE[index % GRAPH_PALETTE.length]);
  }

  function buildPreviewGraph(p) {
    var categories = p.categories || [];
    var terms = p.terms || [];
    var catIndex = {};
    categories.forEach(function (c, i) { catIndex[c.id] = i; });

    var nodes = [];
    var byId = {};
    categories.forEach(function (c, i) {
      var n = {
        id: 'cat:' + c.id, kind: 'category', label: c.label || c.id,
        color: colorForCategory(i), count: c.term_count || 0,
      };
      nodes.push(n); byId[n.id] = n;
    });
    var termSlugToNode = {};
    terms.forEach(function (t) {
      var ci = catIndex.hasOwnProperty(t.category) ? catIndex[t.category] : 0;
      var n = {
        id: 'term:' + t.slug, kind: 'term', label: t.term || t.slug,
        short: t.short || '', category: t.category, color: colorForCategory(ci),
      };
      nodes.push(n); byId[n.id] = n;
      termSlugToNode[t.slug] = n;
    });

    var edges = [];
    terms.forEach(function (t) {
      var catNode = byId['cat:' + t.category];
      if (catNode) edges.push({ a: catNode.id, b: 'term:' + t.slug, kind: 'hub' });
      (t.related || []).forEach(function (slug) {
        if (termSlugToNode[slug]) edges.push({ a: 'term:' + t.slug, b: 'term:' + slug, kind: 'related' });
      });
    });

    layoutRadial(nodes, categories.length);
    graphState = { nodes: nodes, edges: edges, byId: byId, categories: categories };
    renderGraphLegend(categories);
    if (previewView === 'graph') drawPreviewGraph();
  }

  function layoutRadial(nodes, catCount) {
    var cats = nodes.filter(function (n) { return n.kind === 'category'; });
    var terms = nodes.filter(function (n) { return n.kind === 'term'; });
    var byCatTerms = {};
    terms.forEach(function (t) { (byCatTerms[t.category] = byCatTerms[t.category] || []).push(t); });

    var hubR = Math.max(140, catCount * 40);
    cats.forEach(function (c, i) {
      var angle = (i / Math.max(1, cats.length)) * Math.PI * 2 - Math.PI / 2;
      c.x = Math.cos(angle) * hubR;
      c.y = Math.sin(angle) * hubR;
      var catId = c.id.slice(4);
      var members = byCatTerms[catId] || [];
      var ringR = Math.min(120, 40 + members.length * 4);
      members.forEach(function (t, j) {
        var a = (j / Math.max(1, members.length)) * Math.PI * 2;
        // Jitter the radius slightly so dense clusters don't form a perfect
        // ring that reads as a single blob at low zoom.
        var r = ringR * (0.6 + 0.4 * ((j % 3) / 2));
        t.x = c.x + Math.cos(a) * r;
        t.y = c.y + Math.sin(a) * r;
      });
    });
  }

  function renderGraphLegend(categories) {
    var el = $('prev-graph-legend');
    el.textContent = '';
    categories.forEach(function (c, i) {
      var item = document.createElement('span');
      item.className = 'prev-graph-legend-item';
      var dot = document.createElement('span');
      dot.className = 'prev-graph-legend-dot';
      dot.style.background = colorForCategory(i);
      item.appendChild(dot);
      item.appendChild(document.createTextNode(c.label || c.id));
      el.appendChild(item);
    });
  }

  var previewCanvas = $('prev-graph-canvas');
  var previewCtx = previewCanvas.getContext('2d');
  var previewCamera = { zoom: 1 };
  var previewHover = null;

  function resizePreviewCanvas() {
    var wrap = $('prev-graph-wrap');
    var dpr = window.devicePixelRatio || 1;
    var w = wrap.clientWidth || 600;
    var h = wrap.clientHeight || 360;
    previewCanvas.width = w * dpr;
    previewCanvas.height = h * dpr;
    previewCanvas.style.width = w + 'px';
    previewCanvas.style.height = h + 'px';
    previewCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w: w, h: h };
  }

  function drawPreviewGraph() {
    if (!graphState) return;
    var dims = resizePreviewCanvas();
    var cx = dims.w / 2, cy = dims.h / 2;
    var zoom = previewCamera.zoom;
    var ctx = previewCtx;
    ctx.clearRect(0, 0, dims.w, dims.h);

    function toScreen(n) { return { x: cx + n.x * zoom, y: cy + n.y * zoom }; }

    ctx.lineWidth = 1;
    graphState.edges.forEach(function (e) {
      var a = graphState.byId[e.a], b = graphState.byId[e.b];
      if (!a || !b) return;
      var sa = toScreen(a), sb = toScreen(b);
      ctx.beginPath();
      ctx.moveTo(sa.x, sa.y);
      ctx.lineTo(sb.x, sb.y);
      ctx.strokeStyle = e.kind === 'related'
        ? 'color-mix(in srgb, ' + a.color + ' 45%, transparent)'
        : 'color-mix(in srgb, ' + a.color + ' 18%, transparent)';
      ctx.stroke();
    });

    graphState.nodes.forEach(function (n) {
      var s = toScreen(n);
      var r = n.kind === 'category' ? 10 : 4;
      var isHover = n === previewHover;
      ctx.beginPath();
      ctx.arc(s.x, s.y, isHover ? r + 2 : r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.globalAlpha = n.kind === 'category' ? 1 : 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (n.kind === 'category') {
        ctx.font = '600 12px var(--font-sans, sans-serif)';
        ctx.fillStyle = cssVar('--text-strong');
        ctx.textAlign = 'center';
        ctx.fillText(n.label, s.x, s.y - r - 6);
      }
    });
  }

  function hitTestPreview(sx, sy) {
    if (!graphState) return null;
    var wrap = $('prev-graph-wrap');
    var cx = wrap.clientWidth / 2, cy = wrap.clientHeight / 2;
    var zoom = previewCamera.zoom;
    var best = null, bestDist = 12 * 12;
    graphState.nodes.forEach(function (n) {
      var x = cx + n.x * zoom, y = cy + n.y * zoom;
      var d = (x - sx) * (x - sx) + (y - sy) * (y - sy);
      if (d < bestDist) { bestDist = d; best = n; }
    });
    return best;
  }

  previewCanvas.addEventListener('mousemove', function (e) {
    var rect = previewCanvas.getBoundingClientRect();
    var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    var node = hitTestPreview(sx, sy);
    var tip = $('prev-graph-tooltip');
    if (node !== previewHover) {
      previewHover = node;
      drawPreviewGraph();
    }
    if (node) {
      tip.hidden = false;
      tip.style.left = (sx + 14) + 'px';
      tip.style.top = (sy + 10) + 'px';
      tip.textContent = node.kind === 'category'
        ? node.label + ' · ' + node.count + ' terms'
        : node.label + (node.short ? ' — ' + node.short : '');
    } else {
      tip.hidden = true;
    }
  });
  previewCanvas.addEventListener('mouseleave', function () {
    previewHover = null;
    $('prev-graph-tooltip').hidden = true;
    drawPreviewGraph();
  });
  previewCanvas.addEventListener('wheel', function (e) {
    e.preventDefault();
    previewCamera.zoom = Math.min(3, Math.max(0.4, previewCamera.zoom * (e.deltaY > 0 ? 0.9 : 1.1)));
    drawPreviewGraph();
  }, { passive: false });
  window.addEventListener('resize', function () { if (previewView === 'graph') drawPreviewGraph(); });

  $('prev-accept').addEventListener('click', function () {
    $('prev-err').textContent = '';
    api('POST', '/api/worlds/forge/confirm').then(function (res) {
      if (!res.ok) { $('prev-err').textContent = 'Confirm failed (' + res.status + ').'; return; }
      var goal = res.data.suggested_goal;
      if (goal) {
        showToast('World mounted. Suggested goal: ' + goal, goal);
      } else {
        location.reload();
      }
    }).catch(function () { $('prev-err').textContent = 'Network error.'; });
  });

  $('prev-regen').addEventListener('click', function () {
    api('POST', '/api/worlds/forge/discard').then(function () {
      if (lastForgeParams) {
        var p = {};
        Object.keys(lastForgeParams).forEach(function (k) { p[k] = lastForgeParams[k]; });
        p.overwrite = true;
        startForge(p);
      } else {
        showPanel('form');
      }
    });
  });

  $('prev-discard').addEventListener('click', function () {
    api('POST', '/api/worlds/forge/discard').then(function () { showPanel('form'); });
  });

  /* ══ Toast (post-confirm goal suggestion) ══ */
  function showToast(text, goal) {
    var toast = $('worlds-toast');
    $('toast-text').textContent = text;
    var btn = $('toast-goal');
    var done = false;
    var finish = function () {
      if (done) return;
      done = true;
      location.reload();
    };
    if (goal) {
      btn.hidden = false;
      btn.onclick = function () {
        btn.disabled = true;
        api('POST', '/api/goal', { goal: goal }).then(finish).catch(finish);
      };
    } else {
      btn.hidden = true;
    }
    toast.hidden = false;
    setTimeout(finish, 6000);
  }

  /* ══ Catalog ══
     Concurrent Worlds (sprints/2026-07-28-concurrent-worlds/ARCHITECTURE.md
     §5.3): the Mount button's label is now a pure function of two facts —
     whether the TARGET World has a live instance elsewhere (/api/instances,
     the same registry-driven roster the CLI reads) and whether the CURRENT
     root already has a World mounted (/api/worlds' `current`). */
  function renderCatalog() {
    Promise.all([
      api('GET', '/api/worlds'),
      api('GET', '/api/instances'),
    ]).then(function (results) {
      var worldsRes = results[0];
      var instRes = results[1];
      if (!worldsRes.ok) return;
      var instancesBySlug = {};
      if (instRes.ok) {
        (instRes.data.instances || []).forEach(function (inst) {
          if (inst && inst.slug) instancesBySlug[inst.slug] = inst;
        });
      }
      var currentSlug = worldsRes.data.current || null;
      var grid = $('catalog-grid');
      grid.textContent = '';
      var worlds = worldsRes.data.worlds || [];
      var anyCardMounted = worlds.some(function (w) { return w.mounted; });
      worlds.forEach(function (w) {
        grid.appendChild(worldCard(w, instancesBySlug, currentSlug));
      });
      renderStrayMountHint(currentSlug, anyCardMounted);
      // Import hint card
      var imp = document.createElement('div');
      imp.className = 'world-card import-card';
      var t = document.createElement('div');
      t.textContent = 'Import…';
      t.className = 'world-name';
      var d = document.createElement('div');
      d.textContent = 'Use the world switcher in the nav → ＋ Add a World… to import a bundle or .zip';
      imp.appendChild(t);
      imp.appendChild(d);
      grid.appendChild(imp);
    });
  }

  // Stray-mount escape hatch (F3, REVIEW.md ASK-2): if the root is mounted
  // but its bundle dir vanished/was corrupted out from under it, no
  // catalog card claims `mounted`, so no per-card Unmount button renders
  // anywhere -- and the nav dropdown's old catalog-independent unbind door
  // was removed this sprint. Render a standalone Unmount control above the
  // grid so the root can always be freed from the UI, not just the CLI
  // (`./arailctl world unmount`).
  function renderStrayMountHint(currentSlug, anyCardMounted) {
    var host = $('stray-mount-hint');
    if (!host) return;
    host.textContent = '';
    if (!currentSlug || anyCardMounted) return;
    var box = document.createElement('div');
    box.className = 'card stray-mount-hint';
    box.style.cssText = 'display:flex;align-items:center;gap:.6rem;padding:.6rem .9rem;margin-bottom:1rem;';
    var msg = document.createElement('span');
    msg.style.cssText = 'flex:1;font-size:.82rem;';
    msg.textContent = 'This lab is bound to "' + currentSlug + '", but its files ' +
      "aren't in the catalog below (moved, deleted, or corrupted).";
    box.appendChild(msg);
    var btn = document.createElement('button');
    btn.className = 'btn btn-ghost btn-sm';
    btn.textContent = 'Unmount current World (' + currentSlug + ')';
    btn.addEventListener('click', function () {
      api('POST', '/api/worlds/select', { slug: 'default' }).then(function (r) {
        if (r.ok) location.reload();
        else if (r.data && r.data.message) window.alert(r.data.message);
      });
    });
    box.appendChild(btn);
    host.appendChild(box);
  }

  // Launch never spawns a process from the browser — it renders the exact
  // CLI command and copies it to the clipboard (ARCHITECTURE.md §5.3's
  // refinement/partial-overrule of VISION §2: a one-click launch would be a
  // CSRF-reachable process-execution surface). Same fallback shape nav.js's
  // reveal() already uses for "can't act directly, so copy + tell the user".
  function showLaunchCommand(slug) {
    var cmd = './arailctl start --world ' + slug;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).catch(function () {});
    }
    window.alert('Run this in your terminal (copied to clipboard):\n\n' + cmd);
  }

  function worldCard(w, instancesBySlug, currentSlug) {
    var card = document.createElement('div');
    card.className = 'world-card' + (w.valid ? '' : ' invalid');
    if (!w.valid && w.reason) card.title = String(w.reason);

    var top = document.createElement('div');
    top.className = 'world-card-top';
    var sw = document.createElement('div');
    sw.className = 'world-swatch';
    var tp = w.theme_preview;
    if (tp && HEX_RE.test(tp.start || '') && HEX_RE.test(tp.end || '')) {
      sw.classList.add('has-grad');
      sw.style.setProperty('--w-start', tp.start);
      sw.style.setProperty('--w-end', tp.end);
    }
    top.appendChild(sw);

    var names = document.createElement('div');
    names.className = 'world-names';
    var name = document.createElement('div');
    name.className = 'world-name';
    name.textContent = w.display_name || w.slug;
    var slug = document.createElement('div');
    slug.className = 'world-slug';
    slug.textContent = w.slug;
    names.appendChild(name);
    names.appendChild(slug);
    top.appendChild(names);

    if (w.mounted) {
      var pill = document.createElement('span');
      pill.className = 'mounted-pill';
      pill.textContent = 'MOUNTED';
      top.appendChild(pill);
    }
    var inst = instancesBySlug[w.slug];
    var live = !!(inst && inst.live);
    if (live) {
      var livePill = document.createElement('span');
      livePill.className = 'mounted-pill';
      livePill.textContent = '● :' + inst.portal_port;
      livePill.title = 'Running as its own instance';
      top.appendChild(livePill);
    }
    card.appendChild(top);

    // Mount / Launch / Open / Unmount — a pure function of two facts
    // (ARCHITECTURE.md §5.3's matrix):
    //   live instance                       -> Open   (new tab, non-mutating)
    //   currently mounted HERE               -> Unmount
    //   nothing mounted here, no live instance -> Mount (the first-bind case)
    //   something ELSE mounted here, no live instance -> Launch (copy command)
    var actions = document.createElement('div');
    actions.className = 'world-actions';
    if (live) {
      var open = document.createElement('button');
      open.className = 'btn btn-primary btn-sm';
      open.textContent = 'Open';
      open.addEventListener('click', function () {
        var bind = inst.bind || '127.0.0.1';
        window.open('http://' + bind + ':' + inst.portal_port, '_blank');
      });
      actions.appendChild(open);
    } else if (w.mounted) {
      var un = document.createElement('button');
      un.className = 'btn btn-ghost btn-sm';
      un.textContent = 'Unmount';
      un.addEventListener('click', function () {
        api('POST', '/api/worlds/select', { slug: 'default' }).then(function (r) {
          if (r.ok) location.reload();
          else if (r.data && r.data.message) window.alert(r.data.message);
        });
      });
      actions.appendChild(un);

      // Generic reveal button — opens lab/data/user-import/<slug>/findings/
      // in the OS file browser. Harmless no-op (creates an empty folder) for
      // Worlds with no personal-data agents writing findings there.
      var reveal = document.createElement('button');
      reveal.className = 'btn btn-ghost btn-sm';
      reveal.textContent = 'Reveal findings';
      reveal.addEventListener('click', function () {
        window.revealSlot('user_data', w.slug + '/findings');
      });
      actions.appendChild(reveal);
    } else if (!currentSlug) {
      var mnt = document.createElement('button');
      mnt.className = 'btn btn-primary btn-sm';
      mnt.textContent = 'Mount';
      mnt.disabled = !w.valid;
      mnt.addEventListener('click', function () {
        api('POST', '/api/worlds/select', { slug: w.slug }).then(function (r) {
          if (r.ok) { location.reload(); return; }
          // Race: another tab mounted between render and click -- the
          // server is authoritative (in_place_switch_removed). Surface its
          // message, then re-fetch so the button matrix reflects reality
          // instead of trusting stale client state.
          if (r.data && r.data.message) window.alert(r.data.message);
          renderCatalog();
        });
      });
      actions.appendChild(mnt);
    } else {
      var launch = document.createElement('button');
      launch.className = 'btn btn-primary btn-sm';
      launch.textContent = 'Launch';
      launch.disabled = !w.valid;
      launch.title = './arailctl start --world ' + w.slug;
      launch.addEventListener('click', function () { showLaunchCommand(w.slug); });
      actions.appendChild(launch);
    }
    var del = document.createElement('button');
    del.className = 'btn btn-danger btn-sm';
    del.textContent = 'Delete';
    del.addEventListener('click', function () {
      if (!confirm('Delete world "' + (w.display_name || w.slug) + '"? This removes it from disk.')) return;
      api('DELETE', '/api/worlds/' + encodeURIComponent(w.slug)).then(function (r) {
        if (r.ok) renderCatalog();
      });
    });
    actions.appendChild(del);
    card.appendChild(actions);
    return card;
  }

  /* ══ Boot ══ */
  loadPalettes();
  loadBrainAvailability();
  renderCatalog();
  api('GET', '/api/worlds/forge/status').then(function (res) {
    var st = (res.data || {}).state;
    if (st === 'running') beginPolling();
    else if (st === 'done') loadPreview();
    else showPanel('form');
  }).catch(function () { showPanel('form'); });
})();
