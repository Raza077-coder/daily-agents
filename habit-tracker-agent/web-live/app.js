/* HABITOS HUD — app controller */
(function () {
  "use strict";

  var engine = new HabitosEngine({ storageKey: "habitos_state_v1" });
  var weekdays = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

  // ------------------------------------------------------------------ dom
  var el = {
    dateChip: document.getElementById("date-chip"),
    weekChip: document.getElementById("week-chip"),
    habitList: document.getElementById("habit-list"),
    reportOut: document.getElementById("report-out"),
    newName: document.getElementById("new-habit-name"),
    newCat: document.getElementById("new-habit-cat"),
    newFreq: document.getElementById("new-habit-freq"),
    newTarget: document.getElementById("new-habit-target"),
    btnAdd: document.getElementById("btn-add"),
    btnDemo: document.getElementById("btn-demo"),
    btnClear: document.getElementById("btn-clear"),
    btnReport: document.getElementById("btn-report"),
    gActive: document.getElementById("g-active"),
    gOn: document.getElementById("g-ontrack"),
    gRisk: document.getElementById("g-risk"),
    gOff: document.getElementById("g-off"),
    gChecks: document.getElementById("g-checkins")
  };

  // ---------------------------------------------------------------- render
  function refresh() {
    var habits = engine.listHabits(false);
    var monday = habitosUtils.isoWeekMonday(new Date());

    var now = new Date();
    el.dateChip.textContent = now.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
    el.weekChip.textContent = "WK " + habitosUtils.dateISO(monday).slice(5).replace("-", "/");

    if (habits.length === 0) {
      el.habitList.innerHTML = '<div class="empty">NO ACTIVE HABITS — ADD ONE ABOVE</div>';
    } else {
      el.habitList.innerHTML = habits.map(habitRow).join("");
    }

    var s = engine.summary();
    el.gActive.textContent = s.active_habits;
    el.gOn.textContent = s.on_track;
    el.gRisk.textContent = s.at_risk;
    el.gOff.textContent = s.off_track;
    el.gChecks.textContent = s.total_logs;

    el.reportOut.textContent = engine.weeklyReportText();
    bindRowEvents();
  }

  function habitRow(h) {
    var d = engine.habitDetail(h.habit_id);
    var last7 = d.last_7_days;
    var dots = last7.map(function (day) {
      var cls = "wdot" + (day.done ? " done" : "") + (day.date === habitosUtils.todayISO() ? " today" : "");
      return '<div class="' + cls + '" title="' + day.date + '">' + (day.done ? "✓" : "·") + "</div>";
    }).join("");
    var color = h.color || "#00e5ff";
    var todayDone = last7[last7.length - 1].done;
    return (
      '<div class="habit-row" style="--hc:' + color + '">' +
        '<div class="habit-main">' +
          '<span class="habit-name">' + esc(h.name) + "</span>" +
          '<span class="habit-tag tag-' + h.status + '">' + h.status.replace("_", " ") + "</span>" +
          '<span class="streak-line">' +
            "streak <b>" + h.current_streak + "d</b> · best " + h.longest_streak + "d · " +
            h.week_done + "/" + h.target_per_week + " wk · " + Math.round(h.completion_pct) + "%" +
          "</span>" +
          '<div class="week-dots">' + dots + "</div>" +
        "</div>" +
        '<div class="habit-actions">' +
          '<button class="btn log-btn' + (todayDone ? " done" : "") + '" data-log="' + h.habit_id + '">' +
            (todayDone ? "✓ DONE" : "LOG") + "</button>" +
          '<button class="btn ghost small" data-archive="' + h.habit_id + '" title="Archive">⌦</button>' +
          '<button class="btn ghost small" data-del="' + h.habit_id + '" title="Delete permanently">✕</button>' +
        "</div>" +
      "</div>"
    );
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function bindRowEvents() {
    var rows = el.habitList.querySelectorAll("[data-log], [data-del], [data-archive]");
    Array.prototype.forEach.call(rows, function (btn) {
      btn.addEventListener("click", function () {
        if (btn.dataset.log) {
          var d = engine.habitDetail(btn.dataset.log);
          var done = d && d.last_7_days && d.last_7_days[d.last_7_days.length - 1].done;
          if (done) { engine.unlog(btn.dataset.log); }
          else { engine.log(btn.dataset.log, null, "web"); }
        } else if (btn.dataset.del) {
          if (confirm("Permanently delete this habit and its history?")) engine.deleteHabit(btn.dataset.del);
        } else if (btn.dataset.archive) {
          engine.archiveHabit(btn.dataset.archive, true);
        }
        refresh();
      });
    });
  }

  // ------------------------------------------------------------------ init
  function wire() {
    el.btnAdd.addEventListener("click", function () {
      var name = el.newName.value.trim();
      if (!name) { el.newName.focus(); return; }
      var cat = el.newCat.value;
      var freq = el.newFreq.value;
      var target = parseInt(el.newTarget.value, 10);
      if (freq === "" && isNaN(target)) target = 5;
      try {
        engine.addHabit(name, cat, target, freq);
        el.newName.value = "";
      } catch (e) { alert(e.message); }
      refresh();
    });
    el.newName.addEventListener("keydown", function (e) {
      if (e.key === "Enter") el.btnAdd.click();
    });
    el.btnDemo.addEventListener("click", function () {
      if (confirm("Reset to the demo dataset? Your current data will be replaced.")) {
        engine.reset();
        refresh();
      }
    });
    el.btnClear.addEventListener("click", function () {
      if (confirm("Erase ALL habits and check-ins?")) {
        engine.store = new HabitosStore();
        localStorage.removeItem("habitos_state_v1");
        engine = new HabitosEngine({ storageKey: "habitos_state_v1" });
        refresh();
      }
    });
    el.btnReport.addEventListener("click", refresh);
  }

  wire();
  refresh();
})();
