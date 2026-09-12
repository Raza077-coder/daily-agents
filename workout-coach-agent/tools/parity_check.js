// Python <-> JavaScript parity harness for the FORGE engine.
//
// The browser HUD re-implements the progression rules in plain JS because it
// has to run with zero network access. That duplication is the biggest risk in
// this project: the HUD could quietly disagree with the CLI and nobody would
// notice until a user followed advice that the Python engine would never give.
//
// So this script does not "check the JS works" \u2014 it dumps the JS engine's
// answers as JSON and lets `tools/parity_check.py` diff them against the real
// Python engine. Any divergence is a hard failure.
//
// Network is stubbed out first: any fetch/XHR throws, which proves the demo is
// genuinely self-contained rather than passing only because it had connectivity.
//
// Usage:  node tools/parity_check.js > /tmp/forge_js.json

global.XMLHttpRequest = function () {
  throw new Error("NETWORK ATTEMPTED");
};
global.fetch = function () {
  throw new Error("NETWORK ATTEMPTED");
};

var F = require("../web-live/forge-engine.js");

var out = { cases: [] };

// --- set-spec parsing -------------------------------------------------------
[
  "60x8",
  "60x8x3",
  "60x8,60x8,60x6",
  "bw x10",
  "bw+10 x5",
  "60x8@8",
].forEach(function (spec) {
  out.cases.push({ kind: "parse", spec: spec, result: F.parseSetSpec(spec) });
});

// --- e1RM ------------------------------------------------------------------
[[100, 5], [60, 8], [80, 12], [0, 10]].forEach(function (pair) {
  out.cases.push({
    kind: "e1rm",
    weight: pair[0],
    reps: pair[1],
    result: F.epleyE1rm(pair[0], pair[1]),
  });
});

// --- progression: all five outcomes ----------------------------------------
function prog(label, sessions, repLow, repHigh, sets) {
  var hist = F.buildHistory(sessions);
  var advice = F.nextPrescription(
    "barbell_bench_press", hist, repLow, repHigh, sets, 80, "intermediate"
  );
  out.cases.push({
    kind: "prescription",
    label: label,
    action: advice.action,
    weight: advice.weight,
    target_reps_low: advice.target_reps_low,
    target_reps_high: advice.target_reps_high,
    reason: advice.reason,
  });
}

prog("no_history", [], 8, 12, 3);
prog("increase", [
  { date: "2026-01-01", sets: [{ weight: 60, reps: 12 }, { weight: 60, reps: 12 }, { weight: 60, reps: 12 }] },
], 8, 12, 3);
prog("repeat_shortfall", [
  { date: "2026-01-01", sets: [{ weight: 60, reps: 8 }, { weight: 60, reps: 7 }, { weight: 60, reps: 6 }] },
], 8, 12, 3);
prog("repeat_noshortfall", [
  { date: "2026-01-01", sets: [{ weight: 60, reps: 10 }, { weight: 60, reps: 9 }, { weight: 60, reps: 9 }] },
], 8, 12, 3);
prog("deload", [
  { date: "2026-01-01", sets: [{ weight: 60, reps: 6 }, { weight: 60, reps: 5 }] },
  { date: "2026-01-03", sets: [{ weight: 60, reps: 6 }, { weight: 60, reps: 6 }] },
  { date: "2026-01-05", sets: [{ weight: 60, reps: 5 }, { weight: 60, reps: 6 }] },
], 8, 12, 3);

// --- bodyweight progresses by reps, not load -------------------------------
var bwHist = F.buildHistory([
  { date: "2026-01-01", sets: [{ weight: 0, reps: 15 }, { weight: 0, reps: 15 }, { weight: 0, reps: 15 }] },
]);
out.cases.push({
  kind: "prescription",
  label: "bodyweight_reps",
  action: F.nextPrescription("pullup", bwHist, 8, 15, 3, 80, "intermediate").action,
});

// --- program builder: the full split assignment, per day count -------------
out.programs = {};
[2, 3, 4, 5, 6].forEach(function (days) {
  var p = F.buildProgram({
    daysPerWeek: days,
    experience: "intermediate",
    goal: "strength",
    bodyweightKg: 80,
  });
  out.programs[String(days)] = {
    split: p.split,
    days: p.sessions.map(function (s) {
      return {
        title: s.title,
        day: s.day,
        exercises: s.exercises.map(function (e) {
          return {
            id: e.exercise_id,
            sets: e.sets,
            rep_low: e.rep_low,
            rep_high: e.rep_high,
            start_weight: e.start_weight,
          };
        }),
      };
    }),
  };
});

// --- cross-muscle contamination guard --------------------------------------
// An isolation slot on an upper/push/pull day must never be filled by a leg
// movement. This is the bug the picker's constraint ladder exists to prevent,
// so it is asserted rather than assumed.
var forbidden = ["quads", "hamstrings", "calves", "glutes"];
out.cross_muscle_bugs = [];
Object.keys(out.programs).forEach(function (d) {
  out.programs[d].days.forEach(function (sess) {
    if (!/^(Pull|Push|Upper)/.test(sess.title)) return;
    sess.exercises.forEach(function (e) {
      var ex = F.getExercise(e.id);
      if (ex.pattern === "isolation" && forbidden.indexOf(ex.primary) >= 0) {
        out.cross_muscle_bugs.push(sess.title + " -> " + ex.name);
      }
    });
  });
});

// --- projection ladder must climb reps then load ---------------------------
var p3 = F.buildProgram({
  daysPerWeek: 3,
  experience: "intermediate",
  goal: "strength",
  bodyweightKg: 80,
});
out.projection = F.projectOverload(p3, 7).map(function (w) {
  var lift = w.sessions[0].lifts.filter(function (l) {
    return l.exercise === "back_squat";
  })[0];
  return { week: w.week, weight: lift.weight, reps: lift.reps };
});

// --- exercise library integrity -------------------------------------------
out.library = {
  count: F.DATA.exercises.length,
  ids: F.DATA.exercises.map(function (e) { return e.id; }).sort(),
};

process.stdout.write(JSON.stringify(out, null, 2));
