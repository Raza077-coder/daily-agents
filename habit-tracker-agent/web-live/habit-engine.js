/* HABITOS — habit-engine.js
 * Pure-JavaScript port of the Python engine (habit_tracker/*) so the
 * whole Habit Tracker Agent runs 100% client-side in the browser.
 * Deterministic, offline, no dependencies.
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------- utils
  function pad2(n) { return String(n).padStart(2, "0"); }

  function dateISO(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function todayISO() { return dateISO(new Date()); }

  function parseISO(s) {
    var parts = s.split("-");
    return new Date(+parts[0], +parts[1] - 1, +parts[2]);
  }

  function isoWeekMonday(ref) {
    var d = ref || new Date();
    var day = (d.getDay() + 6) % 7; // Monday=0
    var m = new Date(d.getFullYear(), d.getMonth(), d.getDate() - day);
    return m;
  }

  function slugify(text) {
    var s = String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return s || "habit";
  }

  // ------------------------------------------------------------- analytics
  var FREQ = { daily: 7, weekdays: 5, weekly: 1, weekends: 2 };

  function normalizeTarget(target, frequency) {
    if (frequency && FREQ[frequency]) return FREQ[frequency];
    var t = parseInt(target, 10);
    if (!isNaN(t) && t > 0) return Math.min(t, 7);
    return 5;
  }

  function msDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

  function diffDays(a, b) {
    return Math.round((msDay(a) - msDay(b)) / 86400000);
  }

  function computeStreaks(doneSet, today) {
    var current = 0;
    var cursor = today;
    if (!doneSet.has(dateISO(cursor))) {
      var yest = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() - 1);
      if (doneSet.has(dateISO(yest))) cursor = yest;
    }
    while (doneSet.has(dateISO(cursor))) {
      current += 1;
      cursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() - 1);
    }
    var longest = 0, run = 0;
    var keys = Array.from(doneSet).sort();
    var start = keys.length ? parseISO(keys[0]) : today;
    var end = keys.length ? parseISO(keys[keys.length - 1]) : today;
    for (var d = msDay(start); d <= msDay(end); d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)) {
      if (doneSet.has(dateISO(d))) { run += 1; longest = Math.max(longest, run); }
      else run = 0;
    }
    return { current: current, longest: longest };
  }

  function weekStats(doneSet, target, today) {
    var monday = isoWeekMonday(today);
    var elapsed = diffDays(today, monday) + 1;
    var weekDone = 0;
    doneSet.forEach(function (iso) {
      var d = parseISO(iso);
      if (d >= monday && d <= today) weekDone += 1;
    });
    var possible = Math.min(elapsed, 7);
    var pct = possible ? (weekDone / possible * 100) : 0;
    return {
      week_done: weekDone,
      week_possible: possible,
      completion_pct: Math.round(pct * 10) / 10,
      pace_per_week: Math.round((weekDone / elapsed * 7) * 10) / 10,
      target: target
    };
  }

  function statusFor(pct, weekDone, target) {
    if (pct >= 80) return "on_track";
    if (weekDone >= Math.max(1, target - 1)) return "on_track";
    if (pct >= 50) return "at_risk";
    return "off_track";
  }

  // ----------------------------------------------------------------- store
  var DEFAULT_COLORS = {
    health: "#00e5ff", productivity: "#7c4dff", learning: "#00e676",
    fitness: "#ff5252", mindfulness: "#ffab40", general: "#40c4ff"
  };
  var CATEGORIES = ["health", "productivity", "learning", "fitness", "mindfulness", "general"];

  function HabitosStore() {
    this.habits = {};   // id -> habit object
    this.logs = [];     // {habit_id, date}
  }

  HabitosStore.prototype.toJSON = function () {
    var out = { habits: [], logs: this.logs };
    Object.keys(this.habits).forEach(function (k) { out.habits.push(this.habits[k]); }, this);
    return out;
  };

  HabitosStore.prototype.load = function (raw) {
    var self = this;
    this.habits = {};
    this.logs = [];
    (raw.habits || []).forEach(function (h) {
      if (h && h.name) self.habits[h.habit_id || slugify(h.name)] = Object.assign({}, h);
    });
    (raw.logs || []).forEach(function (l) {
      if (l && l.habit_id && l.date) self.logs.push({ habit_id: l.habit_id, date: l.date });
    });
  };

  // --------------------------------------------------------------- engine
  function HabitEngine(opts) {
    opts = opts || {};
    this.store = new HabitosStore();
    this.storageKey = opts.storageKey || "habitos_state";
    this.today = parseISO(todayISO());
    this._load();
    if (Object.keys(this.store.habits).length === 0) {
      this._seedDemo(); // friendly default: demo dataset visible on first open
    }
  }

  HabitEngine.prototype._load = function () {
    try {
      var raw = JSON.parse(localStorage.getItem(this.storageKey) || "null");
      if (raw) this.store.load(raw);
    } catch (e) { /* fresh start */ }
  };

  HabitEngine.prototype._save = function () {
    try { localStorage.setItem(this.storageKey, JSON.stringify(this.store.toJSON())); }
    catch (e) { /* storage full/blocked */ }
  };

  HabitEngine.prototype._logMap = function () {
    var m = {};
    this.store.logs.forEach(function (l) {
      (m[l.habit_id] = m[l.habit_id] || new Set()).add(l.date);
    });
    return m;
  };

  HabitEngine.prototype.addHabit = function (name, category, target, frequency, color) {
    name = String(name || "").trim();
    if (!name) throw new Error("habit name is required");
    if (CATEGORIES.indexOf(category) < 0) category = "general";
    var hid = slugify(name), base = hid, n = 2;
    while (this.store.habits[hid]) { hid = base + "-" + n; n += 1; }
    var habit = {
      name: name, habit_id: hid, category: category,
      target_per_week: normalizeTarget(target, frequency),
      color: color || DEFAULT_COLORS[category] || DEFAULT_COLORS.general,
      created: todayISO(), archived: false
    };
    this.store.habits[hid] = habit;
    this._save();
    return habit;
  };

  HabitEngine.prototype.getHabit = function (id) {
    if (this.store.habits[id]) return this.store.habits[id];
    var low = String(id).toLowerCase();
    var keys = Object.keys(this.store.habits);
    for (var i = 0; i < keys.length; i++) {
      var h = this.store.habits[keys[i]];
      if (h.name.toLowerCase() === low || slugify(h.name) === slugify(id)) return h;
    }
    return null;
  };

  HabitEngine.prototype.listHabits = function (includeArchived) {
    var out = [], self = this;
    Object.keys(this.store.habits).forEach(function (k) {
      var h = self.store.habits[k];
      if (!includeArchived && h.archived) return;
      out.push(h);
    });
    out.sort(function (a, b) { return a.created < b.created ? -1 : a.created > b.created ? 1 : 0; });
    this.refreshMetrics(out);
    return out;
  };

  HabitEngine.prototype.deleteHabit = function (id) {
    var h = this.getHabit(id);
    if (!h) return false;
    delete this.store.habits[h.habit_id];
    this.store.logs = this.store.logs.filter(function (l) { return l.habit_id !== h.habit_id; });
    this._save();
    return true;
  };

  HabitEngine.prototype.archiveHabit = function (id, archived) {
    var h = this.getHabit(id);
    if (!h) return null;
    h.archived = !!archived;
    this._save();
    return h;
  };

  HabitEngine.prototype.log = function (id, date, note) {
    var h = this.getHabit(id);
    if (!h) throw new Error("unknown habit: " + id);
    date = date || todayISO();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("invalid date " + date);
    this.store.logs = this.store.logs.filter(function (l) {
      return !(l.habit_id === h.habit_id && l.date === date);
    });
    this.store.logs.push({ habit_id: h.habit_id, date: date, note: note || "" });
    this._save();
    return { habit_id: h.habit_id, date: date };
  };

  HabitEngine.prototype.unlog = function (id, date) {
    var h = this.getHabit(id);
    if (!h) return false;
    date = date || todayISO();
    var before = this.store.logs.length;
    this.store.logs = this.store.logs.filter(function (l) {
      return !(l.habit_id === h.habit_id && l.date === date);
    });
    var changed = this.store.logs.length !== before;
    if (changed) this._save();
    return changed;
  };

  HabitEngine.prototype.doneOn = function (id, date) {
    var h = this.getHabit(id);
    if (!h) return false;
    date = date || todayISO();
    return this.store.logs.some(function (l) {
      return l.habit_id === h.habit_id && l.date === date;
    });
  };

  HabitEngine.prototype.refreshMetrics = function (habits) {
    var m = this._logMap(), self = this;
    habits = habits || Object.keys(this.store.habits).map(function (k) { return self.store.habits[k]; });
    habits.forEach(function (h) {
      var dates = m[h.habit_id] || new Set();
      var streaks = computeStreaks(dates, self.today);
      var w = weekStats(dates, h.target_per_week, self.today);
      h.current_streak = streaks.current;
      h.longest_streak = streaks.longest;
      h.completion_pct = w.completion_pct;
      h.week_done = w.week_done;
      h.week_possible = w.week_possible;
      h.total_done = dates.size;
      h.status = statusFor(w.completion_pct, w.week_done, h.target_per_week);
    });
  };

  HabitEngine.prototype.summary = function () {
    var habits = this.listHabits(false);
    var onTrack = 0, atRisk = 0, off = 0;
    habits.forEach(function (h) {
      if (h.status === "on_track") onTrack += 1;
      else if (h.status === "at_risk") atRisk += 1;
      else off += 1;
    });
    return {
      active_habits: habits.length,
      total_logs: this.store.logs.length,
      week_logs: this.store.logs.filter(function (l) {
        var d = parseISO(l.date), m = isoWeekMonday(new Date());
        return d >= m && d <= new Date();
      }).length,
      on_track: onTrack, at_risk: atRisk, off_track: off
    };
  };

  HabitEngine.prototype.habitDetail = function (id) {
    var h = this.getHabit(id);
    if (!h) return null;
    this.refreshMetrics([h]);
    var dates = this.store.logs.filter(function (l) { return l.habit_id === h.habit_id; })
      .map(function (l) { return l.date; }).sort();
    var last7 = [];
    for (var i = 6; i >= 0; i--) {
      var d = new Date(this.today.getFullYear(), this.today.getMonth(), this.today.getDate() - i);
      var iso = dateISO(d);
      last7.push({ date: iso, done: dates.indexOf(iso) >= 0 });
    }
    var detail = Object.assign({}, h);
    detail.log_dates = dates;
    detail.last_7_days = last7;
    detail.week_monday = dateISO(isoWeekMonday(this.today));
    return detail;
  };

  HabitEngine.prototype.weeklyReportText = function () {
    var habits = this.listHabits(false);
    var lines = [];
    lines.push("HABITOS Weekly Report — week of " + dateISO(isoWeekMonday(this.today)));
    lines.push("Generated " + todayISO() + " · " + habits.length + " active habit(s)");
    lines.push("-".repeat(60));
    habits.forEach(function (h) {
      lines.push("[" + h.status.toUpperCase() + "] " + h.name +
        " (" + h.week_done + "/" + h.target_per_week + " · " + Math.round(h.completion_pct) +
        "% · streak " + h.current_streak + "d / best " + h.longest_streak + "d)");
    });
    var s = this.summary();
    lines.push("-".repeat(60));
    lines.push("On track " + s.on_track + " · At risk " + s.at_risk + " · Off track " + s.off_track);
    return lines.join("\n");
  };

  HabitEngine.prototype.reset = function () {
    this.store = new HabitosStore();
    localStorage.removeItem(this.storageKey);
    this._seedDemo();
  };

  HabitEngine.prototype._seedDemo = function () {
    var self = this;
    var seeds = [
      ["Meditate", "mindfulness", 7],
      ["Read 20 pages", "learning", 5],
      ["Workout", "fitness", 4],
      ["Ship code", "productivity", 3]
    ];
    var ids = seeds.map(function (s) { return self.addHabit(s[0], s[1], s[2]); });
    var today = new Date();
    seeds.forEach(function (s, idx) {
      var hid = ids[idx].habit_id;
      for (var back = 20; back >= 0; back--) {
        var d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - back);
        var r = pseudoRandom(idx * 31 + back * 17);
        if (s[1] === "fitness" && (d.getDay() === 0 || d.getDay() === 6) && r < 0.7) continue;
        if (r < (s[2] / 7) * 0.92) self.log(hid, dateISO(d), "demo");
      }
    });
  };

  function pseudoRandom(seed) {
    var x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
    return x - Math.floor(x);
  }

  global.HabitosEngine = HabitEngine;
  global.HabitosStore = HabitosStore;
  global.habitosUtils = {
    dateISO: dateISO, todayISO: todayISO, slugify: slugify, parseISO: parseISO,
    isoWeekMonday: isoWeekMonday, normalizeTarget: normalizeTarget,
    computeStreaks: computeStreaks, weekStats: weekStats, statusFor: statusFor
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { HabitEngine: HabitEngine, HabitosStore: HabitosStore, utils: global.habitosUtils };
  }
})(typeof window !== "undefined" ? window : globalThis);
