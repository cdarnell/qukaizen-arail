/* nav.js — shared nav bar logic: clock + mode badge (sync + toggle)
   + goal chip (so the active mission is visible on every page) */

/* Loaded once from base.html. Guard against a stray second <script> tag —
   the document-level delegated listeners below would otherwise double-fire
   (e.g. two POSTs per airgap toggle click). */
if (window.__arailNavJs) {
  console.warn('nav.js loaded twice — duplicate ignored');
  throw new Error('nav.js already loaded');
}
window.__arailNavJs = true;

/* Theming moved server-side (src/arail/ui_theme.py): the mounted World /
   LAB_UI_THEME picks the palette and the portal injects it on every page.
   Clear state left behind by the retired client-side data-theme toggle. */
(function () {
  try { localStorage.removeItem('arail-theme'); } catch (e) {}
  document.documentElement.removeAttribute('data-theme');
})();

(function () {
  // ── Goal chip ──
  // Renders a pill with the current goal text in the nav of every page.
  // Clicking it jumps back to the dashboard, where the goal can be edited.
  (function addGoalChip() {
    return;  // disabled — chip was crowding the nav. Mission still on dashboard.
    var nav = document.querySelector('nav');
    if (!nav) return;
    var logoEl = nav.querySelector('.logo');
    fetch('/api/goal')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (goal) {
        if (!goal || !goal.goal_text) return;
        var chip = document.createElement('a');
        chip.className = 'nav-goal';
        chip.href = '/';
        chip.title = 'Current mission: ' + goal.goal_text +
                     ' \u2014 click to edit on the dashboard';
        var icon = document.createElement('span');
        icon.className = 'nav-goal-icon';
        icon.textContent = '\u25CE'; // target reticle — monochrome, terminal-ish
        var label = document.createElement('span');
        label.className = 'nav-goal-label';
        label.textContent = 'Mission';
        var text = document.createElement('span');
        text.className = 'nav-goal-text';
        var full = String(goal.goal_text);
        text.textContent = full.length > 60 ? full.slice(0, 57) + '\u2026' : full;
        chip.appendChild(icon);
        chip.appendChild(label);
        chip.appendChild(text);
        // Insert immediately after the logo so the mission sits on the left.
        if (logoEl && logoEl.parentNode === nav && logoEl.nextSibling) {
          nav.insertBefore(chip, logoEl.nextSibling);
        } else if (logoEl && logoEl.parentNode === nav) {
          nav.appendChild(chip);
        } else {
          nav.insertBefore(chip, nav.firstChild);
        }
      })
      .catch(function () { /* no goal API — silently skip */ });
  })();

  // ── Mode badge ──
  var badge = document.getElementById('mode-badge');
  if (!badge) return;

  function updateBadge(mode) {
    badge.className = 'mode-badge ' + mode;
    badge.textContent = mode === 'airgapped' ? '\u2B24 Airgapped' : '\u2B24 Hybrid';
    badge.title = mode === 'airgapped'
      ? 'Airgapped \u2014 click to see the operational definition and recent blocks.'
      : 'Hybrid \u2014 agent fetches are allowed. Click to see the egress audit.';
  }

  // Sync on load
  fetch('/api/system/mode')
    .then(function (r) { return r.json(); })
    .then(function (d) { updateBadge(d.mode); })
    .catch(function () {});

  // Click \u2192 open airgap modal (populated from /api/airgap/status)
  badge.style.cursor = 'pointer';
  badge.addEventListener('click', function () {
    var backdrop = document.getElementById('airgap-backdrop');
    if (!backdrop) return;
    fetch('/api/airgap/status')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var pill = document.getElementById('airgap-mode-pill');
        if (pill) {
          pill.textContent = data.lab_mode;
          pill.className = 'mp-pill ' + (data.lab_mode === 'airgapped' ? 'ok' : 'warn');
        }
        var defEl = document.getElementById('airgap-definition');
        if (defEl) defEl.textContent = data.definition || '';
        var probeEl = document.getElementById('airgap-host-probe');
        if (probeEl) {
          if (data.host_can_reach_internet === null || data.host_can_reach_internet === undefined) {
            probeEl.style.display = 'none';
          } else {
            probeEl.style.display = '';
            probeEl.textContent = data.host_can_reach_internet
              ? 'Your host has internet, but the lab refuses to use it. That\u2019s the honest disclosure.'
              : 'Your host can\u2019t reach the internet either.';
          }
        }
        var list = document.getElementById('airgap-activity-list');
        var emptyMsg = document.getElementById('airgap-activity-empty');
        if (list) {
          list.innerHTML = '';
          var items = data.recent_activity || [];
          if (items.length === 0) {
            if (emptyMsg) emptyMsg.style.display = '';
          } else {
            if (emptyMsg) emptyMsg.style.display = 'none';
            items.forEach(function (item) {
              var row = document.createElement('div');
              row.className = 'airgap-row';
              var kind = item.kind || 'blocked';
              var kindPill = '<span class="mp-pill ' + (kind === 'blocked' ? 'warn' : 'ok') + '">'
                + kind + '</span>';
              var ts = item.ts ? item.ts.replace('T', ' ').replace('Z', '') : '';
              row.innerHTML = kindPill
                + ' <code>' + (item.url_host || '?') + '</code>'
                + ' <span class="chat-muted">' + (item.caller || '') + '</span>'
                + ' <time datetime="' + (item.ts || '') + '">' + ts.slice(-8) + '</time>';
              list.appendChild(row);
            });
          }
        }
        var gapsList = document.getElementById('airgap-gaps-list');
        if (gapsList && data.known_gaps) {
          gapsList.innerHTML = '';
          data.known_gaps.forEach(function (gap) {
            var li = document.createElement('li');
            li.textContent = gap;
            gapsList.appendChild(li);
          });
        }

        // -- Segmented control wiring --
        var toggleSection = document.getElementById('airgap-toggle-section');
        var toggleBindWarn = document.getElementById('airgap-toggle-bind-warning');
        var toggleSegmented = document.getElementById('airgap-toggle-segmented');
        var toggleError = document.getElementById('airgap-toggle-error');

        if (toggleSection) {
          // Reset error state on each modal open.
          if (toggleError) toggleError.style.display = 'none';

          if (data.bind_is_loopback === false) {
            // Non-loopback bind: show warning, hide segmented control.
            if (toggleBindWarn) toggleBindWarn.style.display = '';
            if (toggleSegmented) toggleSegmented.style.display = 'none';
          } else {
            // Loopback bind: show segmented control, set active half.
            if (toggleBindWarn) toggleBindWarn.style.display = 'none';
            if (toggleSegmented) {
              toggleSegmented.style.display = '';
              var currentMode = data.lab_mode || 'airgapped';
              var segBtns = toggleSegmented.querySelectorAll('button[data-target]');
              segBtns.forEach(function (btn) {
                btn.classList.toggle('active', btn.dataset.target === currentMode);
              });
            }
          }
        }

        backdrop.classList.add('open');
      })
      .catch(function (err) {
        console.error('[airgap] status fetch failed:', err);
      });
  });

  // -- Modal close: multiple fallback methods --
  // The airgap modal include sits AFTER this script tag on every page,
  // so the element does not exist at IIFE-run time. Defer the listener
  // hookup until DOMContentLoaded so it works uniformly on every tab
  // (was previously silently broken on Autoresearch / Research because
  // its own modal layer captured clicks before falling through to a
  // never-attached backdrop listener).
  function wireAirgapClose() {
    var airgapBackdrop = document.getElementById('airgap-backdrop');
    if (!airgapBackdrop) return;

    // Backdrop click-outside listener
    airgapBackdrop.addEventListener('click', function (e) {
      if (e.target === airgapBackdrop) airgapBackdrop.classList.remove('open');
    });

    // Escape key to close
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && airgapBackdrop.classList.contains('open')) {
        airgapBackdrop.classList.remove('open');
      }
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireAirgapClose);
  } else {
    wireAirgapClose();
  }

  // -- Segmented control: optimistic flip + single POST --
  // Delegate listener on document level to ensure it catches all clicks
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.airgap-segmented button[data-target]');
    if (!btn) return;

    var target = btn.dataset.target;
    if (!target || target.trim() === '') return;

    // Double-check button is actually visible and enabled
    var segmented = document.getElementById('airgap-toggle-segmented');
    if (!segmented || segmented.style.display === 'none') return;

    if (btn.disabled) return;

    // Find the currently active button
    var allBtns = document.querySelectorAll('.airgap-segmented button[data-target]');
    var prevActive = null;
    allBtns.forEach(function (b) {
      if (b.classList.contains('active')) prevActive = b.dataset.target;
    });
    if (prevActive === target) return; // already active

    // Optimistic flip
    allBtns.forEach(function (b) {
      b.classList.toggle('active', b.dataset.target === target);
      b.disabled = true;
    });

    var pill = document.getElementById('airgap-mode-pill');
    if (pill) {
      pill.textContent = target;
      pill.className = 'mp-pill ' + (target === 'airgapped' ? 'ok' : 'warn');
    }

    var errEl = document.getElementById('airgap-toggle-error');
    if (errEl) errEl.style.display = 'none';

    // Send toggle request
    fetch('/api/airgap/toggle', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: target }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, status: r.status, body: body };
        });
      })
      .then(function (res) {
        // Re-enable all buttons
        allBtns.forEach(function (b) { b.disabled = false; });

        if (res.ok && res.status === 200) {
          // Success: use server response as source of truth
          var confirmedMode = res.body.lab_mode || target;
          allBtns.forEach(function (b) {
            b.classList.toggle('active', b.dataset.target === confirmedMode);
          });
          if (pill) {
            pill.textContent = confirmedMode;
            pill.className = 'mp-pill ' + (confirmedMode === 'airgapped' ? 'ok' : 'warn');
          }
          updateBadge(confirmedMode);
        } else {
          // Failure: revert optimistic flip
          allBtns.forEach(function (b) {
            b.classList.toggle('active', b.dataset.target === prevActive);
          });
          if (pill) {
            pill.textContent = prevActive;
            pill.className = 'mp-pill ' + (prevActive === 'airgapped' ? 'ok' : 'warn');
          }

          var errCode = res.body && res.body.error;
          var msg = 'Save failed — check server log.';
          if (errCode === 'bind_not_loopback') {
            msg = 'Toggle disabled when lab is bound beyond loopback. Edit `.env` directly.';
          } else if (errCode === 'cross_origin') {
            msg = 'This action must be initiated from the lab UI.';
          } else if (errCode === 'invalid_target') {
            msg = 'Toggle failed — please reload the modal.';
          }

          if (errEl) {
            errEl.textContent = msg;
            errEl.style.display = '';
            setTimeout(function () {
              if (errEl) errEl.style.display = 'none';
            }, 5000);
          }
        }
      })
      .catch(function (err) {
        console.error('Toggle error:', err);
        allBtns.forEach(function (b) { b.disabled = false; });

        // Revert on network error
        allBtns.forEach(function (b) {
          b.classList.toggle('active', b.dataset.target === prevActive);
        });
        if (pill) {
          pill.textContent = prevActive;
          pill.className = 'mp-pill ' + (prevActive === 'airgapped' ? 'ok' : 'warn');
        }

        if (errEl) {
          errEl.textContent = 'Network error — flip not saved.';
          errEl.style.display = '';
          setTimeout(function () {
            if (errEl) errEl.style.display = 'none';
          }, 5000);
        }
      });
  });
})();

/* ── Work-window badge → schedule modal ──────────────────────────
   Own IIFE (not inside the mode-badge one, which early-returns when
   #mode-badge is absent). Mirrors the airgap modal pattern: click the
   badge → populate from /api/jobs/state → open; segmented control
   POSTs /api/window/override with optimistic flip + revert on error. */
(function () {
  var badge = document.getElementById('window-badge');
  if (!badge) return;

  function renderModal(s) {
    var pill = document.getElementById('window-mode-pill');
    if (pill) {
      pill.textContent = s.window;
      pill.className = 'mp-pill ' + (s.window === 'heavy' ? 'warn' : 'ok');
    }
    var ha = document.getElementById('window-hours-active');
    if (ha) ha.textContent = s.active_hours || '—';
    var hh = document.getElementById('window-hours-heavy');
    if (hh) hh.textContent = s.heavy_hours || '—';

    var status = document.getElementById('window-override-status');
    if (status) {
      if (s.override) {
        var noun = s.override.window === 'active' ? 'Light work' : 'Heavy work';
        status.textContent = 'Pinned to ' + noun + ' until ' +
          String(s.override.expires_at).slice(11, 16) + '.';
        status.style.display = '';
      } else {
        status.style.display = 'none';
      }
    }

    var current = s.override ? s.override.window : '';
    document.querySelectorAll('#window-toggle-segmented button[data-window]')
      .forEach(function (b) {
        b.classList.toggle('active', b.dataset.window === current);
        b.disabled = false;
      });
  }

  badge.style.cursor = 'pointer';
  badge.title = 'Work schedule — click to pin light or heavy work';
  badge.addEventListener('click', function () {
    var backdrop = document.getElementById('window-backdrop');
    if (!backdrop) return;
    fetch('/api/jobs/state')
      .then(function (r) { return r.json(); })
      .then(function (s) {
        renderModal(s);
        backdrop.classList.add('open');
      })
      .catch(function (err) {
        console.error('[window] state fetch failed:', err);
      });
  });

  function wireWindowClose() {
    var backdrop = document.getElementById('window-backdrop');
    if (!backdrop) return;
    backdrop.addEventListener('click', function (e) {
      if (e.target === backdrop) backdrop.classList.remove('open');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && backdrop.classList.contains('open')) {
        backdrop.classList.remove('open');
      }
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireWindowClose);
  } else {
    wireWindowClose();
  }

  // Segmented control — data-window (NOT data-target) so the airgap
  // delegated listener above can never match these buttons.
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('#window-toggle-segmented button[data-window]');
    if (!btn || btn.disabled) return;

    var w = btn.dataset.window; // '' = follow schedule
    var allBtns = document.querySelectorAll('#window-toggle-segmented button[data-window]');
    var prevActive = '';
    allBtns.forEach(function (b) {
      if (b.classList.contains('active')) prevActive = b.dataset.window;
    });
    if (prevActive === w) return;

    allBtns.forEach(function (b) {
      b.classList.toggle('active', b.dataset.window === w);
      b.disabled = true;
    });
    var errEl = document.getElementById('window-toggle-error');
    if (errEl) errEl.style.display = 'none';

    fetch('/api/window/override', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(w ? { window: w } : { window: null, clear: true }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (res) {
        allBtns.forEach(function (b) { b.disabled = false; });
        if (res.ok) {
          renderModal(res.body);
          if (res.body.label) {
            badge.textContent = res.body.label +
              (res.body.override ? ' · until ' + String(res.body.override.expires_at).slice(11, 16) : '');
            badge.className = 'mode-badge window-' + res.body.window;
          }
        } else {
          allBtns.forEach(function (b) {
            b.classList.toggle('active', b.dataset.window === prevActive);
          });
          var errCode = res.body && res.body.error;
          var msg = 'Save failed — check server log.';
          if (errCode === 'bind_not_loopback') {
            msg = 'Override disabled when lab is bound beyond loopback.';
          } else if (errCode === 'cross_origin' || errCode === 'cross_site') {
            msg = 'This action must be initiated from the lab UI.';
          }
          if (errEl) {
            errEl.textContent = msg;
            errEl.style.display = '';
            setTimeout(function () { if (errEl) errEl.style.display = 'none'; }, 5000);
          }
        }
      })
      .catch(function (err) {
        console.error('[window] override error:', err);
        allBtns.forEach(function (b) {
          b.disabled = false;
          b.classList.toggle('active', b.dataset.window === prevActive);
        });
        if (errEl) {
          errEl.textContent = 'Network error — override not saved.';
          errEl.style.display = '';
          setTimeout(function () { if (errEl) errEl.style.display = 'none'; }, 5000);
        }
      });
  });
})();

/* ── Whisper toast component ─────────────────────────────────────
   Proactive low-volume notifications from the personality agent.
   See docs/design.md §6 (corner-toast whisper).

   Public API (any page can call):
       window.ARAIL.whisper.show({
           text:    "found 3 new papers on speculative decoding",
           agent:   "scout",                // optional, shown as a label
           threadId: "abc123",              // optional, click → /chat?thread=abc123
           tone:    "purple",               // purple | blue | green | amber
           ttl:     8000                    // ms, default 8000
       })

   Server hook: every 15s we poll /api/whispers/recent and surface any
   new entries. The endpoint should return:
       { whispers: [ { id, text, agent, thread_id, tone, ts } ] }
   Returning [] is fine — the UI silently does nothing. */
(function () {
  var ARAIL = (window.ARAIL = window.ARAIL || {});
  var stack = null;
  function ensureStack() {
    if (stack) return stack;
    stack = document.getElementById('whisper-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'whisper-stack';
      stack.className = 'whisper-stack';
      document.body.appendChild(stack);
    }
    return stack;
  }

  function show(opts) {
    opts = opts || {};
    var el = document.createElement('div');
    var tone = opts.tone || 'purple';
    el.className = 'whisper tone-' + tone;
    el.setAttribute('role', 'status');

    if (opts.agent) {
      var label = document.createElement('div');
      label.className = 'whisper-agent';
      label.textContent = opts.agent;
      el.appendChild(label);
    }
    var body = document.createElement('div');
    body.className = 'whisper-text';
    body.textContent = opts.text || '';
    el.appendChild(body);

    var close = document.createElement('button');
    close.className = 'whisper-close';
    close.setAttribute('aria-label', 'dismiss');
    close.textContent = '×';
    close.addEventListener('click', function (e) { e.stopPropagation(); dismiss(el); });
    el.appendChild(close);

    if (opts.threadId) {
      el.classList.add('whisper-clickable');
      el.addEventListener('click', function () {
        window.location.href = '/chat?thread=' + encodeURIComponent(opts.threadId);
      });
    }

    ensureStack().appendChild(el);
    // Slide in on next frame so the CSS transition runs.
    requestAnimationFrame(function () { el.classList.add('whisper-in'); });

    var ttl = opts.ttl == null ? 8000 : opts.ttl;
    if (ttl > 0) setTimeout(function () { dismiss(el); }, ttl);
    return el;
  }

  function dismiss(el) {
    if (!el || !el.parentNode) return;
    el.classList.remove('whisper-in');
    el.classList.add('whisper-out');
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 250);
  }

  ARAIL.whisper = { show: show, dismiss: dismiss };

  // ── Poll loop ──
  var seen = new Set();
  function poll() {
    fetch('/api/whispers/recent')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !Array.isArray(d.whispers)) return;
        d.whispers.forEach(function (w) {
          if (seen.has(w.id)) return;
          seen.add(w.id);
          show({
            text: w.text, agent: w.agent, threadId: w.thread_id,
            tone: w.tone || 'purple', ttl: w.ttl
          });
        });
      })
      .catch(function () { /* endpoint may not exist yet — silent */ });
  }
  // Slight delay so the page paints before we poll.
  setTimeout(poll, 1500);
  setInterval(poll, 15000);

  // Demo: ?whisper-demo=1 surfaces a sample toast for visual QA.
  if (location.search.indexOf('whisper-demo=1') !== -1) {
    setTimeout(function () {
      show({
        text: 'found 3 new papers on speculative decoding worth a skim',
        agent: 'pip', tone: 'purple', threadId: 'demo'
      });
    }, 600);
  }
})();

/* revealSlot — open a whitelisted lab folder in the OS file browser.
 * Exposed globally so any page (knowledge, admin, chat providers
 * modal) can call it without re-implementing the fallback toast.
 *
 * Slots: inbox · models · pkb_root · sources · compiled.
 * subpath: optional path *under* the slot root; server rejects
 * traversal escapes.
 *
 * Returns the server response so the caller can chain UI updates.
 * Shows a non-blocking notice with the absolute path when the
 * server can't open Finder (headless / unsupported platform).
 */
window.revealSlot = async function revealSlot(slot, subpath) {
  let resp;
  try {
    resp = await fetch('/api/system/reveal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot: slot, subpath: subpath || '' }),
    });
  } catch (e) {
    window.alert('Reveal failed: ' + e.message);
    return { opened: false, error: e.message };
  }
  let data;
  try { data = await resp.json(); } catch (_) { data = {}; }
  if (!resp.ok) {
    window.alert('Reveal failed: ' + (data.error || resp.status));
    return data;
  }
  if (!data.opened) {
    // Headless / unsupported — copy path to clipboard if we can.
    const path = data.path || '(no path)';
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(path); } catch (_) { /* ignore */ }
    }
    window.alert(
      'Could not open the OS file browser (' + (data.reason || 'unknown') + ').\n\n' +
      'Path copied to clipboard:\n' + path
    );
  }
  return data;
};

/* ── World switcher dropdown ──────────────────────────────────────
   The nav badge is a <details> popover: a pure roster/viewer, not a
   mutator (worlds-select-removal, ARCHITECTURE.md — "nav does not
   mutate at all"). On first open we fetch the catalog (/api/worlds)
   and the instance roster (/api/instances) and render: "AI Lab
   (default)" (routes to /worlds when a World is mounted, otherwise
   inert), then each discovered World — a live instance opens as a
   link, a non-live World reveals its launch command, the currently
   mounted World is an inert ✓ row. Import (path and .zip) stays here —
   both /api/worlds/import and /api/worlds/import-zip carry their own
   in_place_switch_removed guard, refusing server-side when this root
   is already mounted, so the affordance degrades to a 409 + toast
   rather than a silent sweep. Outside-click / Escape closes. Vanilla,
   airgap-safe. */
(function () {
  var details = document.getElementById('world-switcher');
  var menu = document.getElementById('world-menu');
  if (!details || !menu) return;

  var loaded = false;
  var busy = false;  // import-only lock; the switcher itself never mutates
  var _lastJson = null;
  var _lastInstJson = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function row(opts) {
    // opts: {label, action, slug, path, active, disabled, reason, live, port, url}
    var active = opts.active;
    var disabled = opts.disabled;
    var style =
      'display:flex;align-items:center;gap:.4rem;padding:.4rem .6rem;border-radius:7px;' +
      'font-size:.78rem;white-space:nowrap;' +
      (disabled
        ? 'opacity:.45;pointer-events:none;cursor:default;'
        : 'cursor:pointer;') +
      (active ? 'font-weight:700;' : '');
    var mark = active ? '\u2713 ' : '  ';
    // "disabled" covers two different reasons: genuinely unavailable
    // (not launched yet) vs. inert because it's already the active
    // selection (checkmarked). Only the former should say "unavailable".
    var tail = (disabled && !active)
      ? ' <span style="opacity:.7;font-size:.68rem;">(unavailable)</span>'
      : '';
    // Concurrent Worlds (ARCHITECTURE.md \u00a75.4): a liveness dot + :port
    // for a World running as its own instance -- the roster-viewer half of
    // the dropdown, alongside the existing per-World select/mount rows.
    if (opts.live) {
      tail += ' <span style="opacity:.75;font-size:.68rem;color:var(--green,#3a3);">' +
        '\u25cf :' + esc(String(opts.port || '')) + '</span>';
    }
    var attrs = 'class="world-row" role="menuitem" style="' + style + '"';
    if (!disabled) {
      attrs += ' data-action="' + esc(opts.action || '') + '"';
      if (opts.slug) attrs += ' data-slug="' + esc(opts.slug) + '"';
      if (opts.path) attrs += ' data-path="' + esc(opts.path) + '"';
      if (opts.url) attrs += ' data-url="' + esc(opts.url) + '"';
    }
    if (opts.reason) attrs += ' title="' + esc(opts.reason) + '"';
    // Theme swatch placeholder -- rendered empty here, painted afterwards via
    // style assignment (never interpolated into HTML; values are
    // server-validated hex and re-checked client-side).
    var swatch = opts.hasSwatch
      ? '<span class="ws-swatch" data-swatch-slug="' + esc(opts.slug) + '"' +
        ' style="width:26px;height:26px;border-radius:8px;flex:none;' +
        'border:1px solid var(--border-strong);"></span>'
      : '';
    return '<div ' + attrs + '><span>' + mark + '</span>' + swatch + '<span>' +
      esc(opts.label) + tail + '</span></div>';
  }

  function render(json, instJson) {
    _lastJson = json;
    _lastInstJson = instJson;
    // Concurrent Worlds (ARCHITECTURE.md §5.4): a per-slug liveness
    // lookup from /api/instances -- the SAME registry-driven roster the
    // CLI reads, no cross-instance HTTP, no in-memory shared state.
    var instancesBySlug = {};
    ((instJson && instJson.instances) || []).forEach(function (inst) {
      if (inst && inst.slug) instancesBySlug[inst.slug] = inst;
    });
    var html = '';
    var mounted = json.current !== null && json.current !== undefined;
    // "AI Lab (default)" row: non-mutating. When a World is mounted it
    // routes to /worlds -- unmount lives on one surface, the Worlds page --
    // otherwise it's just the (already active) inert row.
    if (mounted) {
      html += row({ label: 'AI Lab (default)', action: 'goto-worlds' });
    } else {
      html += row({ label: 'AI Lab (default)', active: true, disabled: true });
    }
    var worlds = (json && json.worlds) || [];
    worlds.forEach(function (w) {
      if (!w.valid) {
        html += row({
          label: w.display_name || w.slug,
          disabled: true,
          reason: w.reason || 'unavailable',
        });
        return;
      }
      var inst = instancesBySlug[w.slug];
      var live = !!(inst && inst.live);
      if (live) {
        // Live instance -- a plain link, never a mutation.
        var bind = inst.bind || '127.0.0.1';
        html += row({
          label: w.display_name || w.slug,
          action: 'open',
          slug: w.slug,
          url: 'http://' + bind + ':' + inst.portal_port,
          live: true,
          port: inst.portal_port,
          hasSwatch: !!w.theme_preview,
        });
        return;
      }
      if (w.mounted) {
        // Currently mounted here: inert, checkmark only. In-place switching
        // is removed -- clicking another World's row must never mutate.
        html += row({
          label: w.display_name || w.slug,
          active: true,
          disabled: true,
          hasSwatch: !!w.theme_preview,
        });
        return;
      }
      // Not live, not mounted here: non-mutating. Reveal the instance
      // launch command instead of a select POST (worlds-select-removal --
      // the nav dropdown never mounts).
      html += row({
        label: w.display_name || w.slug,
        disabled: true,
        reason: 'Run as its own lab: ./arailctl start --world ' + w.slug,
        hasSwatch: !!w.theme_preview,
      });
    });
    // Consumer-side "Add a World" affordance -- import a sealed bundle from a
    // path outside the catalog (a DaC export, a shared World).
    html +=
      '<div style="border-top:1px solid var(--border);margin:.3rem 0;"></div>' +
      row({ label: '✦ Forge a World…', action: 'forge' }) +
      row({ label: '＋ Add a World…', action: 'add' }) +
      '<div style="padding:.35rem .6rem .2rem;font-size:.62rem;color:var(--text-muted);' +
      'font-family:var(--font-sans);">Themes are token swaps — a World changes the ' +
      'lab’s look and knowledge, nothing else.</div>';
    menu.innerHTML = html;

    // Paint theme swatches (two-stop gradient: world bg → accent).
    var HEX = /^#[0-9a-fA-F]{6}$/;
    worlds.forEach(function (w) {
      var p = w.theme_preview;
      if (!w.valid || !p || !HEX.test(p.start || '') || !HEX.test(p.end || '')) return;
      var sel = '.ws-swatch[data-swatch-slug="' +
        (window.CSS && CSS.escape ? CSS.escape(w.slug) : w.slug) + '"]';
      var el = menu.querySelector(sel);
      if (!el) return;
      el.style.background = 'linear-gradient(135deg, ' + p.start + ' 20%, ' + p.end + ' 80%)';
      if (p.personality) el.title = p.personality;
    });
  }

  // Swap the menu to an inline path input → POST /api/worlds/import, with a
  // ".zip" drop for a World a friend shared → POST /api/worlds/import-zip.
  function showImport() {
    menu.innerHTML =
      '<div style="padding:.45rem .6rem;display:flex;flex-direction:column;gap:.4rem;min-width:240px;">' +
      '<div style="font-size:.72rem;opacity:.8;">Path to a WorldBundle folder:</div>' +
      '<input id="world-import-path" type="text" spellcheck="false" ' +
      'placeholder="/path/to/bundles/physics" ' +
      'style="font-size:.74rem;padding:.35rem .45rem;border-radius:6px;border:1px solid var(--border);' +
      'background:var(--surface);color:var(--text);font-family:inherit;" />' +
      '<div style="display:flex;gap:.4rem;justify-content:flex-end;">' +
      '<button id="world-import-cancel" type="button" class="world-row" ' +
      'style="cursor:pointer;font-size:.74rem;padding:.3rem .6rem;border-radius:6px;' +
      'border:1px solid var(--border);background:transparent;color:var(--text);">Cancel</button>' +
      '<button id="world-import-go" type="button" class="world-row" ' +
      'style="cursor:pointer;font-size:.74rem;padding:.3rem .7rem;border-radius:6px;' +
      'border:1px solid var(--blue);background:transparent;color:var(--blue);font-weight:700;">Import</button>' +
      '</div>' +
      // ── Peer-sharing: bring in a .zip a friend sent ──
      '<div style="border-top:1px dashed var(--border);margin:.15rem 0 .1rem;"></div>' +
      '<div style="font-size:.72rem;opacity:.8;">…or a World a friend shared:</div>' +
      '<button id="world-import-zip-pick" type="button" class="world-row" ' +
      'style="cursor:pointer;font-size:.74rem;padding:.35rem .5rem;border-radius:6px;text-align:left;' +
      'border:1px solid var(--border);background:transparent;color:var(--text);">📦 Choose a .zip…</button>' +
      '<input id="world-import-zip" type="file" accept=".zip,application/zip" ' +
      'style="display:none;" />' +
      '</div>';
    var input = document.getElementById('world-import-path');
    if (input) input.focus();
    var go = document.getElementById('world-import-go');
    var cancel = document.getElementById('world-import-cancel');
    if (cancel) cancel.addEventListener('click', function () { render(_lastJson, _lastInstJson); });
    if (go) go.addEventListener('click', doImport);
    if (input) input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') doImport();
    });
    var pick = document.getElementById('world-import-zip-pick');
    var zipInput = document.getElementById('world-import-zip');
    if (pick && zipInput) {
      pick.addEventListener('click', function () { zipInput.click(); });
      zipInput.addEventListener('change', function () {
        if (zipInput.files && zipInput.files[0]) doImportZip(zipInput.files[0]);
      });
    }
  }

  function doImport() {
    var input = document.getElementById('world-import-path');
    var path = input ? input.value.trim() : '';
    if (!path || busy) return;
    busy = true;
    menu.style.pointerEvents = 'none';
    menu.style.opacity = '.6';
    fetch('/api/worlds/import', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path }),
    })
      .then(function (r) {
        if (r.ok) { window.location.reload(); return null; }
        return r.json().catch(function () { return {}; }).then(function (b) {
          whisper((b && b.message) || 'World import failed');
        });
      })
      .catch(function () { whisper('World import failed'); })
      .then(function () {
        busy = false;
        menu.style.pointerEvents = '';
        menu.style.opacity = '';
      });
  }

  function doImportZip(file) {
    if (!file || busy) return;
    busy = true;
    menu.style.pointerEvents = 'none';
    menu.style.opacity = '.6';
    var fd = new FormData();
    fd.append('file', file, file.name);
    fetch('/api/worlds/import-zip', {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,  // browser sets multipart boundary; no Content-Type header
    })
      .then(function (r) {
        if (r.ok) { window.location.reload(); return null; }
        return r.json().catch(function () { return {}; }).then(function (b) {
          whisper((b && b.message) || 'World import failed');
        });
      })
      .catch(function () { whisper('World import failed'); })
      .then(function () {
        busy = false;
        menu.style.pointerEvents = '';
        menu.style.opacity = '';
      });
  }

  function whisper(text) {
    if (window.ARAIL && window.ARAIL.whisper && window.ARAIL.whisper.show) {
      window.ARAIL.whisper.show({ text: text, tone: 'amber' });
    }
  }

  function load() {
    menu.innerHTML =
      '<div style="padding:.4rem .6rem;font-size:.72rem;opacity:.7;">Loading…</div>';
    // Fetch the catalog and the instance roster in parallel — same posture
    // as worlds.js's renderCatalog() (ARCHITECTURE.md §5.4). A failed
    // /api/instances fetch degrades to "no liveness info", never blocks
    // the catalog itself.
    Promise.all([
      fetch('/api/worlds', { cache: 'no-store', credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r); }),
      fetch('/api/instances', { cache: 'no-store', credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { instances: [] }; })
        .catch(function () { return { instances: [] }; }),
    ])
      .then(function (results) {
        loaded = true;
        render(results[0], results[1]);
      })
      .catch(function () {
        menu.innerHTML =
          '<div style="padding:.4rem .6rem;font-size:.72rem;opacity:.7;">' +
          'Could not load Worlds.</div>';
      });
  }

  details.addEventListener('toggle', function () {
    if (details.open && !loaded) load();
  });

  menu.addEventListener('click', function (e) {
    var el = e.target.closest('.world-row[data-action]');
    if (!el || busy) return;
    var action = el.getAttribute('data-action');
    if (action === 'goto-worlds') { window.location.href = '/worlds'; return; }
    if (action === 'forge') { window.location.href = '/worlds'; return; }
    if (action === 'add') { showImport(); return; }
    if (action === 'open') {
      // Live instance — a plain link, never a mutation.
      var url = el.getAttribute('data-url') || '';
      if (url) window.open(url, '_blank');
      return;
    }
    // No other row mutates. Non-live/non-mounted rows are disabled (their
    // launch command is in the title/reason), and the dropdown never mounts
    // or unmounts a World.
  });

  // Outside-click closes the popover.
  document.addEventListener('click', function (e) {
    if (details.open && !details.contains(e.target)) details.open = false;
  });
  // Escape closes.
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && details.open) details.open = false;
  });
})();
