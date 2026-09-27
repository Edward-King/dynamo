/* ═══════════════════════════════════════════════════════
   Dispatch — Coding Task Intake
   State → Markdown brief. Persistence via localStorage
   with an in-memory fallback for sandboxed iframes.
   ═══════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── Field registry ─────────────────────────────── */
  var FIELDS = [
    'f-title', 'f-project', 'f-context', 'f-agent',
    'f-objective', 'f-inscope', 'f-outscope',
    'f-criteria', 'f-files',
    'f-packaging', 'f-verification', 'f-notes'
  ];

  var REQUIRED = ['f-title', 'f-objective', 'f-criteria'];

  var STORE_KEY = 'dispatch-intake-v1';

  /* ── In-memory state store ──
     Browser storage APIs are unavailable inside the preview iframe,
     so state lives in memory for the session. Export JSON to persist
     a brief between visits, and re-import it later. ── */
  var memoryStore = {};
  var storage = {
    get: function (k) { return k in memoryStore ? memoryStore[k] : null; },
    set: function (k, v) { memoryStore[k] = v; },
    del: function (k) { delete memoryStore[k]; }
  };

  /* ── DOM refs ───────────────────────────────────── */
  var $ = function (id) { return document.getElementById(id); };
  var previewOut = $('preview-output');
  var completenessEl = $('completeness');
  var toastEl = $('toast');

  /* ── Theme toggle ───────────────────────────────── */
  var themeBtn = $('theme-toggle');
  var iconSun = $('icon-sun');
  var iconMoon = $('icon-moon');
  var theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  var savedTheme = storage.get('dispatch-theme');
  if (savedTheme === 'dark' || savedTheme === 'light') theme = savedTheme;

  function applyTheme() {
    document.documentElement.setAttribute('data-theme', theme);
    var dark = theme === 'dark';
    iconSun.style.display = dark ? 'block' : 'none';
    iconMoon.style.display = dark ? 'none' : 'block';
    themeBtn.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  }
  applyTheme();
  themeBtn.addEventListener('click', function () {
    theme = theme === 'dark' ? 'light' : 'dark';
    storage.set('dispatch-theme', theme);
    applyTheme();
  });

  /* ── State ──────────────────────────────────────── */
  function getState() {
    var s = {};
    FIELDS.forEach(function (id) {
      var el = $(id);
      s[id] = el ? el.value : '';
    });
    var radio = document.querySelector('input[name="deliverable"]:checked');
    s.deliverable = radio ? radio.value : 'Pull request';
    return s;
  }

  function setState(s) {
    if (!s || typeof s !== 'object') return;
    FIELDS.forEach(function (id) {
      if (typeof s[id] === 'string' && $(id)) $(id).value = s[id];
    });
    if (typeof s.deliverable === 'string') {
      var radio = document.querySelector('input[name="deliverable"][value="' + s.deliverable + '"]');
      if (radio) radio.checked = true;
    }
  }

  function saveState() {
    try { storage.set(STORE_KEY, JSON.stringify(getState())); } catch (e) { /* ignore */ }
  }

  function loadState() {
    try {
      var raw = storage.get(STORE_KEY);
      if (raw) setState(JSON.parse(raw));
    } catch (e) { /* ignore */ }
  }

  /* ── Markdown generation ────────────────────────── */
  function lines(val) {
    return val.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
  }

  function slugify(str) {
    var s = str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return s || 'task-brief';
  }

  function buildMarkdown(s) {
    var out = [];
    var title = s['f-title'].trim() || 'Untitled task';
    var today = new Date().toISOString().slice(0, 10);

    out.push('# Task Brief — ' + title);
    out.push('');

    var meta = [];
    if (s['f-project'].trim()) meta.push(['Project', s['f-project'].trim()]);
    if (s['f-context'].trim()) meta.push(['Context', s['f-context'].trim()]);
    if (s['f-agent'].trim()) meta.push(['Agent & constraints', s['f-agent'].trim()]);
    meta.push(['Prepared', today]);
    meta.forEach(function (m) { out.push('- **' + m[0] + ':** ' + m[1]); });
    out.push('');

    out.push('## Objective');
    out.push('');
    out.push(s['f-objective'].trim() || '_Not specified._');
    out.push('');

    var inScope = lines(s['f-inscope']);
    var outScope = lines(s['f-outscope']);
    if (inScope.length || outScope.length) {
      out.push('## Scope');
      out.push('');
      if (inScope.length) {
        out.push('**In scope**');
        out.push('');
        inScope.forEach(function (l) { out.push('- ' + l); });
        out.push('');
      }
      if (outScope.length) {
        out.push('**Out of scope**');
        out.push('');
        outScope.forEach(function (l) { out.push('- ' + l); });
        out.push('');
      }
    }

    var criteria = lines(s['f-criteria']);
    if (criteria.length) {
      out.push('## Acceptance Criteria');
      out.push('');
      criteria.forEach(function (l) { out.push('- [ ] ' + l); });
      out.push('');
    }

    var files = lines(s['f-files']);
    if (files.length) {
      out.push('## Files to Touch');
      out.push('');
      files.forEach(function (l) {
        var m = l.match(/^(.+?)\s+—\s+(.+)$/);
        if (m) out.push('- `' + m[1].trim() + '` — ' + m[2].trim());
        else out.push('- `' + l + '`');
      });
      out.push('');
    }

    var hasPackaging = s['f-packaging'].trim() || s['f-verification'].trim();
    if (hasPackaging) {
      out.push('## Packaging & Delivery');
      out.push('');
      out.push('- **Deliverable format:** ' + s.deliverable);
      out.push('');
      if (s['f-packaging'].trim()) {
        out.push(s['f-packaging'].trim());
        out.push('');
      }
      if (s['f-verification'].trim()) {
        out.push('**Verify with**');
        out.push('');
        out.push('```sh');
        s['f-verification'].trim().split('\n').forEach(function (l) {
          if (l.trim()) out.push(l.trim());
        });
        out.push('```');
        out.push('');
      }
    } else {
      out.push('## Packaging & Delivery');
      out.push('');
      out.push('- **Deliverable format:** ' + s.deliverable);
      out.push('');
    }

    if (s['f-notes'].trim()) {
      out.push('## Notes');
      out.push('');
      out.push(s['f-notes'].trim());
      out.push('');
    }

    return out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
  }

  /* ── Completeness ───────────────────────────────── */
  function updateCompleteness(s) {
    var filled = REQUIRED.filter(function (id) { return s[id].trim().length > 0; }).length;
    var pct = Math.round((filled / REQUIRED.length) * 100);
    completenessEl.textContent = pct + '%';
    completenessEl.classList.toggle('done', pct === 100);
    completenessEl.title = pct === 100
      ? 'All required fields filled'
      : 'Required: title, objective, acceptance criteria';
  }

  /* ── Render ─────────────────────────────────────── */
  var EMPTY_MSG = '# Task Brief — Untitled task\n\n- **Prepared:** ' + new Date().toISOString().slice(0, 10) + '\n\n## Objective\n\n_Not specified._\n\n_Start filling the form — the brief assembles here as you type._';

  function render() {
    var s = getState();
    var empty = FIELDS.every(function (id) { return !s[id].trim(); });
    previewOut.textContent = empty ? EMPTY_MSG : buildMarkdown(s);
    updateCompleteness(s);
    saveState();
  }

  /* ── Toast ──────────────────────────────────────── */
  var toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 2200);
  }

  /* ── Download helper ────────────────────────────── */
  function download(filename, text, mime) {
    var blob = new Blob([text], { type: mime || 'text/plain' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  /* ── Export actions ─────────────────────────────── */
  $('btn-copy').addEventListener('click', function () {
    var s = getState();
    var md = buildMarkdown(s);
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = md;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); toast('Brief copied to clipboard'); }
      catch (e) { toast('Copy failed — select the preview text manually'); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(md).then(
        function () { toast('Brief copied to clipboard'); },
        fallback
      );
    } else fallback();
  });

  $('btn-download').addEventListener('click', function () {
    var s = getState();
    download(slugify(s['f-title']) + '.md', buildMarkdown(s), 'text/markdown');
    toast('Markdown brief downloaded');
  });

  $('btn-json').addEventListener('click', function () {
    var s = getState();
    var payload = {
      _format: 'dispatch-brief-v1',
      exported: new Date().toISOString(),
      fields: s
    };
    download(slugify(s['f-title']) + '.json', JSON.stringify(payload, null, 2), 'application/json');
    toast('JSON exported — re-import it any time');
  });

  /* ── Import ─────────────────────────────────────── */
  var importFile = $('import-file');
  $('btn-import').addEventListener('click', function () { importFile.click(); });
  importFile.addEventListener('change', function () {
    var file = importFile.files && importFile.files[0];
    importFile.value = '';
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var data = JSON.parse(String(reader.result));
        var fields = data && data._format === 'dispatch-brief-v1' && data.fields ? data.fields : data;
        setState(fields);
        render();
        toast('Brief imported');
      } catch (e) {
        toast('Could not parse that JSON file');
      }
    };
    reader.readAsText(file);
  });

  /* ── Clear ──────────────────────────────────────── */
  $('btn-clear').addEventListener('click', function () {
    if (!window.confirm('Clear all fields? This cannot be undone.')) return;
    FIELDS.forEach(function (id) { $(id).value = ''; });
    document.querySelector('input[name="deliverable"][value="Pull request"]').checked = true;
    storage.del(STORE_KEY);
    render();
    toast('Form cleared');
  });

  /* ── Quick-add chips ────────────────────────────── */
  document.querySelectorAll('.chip[data-add]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var ta = $('f-criteria');
      var existing = lines(ta.value);
      var item = chip.getAttribute('data-add');
      if (existing.indexOf(item) === -1) {
        existing.push(item);
        ta.value = existing.join('\n');
        render();
      }
    });
  });

  /* ── Wire up ────────────────────────────────────── */
  FIELDS.forEach(function (id) { $(id).addEventListener('input', render); });
  document.querySelectorAll('input[name="deliverable"]').forEach(function (r) {
    r.addEventListener('change', render);
  });

  loadState();
  render();
})();
