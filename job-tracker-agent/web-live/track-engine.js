  /* ---------------------------------------------------- follow-up engine  */

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
      return a.label + ': ' + d.kind + ' interview in ' + d.days_away +
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
              'Deadline in ' + daysAway + ' day(s) (' + app.deadline_on + ').',
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
           jobtracker/followups.py \u2014 previously this key was accepted and
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
