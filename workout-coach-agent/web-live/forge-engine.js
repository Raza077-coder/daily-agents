/* FORGE — browser engine.
 *
 * A faithful JavaScript port of forge/engine.py, forge/program.py and
 * forge/analytics.py. It exists so the live demo runs the REAL progression
 * rules in the browser instead of reimplementing them approximately.
 *
 * There is no network access anywhere in this file by design.
 */
(function (global) {
  "use strict";

  var DATA = global.FORGE_DATA || (typeof require !== "undefined" ? require("./forge-data.js") : {});
  var EX = {};
  (DATA.exercises || []).forEach(function (e) { EX[e.id] = e; });

  var STALL_WINDOW = 3;
  var PLATEAU_SESSIONS = 4;
  var SECONDARY_CREDIT = 0.5;
  var EXPERIENCE_FACTOR = { beginner: 0.6, intermediate: 0.8, advanced: 1.0 };

  /* ---------------------------------------------------------------- maths */

  function epleyE1rm(weight, reps) {
    if (weight <= 0 || reps <= 0) return 0;
    if (reps === 1) return round(weight, 1);
    return round(weight * (1 + reps / 30), 1);
  }

  function round(v, dp) {
    var f = Math.pow(10, dp || 1);
    return Math.round(v * f) / f;
  }

  function roundToIncrement(weight, increment) {
    if (!increment || increment <= 0) return 0;
    return round(Math.round(weight / increment) * increment, 2);
  }

  function fmtKg(v) {
    // 0 kg on a bodyweight movement means "the athlete", so name it. Printing
    // "Hold 0kg" would be nonsense. Mirrors _fmt() in forge/engine.py.
    if (!v) return "bodyweight";
    return v === Math.floor(v) ? v + "kg" : v + "kg";
  }

  function fmtIncrement(v) {
    // Increments are always stated in kilos, including 0 where there is no
    // load to add — mirrors _fmt_kg() in forge/engine.py.
    return v + "kg";
  }

  /* ------------------------------------------------------------- library */

  function getExercise(id) {
    if (EX[id]) return EX[id];
    var key = String(id || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
    return EX[key] || null;
  }

  function searchExercises(q) {
    q = String(q || "").trim().toLowerCase();
    if (!q) return [];
    return (DATA.exercises || []).filter(function (e) {
      return (
        e.id.indexOf(q) >= 0 ||
        e.name.toLowerCase().indexOf(q) >= 0 ||
        e.primary.indexOf(q) >= 0 ||
        (e.secondary || []).some(function (m) { return m.indexOf(q) >= 0; }) ||
        e.equipment.indexOf(q) >= 0
      );
    });
  }

  function filterExercises(opt) {
    opt = opt || {};
    var equip = opt.equipment ? opt.equipment.map(function (s) { return s.toLowerCase(); }) : null;
    // `exclude` arrives either as an array (callers building ad-hoc lists) or as
    // a set-like object (the builder's `used` map) — accept both.
    var exclude = {};
    var raw = opt.exclude;
    if (raw) {
      if (Array.isArray(raw)) {
        raw.forEach(function (id) { exclude[id] = true; });
      } else {
        Object.keys(raw).forEach(function (id) { if (raw[id]) exclude[id] = true; });
      }
    }
    // Heaviest-loadable equipment first. Mirrors EQUIPMENT_RANK in
    // forge/exercises.py: with no equipment filter the old alphabetic sort let
    // the first bodyweight movement beat every barbell lift, so a strength
    // programme prescribed Back Extension instead of Romanian Deadlift.
    var equipRank = { barbell: 0, machine: 1, cable: 2, dumbbell: 3, kettlebell: 4, bodyweight: 5, band: 6 };
    var levelOrder = { beginner: 0, intermediate: 1, advanced: 2 };
    var ceiling = levelOrder[opt.maxLevel || "advanced"];
    return (DATA.exercises || [])
      .filter(function (e) {
        if (exclude[e.id]) return false;
        if (equip && equip.indexOf(e.equipment) < 0) return false;
        if (opt.muscle && [e.primary].concat(e.secondary || []).indexOf(opt.muscle) < 0) return false;
        if (opt.pattern && e.pattern !== opt.pattern) return false;
        if (opt.kind && e.kind !== opt.kind) return false;
        if ((levelOrder[e.level] || 0) > ceiling) return false;
        return true;
      })
      .sort(function (a, b) {
        var ka = a.kind === "compound" ? 0 : 1;
        var kb = b.kind === "compound" ? 0 : 1;
        if (ka !== kb) return ka - kb;
        var ra = equipRank[a.equipment];
        var rb = equipRank[b.equipment];
        ra = ra === undefined ? 9 : ra;
        rb = rb === undefined ? 9 : rb;
        if (ra !== rb) return ra - rb;
        return a.name < b.name ? -1 : 1;
      });
  }

  /* ---------------------------------------------------------- set parsing */

  function parseSetSpec(spec) {
    var out = [];
    if (!spec || !String(spec).trim()) throw new Error("empty set spec");

    String(spec).replace(/\|/g, ",").split(",").forEach(function (chunk) {
      chunk = chunk.trim();
      if (!chunk) return;

      var body = chunk, rpe = null;
      if (body.indexOf("@") >= 0) {
        var seg = body.split("@");
        body = seg[0].trim();
        var r = parseFloat(seg[1]);
        if (isNaN(r)) throw new Error("bad RPE in " + JSON.stringify(chunk));
        rpe = r;
      }

      if (body.toLowerCase().indexOf("x") < 0) {
        throw new Error("bad set " + JSON.stringify(chunk) + " — expected WEIGHTxREPS or WEIGHTxREPSxSETS");
      }
      var parts = body.toLowerCase().split("x").map(function (s) { return s.trim(); });
      if (parts.length !== 2 && parts.length !== 3) {
        throw new Error("bad set " + JSON.stringify(chunk) + " — expected WEIGHTxREPS or WEIGHTxREPSxSETS");
      }

      var weight = parseWeight(parts[0], chunk);
      var reps = parseInt(parseFloat(parts[1]), 10);
      var count = parts.length === 3 ? parseInt(parseFloat(parts[2]), 10) : 1;

      if (isNaN(reps) || reps <= 0) throw new Error("rep count must be positive in " + JSON.stringify(chunk));
      if (isNaN(count) || count <= 0) throw new Error("set count must be positive in " + JSON.stringify(chunk));

      for (var i = 0; i < count; i++) out.push({ weight: weight, reps: reps, rpe: rpe });
    });

    if (!out.length) throw new Error("no sets parsed from " + JSON.stringify(spec));
    return out;
  }

  function parseWeight(token, chunk) {
    token = String(token).trim().toLowerCase().replace(/\s+/g, "");
    if (!token) throw new Error("missing weight in " + JSON.stringify(chunk));
    if (token === "bw" || token === "bodyweight" || token === "bw+0") return 0;
    if (token.indexOf("bw") === 0) {
      var rest = token.slice(2);
      if (rest.charAt(0) === "+") rest = rest.slice(1);
      var v = parseFloat(rest);
      if (isNaN(v)) throw new Error("bad bodyweight offset " + JSON.stringify(token));
      return v;
    }
    var w = parseFloat(token);
    if (isNaN(w)) throw new Error("bad weight " + JSON.stringify(token));
    return w;
  }

  /* ----------------------------------------------------- progression core */

  function startingWeight(exerciseId, bodyweightKg, experience) {
    var ex = getExercise(exerciseId);
    if (!ex || ex.equipment === "bodyweight") return 0;
    var bw = bodyweightKg || 75;
    var factor = EXPERIENCE_FACTOR[experience] || 0.6;
    var raw = bw * ex.ratio * factor;
    if (raw <= 0) return ex.increment;
    var snapped = roundToIncrement(raw, ex.increment);
    return snapped > 0 ? snapped : ex.increment;
  }

  /* nextPrescription — mirrors ForgeEngine.next_prescription exactly.
   * `history` is an array of sessions: { date, sets:[{weight,reps}], topWeight,
   * topReps, bestE1rm, volume } in chronological order. */
  function nextPrescription(exerciseId, history, repLow, repHigh, sets, bodyweightKg, experience) {
    repLow = repLow || 8;
    repHigh = repHigh || 12;
    sets = sets || 3;
    var ex = getExercise(exerciseId);
    if (!ex) throw new Error("unknown exercise " + exerciseId);
    history = history || [];

    if (!history.length) {
      var start = startingWeight(exerciseId, bodyweightKg, experience);
      return {
        exercise: ex.id,
        name: ex.name,
        action: "start",
        weight: start,
        target_reps_low: repLow,
        target_reps_high: repHigh,
        sets: sets,
        status: "new",
        reason:
          "No history for this lift yet — here is a deliberately conservative starting " +
          "load. Leave 2-3 reps in reserve and we will calibrate from real data next session."
      };
    }

    var recent = history.slice(-STALL_WINDOW);
    var last = history[history.length - 1];
    var lastSets = last.sets || [];
    var lowHits = lastSets.filter(function (s) { return s.reps < repLow; }).length;
    var topHits = lastSets.filter(function (s) { return s.reps >= repHigh; }).length;
    var working = last.topWeight;

    // Earned an increase.
    if (lastSets.length && topHits === lastSets.length && working > 0) {
      var nxt = roundToIncrement(working + ex.increment, ex.increment);
      return {
        exercise: ex.id,
        name: ex.name,
        action: "increase",
        weight: nxt,
        target_reps_low: repLow,
        target_reps_high: repHigh,
        sets: sets,
        status: "progressing",
        previous_weight: working,
        reason:
          "All " + lastSets.length + " sets hit " + repHigh + "+ reps on " + fmtKg(working) +
          ". Add one increment (" + fmtKg(ex.increment) + ") to " + fmtKg(nxt) +
          " and start again at " + repLow + " reps — that is the double-progression rule."
      };
    }

    // Bodyweight work progresses by reps.
    if (ex.equipment === "bodyweight" && working === 0 && topHits === lastSets.length && lastSets.length) {
      return {
        exercise: ex.id,
        name: ex.name,
        action: "increase_reps",
        weight: 0,
        target_reps_low: repHigh + 2,
        target_reps_high: repHigh + 5,
        sets: sets,
        status: "progressing",
        reason:
          "Bodyweight sets are all at " + repHigh + "+ reps. Move the range up " +
          "(or add external load) to keep the stimulus."
      };
    }

    // Repeated misses -> deload.
    var misses = recent.filter(function (r) {
      return (r.sets || []).some(function (s) { return s.reps < repLow; });
    });
    if (misses.length >= STALL_WINDOW && working > 0) {
      var back = roundToIncrement(working * 0.9, ex.increment);
      return {
        exercise: ex.id,
        name: ex.name,
        action: "deload",
        weight: back,
        target_reps_low: repLow,
        target_reps_high: repHigh,
        sets: sets,
        status: "deload",
        previous_weight: working,
        reason:
          STALL_WINDOW + " sessions in a row came in under " + repLow + " reps. " +
          "Drop ~10% to " + fmtKg(back) + ", rebuild quality reps, and we will climb " +
          "back past this weight within a few sessions."
      };
    }

    // Plateau: flat estimated 1RM.
    var e1rms = history.slice(-(PLATEAU_SESSIONS + 1)).map(function (r) { return r.bestE1rm; });
    if (e1rms.length > PLATEAU_SESSIONS && working > 0) {
      var spread = Math.max.apply(null, e1rms) - Math.min.apply(null, e1rms);
      var threshold = Math.max(0.5, 0.01 * Math.max.apply(null, e1rms));
      if (spread < threshold) {
        return {
          exercise: ex.id,
          name: ex.name,
          action: "change_stimulus",
          weight: working,
          target_reps_low: Math.max(3, repLow - 3),
          target_reps_high: Math.max(5, repLow - 1),
          sets: sets + 1,
          status: "plateau",
          previous_weight: working,
          reason:
            "Estimated 1RM has not moved across " + PLATEAU_SESSIONS + " sessions. " +
            "Rather than grinding the same numbers, drop the rep range and add a set " +
            "for a few weeks — a different stimulus, same lift."
        };
      }
    }

    // Default: repeat and chase reps.
    var targetLow = Math.min(repHigh, Math.max(repLow, (last.topReps || repLow) + 1));
    return {
      exercise: ex.id,
      name: ex.name,
      action: "repeat",
      weight: working,
      target_reps_low: targetLow,
      target_reps_high: repHigh,
      sets: sets,
      status: "building",
      previous_weight: working,
      reason:
        "Hold " + fmtKg(working) + " and beat your last session (" + fmtKg(last.topWeight) +
        "x" + last.topReps + "). " +
        // Naming a shortfall when every set cleared the bottom of the range
        // reads as a contradiction ("0 set(s) fell under 8 — close that gap").
        (lowHits
          ? lowHits + " of " + lastSets.length + " set(s) fell under " + repLow +
            " last time — closing that gap is the next win. "
          : "Every set cleared " + repLow + " reps but none reached " + repHigh +
            " yet. ") +
        "Hit top of range on every set and the weight goes up."
    };
  }

  /* Build a history array from raw {date, sets:[{weight,reps}]} sessions. */
  function buildHistory(sessions) {
    return (sessions || []).slice().sort(function (a, b) {
      return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
    }).map(function (s) {
      var sets = s.sets || [];
      var top = null;
      sets.forEach(function (x) {
        if (!top || x.weight > top.weight || (x.weight === top.weight && x.reps > top.reps)) top = x;
      });
      var vol = sets.reduce(function (a, x) { return a + x.weight * x.reps; }, 0);
      var best = sets.reduce(function (a, x) { return Math.max(a, epleyE1rm(x.weight, x.reps)); }, 0);
      return {
        date: s.date,
        session: s.session || "",
        sets: sets,
        setCount: sets.length,
        topWeight: top ? top.weight : 0,
        topReps: top ? top.reps : 0,
        bestE1rm: round(best, 1),
        volume: round(vol, 1)
      };
    });
  }

  /* ------------------------------------------------------ program builder */

  function chooseSplit(days) {
    if (days <= 2) return "full_body_2";
    if (days === 3) return "full_body_3";
    if (days === 4) return "upper_lower_4";
    if (days === 5) return "ppl_5";
    return "ppl_6";
  }

  function pick(pattern, muscle, equipFilter, maxLevel, used, allowReuse) {
    // Same relaxation ladder as program.py: hold the muscle constraint longest
    // so a pull-day isolation slot can never be filled by a leg movement.
    // `allowReuse` is the second-chance call — the caller passes it only after a
    // fresh search came back empty, so a movement repeats in one session rather
    // than the whole week quietly filling up with duplicates.
    var ladder = muscle
      ? [
          [pattern, muscle, true], [pattern, muscle, false],
          [null, muscle, true], [null, muscle, false],
          [pattern, null, true], [pattern, null, false]
        ]
      : [[pattern, null, true], [pattern, null, false]];

    for (var i = 0; i < ladder.length; i++) {
      var step = ladder[i];
      var cands = filterExercises({
        equipment: equipFilter,
        muscle: step[1],
        pattern: step[0],
        maxLevel: maxLevel,
        exclude: (step[2] && !allowReuse) ? used : null
      });
      if (cands.length) return cands[0];
    }
    return null;
  }

  function buildProgram(opts) {
    opts = opts || {};
    var days = Math.max(2, Math.min(6, opts.daysPerWeek || 3));
    var experience = opts.experience || "beginner";
    var goal = opts.goal || "strength";
    var bodyweight = opts.bodyweightKg || 75;
    var key = opts.split || chooseSplit(days);
    var template = (DATA.splits || {})[key];
    if (!template) throw new Error("unknown split " + key);

    var schemes = DATA.goalSchemes || {};
    var scheme = schemes[goal] || schemes.strength;
    var equip = opts.equipment && opts.equipment.length
      ? opts.equipment.map(function (s) { return s.toLowerCase(); }).concat(["bodyweight"])
      : null;
    var maxLevel = experience === "beginner" ? "beginner" : experience === "intermediate" ? "intermediate" : "advanced";
    var cap = (DATA.weeklySetCap || {})[experience] || 12;
    var MIN_SETS_PER_SLOT = 3;
    var used = {};
    var weekly = {};

    var sessions = (template.sessions || []).map(function (sess, idx) {
      var slots = (DATA.sessionTemplates || {})[sess.kind] || [];
      var chosen = [];
      var focus = [];

      slots.forEach(function (slot) {
        for (var i = 0; i < slot.slots; i++) {
          var wanted = slot.focus && slot.focus.length ? slot.focus[(i + idx) % slot.focus.length] : null;
          var ex = pick(slot.pattern, wanted, equip, maxLevel, used);
          if (!ex) {
            // Nothing fresh left for this slot: allow one repeat in THIS
            // session rather than leaving the slot empty.
            ex = pick(slot.pattern, wanted, equip, maxLevel, used, true);
          }
          if (!ex) continue;

          var dose = scheme[ex.kind] || scheme.compound;
          var setCount = dose[0], repLow = dose[1], repHigh = dose[2];

          // If the weekly cap cannot fund a real dose, SKIP the slot instead of
          // padding it to a token 2 sets — padding both breached the cap and
          // produced a prescription too small to adapt to. Mirrors program.py.
          var muscle = ex.primary;
          var remaining = cap - (weekly[muscle] || 0);
          if (remaining < MIN_SETS_PER_SLOT) continue;
          if (setCount > remaining) setCount = remaining;
          weekly[muscle] = (weekly[muscle] || 0) + setCount;

          chosen.push({
            exercise_id: ex.id,
            name: ex.name,
            sets: setCount,
            rep_low: repLow,
            rep_high: repHigh,
            start_weight: startingWeight(ex.id, bodyweight, experience),
            increment: ex.increment,
            rest_sec: ex.kind === "compound" ? scheme.rest : Math.max(45, Math.floor(scheme.rest / 2))
          });
          used[ex.id] = true;
          [ex.primary].concat(ex.secondary || []).forEach(function (m) {
            if (focus.indexOf(m) < 0) focus.push(m);
          });
        }
      });

      return { day: sess.day, title: sess.title, focus: focus, exercises: chosen };
    });

    return {
      name: opts.name || (template.split + " · " + goal.charAt(0).toUpperCase() + goal.slice(1)),
      goal: goal,
      experience: experience,
      days_per_week: sessions.length,
      weeks: opts.weeks || (DATA.experienceWeeks || {})[experience] || 8,
      split: template.split,
      bodyweight_kg: bodyweight,
      sessions: sessions
    };
  }

  function projectOverload(program, weeks) {
    weeks = weeks || 4;
    var out = [];
    for (var w = 1; w <= weeks; w++) {
      var rows = program.sessions.map(function (sess) {
        var lifts = [];
        sess.exercises.forEach(function (pe) {
          var ex = getExercise(pe.exercise_id);
          if (!ex || ex.kind !== "compound" || ex.increment <= 0) return;
          var span = Math.max(1, pe.rep_high - pe.rep_low);
          var cycles = Math.floor((w - 1) / (span + 1));
          var offset = (w - 1) % (span + 1);
          var weight = roundToIncrement(pe.start_weight + cycles * ex.increment, ex.increment);
          var reps = pe.rep_low + offset;
          lifts.push({
            exercise: pe.exercise_id,
            name: ex.name,
            sets: pe.sets,
            weight: weight,
            reps: reps,
            e1rm: weight > 0 ? round(weight * (1 + reps / 30), 1) : 0
          });
        });
        return { session: sess.title, day: sess.day, lifts: lifts };
      });
      out.push({ week: w, sessions: rows });
    }
    return out;
  }

  /* ------------------------------------------------------------ analytics */

  function volumeByMuscle(workouts) {
    var out = {};
    var targets = {
      chest: 10, back: 12, lats: 10, front_delts: 8, side_delts: 8, rear_delts: 6,
      quads: 10, hamstrings: 8, glutes: 8, calves: 6, biceps: 8, triceps: 8, core: 6
    };
    workouts.forEach(function (w) {
      (w.entries || []).forEach(function (entry) {
        var ex = getExercise(entry.exercise_id);
        if (!ex || !entry.sets || !entry.sets.length) return;
        var n = entry.sets.length;
        var vol = entry.sets.reduce(function (a, s) { return a + s.weight * s.reps; }, 0);
        var shares = [[ex.primary, 1]].concat((ex.secondary || []).map(function (m) { return [m, SECONDARY_CREDIT]; }));
        shares.forEach(function (sh) {
          if (!out[sh[0]]) out[sh[0]] = { muscle: sh[0], sets: 0, tonnage: 0 };
          out[sh[0]].sets += n * sh[1];
          out[sh[0]].tonnage += vol * sh[1];
        });
      });
    });
    return Object.keys(out).sort(function (a, b) { return out[b].sets - out[a].sets; }).map(function (m) {
      var row = out[m];
      var target = targets[m] || 0;
      return {
        muscle: m,
        sets: round(row.sets, 1),
        tonnage: round(row.tonnage, 1),
        target: target,
        pct_of_target: target ? round((row.sets / target) * 100, 1) : 0
      };
    });
  }

  function balanceReport(workouts) {
    var vol = {};
    volumeByMuscle(workouts).forEach(function (r) { vol[r.muscle] = r.sets; });
    var get = function (m) { return vol[m] || 0; };
    var findings = [];

    var push = get("chest") + get("front_delts") + get("triceps");
    var pull = get("back") + get("lats") + get("rear_delts") + get("biceps");
    if (push && pull) {
      var ratio = round(pull / push, 2);
      if (ratio < 0.8) {
        findings.push({
          issue: "push_dominant",
          severity: ratio < 0.65 ? "high" : "medium",
          detail: "Pulling volume is only " + ratio + "x pushing. Add rows or lat pulldowns — balanced shoulders depend on it.",
          ratio: ratio
        });
      } else if (ratio > 1.4) {
        findings.push({
          issue: "pull_dominant", severity: "low", ratio: ratio,
          detail: "Pulling volume is " + ratio + "x pushing. Unusual, and rarely a problem — just do not neglect pressing."
        });
      }
    }

    var quads = get("quads"), hams = get("hamstrings") + get("glutes");
    if (quads && hams) {
      var qr = round(quads / hams, 2);
      if (qr > 1.6) {
        findings.push({
          issue: "quad_dominant", severity: "medium", ratio: qr,
          detail: "Quad volume is " + qr + "x posterior chain. RDLs, leg curls or hip thrusts will protect the knees and lift the deadlift."
        });
      }
    }

    if (get("rear_delts") < 4 && (push || pull)) {
      findings.push({
        issue: "neglected_rear_delts", severity: "medium", ratio: 0,
        detail: "Only " + round(get("rear_delts"), 1) + " rear-delt sets. Face pulls or reverse flyes are cheap insurance for shoulder health."
      });
    }
    if (get("calves") < 3 && quads + hams > 0) {
      findings.push({
        issue: "neglected_calves", severity: "low", ratio: 0,
        detail: "Calves are getting almost nothing. Two sets at the end of a leg day is enough."
      });
    }

    if (!findings.length) {
      findings.push({
        issue: "balanced", severity: "none", ratio: 1,
        detail: "No structural imbalances detected in the logged data. Keep doing what you are doing."
      });
    }

    var order = { high: 0, medium: 1, low: 2, none: 3 };
    findings.sort(function (a, b) { return order[a.severity] - order[b.severity]; });
    return { balanced: findings.every(function (f) { return f.severity === "none"; }), findings: findings };
  }

  /* Public surface — deliberately identical names to the Python module. */
  global.Forge = {
    DATA: DATA,
    epleyE1rm: epleyE1rm,
    roundToIncrement: roundToIncrement,
    fmtKg: fmtKg,
    getExercise: getExercise,
    searchExercises: searchExercises,
    filterExercises: filterExercises,
    parseSetSpec: parseSetSpec,
    startingWeight: startingWeight,
    nextPrescription: nextPrescription,
    buildHistory: buildHistory,
    chooseSplit: chooseSplit,
    buildProgram: buildProgram,
    projectOverload: projectOverload,
    volumeByMuscle: volumeByMuscle,
    balanceReport: balanceReport
  };

  if (typeof module !== "undefined" && module.exports) module.exports = global.Forge;
})(typeof window !== "undefined" ? window : globalThis);
