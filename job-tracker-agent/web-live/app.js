/* JOBFLOW demo HUD — rendering.
 *
 * Pure rendering over window.JobFlowEngine and the baked-in
 * window.JOBFLOW_DEMO dataset.  No fetch, no XHR, no timers: the page is
 * fully static and works from file:// as well as GitHub Pages.
 */
(function () {
  'use strict';

  var E = window.JobFlowEngine;
  var DEMO = window.JOBFLOW_DEMO;

  if (!E || !DEMO) {
    document.body.insertAdjacentHTML('afterbegin',
      '<p style="padding:20px;color:#f85149">Engine or demo data failed to load.</p>');
    return;
  }

  var TODAY = DEMO.today;
  var APPS = DEMO.applications || [];

  var STATUS_MARK = {
    wishlist: '\u00b7', applied: '\u2192', screen: '\u25d4', interview: '\u25d1',
    onsite: '\u25d5', offer: '\u2605', accepted: '\u2714',
    rejected: '\u2718', withdrawn: '\u2298'
  };

  var SEV_ORDER = ['critical', 'high', 'medium', 'low'];

  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function h(html) { return html; }

  function el(id) { return document.getElementById(id); }

  function pct(value) {
    return (Math.round(value * 10) / 10).toFixed(1) + '%';
  }

  /* ------------------------------------------------------------ topbar */

  function renderTopbar() {
    el('asof-pill').textContent = 'as of ' + TODAY;
    el('vault-pill').textContent = APPS.length + ' application' +
      (APPS.length === 1 ? '' : 's');
  }

  /* ------------------------------------------------------------- stats */

  function renderStats(sum) {
    var f = sum.funnel;
    var cards = [
      {
        label: 'Applications',
        value: String(sum.totals.submitted),
        sub: sum.totals.wishlist + ' still on the wishlist'
      },
      {
        label: 'Open',
        value: String(sum.totals.open),
        sub: sum.stalled.length + ' going quiet',
        tone: sum.stalled.length ? 'warn' : ''
      },
      {
        label: 'Response rate',
        value: pct(f.response_rate),
        sub: f.responded + ' of ' + f.response_denominator + ' mature',
        tone: f.response_rate >= 30 ? 'good' : f.response_rate >= 10 ? 'warn' : 'bad'
      },
      {
        label: 'Interviews',
        value: String(f.interviewed),
        sub: pct(f.interview_rate) + ' of submitted',
        tone: f.interviewed ? 'accent' : ''
      },
      {
        label: 'Offers',
        value: String(f.offered),
        sub: f.offered ? pct(f.offer_rate) + ' of submitted' : 'none yet',
        tone: f.offered ? 'good' : ''
      },
      {
        label: 'Closed',
        value: String(sum.totals.closed),
        sub: sum.status_counts.rejected + ' rejected \u00b7 ' +
             sum.status_counts.withdrawn + ' withdrawn'
      }
    ];

    el('stat-grid').innerHTML = cards.map(function (c) {
      return h('<div class="stat ' + (c.tone ? 'is-' + c.tone : '') + '">' +
        '<div class="stat-label">' + esc(c.label) + '</div>' +
        '<div class="stat-value">' + esc(c.value) + '</div>' +
        '<div class="stat-sub">' + esc(c.sub) + '</div>' +
        '</div>');
    }).join('');
  }

  /* ------------------------------------------------------------ funnel */

  function renderFunnel(sum) {
    var rows = sum.funnel.rows;
    var top = rows.reduce(function (max, r) {
      return Math.max(max, r.reached);
    }, 0) || 1;

    el('funnel').innerHTML = rows.map(function (r) {
      var width = Math.round((r.reached / top) * 100);
      return h('<div class="funnel-row">' +
        '<div class="funnel-name">' + esc(r.label) + '</div>' +
        '<div class="funnel-track">' +
          '<div class="funnel-fill' + (r.reached ? '' : ' is-zero') +
            '" style="width:' + width + '%"></div>' +
        '</div>' +
        '<div class="funnel-metrics"><b>' + r.reached + '</b> reached \u00b7 ' +
          pct(r.step_rate) + ' step</div>' +
        '</div>');
    }).join('');

    var rejectedAfterInterview = APPS.filter(function (a) {
      return a.status === 'rejected' && E.reachedIndex(a) >= E.STAGE_INDEX.interview;
    });
    var note = 'Cumulative rates use a maturity window per rung (' +
      rows.map(function (r) { return r.label + ' ' + r.maturity_days + 'd'; }).join(', ') +
      '), so a fresh application is never counted as a failure.';
    if (rejectedAfterInterview.length) {
      note += ' ' + rejectedAfterInterview.length +
        ' rejection(s) are counted at their furthest rung, not at "applied": ' +
        rejectedAfterInterview.map(function (a) {
          return a.company + ' (reached ' + E.furthestStage(a) + ')';
        }).join(', ') + '.';
    }
    el('funnel-note').textContent = note;
  }

  /* ---------------------------------------------------------- response */

  function renderResponse(sum) {
    var f = sum.funnel;
    var target = 100;
    var width = Math.max(0, Math.min(100, f.response_rate));
    el('response-block').innerHTML = h(
      '<div class="big-rate">' +
        '<span class="value">' + pct(f.response_rate) + '</span>' +
        '<span class="of">' + f.responded + ' responses from ' +
          f.response_denominator + ' mature applications</span>' +
      '</div>' +
      '<div class="rate-bar"><span style="width:' + width + '%"></span></div>' +
      '<div class="stat-sub">' +
        'A rejection counts as a response \u2014 the employer replied. Only silence is silence. ' +
        'Applications younger than ' + E.STAGE_MATURITY_DAYS.applied +
        ' days are excluded from the denominator, which is why this reads ' +
        f.responded + '/' + f.response_denominator + ' and not ' +
        f.responded + '/' + f.submitted + '.' +
      '</div>' +
      '<table class="data" style="margin-top:14px">' +
        '<thead><tr><th>Screened</th><th>Interviewed</th><th>Onsite</th><th>Offered</th></tr></thead>' +
        '<tbody><tr>' +
          '<td>' + f.screened + '</td>' +
          '<td>' + f.interviewed + '</td>' +
          '<td>' + f.rows[3].reached + '</td>' +
          '<td>' + f.offered + '</td>' +
        '</tr></tbody>' +
      '</table>');
    void target;
  }

  /* ------------------------------------------------------------ weekly */

  function renderWeekly(sum) {
    var rows = sum.weekly.slice(-6);
    var peak = rows.reduce(function (m, r) {
      return Math.max(m, r.applied, r.interviews);
    }, 1);

    el('weekly').innerHTML = rows.map(function (r) {
      var aw = Math.round((r.applied / peak) * 100);
      var iw = Math.round((r.interviews / peak) * 100);
      return h('<div class="week-row">' +
        '<div class="week-label">' + esc(r.iso_week) + '</div>' +
        '<div class="week-bars">' +
          '<div class="week-bar applied"><span style="width:' + aw + '%"></span></div>' +
          '<div class="week-bar interviews"><span style="width:' + iw + '%"></span></div>' +
        '</div>' +
        '<div class="week-counts">' + r.applied + ' app \u00b7 ' + r.interviews + ' int</div>' +
        '</div>');
    }).join('');

    el('streak-note').textContent =
      'Current streak: ' + sum.streak.current_weeks + ' week(s) with at least one application \u00b7 ' +
      'best: ' + sum.streak.longest_weeks + ' week(s). Blue bars are applications, green are interviews.';
  }

  /* -------------------------------------------------------------- plan */

  function renderPlan(sum) {
    var plan = E.plan(APPS, TODAY, 25);

    el('plan-headline').textContent = plan.headline;

    el('severity-row').innerHTML = SEV_ORDER.map(function (sev) {
      var count = plan.by_severity[sev] || 0;
      if (!count) { return ''; }
      return h('<span class="sev-chip ' + sev + '">' + count + ' ' + sev + '</span>');
    }).join('');

    if (!plan.actions.length) {
      el('plan-list').innerHTML = h('<li class="empty">' + esc(plan.headline) + '</li>');
      return;
    }

    el('plan-list').innerHTML = plan.actions.map(function (a, i) {
      var terms = Object.keys(a.terms).map(function (k) {
        return k + ' ' + a.terms[k];
      }).join(' \u00b7 ');

      return h('<li class="plan-item">' +
        '<div class="plan-rank">' + (i + 1) + '</div>' +
        '<div>' +
          '<span class="plan-rule ' + esc(a.severity) + '">' + esc(a.title) + '</span>' +
          '<div class="plan-title">' + esc(a.label) + '</div>' +
          '<div class="plan-action">' + esc(a.action) + '</div>' +
          '<div class="plan-why">why: ' + esc(a.explain) + '</div>' +
          '<div class="plan-score">score ' + a.score + ' \u00b7 ' + esc(terms) + '</div>' +
        '</div>' +
        '</li>');
    }).join('');

    void sum;
  }

  /* ------------------------------------------------------------- board */

  function renderBoard() {
    var order = ['wishlist', 'applied', 'screen', 'interview', 'onsite', 'offer',
                 'accepted', 'rejected', 'withdrawn'];
    var groups = {};

    APPS.forEach(function (a) {
      (groups[a.status] = groups[a.status] || []).push(a);
    });

    var html = order.filter(function (s) { return groups[s] && groups[s].length; })
      .map(function (status) {
        var items = groups[status].slice().sort(function (a, b) {
          return (b.priority - a.priority) ||
            (E.daysSinceActivity(a, TODAY) - E.daysSinceActivity(b, TODAY)) ||
            (a.id < b.id ? -1 : 1);
        });

        return h('<div class="board-group">' +
          '<h3>' + esc(STATUS_MARK[status] + ' ' + E.STATUS_LABELS[status]) +
            '<span class="board-count">' + items.length + '</span></h3>' +
          items.map(function (a) { return boardItem(a); }).join('') +
          '</div>');
      }).join('');

    el('board').innerHTML = html || h('<p class="empty">No applications in the vault.</p>');
  }

  function boardItem(a) {
    var quiet = E.daysSinceActivity(a, TODAY);
    var threshold = E.STALL_DAYS[a.status] !== undefined
      ? E.STALL_DAYS[a.status] : E.DEFAULT_STALL_DAYS;
    var isStalled = E.isActive(a) && quiet >= threshold;

    var classes = ['board-item', 'status-' + a.status];
    if (isStalled) { classes.push('is-stalled'); }
    if (E.isClosed(a)) { classes.push('is-closed'); }

    var salary = E.formatRange(a.salary_min, a.salary_max, a.currency, true);
    var meta = [];
    if (E.wasApplied(a)) { meta.push(E.ageDays(a, TODAY) + 'd old'); }
    meta.push(E.daysInStage(a, TODAY) + 'd in stage');
    if (E.isActive(a)) { meta.push('quiet ' + quiet + 'd'); }

    var tags = [];
    if (salary !== '\u2014') { tags.push(salary); }
    tags.push(E.SOURCE_LABELS[a.source] || a.source);
    if (a.location) { tags.push(a.location); }
    if (a.work_mode && a.work_mode !== 'unspecified') { tags.push(a.work_mode); }
    if (a.followupCount && E.followupCount(a)) {
      tags.push(E.followupCount(a) + ' follow-up(s)');
    }
    if (a.deadline_on) { tags.push('deadline ' + a.deadline_on); }

    return h('<div class="' + classes.join(' ') + '">' +
      '<div>' +
        '<div class="board-title">' + esc(a.role) + ' ' +
          '<span class="board-company">\u00b7 ' + esc(a.company) + '</span>' +
          (isStalled ? ' <span class="stall-flag" title="past its stall threshold">\u26a0</span>' : '') +
        '</div>' +
        '<div class="board-sub">' +
          tags.map(function (t) { return '<span class="board-tag">' + esc(t) + '</span>'; }).join('') +
        '</div>' +
        (a.next_action
          ? '<div class="board-sub">next: ' + esc(a.next_action) +
            (a.next_action_on ? ' (due ' + esc(a.next_action_on) + ')' : '') + '</div>'
          : '') +
      '</div>' +
      '<div class="board-meta">' + esc(meta.join(' \u00b7 ')) + '</div>' +
      '</div>');
  }

  /* ---------------------------------------------------------- insights */

  function renderSources(sum) {
    var rows = sum.sources;
    if (!rows.length) {
      el('sources').innerHTML = h('<p class="empty">No submitted applications yet.</p>');
      return;
    }
    el('sources').innerHTML = rows.map(function (r) {
      var width = Math.round(Math.min(100, r.interview_rate));
      var tone = r.interview_rate >= 40 ? 'is-good' : (r.interview_rate ? '' : 'is-zero');
      return h('<div class="src-row">' +
        '<div class="src-name">' + esc(r.label) + '</div>' +
        '<div class="src-track"><span class="' + tone + '" style="width:' + width + '%"></span></div>' +
        '<div class="src-stats">' + r.applied + ' applied \u00b7 ' + r.interviewed +
          ' int \u00b7 ' + pct(r.interview_rate) + '</div>' +
        '</div>');
    }).join('');
  }

  function renderSalary(sum) {
    var salary = sum.salary;
    var keys = Object.keys(salary.currencies).sort();
    if (!keys.length) {
      el('salary').innerHTML = h('<p class="empty">No salary data recorded.</p>');
      return;
    }
    el('salary').innerHTML = keys.map(function (cur) {
      var row = salary.currencies[cur];
      return h('<div class="salary-row">' +
        '<span class="salary-cur">' + esc(cur) + ' \u00b7 ' + row.count + ' role(s)</span>' +
        '<span class="salary-val">' + esc(row.min_display) + '\u2013' + esc(row.max_display) +
          '</span>' +
        '</div>' +
        '<div class="stat-sub" style="padding-bottom:8px">median ' +
          esc(row.median_display) + '</div>');
    }).join('');
  }

  function renderTimeInStage(sum) {
    var tis = sum.time_in_stage;
    var stages = Object.keys(tis).filter(function (s) { return tis[s].samples > 0; });
    if (!stages.length) {
      el('time-in-stage').innerHTML = h(
        '<p class="empty">No completed stage stretches yet \u2014 nothing has moved rung.</p>');
      return;
    }
    el('time-in-stage').innerHTML = h(
      '<table class="data"><thead><tr>' +
        '<th>Stage</th><th>Median</th><th>Min</th><th>Max</th><th>Samples</th>' +
      '</tr></thead><tbody>' +
      stages.map(function (s) {
        var r = tis[s];
        return '<tr><td>' + esc(r.label) + '</td>' +
          '<td class="num">' + r.median_days + 'd</td>' +
          '<td class="num">' + r.min_days + 'd</td>' +
          '<td class="num">' + r.max_days + 'd</td>' +
          '<td class="num">' + r.samples + '</td></tr>';
      }).join('') +
      '</tbody></table>');
  }

  function renderStalled(sum) {
    var rows = sum.stalled;
    if (!rows.length) {
      el('stalled').innerHTML = h('<p class="empty">Nothing is going quiet. \ud83c\udf89</p>');
      return;
    }
    el('stalled').innerHTML = h(
      '<table class="data"><thead><tr>' +
        '<th>Application</th><th>Stage</th><th>Quiet</th><th>Threshold</th>' +
      '</tr></thead><tbody>' +
      rows.map(function (r) {
        return '<tr><td>' + esc(r.label) + '</td>' +
          '<td>' + esc(r.status_label) + '</td>' +
          '<td class="num">' + r.days_since_activity + 'd</td>' +
          '<td class="num">' + r.stall_threshold + 'd</td></tr>';
      }).join('') +
      '</tbody></table>');
  }

  /* --------------------------------------------------------------- tabs */

  function wireTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        var name = tab.getAttribute('data-tab');
        tabs.forEach(function (t) {
          var active = t === tab;
          t.classList.toggle('is-active', active);
          t.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        Array.prototype.forEach.call(document.querySelectorAll('.view'), function (view) {
          view.classList.toggle('is-active', view.id === 'view-' + name);
        });
      });
    });
  }

  /* --------------------------------------------------------------- boot */

  function render() {
    var sum = E.summary(APPS, TODAY);
    renderTopbar();
    renderStats(sum);
    renderFunnel(sum);
    renderResponse(sum);
    renderWeekly(sum);
    renderPlan(sum);
    renderBoard();
    renderSources(sum);
    renderSalary(sum);
    renderTimeInStage(sum);
    renderStalled(sum);
  }

  wireTabs();
  render();

  // Expose the computed view for the smoke check / console users.
  window.JOBFLOW_VIEW = {
    today: TODAY,
    applications: APPS.length,
    summary: E.summary(APPS, TODAY),
    plan: E.plan(APPS, TODAY, 25)
  };
}());
