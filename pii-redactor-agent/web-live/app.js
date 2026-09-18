/* VEIL demo — tab wiring and rendering.
 *
 * Loads `demo-data.js` (generated from the Python engine) and `veil-engine.js`
 * (the parity-checked browser port), then renders five views.
 *
 * Two rules this file follows, both learned the hard way on sibling projects:
 *   1. Never render a value that could be `undefined` or `NaN`. Every number that
 *      reaches the DOM is passed through `num()` and every string through `esc()`,
 *      so a missing field shows a dash instead of the word "undefined".
 *   2. Never hide content behind a nested scroll container. Panels grow and the
 *      document scrolls.
 */

(function () {
  "use strict";

  var DATA = window.VEIL_DATA;
  var VEIL = window.VEIL;

  if (!DATA || !VEIL) {
    document.body.innerHTML =
      '<div class="wrap"><div class="card"><h2>Could not load</h2>' +
      '<p class="hint">Either <code>demo-data.js</code> or ' +
      '<code>veil-engine.js</code> failed to load. Both must sit next to this ' +
      'page.</p></div></div>';
    return;
  }

  /* ------------------------------------------------------------ helpers */

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function num(value, fallback) {
    var n = Number(value);
    return isFinite(n) ? n : (fallback === undefined ? 0 : fallback);
  }

  function text(value) {
    return (value === null || value === undefined || value === "") ? "\u2014" : esc(value);
  }

  function el(id) { return document.getElementById(id); }

  function badgeClass(level) {
    return "badge badge--" + String(level || "none").toLowerCase();
  }

  function barClass(level) {
    return "bar__fill bar__fill--" + String(level || "none").toLowerCase();
  }

  function policySpecFor(name) {
    return (DATA.policies[name] && DATA.policies[name].spec) || null;
  }

  function policyFor(name) {
    var spec = policySpecFor(name);
    return spec ? VEIL.policyFromSpec(spec) : VEIL.defaultPolicy();
  }

  /* ------------------------------------------------------ sample setup */

  var sampleSelect = el("sample-select");
  DATA.documents.forEach(function (doc, index) {
    var option = document.createElement("option");
    option.value = String(index);
    option.textContent = doc.name;
    sampleSelect.appendChild(option);
  });

  var policyNames = Object.keys(DATA.policies);
  [el("policy-select"), el("redact-policy")].forEach(function (select) {
    var blank = document.createElement("option");
    blank.value = "__default__";
    blank.textContent = "default (built-in)";
    select.appendChild(blank);
    policyNames.forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
  });

  function currentSample() {
    var index = num(sampleSelect.value, 0);
    return DATA.documents[index] || DATA.documents[0];
  }

  function currentPolicy() {
    var name = el("policy-select").value;
    return name === "__default__" ? VEIL.defaultPolicy() : policyFor(name);
  }

  function withPersonToggle(policy) {
    if (!el("include-person").checked) { return policy; }
    if (policy.entities.indexOf("PERSON") !== -1) { return policy; }
    var copy = JSON.parse(JSON.stringify(policy));
    copy.entities = copy.entities.concat(["PERSON"]);
    // The browser engine reads the spec keys in snake_case.
    return VEIL.policyFromSpec({
      name: copy.name, entities: copy.entities, actions: copy.actions,
      keep_first: copy.keepFirst, keep_last: copy.keepLast,
      mask_char: copy.maskChar, hash_salt: copy.hashSalt,
      token_prefix: copy.tokenPrefix, allowlist: copy.allowlist,
      denylist: copy.denylist, names: copy.names,
      case_sensitive_denylist: copy.caseSensitiveDenylist
    });
  }

  /* ------------------------------------------------------------- SCAN */

  var scanText = el("sample-text");

  function loadSample() {
    var doc = currentSample();
    scanText.value = doc.text;
    el("sample-why").textContent = doc.why ? "Why: " + doc.why : "";
    renderScan();
    renderRedact();
    renderVerify();
  }

  function renderScan() {
    var policy = withPersonToggle(currentPolicy());
    var result = VEIL.scan(scanText.value, policy);

    var byEntity = result.risk.by_entity || {};
    var score = num(result.risk.score);
    var pct = Math.max(0, Math.min(100, score));

    var html = '<div class="stat-row">' +
      '<div class="stat"><div class="stat__label">Risk score</div>' +
      '<div class="stat__value">' + score + '</div>' +
      '<div class="bar"><div class="' + barClass(result.risk.level) +
      '" style="width:' + pct + '%"></div></div></div>' +
      '<div class="stat"><div class="stat__label">Level</div>' +
      '<div class="stat__value"><span class="' + badgeClass(result.risk.level) +
      '">' + text(result.risk.level) + '</span></div>' +
      '<div class="stat__note">weighted, occurrence-aware</div></div>' +
      '<div class="stat"><div class="stat__label">Entity types</div>' +
      '<div class="stat__value">' + num(result.risk.distinct_findings) + '</div>' +
      '<div class="stat__note">distinct</div></div>' +
      '<div class="stat"><div class="stat__label">Occurrences</div>' +
      '<div class="stat__value">' + num(result.risk.total_occurrences) + '</div>' +
      '<div class="stat__note">total hits</div></div>' +
      '</div>';
    el("scan-summary").innerHTML = html;

    var rows = result.findings.map(function (f) {
      return "<tr>" +
        "<td><strong>" + text(DATA.entityLabels[f.entity] || f.entity) + "</strong>" +
        '<div class="muted mono">' + text(f.entity) + "</div></td>" +
        '<td class="mono nowrap">' + esc(VEIL.maskValue(
          String(f.value), 0, Math.min(4, Math.max(0, String(f.value).length - 1))
        )) + "</td>" +
        '<td><span class="action-tag action-tag--' + esc(f.action) + '">' +
        esc(f.action) + "</span></td>" +
        '<td class="num">' + num(f.count) + "</td>" +
        '<td class="muted">' + text(f.detector) + "</td>" +
        "</tr>";
    }).join("");

    el("scan-table").innerHTML =
      "<thead><tr><th>Entity</th><th>Value (masked)</th><th>Action</th>" +
      '<th class="num">Count</th><th>Detector</th></tr></thead>' +
      "<tbody>" + (rows || '<tr><td colspan="5" class="muted">' +
        "No findings \u2014 nothing in this document matched a detector.</td></tr>") +
      "</tbody>";
  }

  /* ----------------------------------------------------------- REDACT */

  function renderRedact() {
    var name = el("redact-policy").value;
    var base = name === "__default__" ? VEIL.defaultPolicy() : policyFor(name);

    var keepFirst = Math.max(0, Math.min(12, num(el("keep-first").value, 0)));
    var keepLast = Math.max(0, Math.min(12, num(el("keep-last").value, 4)));
    var spec = {
      name: base.name, entities: base.entities, actions: base.actions,
      keep_first: keepFirst, keep_last: keepLast, mask_char: base.maskChar,
      hash_salt: base.hashSalt, token_prefix: base.tokenPrefix,
      allowlist: base.allowlist, denylist: base.denylist, names: base.names,
      case_sensitive_denylist: base.caseSensitiveDenylist
    };
    var policy = VEIL.policyFromSpec(spec);

    var source = el("redact-input").value;
    var result = VEIL.redact(source, policy);
    el("redact-output").textContent = result.text;

    var status = result.verification ? result.verification.status : "NOT_RUN";
    var clean = !!(result.verification && result.verification.clean);

    el("redact-summary").innerHTML =
      '<div class="stat-row">' +
      '<div class="stat"><div class="stat__label">Policy</div>' +
      '<div class="stat__value" style="font-size:1.05rem">' + text(result.policy) +
      "</div></div>" +
      '<div class="stat"><div class="stat__label">Findings</div>' +
      '<div class="stat__value">' + result.findings.length + "</div>" +
      '<div class="stat__note">distinct values</div></div>' +
      '<div class="stat"><div class="stat__label">Risk</div>' +
      '<div class="stat__value"><span class="' + badgeClass(result.risk.level) +
      '">' + text(result.risk.level) + "</span></div>" +
      '<div class="stat__note">score ' + num(result.risk.score) + "</div></div>" +
      '<div class="stat"><div class="stat__label">Verification</div>' +
      '<div class="stat__value"><span class="badge badge--' +
      (clean ? "clean" : "dirty") + '">' + esc(status) + "</span></div>" +
      '<div class="stat__note">re-scanned output</div></div>' +
      "</div>";

    var tokens = Object.keys(result.tokenMap || {});
    el("vault-card").style.display = tokens.length ? "block" : "none";

    var vaultRows = tokens.map(function (token) {
      return "<tr><td class=\"mono\">" + esc(token) + "</td>" +
        '<td class="mono muted">' + esc(String(result.tokenMap[token]).slice(0, 48)) +
        "</td></tr>";
    }).join("");

    el("vault-table").innerHTML =
      "<thead><tr><th>Token</th><th>Original (truncated)</th></tr></thead>" +
      "<tbody>" + vaultRows + "</tbody>";
  }

  /* ----------------------------------------------------------- VERIFY */

  function renderVerify() {
    var policy = withPersonToggle(currentPolicy());
    var result = VEIL.redact(scanText.value, policy);
    var verification = result.verification;
    var clean = !!(verification && verification.clean);
    var residuals = (verification && verification.residuals) || [];
    var preserved = (verification && verification.preserved) || [];

    el("verify-summary").innerHTML =
      '<div class="stat-row">' +
      '<div class="stat"><div class="stat__label">Verdict</div>' +
      '<div class="stat__value"><span class="badge badge--' +
      (clean ? "clean" : "dirty") + '">' +
      text(verification ? verification.status : "NOT RUN") + "</span></div>" +
      '<div class="stat__note">' + (clean
        ? "no residual found" : "residual data still detectable") + "</div></div>" +
      '<div class="stat"><div class="stat__label">Residuals</div>' +
      '<div class="stat__value">' + residuals.length + "</div>" +
      '<div class="stat__note">unintended survivors</div></div>' +
      '<div class="stat"><div class="stat__label">Preserved</div>' +
      '<div class="stat__value">' + preserved.length + "</div>" +
      '<div class="stat__note">deliberately kept</div></div>' +
      '<div class="stat"><div class="stat__label">Entities checked</div>' +
      '<div class="stat__value">' +
      ((verification && verification.checked_entities) || []).length + "</div>" +
      '<div class="stat__note">detectors re-run</div></div>' +
      "</div>";

    var rows = residuals.map(function (r) {
      return "<tr><td>" + text(DATA.entityLabels[r.entity] || r.entity) + "</td>" +
        '<td class="mono">' + esc(r.value) + "</td>" +
        '<td class="num">' + num(r.start) + "</td>" +
        '<td class="muted">' + text(r.detector) + "</td></tr>";
    }).join("");

    el("verify-table").innerHTML =
      "<thead><tr><th>Entity</th><th>Value</th><th>Offset</th><th>Detector</th>" +
      "</tr></thead><tbody>" +
      (rows || '<tr><td colspan="4" class="muted">' +
        "No residual findings. The redacted text contains nothing the detectors " +
        "still recognise.</td></tr>") + "</tbody>";
  }

  /* ---------------------------------------------------------- STORIES */

  function renderStories() {
    el("stories-table").innerHTML =
      "<thead><tr><th>Document</th><th>Kind</th><th>What it exercises</th></tr></thead>" +
      "<tbody>" + DATA.documents.map(function (doc) {
        return "<tr><td class=\"mono\">" + esc(doc.name) + "</td>" +
          "<td>" + text(doc.kind) + "</td><td>" + text(doc.why) + "</td></tr>";
      }).join("") + "</tbody>";

    el("policies-table").innerHTML =
      "<thead><tr><th>Policy</th><th>Posture</th><th>Entities</th></tr></thead>" +
      "<tbody>" + (DATA.policySummary || []).map(function (row) {
        return "<tr><td class=\"mono\">" + esc(row.name) + "</td>" +
          "<td>" + text(row.posture) + "</td>" +
          "<td class=\"num\">" + num(row.entities) + "</td></tr>";
      }).join("") + "</tbody>";
  }

  /* --------------------------------------------------------- ENTITIES */

  function renderEntities() {
    var defaultActions = (DATA.defaultPolicy && DATA.defaultPolicy.actions) || {};
    el("entities-table").innerHTML =
      "<thead><tr><th>Entity</th><th>Validator</th><th>Default action</th>" +
      '<th class="num">Weight</th></tr></thead><tbody>' +
      DATA.entities.map(function (row) {
        return "<tr><td><strong>" + text(row.label) + "</strong>" +
          '<div class="muted mono">' + text(row.entity) + "</div></td>" +
          '<td class="muted">' + text(row.validator) + "</td>" +
          '<td><span class="action-tag action-tag--' +
          esc(defaultActions[row.entity]) + '">' +
          text(defaultActions[row.entity]) + "</span></td>" +
          '<td class="num">' + num(row.weight) + "</td></tr>";
      }).join("") + "</tbody>";

    el("actions-table").innerHTML =
      "<thead><tr><th>Action</th><th>Reversible</th><th>Description</th></tr></thead>" +
      "<tbody>" + (DATA.actions || []).map(function (row) {
        return "<tr><td><span class=\"action-tag action-tag--" + esc(row.action) +
          '">' + esc(row.action) + "</span></td>" +
          "<td>" + (row.reversible ? "yes" : "no") + "</td>" +
          '<td class="muted">' + text(row.description) + "</td></tr>";
      }).join("") + "</tbody>";
  }

  /* ------------------------------------------------------------- tabs */

  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));

  function activate(name) {
    tabs.forEach(function (tab) {
      var active = tab.getAttribute("data-tab") === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    ["scan", "redact", "verify", "stories", "entities"].forEach(function (panel) {
      el("panel-" + panel).classList.toggle("is-active", panel === name);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activate(tab.getAttribute("data-tab"));
    });
  });

  /* ------------------------------------------------------------ wiring */

  sampleSelect.addEventListener("change", loadSample);
  el("policy-select").addEventListener("change", function () {
    var spec = policySpecFor(el("policy-select").value);
    var summary = spec
      ? spec.entities.length + " entities, actions: " +
        Object.keys(spec.actions).map(function (k) {
          return k + "=" + spec.actions[k];
        }).join(", ")
      : "All entities except the opt-in PERSON detector, masked with the default shape.";
    el("policy-posture").textContent = summary;
    renderScan();
    renderVerify();
  });

  el("include-person").addEventListener("change", function () {
    renderScan();
    renderVerify();
  });

  scanText.addEventListener("input", function () {
    renderScan();
    renderVerify();
  });

  ["redact-policy", "keep-first", "keep-last"].forEach(function (id) {
    el(id).addEventListener("input", renderRedact);
    el(id).addEventListener("change", renderRedact);
  });

  el("redact-input").addEventListener("input", renderRedact);

  el("copy-output").addEventListener("click", function () {
    var button = el("copy-output");
    var value = el("redact-output").textContent;
    var done = function () {
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = "Copy"; }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done, done);
    } else {
      done();
    }
  });

  el("version-badge").textContent = "v" + VEIL.version;

  /* -------------------------------------------------------------- boot */

  el("redact-input").value = DATA.documents[0].text;
  renderStories();
  renderEntities();
  loadSample();

  if (sampleSelect.options.length) { sampleSelect.selectedIndex = 0; }
  el("policy-select").dispatchEvent(new Event("change"));
})();
