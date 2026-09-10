/* ==========================================================================
 * VAULTGUARD — HUD controller
 * All logic is client-side. No fetch(), no XHR, no network of any kind.
 * ========================================================================== */

(function () {
  'use strict';

  const VG = window.VaultGuard;
  const $ = id => document.getElementById(id);

  const engine = new VG.VaultEngine({ iterations: VG.FAST_ITERATIONS });
  let demoMode = false;

  /* ── UI helpers ───────────────────────────────────────────────────────── */
  function toast(message, ms) {
    const el = $('toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, ms || 2600);
  }

  function setStatus(state, text) {
    $('statusText').textContent = text;
    document.querySelector('.status').classList.toggle('unlocked', state === 'unlocked');
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function severityOrder(f) {
    return { critical: 0, high: 1, medium: 2, low: 3 }[f.severity] ?? 9;
  }

  /* ── rendering ────────────────────────────────────────────────────────── */
  function renderSession() {
    const unlocked = engine.unlocked;
    $('lockedView').hidden = unlocked;
    $('unlockedView').hidden = !unlocked;
    setStatus(unlocked ? 'unlocked' : 'locked', unlocked ? 'unlocked' : 'locked');

    const select = $('categoryFilter');
    if (select.options.length <= 1) {
      VG.CATEGORIES.forEach(c => {
        const o = document.createElement('option');
        o.value = c; o.textContent = c;
        select.appendChild(o);
      });
    }
    const form = $('fCategory');
    if (!form.options.length) {
      VG.CATEGORIES.forEach(c => {
        const o = document.createElement('option');
        o.value = c; o.textContent = c;
        form.appendChild(o);
      });
    }
  }

  function renderStats(report) {
    $('scoreValue').textContent = report.total_entries ? report.score : '—';
    $('scoreGrade').textContent = report.total_entries ? report.grade : '—';
    $('scoreRing').style.setProperty('--pct', report.total_entries ? report.score : 0);
    $('statEntries').textContent = report.total_entries;
    $('statWeak').textContent = report.health.weak;
    $('statReused').textContent = report.health.reused;
    $('statStale').textContent = report.health.stale;

    if (!report.total_entries) {
      $('verdict').textContent = 'Vault is empty — add a credential to begin tracking hygiene.';
      return;
    }
    const bits = [
      `Score ${report.score}/100 (${report.grade} · ${report.verdict})`,
      `${report.health.strong} strong`,
      `${report.health.reasonable} reasonable`,
      `${report.health.weak} weak`,
      `${report.health.with_2fa} with 2FA`,
    ];
    $('verdict').textContent = bits.join(' · ');
  }

  function renderFindings(report) {
    const list = $('findingList');
    list.innerHTML = '';
    if (!report.total_entries) {
      list.innerHTML = '<li class="empty">Add an entry to run the first audit.</li>';
      return;
    }
    if (!report.findings.length) {
      list.innerHTML = '<li class="empty">No findings. Excellent hygiene.</li>';
      return;
    }
    report.findings.slice().sort((a, b) => severityOrder(a) - severityOrder(b)).forEach(f => {
      const li = document.createElement('li');
      li.innerHTML =
        `<div class="entry-head">
           <span class="finding-title">${esc(f.title)}</span>
           <span class="badge ${esc(f.severity)}">${esc(f.severity)}</span>
         </div>
         <div class="finding-detail">${esc(f.detail)}</div>`;
      list.appendChild(li);
    });

    if (report.recommendations && report.recommendations.length) {
      const li = document.createElement('li');
      li.innerHTML = `<div class="finding-title">Recommended next steps</div>` +
        report.recommendations.map(r => `<div class="finding-detail">• ${esc(r)}</div>`).join('');
      list.appendChild(li);
    }
  }

  function renderEntries() {
    const list = $('entryList');
    list.innerHTML = '';
    if (!engine.unlocked) {
      list.innerHTML = '<li class="empty">Vault is locked.</li>';
      return;
    }
    const rows = engine.search({
      text: $('searchInput').value,
      category: $('categoryFilter').value,
      weakOnly: $('weakOnly').checked,
      sort: 'title',
    });
    if (!rows.length) {
      list.innerHTML = '<li class="empty">No entries match this filter.</li>';
      return;
    }
    rows.forEach(entry => {
      const strength = VG.estimateStrength(entry.password, {
        ageDays: engine.ageOf(entry),
        reused: engine.duplicates().some(g => g.ids.includes(entry.id)),
      });
      const li = document.createElement('li');
      li.innerHTML =
        `<div class="entry-head">
           <span class="entry-title">${esc(entry.title)}</span>
           <span class="entry-cat">${esc(entry.category)}</span>
         </div>
         <div class="entry-sub">${esc(entry.username || '—')}${entry.url ? ' · ' + esc(entry.url) : ''}</div>
         <div class="entry-sub">
           <span class="entry-secret" data-secret="${esc(entry.password)}">${esc(VG.maskSecret(entry.password))}</span>
           · score ${strength.score} (${esc(strength.grade)}) · ${esc(strength.crack_time_human)}
         </div>
         <div class="entry-actions">
           <button data-act="reveal" data-id="${esc(entry.id)}">reveal</button>
           <button data-act="copy"   data-id="${esc(entry.id)}">copy</button>
           <button data-act="regen"  data-id="${esc(entry.id)}">rotate</button>
           <button data-act="delete" data-id="${esc(entry.id)}">delete</button>
         </div>`;
      list.appendChild(li);
    });
  }

  function renderAll() {
    if (!engine.unlocked) {
      renderSession();
      $('entryList').innerHTML = '<li class="empty">Vault is locked.</li>';
      $('findingList').innerHTML = '<li class="empty">Unlock the vault to run an audit.</li>';
      return;
    }
    const report = engine.audit();
    renderSession();
    renderStats(report);
    renderFindings(report);
    renderEntries();
  }

  /* ── session actions ──────────────────────────────────────────────────── */
  async function doCreate() {
    const master = $('masterInput').value;
    $('authMsg').textContent = '';
    if (master.length < 8) { $('authMsg').textContent = 'Master password must be at least 8 characters.'; return; }
    try {
      await engine.create(master);
      VG.demoEntries().forEach(e => engine.add(e));
      demoMode = true;
      await engine.save();
      $('masterInput').value = '';
      $('saveMsg').textContent = 'Demo vault created in this tab. Nothing was uploaded.';
      renderAll();
      toast('Vault created with 4 sample entries.');
    } catch (err) {
      $('authMsg').textContent = err.message;
    }
  }

  async function doUnlock() {
    const master = $('masterInput').value;
    $('authMsg').textContent = '';
    if (engine.header && engine.header.payload) {
      // restore an in-memory document (e.g. after Lock) or an uploaded backup
      try {
        await engine.unlock(master, engine.header);
      } catch (err) {
        $('authMsg').textContent = err.message;
        return;
      }
    } else {
      // nothing to unlock — offer a fresh vault instead
      $('authMsg').textContent = 'No vault found in this tab. Create one, or restore an encrypted backup file.';
      return;
    }
    demoMode = false;
    $('masterInput').value = '';
    renderAll();
    toast('Vault unlocked.');
  }

  async function doLoadFile(file) {
    try {
      const text = await file.text();
      const doc = JSON.parse(text);
      const master = $('masterInput').value;
      if (!master) { $('authMsg').textContent = 'Enter the master password before restoring a backup.'; return; }
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
      const target = JSON.parse(JSON.stringify(doc));   // keep a clean copy
      // unlock a detached engine so a failed attempt leaves the live one intact
      const probe = new VG.VaultEngine();
      await probe.unlock(master, target);
      engine.key = probe.key;
      engine.entries = probe.entries;
      engine.header = probe.header;
      engine.iterations = probe.iterations;
      engine.unlocked = true;
      $('authMsg').textContent = '';
      $('masterInput').value = '';
      renderAll();
      toast(`Restored ${engine.entries.length} entries from backup.`);
    } catch (err) {
      $('authMsg').textContent = 'Could not restore backup: ' + err.message;
    }
  }

  function doSave() {
    const blob = new Blob([engine.serialise()], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vaultguard-vault.json';
    a.click();
    URL.revokeObjectURL(url);
    $('saveMsg').textContent = 'Encrypted vault downloaded. Keep it — the master password is unrecoverable.';
    toast('Encrypted vault saved to your downloads.');
  }

  function doLock() {
    engine.lock();
    demoMode = false;
    renderAll();
    toast('Vault locked. The key was dropped from memory.');
  }

  /* ── entry actions ────────────────────────────────────────────────────── */
  function onEntryClick(event) {
    const btn = event.target.closest('button[data-act]');
    if (!btn) return;
    const { act, id } = btn.dataset;
    try {
      if (act === 'reveal') {
        const span = btn.closest('li').querySelector('.entry-secret');
        const shown = span.getAttribute('data-shown') === '1';
        span.textContent = shown ? VG.maskSecret(span.dataset.secret) : span.dataset.secret;
        span.setAttribute('data-shown', shown ? '0' : '1');
        btn.textContent = shown ? 'reveal' : 'hide';
      } else if (act === 'copy') {
        const secret = engine.find(id).password;
        navigator.clipboard.writeText(secret)
          .then(() => toast('Copied to clipboard. Clear it when you are done.'))
          .catch(() => toast('Clipboard blocked by the browser — use reveal instead.'));
      } else if (act === 'regen') {
        const entry = engine.find(id);
        entry.password = VG.generatePassword(24);
        engine.update(id, { password: entry.password });
        engine.save();
        renderAll();
        toast(`Rotated '${entry.title}' with a 24-character password.`);
      } else if (act === 'delete') {
        const entry = engine.find(id);
        engine.remove(id);
        engine.save();
        renderAll();
        toast(`Deleted '${entry.title}'.`);
      }
    } catch (err) {
      toast(err.message);
    }
  }

  function onSubmit(event) {
    event.preventDefault();
    try {
      const generated = !$('fPassword').value;
      engine.add({
        title: $('fTitle').value,
        username: $('fUser').value,
        category: $('fCategory').value,
        url: $('fUrl').value,
        tags: $('fTags').value,
        password: $('fPassword').value || VG.generatePassword(24),
        totp: $('fTotp').checked ? 'TOTP-SEED-PENDING' : '',
      });
      engine.save();
      const title = $('fTitle').value;
      $('entryForm').reset();
      if ($('fCategory').options.length) {
        VG.CATEGORIES.forEach((c, i) => { if (c === 'login') $('fCategory').selectedIndex = i; });
      }
      renderAll();
      toast(generated ? `Added '${title}' with a generated password.` : `Added '${title}'.`);
    } catch (err) {
      toast(err.message);
    }
  }

  /* ── wiring ───────────────────────────────────────────────────────────── */
  function bind() {
    $('btnCreate').addEventListener('click', doCreate);
    $('btnUnlock').addEventListener('click', doUnlock);
    $('btnSave').addEventListener('click', doSave);
    $('btnBackup').addEventListener('click', doSave);
    $('btnLock').addEventListener('click', doLock);
    $('btnGen').addEventListener('click', () => {
      $('fPassword').value = VG.generatePassword(24);
      toast('Generated a 24-character password.');
    });
    $('vaultFile').addEventListener('change', e => {
      if (e.target.files && e.target.files[0]) doLoadFile(e.target.files[0]);
    });
    $('searchInput').addEventListener('input', renderEntries);
    $('categoryFilter').addEventListener('change', renderEntries);
    $('weakOnly').addEventListener('change', renderEntries);
    $('entryList').addEventListener('click', onEntryClick);
    $('entryForm').addEventListener('submit', onSubmit);
    $('masterInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') (engine.header && engine.header.payload) ? doUnlock() : doCreate();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!window.crypto || !crypto.subtle) {
      $('authMsg').textContent = 'WebCrypto is unavailable. Serve this page over HTTPS or localhost.';
      return;
    }
    $('cryptoBadge').textContent = 'WebCrypto · AES-256-GCM';
    bind();
    renderAll();
  });
})();
