/* JOBFLOW engine — JavaScript port of jobtracker/{models,analytics,followups}.py
 *
 * This is a real re-implementation, not a mock.  tools/parity_check.py runs
 * this file and the Python engine over identical vaults and asserts the
 * outputs match field-for-field, so the browser demo and the CLI cannot drift
 * apart silently.
 *
 * Conventions kept identical to the Python:
 *   - dates are 'YYYY-MM-DD' strings; all arithmetic is UTC-midnight ms
 *   - salaries are integer minor units (cents)
 *   - every entry point takes an explicit `today` — nothing reads the clock
 */
(function (global) {
  'use strict';

  var STAGES = ['applied', 'screen', 'interview', 'onsite', 'offer', 'accepted'];
  var TERMINAL = ['rejected', 'withdrawn'];
  var ACTIVE = ['applied', 'screen', 'interview', 'onsite', 'offer'];
  var PRE = ['wishlist'];
  var CLOSED = ['rejected', 'withdrawn', 'accepted'];

  var STAGE_INDEX = {};
  STAGES.forEach(function (s, i) { STAGE_INDEX[s] = i; });

  var STATUS_LABELS = {
    wishlist: 'Wishlist', applied: 'Applied', screen: 'Recruiter Screen',
    interview: 'Interview', onsite: 'Onsite / Final', offer: 'Offer',
    accepted: 'Accepted', rejected: 'Rejected', withdrawn: 'Withdrawn'
  };

  var SOURCE_LABELS = {
    referral: 'Referral', company_site: 'Company site', linkedin: 'LinkedIn',
    job_board: 'Job board', recruiter: 'Recruiter', networking: 'Networking',
    other: 'Other'
  };

  var STAGE_MATURITY_DAYS = {
    applied: 21, screen: 30, interview: 45, onsite: 60, offer: 75, accepted: 90
  };

  var STALL_DAYS = {
    applied: 14, screen: 10, interview: 12, onsite: 10, offer: 7
  };
  var DEFAULT_STALL_DAYS = 14;

  var RULE_ORDER = [
    'offer_expiring', 'deadline_today', 'deadline_soon', 'interview_today',
    'interview_soon', 'interview_prep', 'offer_waiting', 'overdue_next_action',
    'no_response_overdue', 'stalled', 'close_the_loop', 'second_followup',
    'rejection_review', 'weekly_target', 'wishlist_nudge', 'log_wins'
  ];

  var RULE_LABELS = {
    offer_expiring: 'Offer decision due',
    deadline_today: 'Application deadline TODAY',
    deadline_soon: 'Deadline approaching',
    interview_today: 'Interview today',
    interview_soon: 'Interview coming up',
    interview_prep: 'Prep for interview',
    offer_waiting: 'Awaiting decision on offer',
    overdue_next_action: 'Next action overdue',
    no_response_overdue: 'Follow up — no response',
    stalled: 'Going stale',
    close_the_loop: 'Send a thank-you',
    second_followup: 'Second follow-up due',
    rejection_review: 'Learn from a rejection',
    weekly_target: 'Weekly application target',
    wishlist_nudge: 'Wishlist sitting idle',
    log_wins: 'Log your progress'
  };

  var WEIGHTS = {
    critical: 1000, high: 500, medium: 200, low: 60,
    overdue_per_day: 12, overdue_cap: 240,
    stale_per_day: 4, stale_cap: 160,
    priority_per_point: 15, deadline_imminence: 30
  };

  var SEVERITIES = ['critical', 'high', 'medium', 'low'];

  var CURRENCY_SYMBOLS = {
    USD: '$', EUR: '\u20ac', GBP: '\u00a3', PKR: '\u20a8', INR: '\u20b9',
    AED: 'AED ', CAD: 'C$', AUD: 'A$', SGD: 'S$'
  };

  /* ---------------------------------------------------------------- dates */

  var DAY_MS = 86400000;

  function parseDate(text) {
    if (!text) { return null; }
    if (text instanceof Date) { return text; }
    var s = String(text).slice(0, 10);
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (!m) { throw new Error('invalid date: ' + text); }
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  }

  function iso(d) {
    if (!d) { return null; }
    return d.toISOString().slice(0, 10);
  }

  function daysBetween(a, b) { return Math.round((b - a) / DAY_MS); }

  function diffDays(fromIso, toIso) {
    var a = parseDate(fromIso), b = parseDate(toIso);
    if (!a || !b) { return 0; }
    return daysBetween(a, b);
  }

  /* ---------------------------------------------------------------- money */

  function formatMoney(minor, currency, compact) {
    if (minor === null || minor === undefined) { return '\u2014'; }
    var sym = CURRENCY_SYMBOLS[(currency || 'USD').toUpperCase()] ||
      ((currency || '').toUpperCase() + ' ');
    var whole = Math.floor(minor / 100);
    var cents = minor % 100;
    if (compact && whole >= 1000) {
      if (whole >= 1000000) {
        return sym + roundHalfEven(whole / 100000) / 10 + 'M';
      }
      return sym + roundHalfEven(whole / 1000) + 'k';
    }
    var body = whole.toLocaleString('en-US');
    if (cents) { body = body + '.' + (cents < 10 ? '0' + cents : cents); }
    return sym + body;
  }

  function formatRange(lo, hi, currency, compact) {
    if ((lo === null || lo === undefined) && (hi === null || hi === undefined)) {
      return '\u2014';
    }
    if (lo !== null && lo !== undefined && hi !== null && hi !== undefined) {
      if (lo === hi) { return formatMoney(lo, currency, compact); }
      return formatMoney(lo, currency, compact) + '\u2013' +
        formatMoney(hi, currency, compact);
    }
    var one = (lo !== null && lo !== undefined) ? lo : hi;
    return (lo === null || lo === undefined ? 'up to ' : 'from ') +
      formatMoney(one, currency, compact);
  }

  /* ------------------------------------------------------------- derived  */

  function wasApplied(app) {
    if (app.applied_on) { return true; }
    return (app.events || []).some(function (e) { return e.kind === 'applied'; });
  }

  function furthestStage(app) {
    var best = null, bestIdx = -1;
    if (app.applied_on) { best = 'applied'; bestIdx = 0; }
    (app.events || []).forEach(function (e) {
      [e.to_status, e.from_status].forEach(function (c) {
        if (c && STAGE_INDEX[c] !== undefined && STAGE_INDEX[c] > bestIdx) {
          best = c; bestIdx = STAGE_INDEX[c];
        }
      });
    });
    if (app.status && STAGE_INDEX[app.status] !== undefined &&
        STAGE_INDEX[app.status] > bestIdx) {
      best = app.status; bestIdx = STAGE_INDEX[app.status];
    }
    return best;
  }

  function reachedIndex(app) {
    var s = furthestStage(app);
    return s ? STAGE_INDEX[s] : -1;
  }

  function hasResponse(app) {
    if (reachedIndex(app) > 0) { return true; }
    return (app.events || []).some(function (e) {
      return e.kind === 'followup' || e.kind === 'interview' ||
        e.kind === 'moved' || e.kind === 'closed';
    });
  }

  function lastActivityOn(app) {
    var stamps = [];
    (app.events || []).forEach(function (e) { stamps.push(e.on); });
    (app.interviews || []).forEach(function (i) { stamps.push(i.on); });
    (app.notes || []).forEach(function (n) { stamps.push(n.on); });
    ['applied_on', 'created_on'].forEach(function (k) {
      if (app[k]) { stamps.push(app[k]); }
    });
    if (!stamps.length) { return null; }
    stamps.sort();
    return stamps[stamps.length - 1];
  }

  function daysSinceActivity(app, today) {
    var last = lastActivityOn(app);
    if (!last) { return 0; }
    return Math.max(0, diffDays(last, today));
  }

  function stageEnteredOn(app) {
    var markers = [];
    if (app.status === 'applied' && app.applied_on) { markers.push(app.applied_on); }
    (app.events || []).forEach(function (e) {
      if (e.to_status === app.status) { markers.push(e.on); }
    });
    if (markers.length) {
      markers.sort();
      return markers[markers.length - 1];
    }
    if (app.applied_on) { return app.applied_on; }
    if (app.created_on) { return app.created_on; }
    return null;
  }

  function daysInStage(app, today) {
    var entered = stageEnteredOn(app);
    if (!entered) { return 0; }
    return Math.max(0, diffDays(entered, today));
  }

  function ageDays(app, today) {
    if (!app.applied_on) { return 0; }
    return Math.max(0, diffDays(app.applied_on, today));
  }

  function followupCount(app) {
    return (app.events || []).filter(function (e) { return e.kind === 'followup'; }).length;
  }

  function upcomingInterviews(app, today) {
    var t = parseDate(today);
    return (app.interviews || []).filter(function (i) {
      return !i.done && parseDate(i.on) >= t;
    }).sort(function (a, b) {
      return a.on < b.on ? -1 : a.on > b.on ? 1 : 0;
    });
  }

  function label(app) { return app.company + ' \u2014 ' + app.role; }

  function isActive(app) { return ACTIVE.indexOf(app.status) >= 0; }
  function isClosed(app) { return CLOSED.indexOf(app.status) >= 0; }

  /* ------------------------------------------------------------- funnel   */

  function funnelRows(apps, today) {
    var submitted = apps.filter(wasApplied);
    var total = submitted.length;
    var rows = [];

    STAGES.forEach(function (stage, index) {
      var reached = submitted.filter(function (a) { return reachedIndex(a) >= index; });
      var window = STAGE_MATURITY_DAYS[stage];
      var mature = index > 0
        ? submitted.filter(function (a) { return ageDays(a, today) >= window; })
        : submitted;
      var denominator = index > 0 ? mature.length : total;
      var rate = denominator ? (reached.length / denominator) * 100 : 0;
      var prevReached = index > 0
        ? submitted.filter(function (a) { return reachedIndex(a) >= index - 1; })
        : reached;
      var stepRate = index === 0
        ? (total ? 100 : 0)
        : (prevReached.length ? (reached.length / prevReached.length) * 100 : 0);

      rows.push({
        stage: stage,
        label: STATUS_LABELS[stage],
        reached: reached.length,
        denominator: denominator,
        rate: round1(rate),
        step_rate: round1(stepRate),
        maturity_days: window
      });
    });
    return rows;
  }

  function round1(x) { return Math.round(x * 10) / 10; }

  /* Python's f"{x:.0f}" rounds half to EVEN (106.5 -> 106), while JS
   * Math.round rounds half UP (106.5 -> 107).  Compacting a salary like
   * 10650000 minor units lands exactly on a .5 boundary, so the two engines
   * disagreed by 1k in the displayed median.  Replicate Python's rule. */
  function roundHalfEven(x) {
    var floor = Math.floor(x);
    var diff = x - floor;
    if (diff > 0.5) { return floor + 1; }
    if (diff < 0.5) { return floor; }
    return floor % 2 === 0 ? floor : floor + 1;
  }

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /* Python's strftime('%b %d') zero-pads the day; toLocaleDateString does
   * not, so 'Aug 03' vs 'Aug 3' was a real parity break. */
  function weekLabel(d) {
    var day = d.getUTCDate();
    return MONTHS[d.getUTCMonth()] + ' ' + (day < 10 ? '0' + day : day);
  }

  function funnel(apps, today) {
    var submitted = apps.filter(wasApplied);
    var rows = funnelRows(apps, today);
    var byStage = {};
    rows.forEach(function (r) { byStage[r.stage] = r; });

    var responded = submitted.filter(hasResponse);
    var matured = submitted.filter(function (a) {
      return ageDays(a, today) >= STAGE_MATURITY_DAYS.applied;
    });
    var interviewed = submitted.filter(function (a) {
      return reachedIndex(a) >= STAGE_INDEX.interview;
    });
    var offered = submitted.filter(function (a) {
      return reachedIndex(a) >= STAGE_INDEX.offer;
    });

    return {
      today: today,
      submitted: submitted.length,
      open: apps.filter(isActive).length,
      wishlist: apps.filter(function (a) { return PRE.indexOf(a.status) >= 0; }).length,
      closed: apps.filter(isClosed).length,
      responded: responded.length,
      response_denominator: matured.length,
      response_rate: round1(matured.length ? (responded.length / matured.length) * 100 : 0),
      screened: submitted.filter(function (a) {
        return reachedIndex(a) >= STAGE_INDEX.screen;
      }).length,
      interviewed: interviewed.length,
      offered: offered.length,
      rows: rows,
      offer_rate: byStage.offer ? byStage.offer.rate : 0,
      interview_rate: byStage.interview ? byStage.interview.rate : 0,
      onsite_rate: byStage.onsite ? byStage.onsite.rate : 0
    };
  }

  /* ------------------------------------------------------------- aging    */

  function agingRows(apps, today) {
    var out = [];
    apps.forEach(function (app) {
      if (!isActive(app)) { return; }
      var threshold = STALL_DAYS[app.status] !== undefined
        ? STALL_DAYS[app.status] : DEFAULT_STALL_DAYS;
      var quiet = daysSinceActivity(app, today);
      out.push({
        id: app.id,
        label: label(app),
        status: app.status,
        status_label: STATUS_LABELS[app.status],
        age_days: ageDays(app, today),
        days_in_stage: daysInStage(app, today),
        days_since_activity: quiet,
        stall_threshold: threshold,
        stalled: quiet >= threshold,
        last_activity_on: lastActivityOn(app),
        priority: app.priority === undefined ? 3 : app.priority,
        next_action: app.next_action || '',
        next_action_on: app.next_action_on || null
      });
    });
    out.sort(function (a, b) {
      return (b.days_since_activity - a.days_since_activity) ||
        (b.priority - a.priority) ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    });
    return out;
  }

  function stalled(apps, today) {
    return agingRows(apps, today).filter(function (r) { return r.stalled; });
  }

  function median(values) {
    if (!values.length) { return null; }
    var sorted = values.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid]
      : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
  }

  function timeInStage(apps, today) {
    var samples = {};
    STAGES.forEach(function (s) { samples[s] = []; });

    apps.forEach(function (app) {
      if (!wasApplied(app)) { return; }
      var entered = app.applied_on;
      if (!entered) { return; }
      var transitions = [];
      (app.events || []).forEach(function (e) {
        if (e.to_status && STAGE_INDEX[e.to_status] !== undefined) {
          transitions.push({ on: e.on, to: e.to_status });
        }
      });
      transitions.sort(function (a, b) { return a.on < b.on ? -1 : a.on > b.on ? 1 : 0; });

      var cursorStage = 'applied';
      var cursorStart = entered;
      transitions.forEach(function (t) {
        if (t.to === cursorStage) { return; }
        if (samples[cursorStage] && t.on >= cursorStart) {
          samples[cursorStage].push(diffDays(cursorStart, t.on));
        }
        cursorStage = t.to;
        cursorStart = t.on;
      });
      if (app.closed_on && CLOSED.indexOf(app.status) >= 0) {
        if (samples[cursorStage] && app.closed_on >= cursorStart) {
          samples[cursorStage].push(diffDays(cursorStart, app.closed_on));
        }
      }
    });

    var out = {};
    STAGES.forEach(function (stage) {
      var clean = (samples[stage] || []).filter(function (v) { return v >= 0; });
      out[stage] = {
        stage: stage,
        label: STATUS_LABELS[stage],
        samples: clean.length,
        median_days: clean.length ? median(clean) : null,
        min_days: clean.length ? Math.min.apply(null, clean) : null,
        max_days: clean.length ? Math.max.apply(null, clean) : null,
        mean_days: clean.length
          ? Math.round((clean.reduce(function (a, b) { return a + b; }, 0) / clean.length) * 10) / 10
          : null
      };
    });
    return out;
  }

  /* ------------------------------------------------------------ sources   */

  function sourceBreakdown(apps, today) {
    var submitted = apps.filter(wasApplied);
    var buckets = {};
    submitted.forEach(function (a) {
      var key = a.source || 'other';
      (buckets[key] = buckets[key] || []).push(a);
    });
    var rows = [];
    Object.keys(buckets).forEach(function (source) {
      var group = buckets[source];
      var mature = group.filter(function (a) {
        return ageDays(a, today) >= STAGE_MATURITY_DAYS.applied;
      });
      var responded = group.filter(hasResponse);
      var interviewed = group.filter(function (a) {
        return reachedIndex(a) >= STAGE_INDEX.interview;
      });
      var offered = group.filter(function (a) {
        return reachedIndex(a) >= STAGE_INDEX.offer;
      });
      rows.push({
        source: source,
        label: SOURCE_LABELS[source] || source,
        applied: group.length,
        responded: responded.length,
        interviewed: interviewed.length,
        offered: offered.length,
        response_rate: round1(mature.length ? (responded.length / mature.length) * 100 : 0),
        interview_rate: round1(group.length ? (interviewed.length / group.length) * 100 : 0)
      });
    });
    rows.sort(function (a, b) {
      return (b.interview_rate - a.interview_rate) ||
        (b.applied - a.applied) ||
        (a.source < b.source ? -1 : a.source > b.source ? 1 : 0);
    });
    return rows;
  }

  function salarySummary(apps) {
    var byCurrency = {};
    apps.forEach(function (app) {
      var has = (app.salary_min !== null && app.salary_min !== undefined) ||
        (app.salary_max !== null && app.salary_max !== undefined);
      if (!has) { return; }
      var cur = app.currency || 'USD';
      var bucket = byCurrency[cur] = byCurrency[cur] ||
        { currency: cur, count: 0, mins: [], maxs: [] };
      bucket.count++;
      if (app.salary_min !== null && app.salary_min !== undefined) {
        bucket.mins.push(app.salary_min);
      }
      if (app.salary_max !== null && app.salary_max !== undefined) {
        bucket.maxs.push(app.salary_max);
      }
    });

    var out = { currencies: {}, entries: 0 };
    Object.keys(byCurrency).forEach(function (cur) {
      var b = byCurrency[cur];
      var medMin = b.mins.length ? median(b.mins) : null;
      var medMax = b.maxs.length ? median(b.maxs) : null;
      out.currencies[cur] = {
        currency: cur,
        count: b.count,
        min: b.mins.length ? Math.min.apply(null, b.mins) : null,
        max: b.maxs.length ? Math.max.apply(null, b.maxs) : null,
        median_min: medMin,
        median_max: medMax,
        min_display: b.mins.length ? formatMoney(Math.min.apply(null, b.mins), cur, true) : '\u2014',
        max_display: b.maxs.length ? formatMoney(Math.max.apply(null, b.maxs), cur, true) : '\u2014',
        median_display: formatRange(medMin, medMax, cur, true)
      };
      out.entries += b.count;
    });
    return out;
  }

  /* ------------------------------------------------------------- weekly   */

  function mondayOf(today) {
    var d = parseDate(today);
    var dow = d.getUTCDay();          // 0=Sun .. 6=Sat
    var back = (dow + 6) % 7;         // days since Monday
    return new Date(d.getTime() - back * DAY_MS);
  }

  function isoWeekKey(d) {
    var target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    var dayNum = (target.getUTCDay() + 6) % 7;
    target = new Date(target.getTime() - dayNum * DAY_MS + 3 * DAY_MS);
    var firstThursday = new Date(Date.UTC(target.getUTCFullYear(), 0, 4));
    var fDayNum = (firstThursday.getUTCDay() + 6) % 7;
    firstThursday = new Date(firstThursday.getTime() - fDayNum * DAY_MS + 3 * DAY_MS);
    var week = 1 + Math.round((target - firstThursday) / (7 * DAY_MS));
    return target.getUTCFullYear() + '-W' + (week < 10 ? '0' + week : week);
  }

  function weeklyActivity(apps, today, weeks) {
    weeks = Math.max(1, Math.min(weeks || 12, 52));
    var start = mondayOf(today);
    var out = [];
    for (var i = weeks - 1; i >= 0; i--) {
      var ws = new Date(start.getTime() - 7 * i * DAY_MS);
      var we = new Date(ws.getTime() + 6 * DAY_MS);
      var wsIso = iso(ws), weIso = iso(we);
      var applied = apps.filter(function (a) {
        return a.applied_on && a.applied_on >= wsIso && a.applied_on <= weIso;
      }).length;
      var interviews = 0;
      apps.forEach(function (a) {
        (a.interviews || []).forEach(function (it) {
          if (it.on >= wsIso && it.on <= weIso) { interviews++; }
        });
      });
      var closed = apps.filter(function (a) {
        return a.closed_on && a.closed_on >= wsIso && a.closed_on <= weIso;
      }).length;
      out.push({
        week_start: wsIso, week_end: weIso, iso_week: isoWeekKey(ws),
        applied: applied, interviews: interviews, closed: closed,
        label: weekLabel(ws)
      });
    }
    return out;
  }

  function streak(apps, today) {
    var counts = {};
    apps.forEach(function (a) {
      if (!a.applied_on) { return; }
      var key = isoWeekKey(parseDate(a.applied_on));
      counts[key] = (counts[key] || 0) + 1;
    });
    var current = 0;
    var cursor = mondayOf(today);
    for (;;) {
      var key = isoWeekKey(cursor);
      if (counts[key]) { current++; cursor = new Date(cursor.getTime() - 7 * DAY_MS); }
      else { break; }
    }
    var best = 0, run = 0;
    weeklyActivity(apps, today, 52).forEach(function (row) {
      if (row.applied > 0) { run++; best = Math.max(best, run); }
      else { run = 0; }
    });
    return {
      current_weeks: current, longest_weeks: best,
      weeks_logged: Object.keys(counts).length
    };
  }

  function statusCounts(apps) {
    var counts = {};
    Object.keys(STATUS_LABELS).forEach(function (s) { counts[s] = 0; });
    apps.forEach(function (a) {
      counts[a.status] = (counts[a.status] || 0) + 1;
    });
    return counts;
  }

  /* ---------------------------------------------- follow-up engine  */

  function score(severity, app, opts) {
    opts = opts || {};
    var terms = {};
    terms.severity = WEIGHTS[severity] || 0;
    if (opts.overdue_days > 0) {
      terms.overdue = Math.min(opts.overdue_days * WEIGHTS.overdue_per_day,
        WEIGHTS.overdue_cap);
    }
    if (opts.stale_days > 0) {
      terms.staleness = Math.min(opts.stale_days * WEIGHTS.stale_per_day,
        WEIGHTS.stale_cap);
    }
    if (app) {
      terms.priority = (app.priority === undefined ? 3 : app.priority) *
        WEIGHTS.priority_per_point;
      var stage = furthestStage(app);
      if (stage) {
        terms.stage = STAGE_INDEX[stage] * 12;
      }
    }
    if (opts.imminent) { terms.imminence = WEIGHTS.deadline_imminence; }
    var total = 0;
    Object.keys(terms).forEach(function (k) {
      if (terms[k]) { total += terms[k]; } else { delete terms[k]; }
    });
    return { total: total, terms: terms };
  }

  /* Mirrors jobtracker.engine.DEFAULT_CONFIG.  followup_days defaults to
     null = "use the per-status STALL_DAYS table"; a number overrides all of
     them.  parse-int only applies to keys that already hold a number, so
     null stays null rather than becoming NaN. */
  function config(cfg) {
    var merged = {
      weekly_target: 5, followup_days: null, second_followup_days: 14,
      wishlist_idle_days: 10, max_followups: 2
    };
    Object.keys(cfg || {}).forEach(function (k) {
      if (merged[k] !== undefined && cfg[k] !== null && cfg[k] !== undefined) {
        var n = parseInt(cfg[k], 10);
        if (!isNaN(n)) { merged[k] = n; }
      }
    });
    return merged;
  }

  function makeAction(rule, severity, app, summary, action, dueOn, sc, details) {
    return {
      rule: rule, severity: severity,
      app_id: app ? app.id : null,
      label: app ? label(app) : 'Weekly target',
      summary: summary, action: action, due_on: dueOn || null,
      score: sc.total, terms: sc.terms, details: details || {}
    };
  }

  function explainText(a) {
    var d = a.details || {};
    if (a.rule === 'interview_prep' && d.days_away !== undefined) {
      return a.label + ': ' + d.kind + 'interview in ' + d.days_away +
        ' day(s) \u2014 prepare the stories and questions.';
    }
    if (a.rule === 'no_response_overdue' || a.rule === 'second_followup' ||
        a.rule === 'stalled') {
      return a.label + ': ' + d.days_since_activity + ' days since the last activity in ' +
        d.status_label + ' (threshold ' + d.threshold + ' days).';
    }
    if (a.rule === 'overdue_next_action') {
      return a.label + ": next action '" + d.next_action + "' was due " + a.due_on +
        ' \u2014 ' + d.overdue_days + ' day(s) ago.';
    }
    if (a.rule === 'deadline_soon' || a.rule === 'deadline_today') {
      return a.label + ': application deadline is ' + a.due_on + '.';
    }
    return a.summary;
  }

  function actions(apps, today, cfgIn) {
    var cfg = config(cfgIn);
    var found = [];
    var todayMs = parseDate(today).getTime();

    apps.forEach(function (app) {
      /* -- deadlines (wishlist only) -- */
      if (app.deadline_on && PRE.indexOf(app.status) >= 0) {
        var daysAway = Math.round((parseDate(app.deadline_on).getTime() - todayMs) / DAY_MS);
        if (daysAway >= 0) {
          if (daysAway === 0) {
            var sc0 = score('critical', app, { imminent: true });
            found.push(makeAction('deadline_today', 'critical', app,
              'Submit today \u2014 the deadline is ' + app.deadline_on + '.',
              'Submit the application for ' + label(app) + ' today.',
              app.deadline_on, sc0, { days_away: 0, url: app.url || '' }));
          } else if (daysAway <= 3) {
            var sev = daysAway <= 1 ? 'high' : 'medium';
            var scA = score(sev, app, { overdue_days: Math.max(0, 3 - daysAway) });
            found.push(makeAction('deadline_soon', sev, app,
              'Deadline in ' + daysAway + ' day(s) (' + app.deadline_on + ')',
              'Finish and submit the application for ' + label(app) + '.',
              app.deadline_on, scA, { days_away: daysAway, url: app.url || '' }));
          }
        }
      }

      /* -- interviews -- */
      upcomingInterviews(app, today).forEach(function (iv) {
        var away = Math.round((parseDate(iv.on).getTime() - todayMs) / DAY_MS);
        if (away > 7) { return; }
        var pretty = String(iv.kind).replace(/_/g, ' ');
        var rule, sev, act;
        if (away === 0) {
          rule = 'interview_today'; sev = 'critical';
          act = 'Interview TODAY: ' + pretty + ' with ' + app.company +
            '. Join early and have questions ready.';
        } else if (away <= 1) {
          rule = 'interview_soon'; sev = 'high';
          act = 'Final prep for the ' + pretty + ' interview with ' + app.company + ' tomorrow.';
        } else {
          rule = 'interview_prep'; sev = 'medium';
          act = 'Prepare for the ' + pretty + ' interview with ' + app.company +
            ' in ' + away + ' days.';
        }
        var scI = score(sev, app, { imminent: away <= 1 });
        found.push(makeAction(rule, sev, app,
          pretty.charAt(0).toUpperCase() + pretty.slice(1) + ' interview on ' + iv.on +
          ' (' + away + ' day(s)).', act, iv.on, scI,
          { days_away: away, kind: pretty, interview_note: iv.note || '' }));
      });

      /* -- offers -- */
      if (app.status === 'offer') {
        var entered = stageEnteredOn(app);
        var daysOpen = entered ? Math.round((todayMs - parseDate(entered).getTime()) / DAY_MS) : 0;
        if (daysOpen >= 5) {
          var scO = score('critical', app, { overdue_days: daysOpen - 5 + 1 });
          found.push(makeAction('offer_expiring', 'critical', app,
            'Offer outstanding for ' + daysOpen + ' days.',
            'Decide on ' + app.company + ' \u2014 respond to the offer, ask about the ' +
            'deadline, or negotiate. Silence reads as disinterest.',
            null, scO,
            { days_open: daysOpen, salary: formatMoney(app.salary_max, app.currency) }));
        } else {
          var scOw = score('high', app, {});
          found.push(makeAction('offer_waiting', 'high', app,
            'Offer received ' + daysOpen + ' day(s) ago.',
            'Reply to ' + app.company +
            ': acknowledge the offer and ask for the decision deadline.',
            null, scOw, { days_open: daysOpen }));
        }
      }

      /* -- own commitments -- */
      if (app.next_action_on && app.next_action) {
        var overdue = Math.round((todayMs - parseDate(app.next_action_on).getTime()) / DAY_MS);
        if (overdue >= 1) {
          var sevN = overdue >= 7 ? 'critical' : overdue >= 3 ? 'high' : 'medium';
          var scN = score(sevN, app, { overdue_days: overdue });
          found.push(makeAction('overdue_next_action', sevN, app,
            "'" + app.next_action + "' was due " + app.next_action_on +
            ' (' + overdue + ' day(s) overdue).',
            'Do it now: ' + app.next_action + ' (' + label(app) + ').',
            app.next_action_on, scN,
            { overdue_days: overdue, next_action: app.next_action }));
        }
      }

      /* -- silence (offers excluded: the offer rules own them) -- */
      if (isActive(app) && wasApplied(app) && app.status !== 'offer') {
        /* An explicit followup_days overrides every per-status threshold at
           once; unset means "use the STALL_DAYS table".  Kept identical to
           jobtracker/followups.py — previously this key was accepted and
           then ignored, so the documented knob did nothing here. */
        var threshold = cfg.followup_days
          ? cfg.followup_days
          : (STALL_DAYS[app.status] !== undefined
             ? STALL_DAYS[app.status] : DEFAULT_STALL_DAYS);
        var quiet = daysSinceActivity(app, today);
        if (quiet >= threshold) {
          var count = followupCount(app);
          var rule2, sev2, act2;
          if (count === 0) {
            rule2 = 'no_response_overdue';
            sev2 = quiet >= threshold * 2 ? 'high' : 'medium';
            act2 = 'Send a short follow-up to ' + app.company +
              ' \u2014 reference the role and restate your interest.';
          } else if (count < cfg.max_followups) {
            rule2 = 'second_followup'; sev2 = 'medium';
            act2 = 'Send one final follow-up to ' + app.company +
              ', then stop and move on.';
          } else {
            rule2 = 'stalled'; sev2 = 'low';
            act2 = 'Stop chasing ' + app.company + ' \u2014 you have followed up ' + count +
              ' time(s). Mark it rejected or withdrawn and put the effort into new applications.';
          }
          var scS = score(sev2, app, {
            stale_days: quiet - threshold + 1,
            overdue_days: quiet - threshold
          });
          found.push(makeAction(rule2, sev2, app,
            quiet + ' days quiet in ' + STATUS_LABELS[app.status] +
            ' (' + count + ' follow-up(s) sent).', act2,
            iso(new Date(todayMs - (quiet - threshold) * DAY_MS)), scS,
            { days_since_activity: quiet, status_label: STATUS_LABELS[app.status],
              threshold: threshold, followups_sent: count, contact: app.contact || '' }));
        }
      }

      /* -- thank-you -- */
      if (isActive(app)) {
        var done = (app.interviews || []).filter(function (i) { return i.done; });
        if (done.length) {
          done.sort(function (a, b) { return a.on < b.on ? -1 : a.on > b.on ? 1 : 0; });
          var latest = done[done.length - 1];
          var since = Math.round((todayMs - parseDate(latest.on).getTime()) / DAY_MS);
          var already = (app.events || []).some(function (e) {
            return e.kind === 'followup' && e.on >= latest.on;
          });
          if (since >= 1 && since <= 3 && !already) {
            var scT = score('high', app, { overdue_days: since });
            var prettyT = String(latest.kind).replace(/_/g, ' ');
            found.push(makeAction('close_the_loop', 'high', app,
              prettyT.charAt(0).toUpperCase() + prettyT.slice(1) + ' interview ' + since +
              ' day(s) ago \u2014 no thank-you logged.',
              'Send a thank-you note to ' + (app.contact || 'your interviewer') + ' at ' +
              app.company + ' and restate one concrete reason you fit.',
              latest.on, scT,
              { days_since_interview: since, kind: latest.kind, contact: app.contact || '' }));
          }
        }
      }

      /* -- rejection learning -- */
      if (app.status === 'rejected' && reachedIndex(app) >= STAGE_INDEX.interview &&
          app.closed_on) {
        var dSince = Math.round((todayMs - parseDate(app.closed_on).getTime()) / DAY_MS);
        var hasPostMortem = (app.notes || []).some(function (n) {
          return String(n.text || '').toLowerCase().indexOf('post-mortem') === 0;
        });
        if (dSince >= 0 && dSince <= 10 && !hasPostMortem) {
          var stage = furthestStage(app) || 'applied';
          var scR = score('medium', app, { overdue_days: dSince });
          found.push(makeAction('rejection_review', 'medium', app,
            'Rejected at the ' + stage + ' rung ' + dSince + ' day(s) ago.',
            'Write a post-mortem note for ' + label(app) +
            ': what was asked, where it went thin, and the one thing to drill before the next loop.',
            null, scR, { reached_stage: stage, days_since: dSince }));
        }
      }

      /* -- wishlist rot -- */
      if (PRE.indexOf(app.status) >= 0 && cfg.wishlist_idle_days > 0) {
        var last = lastActivityOn(app);
        var idle = last ? Math.round((todayMs - parseDate(last).getTime()) / DAY_MS) : 0;
        if (idle >= cfg.wishlist_idle_days) {
          var sevW = idle < cfg.wishlist_idle_days * 3 ? 'low' : 'medium';
          var scW = score(sevW, app, { stale_days: idle - cfg.wishlist_idle_days + 1 });
          found.push(makeAction('wishlist_nudge', sevW, app,
            'On the wishlist ' + idle + ' day(s) without progress.',
            'Either apply to ' + app.role + ' at ' + app.company +
            ' or drop it off the list.', last, scW, { idle_days: idle }));
        }
      }
    });

    /* -- pipeline-level weekly target -- */
    var target = cfg.weekly_target;
    if (target > 0) {
      var weekStart = mondayOf(today);
      var sent = apps.filter(function (a) {
        return a.applied_on && a.applied_on >= iso(weekStart);
      }).length;
      var left = 7 - ((parseDate(today).getUTCDay() + 6) % 7);
      if (target - sent > 0) {
        var sevT = left <= 1 ? 'high' : 'medium';
        var scT2 = score(sevT, null, { overdue_days: target - sent });
        scT2.terms.shortfall = (target - sent) * 5;
        scT2.total += scT2.terms.shortfall;
        found.push({
          rule: 'weekly_target', severity: sevT, app_id: null, label: 'Weekly target',
          summary: sent + '/' + target + ' applications sent this week with ' +
            left + ' day(s) left.',
          action: 'Send ' + (target - sent) + ' more application(s) before the week ends.',
          due_on: iso(new Date(weekStart.getTime() + 6 * DAY_MS)),
          score: scT2.total, terms: scT2.terms,
          details: { sent: sent, target: target, shortfall: target - sent }
        });
      }
    }

    /* -- momentum -- */
    var received = apps.filter(function (a) {
      if (a.status !== 'offer' && a.status !== 'accepted') { return false; }
      var e = stageEnteredOn(a);
      if (!e) { return false; }
      return Math.round((todayMs - parseDate(e).getTime()) / DAY_MS) <= 7;
    });
    if (received.length) {
      var scM = score('low', null, {});
      found.push({
        rule: 'log_wins', severity: 'low', app_id: null, label: 'Momentum',
        summary: received.length + ' offer/accepted in the last 7 days.',
        action: 'Record the outcome for ' + received.slice(0, 3).map(function (a) {
          return a.company;
        }).join(', ') + ' and note what worked while it is fresh.',
        due_on: null, score: scM.total, terms: scM.terms,
        details: { companies: received.map(function (a) { return a.company; }) }
      });
    }

    var rank = {};
    RULE_ORDER.forEach(function (r, i) { rank[r] = i; });
    found.sort(function (a, b) {
      if (b.score !== a.score) { return b.score - a.score; }
      var ra = rank[a.rule] === undefined ? 99 : rank[a.rule];
      var rb = rank[b.rule] === undefined ? 99 : rank[b.rule];
      if (ra !== rb) { return ra - rb; }
      return (a.app_id || '') < (b.app_id || '') ? -1 : (a.app_id || '') > (b.app_id || '') ? 1 : 0;
    });
    return found;
  }

  function plan(apps, today, limit, cfgIn) {
    var cfg = config(cfgIn);
    var all = actions(apps, today, cfgIn);
    var bySeverity = { critical: 0, high: 0, medium: 0, low: 0 };
    all.forEach(function (a) { bySeverity[a.severity] = (bySeverity[a.severity] || 0) + 1; });

    var top = all.slice(0, Math.max(0, limit === undefined ? 10 : limit));
    return {
      today: today,
      total: all.length,
      by_severity: bySeverity,
      config: cfg,
      actions: top.map(function (a) {
        var copy = {};
        Object.keys(a).forEach(function (k) { copy[k] = a[k]; });
        copy.title = RULE_LABELS[a.rule] || a.rule;
        copy.explain = explainText(a);
        return copy;
      }),
      headline: headline(all)
    };
  }

  function headline(all) {
    if (!all.length) {
      return 'Nothing is burning. Send another application or take the evening off.';
    }
    var critical = all.filter(function (a) { return a.severity === 'critical'; });
    if (critical.length) {
      return critical.length + ' urgent item(s). Start with: ' + critical[0].action;
    }
    var high = all.filter(function (a) { return a.severity === 'high'; });
    if (high.length) {
      return high.length + ' important item(s). Next up: ' + high[0].action;
    }
    return all.length + ' thing(s) worth doing today. Start with: ' + all[0].action;
  }

  /* -------------------------------------------------------------- summary */

  function summary(apps, today) {
    var fun = funnel(apps, today);
    return {
      today: today,
      totals: {
        applications: apps.length,
        submitted: fun.submitted,
        open: fun.open,
        wishlist: fun.wishlist,
        closed: fun.closed
      },
      funnel: fun,
      aging: agingRows(apps, today),
      stalled: stalled(apps, today),
      time_in_stage: timeInStage(apps, today),
      sources: sourceBreakdown(apps, today),
      salary: salarySummary(apps),
      weekly: weeklyActivity(apps, today, 8),
      streak: streak(apps, today),
      status_counts: statusCounts(apps)
    };
  }

  global.JobFlowEngine = {
    /* vocabulary */
    STAGES: STAGES, ACTIVE: ACTIVE, CLOSED: CLOSED, PRE: PRE,
    STATUS_LABELS: STATUS_LABELS, SOURCE_LABELS: SOURCE_LABELS,
    STAGE_INDEX: STAGE_INDEX, STAGE_MATURITY_DAYS: STAGE_MATURITY_DAYS,
    STALL_DAYS: STALL_DAYS, DEFAULT_STALL_DAYS: DEFAULT_STALL_DAYS,
    RULE_ORDER: RULE_ORDER, RULE_LABELS: RULE_LABELS, SEVERITIES: SEVERITIES,
    /* primitives */
    parseDate: parseDate, iso: iso, diffDays: diffDays,
    formatMoney: formatMoney, formatRange: formatRange,
    /* derived */
    wasApplied: wasApplied, furthestStage: furthestStage, reachedIndex: reachedIndex,
    hasResponse: hasResponse, lastActivityOn: lastActivityOn,
    daysSinceActivity: daysSinceActivity, stageEnteredOn: stageEnteredOn,
    daysInStage: daysInStage, ageDays: ageDays, followupCount: followupCount,
    upcomingInterviews: upcomingInterviews, label: label,
    isActive: isActive, isClosed: isClosed,
    /* analytics */
    funnelRows: funnelRows, funnel: funnel, agingRows: agingRows, stalled: stalled,
    timeInStage: timeInStage, sourceBreakdown: sourceBreakdown,
    salarySummary: salarySummary, weeklyActivity: weeklyActivity, streak: streak,
    statusCounts: statusCounts, summary: summary, median: median,
    /* follow-ups */
    actions: actions, plan: plan, explainText: explainText, headline: headline,
    config: config
  };
}(typeof window !== 'undefined' ? window : globalThis));
