/* FORGE — browser demo UI.
 *
 * All logic lives in forge-engine.js; this file only reads the DOM, calls the
 * engine and renders. No fetch, no XHR, no external resources.
 */
(function () {
  "use strict";

  var F = window.Forge;
  var DATA = F.DATA;

  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };
  var titleCase = function (s) {
    return String(s || "").replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  };

  var selectedExercise = null;

  /* ------------------------------------------------------------ status */

  $("engine-status").textContent = DATA.exercises.length + " movements loaded";

  /* -------------------------------------------------------------- tabs */

  document.querySelectorAll(".tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (b) {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      document.querySelectorAll(".panel").forEach(function (p) { p.classList.remove("active"); });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      $("panel-" + btn.dataset.tab).classList.add("active");
    });
  });

  /* ---------------------------------------------------- exercise picker */

  var searchBox = $("ex-search");
  var suggestBox = $("ex-suggest");

  function renderSuggestions(term) {
    var matches = term
      ? F.searchExercises(term).slice(0, 8)
      : DATA.exercises.slice(0, 8);
    if (!matches.length) { suggestBox.hidden = true; return; }
    suggestBox.innerHTML = matches.map(function (e) {
      return '<button type="button" data-id="' + esc(e.id) + '">' + esc(e.name) +
        ' <small>' + esc(e.primary) + ' · ' + esc(e.equipment) + '</small></button>';
    }).join("");
    suggestBox.hidden = false;
    suggestBox.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        pickExercise(b.dataset.id);
      });
    });
  }

  function pickExercise(id) {
    var ex = F.getExercise(id);
    if (!ex) return;
    selectedExercise = ex;
    searchBox.value = ex.name;
    suggestBox.hidden = true;
  }

  searchBox.addEventListener("input", function () { renderSuggestions(searchBox.value); });
  searchBox.addEventListener("focus", function () { renderSuggestions(searchBox.value); });
  document.addEventListener("click", function (e) {
    if (!suggestBox.contains(e.target) && e.target !== searchBox) suggestBox.hidden = true;
  });

  /* -------------------------------------------------------- prescribe */

  function prescribe() {
    var errBox = $("prescribe-error");
    errBox.hidden = true;

    var ex = selectedExercise || F.searchExercises(searchBox.value)[0];
    if (!ex) {
      errBox.textContent = "Pick an exercise first — start typing a name like “bench”.";
      errBox.hidden = false;
      return;
    }
    selectedExercise = ex;

    var repLow = parseInt($("rep-low").value, 10) || 8;
    var repHigh = parseInt($("rep-high").value, 10) || 12;
    var sets = parseInt($("set-count").value, 10) || 3;

    if (repHigh <= repLow) {
      errBox.textContent = "The top of the rep range must be higher than the bottom.";
      errBox.hidden = false;
      return;
    }

    var spec = $("last-sets").value.trim();
    var history = [];
    try {
      if (spec) {
        history = F.buildHistory([{ date: "2026-01-01", sets: F.parseSetSpec(spec) }]);
      }
    } catch (e) {
      errBox.textContent = e.message;
      errBox.hidden = false;
      return;
    }

    var bw = parseFloat($("p-bw").value) || 80;
    var exp = $("p-exp").value || "beginner";

    var advice;
    try {
      advice = F.nextPrescription(ex.id, history, repLow, repHigh, sets, bw, exp);
    } catch (e) {
      errBox.textContent = e.message;
      errBox.hidden = false;
      return;
    }

    renderPrescription(advice, history);
  }

  function renderPrescription(a, history) {
    var scheme = a.sets + " × " + a.target_reps_low + "-" + a.target_reps_high +
      " reps";
    var load = a.weight > 0 ? F.fmtKg(a.weight) : "bodyweight";

    var html = '<div class="prescription">' +
      '<div class="pres-head">' +
        '<span class="pres-name">' + esc(a.name) + '</span>' +
        '<span class="pres-load">' + esc(load) + '</span>' +
      '</div>' +
      '<div class="pres-scheme">' + esc(scheme) +
        '  <span class="badge ' + esc(a.status) + '">' + esc(a.action.replace(/_/g, " ")) + '</span>' +
      '</div>' +
      '<div class="pres-reason">' + esc(a.reason) + '</div>' +
    '</div>';

    if (a.previous_weight !== undefined && a.previous_weight !== null) {
      html += '<div class="lift"><span class="lift-name">Previous working load</span>' +
        '<span class="lift-meta">' + esc(F.fmtKg(a.previous_weight)) + '</span></div>';
    }
    html += '<div class="lift"><span class="lift-name">Suggested rest</span>' +
      '<span class="lift-meta">' + (a.exercise && F.getExercise(a.exercise) &&
        F.getExercise(a.exercise).kind === "compound" ? "180s (compound)" : "90s (isolation)") +
      '</span></div>';

    if (history && history.length) {
      var h = history[history.length - 1];
      html += '<div class="lift"><span class="lift-name">Estimated 1RM from last session</span>' +
        '<span class="lift-meta">' + h.bestE1rm + ' kg</span></div>';
      html += '<div class="lift"><span class="lift-name">Last session volume</span>' +
        '<span class="lift-meta">' + h.volume + ' kg</span></div>';
    }

    $("prescribe-result").innerHTML = html;
  }

  $("btn-prescribe").addEventListener("click", prescribe);

  $("btn-sample").addEventListener("click", function () {
    pickExercise("barbell_bench_press");
    $("rep-low").value = 3;
    $("rep-high").value = 6;
    $("set-count").value = 4;
    $("last-sets").value = "60x6x4";
    prescribe();
  });

  $("last-sets").addEventListener("keydown", function (e) {
    if (e.key === "Enter") prescribe();
  });

  /* -------------------------------------------------- program builder */

  var equipChips = $("equip-chips");
  equipChips.innerHTML = DATA.equipment.map(function (e) {
    return '<span class="chip" data-equip="' + esc(e) + '">' + esc(e) + '</span>';
  }).join("");
  equipChips.querySelectorAll(".chip").forEach(function (c) {
    c.addEventListener("click", function () { c.classList.toggle("on"); });
  });

  function chosenEquipment() {
    return Array.prototype.slice
      .call(equipChips.querySelectorAll(".chip.on"))
      .map(function (c) { return c.dataset.equip; });
  }

  function buildCurrentProgram() {
    return F.buildProgram({
      daysPerWeek: parseInt($("p-days").value, 10),
      experience: $("p-exp").value,
      goal: $("p-goal").value,
      bodyweightKg: parseFloat($("p-bw").value) || 80,
      equipment: chosenEquipment()
    });
  }

  function renderProgram(program) {
    var out = '<div class="card"><h2>' + esc(program.name) + '</h2>' +
      '<p class="hint">' + esc(program.split) + ' · ' + program.days_per_week +
      ' days/week · ' + program.weeks + ' week block · ' +
      esc(program.sessions.length) + ' sessions</p>';

    program.sessions.forEach(function (s) {
      out += '<div class="session">' +
        '<div class="session-head">' +
          '<span class="session-title">' + esc(s.title) + '</span>' +
          '<span class="session-day">' + esc(s.day) + '</span>' +
        '</div>' +
        '<div class="focus">' + esc(s.focus.map(titleCase).join(" · ")) + '</div>';
      s.exercises.forEach(function (e) {
        var ex = F.getExercise(e.exercise_id);
        var load = e.start_weight > 0 ? F.fmtKg(e.start_weight) : "bodyweight";
        out += '<div class="lift">' +
          '<span class="lift-name">' + esc(e.name) + '</span>' +
          '<span class="lift-meta">' + e.sets + '×' + e.rep_low + '-' + e.rep_high +
          ' @ ' + esc(load) + '</span></div>';
      });
      out += '</div>';
    });

    out += '<p class="sub">⚠ ' + esc(DATA.disclaimer) + '</p></div>';
    $("program-output").innerHTML = out;
  }

  $("btn-build").addEventListener("click", function () {
    renderProgram(buildCurrentProgram());
  });

  $("btn-project").addEventListener("click", function () {
    var program = buildCurrentProgram();
    var weeks = F.projectOverload(program, 6);
    var out = '<div class="card"><h2>Projected overload — 6 weeks</h2>' +
      '<p class="hint">If every session is completed as prescribed. Missed sessions ' +
      'push this out; they do not break it.</p>';

    weeks.forEach(function (w) {
      out += '<div class="week-block"><div class="week-label">Week ' + w.week + '</div>';
      w.sessions.forEach(function (s) {
        if (!s.lifts.length) return;
        var parts = s.lifts.map(function (l) {
          return esc(l.name) + " " + l.weight + "kg×" + l.reps;
        });
        out += '<div class="week-line">' + esc(s.session) + ': ' + parts.join("  ·  ") + '</div>';
      });
      out += '</div>';
    });
    out += '</div>';
    $("program-output").innerHTML = out;
  });

  /* --------------------------------------------------------- library */

  var muscleSel = $("lib-muscle");
  muscleSel.innerHTML = '<option value="">any</option>' + DATA.muscles.map(function (m) {
    return '<option value="' + esc(m) + '">' + esc(titleCase(m)) + '</option>';
  }).join("");

  var patterns = [];
  DATA.exercises.forEach(function (e) {
    if (patterns.indexOf(e.pattern) < 0) patterns.push(e.pattern);
  });
  patterns.sort();
  $("lib-pattern").innerHTML = '<option value="">any</option>' + patterns.map(function (p) {
    return '<option value="' + esc(p) + '">' + esc(titleCase(p)) + '</option>';
  }).join("");

  function renderLibrary() {
    var q = $("lib-q").value.trim();
    var muscle = $("lib-muscle").value;
    var pattern = $("lib-pattern").value;

    var rows = q ? F.searchExercises(q) : DATA.exercises.slice();
    if (muscle) {
      rows = rows.filter(function (e) {
        return e.primary === muscle || (e.secondary || []).indexOf(muscle) >= 0;
      });
    }
    if (pattern) rows = rows.filter(function (e) { return e.pattern === pattern; });

    $("lib-count").textContent = rows.length + " of " + DATA.exercises.length +
      " movements match.";

    if (!rows.length) {
      $("library-output").innerHTML = '<div class="card"><p class="hint">No matches — try a broader term.</p></div>';
      return;
    }

    var html = '<div class="card"><table class="data"><thead><tr>' +
      '<th>Exercise</th><th>Primary</th><th>Equipment</th>' +
      '<th>Pattern</th><th>Type</th><th>Inc.</th></tr></thead><tbody>';
    rows.forEach(function (e) {
      html += '<tr>' +
        '<td><strong>' + esc(e.name) + '</strong></td>' +
        '<td><span class="pill">' + esc(titleCase(e.primary)) + '</span></td>' +
        '<td class="num">' + esc(e.equipment) + '</td>' +
        '<td class="num">' + esc(titleCase(e.pattern)) + '</td>' +
        '<td class="num">' + esc(e.kind) + '</td>' +
        '<td class="num">' + (e.increment ? e.increment + 'kg' : '—') + '</td>' +
      '</tr>';
    });
    html += '</tbody></table></div>';
    $("library-output").innerHTML = html;
  }

  ["lib-q", "lib-muscle", "lib-pattern"].forEach(function (id) {
    $(id).addEventListener("input", renderLibrary);
    $(id).addEventListener("change", renderLibrary);
  });

  /* ------------------------------------------------------ initial state */

  pickExercise("barbell_bench_press");
  $("last-sets").value = "60x8x3";
  prescribe();
  renderLibrary();
  renderProgram(buildCurrentProgram());
})();
