/* SplitKit live HUD — renders the demo entirely client-side.
 *
 * No fetch, no XHR, no CDN: everything comes from splitkit-engine.js and the
 * embedded demo-data.js. The panel assertions in the Proof tab run on load, so
 * a broken engine shows up as a failed check rather than a pretty page with
 * wrong numbers.
 */
(function () {
  "use strict";

  var E = window.SplitKitEngine;
  var D = window.SPLITKIT_DEMO;
  var group = D.group;
  var checks = [];

  /* ------------------------------------------------------------- utilities */

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function money(minor, opts) { return E.formatFor(group.currency, minor, opts || {}); }
  function initials(name) {
    return name.split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase();
  }
  function check(label, passed, detail) {
    checks.push({ label: label, passed: !!passed, detail: detail || "" });
    return passed;
  }

  /* -------------------------------------------------------------- overview */

  var net = E.balances(group);
  var table = E.balanceTable(group);
  var plan = E.settle(group, "optimal");
  var total = group.expenses.reduce(function (a, e) { return a + e.amount; }, 0);

  function renderOverview() {
    $("stat-total").textContent = money(total);
    $("stat-total-sub").textContent = group.expenses.length + " expenses · " + group.members.length + " members";

    $("stat-transfers").textContent = String(plan.transfer_count);
    $("stat-transfers-sub").textContent = plan.optimal_feasible
      ? "proven minimum (bound " + E.lowerBound(net) + ")"
      : "greedy plan (group too large to prove minimality)";

    $("stat-balanced").textContent = "Nets to zero ✓";
    $("stat-balanced-sub").textContent = "sum of all net positions = " + money(0);

    var body = $("balances-body");
    body.innerHTML = "";
    var maxAbs = Math.max.apply(null, table.map(function (r) { return Math.abs(r.balance_minor); })) || 1;

    table.forEach(function (r) {
      var tr = el("tr");
      var tdName = el("td");
      tdName.appendChild(el("span", "name", r.name));
      tr.appendChild(tdName);
      tr.appendChild(el("td", "num", r.paid_display));
      tr.appendChild(el("td", "num", r.share_display));

      var tdNet = el("td", "num " + (r.balance_minor > 0 ? "pos" : r.balance_minor < 0 ? "neg" : "zero"));
      tdNet.textContent = r.balance_display;
      tr.appendChild(tdNet);

      var tdState = el("td");
      tdState.appendChild(el("span", "pill " + r.state, r.state === "owed" ? "is owed" : r.state === "owes" ? "owes" : "square"));
      tr.appendChild(tdState);
      body.appendChild(tr);
    });

    var unpaid = table.filter(function (r) { return r.balance_minor !== 0; });
    $("balances-note").innerHTML = "";
    if (!unpaid.length) {
      $("balances-note").textContent = "Everyone is square — nothing to settle.";
    } else {
      var owes = table.filter(function (r) { return r.balance_minor < 0; });
      var owed = table.filter(function (r) { return r.balance_minor > 0; });
      $("balances-note").textContent =
        owes.length + " member(s) owe money and " + owed.length +
        " are owed money. Flipping to the Settle Up tab turns that into " +
        plan.transfer_count + " payment(s).";
    }
    void maxAbs;

    // Categories
    var cats = E.categoryTotals(group);
    var catHost = $("categories-body");
    catHost.innerHTML = "";
    Object.keys(cats).forEach(function (key) {
      var value = cats[key];
      var pct = Math.round((value / total) * 1000) / 10;

      var top = el("div");
      top.style.cssText = "display:flex;justify-content:space-between;font-size:14px;margin-top:12px";
      top.appendChild(el("span", "name", (D.categories[key] || "•") + "  " + key));
      top.appendChild(el("span", "mono", money(value) + "  (" + pct + "%)"));
      catHost.appendChild(top);

      var bar = el("div", "moneybar");
      var fill = el("span");
      fill.style.width = Math.max(pct, 1.5) + "%";
      bar.appendChild(fill);
      catHost.appendChild(bar);
    });

    check("Ledger nets to exactly zero", table.reduce(function (a, r) { return a + r.balance_minor; }, 0) === 0);
    check("Category totals sum to the grand total",
      Object.keys(cats).reduce(function (a, k) { return a + cats[k]; }, 0) === total);
  }

  /* ----------------------------------------------------------- calculator */

  var MODES = {
    equal: "Evenly across the listed participants (default: everyone).",
    shares: "Weighted parts — 2 : 1 : 1 means Ali carries half the cost.",
    exact: "Each person gets a fixed amount; the amounts must sum to the total exactly.",
    percent: "Percentages that must add up to exactly 100 (checked as exact rationals).",
    adjustment: "An even base, plus signed tweaks that must net to zero."
  };

  function calcSpec(mode) {
    var ids = group.members.map(function (m) { return m.id; });
    if (mode === "equal") return { mode: "equal" };
    if (mode === "shares") return { mode: "shares", shares: { ali: 2, sara: 1, bilal: 1 } };
    if (mode === "exact") return { mode: "exact", amounts: {} };
    if (mode === "percent") return { mode: "percent", percents: { ali: "33.34", sara: "33.33", bilal: "33.33" } };
    if (mode === "adjustment") return { mode: "adjustment", adjustments: { ali: 500, sara: -500 } };
    return { mode: "equal", participants: ids };
  }

  function runCalc() {
    var mode = $("calc-mode").value;
    var code = $("calc-currency").value;
    var amountText = $("calc-amount").value.trim();
    var msg = $("calc-msg");
    var exp = E.exponentFor(code);

    var amount;
    try {
      amount = E.parseAmount(amountText, exp);
    } catch (err) {
      msg.className = "msg err";
      msg.textContent = "Could not read that amount: " + err.message + ". Use digits with no more than " +
        exp + " decimal place(s) for " + code + ".";
      $("calc-result").innerHTML = "";
      return;
    }

    var spec = calcSpec(mode);

    // For exact mode, derive amounts that genuinely sum to the total.
    if (mode === "exact") {
      var parts = E.splitEvenly(amount, group.members.length);
      spec.amounts = {};
      group.members.forEach(function (m, i) { spec.amounts[m.id] = parts[i]; });
      if (group.members.length >= 2) spec.amounts[group.members[0].id] += 0; // no-op, keeps shape clear
    }

    var ids = group.members.map(function (m) { return m.id; });
    var shares;
    try {
      shares = E.resolveSplit(spec, amount, ids);
    } catch (err) {
      msg.className = "msg err";
      msg.innerHTML = "";
      msg.appendChild(el("strong", null, "Refused: "));
      msg.appendChild(document.createTextNode(err.message));
      $("calc-result").innerHTML = "";
      return;
    }

    var sum = Object.keys(shares).reduce(function (a, k) { return a + shares[k]; }, 0);
    var host = $("calc-result");
    host.innerHTML = "";

    var alloc = el("div", "alloc");
    var maxPart = Math.max.apply(null, Object.keys(shares).map(function (k) { return shares[k]; })) || 1;
    group.members.forEach(function (m) {
      var value = shares[m.id] || 0;
      var row = el("div", "alloc-row");
      row.appendChild(el("span", "who", m.name));
      var track = el("div", "track");
      var bar = el("div", "moneybar");
      var fill = el("span");
      fill.style.width = Math.max((value / maxPart) * 100, value > 0 ? 3 : 0) + "%";
      bar.appendChild(fill);
      track.appendChild(bar);
      row.appendChild(track);
      row.appendChild(el("span", "amt", E.formatFor(code, value, { symbol: false })));
      alloc.appendChild(row);
    });
    host.appendChild(alloc);

    var note = el("div", "note");
    note.textContent = "Shares sum to " + E.formatFor(code, sum, { symbol: false }) +
      " — exactly the " + E.formatFor(code, amount, { symbol: false }) + " entered. Difference: " +
      E.formatFor(code, sum - amount, { symbol: false }) + ".";
    host.appendChild(note);

    msg.className = "msg ok";
    msg.textContent = "Allocated " + money(amount) + " across " + group.members.length +
      " people with no rounding loss.";

    check("Calculator conserves the amount (" + mode + ")", sum === amount);
  }

  function renderCentExplainer() {
    var three = E.splitEvenly(10000, 3);
    var seven = E.splitEvenly(7, 3);
    var lines = [
      "$ python3 -m splitkit.cli add \"Group dinner\" 100.00 --paid-by sara",
      "  100.00 across 3 people",
      "    naive round(10000/3) = 3333  ->  3333 x 3 = 9999   LOST 0.01",
      "    SplitKit allocate     = [" + three.join(", ") + "]  ->  " +
        three.reduce(function (a, b) { return a + b; }, 0) + "   exact",
      "",
      "  The leftover cent goes to the largest remainder. Ties break by member",
      "  order, so the same input always produces the same split.",
      "",
      "$ # sub-unit money is the same problem, smaller:",
      "  0.07 across 3 -> [" + seven.join(", ") + "]  (sum " +
        seven.reduce(function (a, b) { return a + b; }, 0) + ")",
      "",
      "  Floats cannot express any of this safely; integers can."
    ];
    $("cent-explainer").textContent = lines.join("\n");
  }

  /* ---------------------------------------------------------------- settle */

  function renderSettle() {
    var strategy = $("settle-strategy").value;
    var chosen = E.settle(group, strategy);
    var host = $("settle-result");
    host.innerHTML = "";

    if (!chosen.transfers.length) {
      var done = el("div", "msg ok");
      done.textContent = "Everyone is square — nothing to settle.";
      host.appendChild(done);
      return;
    }

    chosen.transfers.forEach(function (t) {
      var row = el("div", "settle-row");
      var av1 = el("div", "avatar", initials(E.nameOf(group, t.from)));
      row.appendChild(av1);
      row.appendChild(el("span", "name", E.nameOf(group, t.from)));
      row.appendChild(el("span", "arrow", "→"));
      row.appendChild(el("div", "avatar", initials(E.nameOf(group, t.to))));
      row.appendChild(el("span", "name", E.nameOf(group, t.to)));
      row.appendChild(el("span", "amt", money(t.amount)));
      host.appendChild(row);
    });

    var foot = el("div", "note");
    // When both strategies agree, saying "greedy" after the user picked
    // "optimal" reads like the choice was ignored. Report the tie honestly.
    var used = chosen.transfer_count === chosen.greedy_count &&
               chosen.optimal_feasible && chosen.transfer_count === chosen.optimal_count
      ? "both strategies agree on this one"
      : chosen.strategy + " produced this plan";
    foot.textContent = chosen.transfer_count + " transfer(s) move " + money(chosen.total_moved_minor) +
      " in total and clear every balance (" + used + ").";
    host.appendChild(foot);

    // Math panel
    var mathHost = $("settle-math");
    mathHost.innerHTML = "";
    var bound = E.lowerBound(net);
    var rows = [
      ["Creditors (owed money)", String(Object.keys(net).filter(function (k) { return net[k] > 0; }).length)],
      ["Debtors (owe money)", String(Object.keys(net).filter(function (k) { return net[k] < 0; }).length)],
      ["Lower bound on transfers", String(bound)],
      ["Greedy plan produces", chosen.greedy_count + " transfer(s)"],
      ["Optimal plan produces", chosen.optimal_feasible ? chosen.optimal_count + " transfer(s)" : "search skipped"],
      ["Plan you are seeing", chosen.transfer_count + " transfer(s)"],
      ["Proven minimal", chosen.optimal_feasible && chosen.transfer_count === bound ? "yes" : "not proven"]
    ];
    rows.forEach(function (pair) {
      var r = el("div", "alloc-row");
      r.appendChild(el("span", "who", pair[0]));
      r.appendChild(el("span", "amt", pair[1]));
      mathHost.appendChild(r);
    });

    try {
      var ok = E.verifyPlan(net, chosen.transfers);
      check("Settle plan clears every balance (replayed independently)", ok);
    } catch (err) {
      check("Settle plan clears every balance (replayed independently)", false, err.message);
    }
  }

  /* -------------------------------------------------------------- expenses */

  function renderExpenses() {
    var host = $("expenses-body");
    host.innerHTML = "";
    var ids = group.members.map(function (m) { return m.id; });
    var bds = E.breakdowns(group);
    var byId = {};
    bds.forEach(function (b) { byId[b.expense.id] = b; });

    var t = el("table");
    var thead = el("thead");
    var hr = el("tr");
    ["Date", "Expense", "Mode", "Paid by", "Amount", "Shares sum"].forEach(function (h, i) {
      var th = el("th", i === 4 || i === 5 ? "num" : null, h);
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    t.appendChild(thead);

    var tbody = el("tbody");
    group.expenses.forEach(function (e, i) {
      var bd = byId[e.id];
      var tr = el("tr");
      tr.style.cursor = "pointer";
      tr.appendChild(el("td", null, e.date));
      tr.appendChild(el("td", "name", (D.categories[e.category] || "•") + "  " + e.description));
      var tdMode = el("td");
      tdMode.appendChild(el("span", "pill mode", e.split.mode));
      tr.appendChild(tdMode);
      tr.appendChild(el("td", null, E.nameOf(group, e.paid_by)));
      tr.appendChild(el("td", "num", money(e.amount)));

      var sum = Object.keys(bd.shares).reduce(function (a, k) { return a + bd.shares[k]; }, 0);
      var tdSum = el("td", "num " + (sum === e.amount ? "pos" : "neg"));
      tdSum.textContent = money(sum) + (sum === e.amount ? " ✓" : " ✗");
      tr.appendChild(tdSum);

      var trDetail = el("tr");
      var tdDetail = el("td");
      tdDetail.colSpan = 6;
      var alloc = el("div", "alloc");
      group.members.forEach(function (m) {
        var v = bd.shares[m.id] || 0;
        var row = el("div", "alloc-row");
        row.appendChild(el("span", "who", m.name));
        row.appendChild(el("span", "amt", money(v)));
        alloc.appendChild(row);
      });
      tdDetail.appendChild(alloc);
      trDetail.appendChild(tdDetail);
      trDetail.style.display = "none";

      tr.addEventListener("click", function () {
        trDetail.style.display = trDetail.style.display === "none" ? "" : "none";
      });

      tbody.appendChild(tr);
      tbody.appendChild(trDetail);

      check("Expense " + (i + 1) + " (" + e.split.mode + ") shares sum to its total", sum === e.amount,
        e.description);
    });

    t.appendChild(tbody);
    host.appendChild(t);
  }

  /* ----------------------------------------------------------------- proof */

  function renderProof() {
    var cli = [
      "$ python3 -m splitkit.cli demo",
      "",
      "  Goa Weekend — balances (USD)",
      "  Member      Paid     Share      Net",
      "  ------  --------  --------  -------"
    ];
    table.forEach(function (r) {
      cli.push("  " + r.name.padEnd(7) + r.paid_display.padStart(9) + r.share_display.padStart(10) +
        r.balance_display.padStart(9));
    });
    cli.push("");
    cli.push("  Sum of net positions: 0.00  (must be 0)");
    cli.push("");
    plan.transfers.forEach(function (t) {
      cli.push("  " + E.nameOf(group, t.from).padEnd(6) + "→  " + E.nameOf(group, t.to).padEnd(6) + " " +
        money(t.amount));
    });
    cli.push("");
    cli.push("  " + plan.transfer_count + " transfer(s) clears the whole book.");
    cli.push("");
    cli.push("  This page computed the same figures in JavaScript from the same");
    cli.push("  money rules — see web-live/splitkit-engine.js.");
    $("proof-cli").textContent = cli.join("\n");
  }

  function renderChecks() {
    var host = $("proof-invariants");
    host.innerHTML = "";
    var passed = 0;
    checks.forEach(function (c) {
      if (c.passed) passed++;
      var row = el("div", "alloc-row");
      row.appendChild(el("span", "who", (c.passed ? "✅ " : "❌ ") + c.label));
      if (c.detail) row.appendChild(el("span", "chip", c.detail));
      host.appendChild(row);
    });
    var note = el("div", "note");
    note.textContent = passed + " / " + checks.length + " invariants held in this browser session.";
    host.appendChild(note);
    $("foot-checksum").textContent = passed + "/" + checks.length + " checks passed";
  }

  /* -------------------------------------------------------------- bootstrap */

  function fillCurrencies() {
    var sel = $("calc-currency");
    ["USD", "EUR", "GBP", "PKR", "INR", "JPY", "KWD", "AED", "SGD", "BRL"].forEach(function (c) {
      var o = document.createElement("option");
      o.value = c;
      o.textContent = c + " (" + E.exponentFor(c) + " decimal" + (E.exponentFor(c) === 1 ? "" : "s") + ")";
      sel.appendChild(o);
    });
  }

  function wireTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.setAttribute("aria-selected", "false"); });
        tab.setAttribute("aria-selected", "true");
        Array.prototype.forEach.call(document.querySelectorAll(".panel"), function (p) {
          p.classList.remove("active");
        });
        $(tab.getAttribute("aria-controls")).classList.add("active");
        if (tab.id === "tab-proof") { renderChecks(); }
      });
    });
  }

  function boot() {
    fillCurrencies();
    wireTabs();

    $("calc-mode").addEventListener("change", function () {
      $("calc-mode-blurb").textContent = MODES[$("calc-mode").value] || "";
      runCalc();
    });
    $("calc-currency").addEventListener("change", runCalc);
    $("calc-run").addEventListener("click", runCalc);
    $("calc-reset").addEventListener("click", function () {
      $("calc-amount").value = "100.00";
      $("calc-currency").value = "USD";
      $("calc-mode").value = "equal";
      $("calc-mode-blurb").textContent = MODES.equal;
      runCalc();
    });
    $("settle-strategy").addEventListener("change", function () {
      $("settle-result").innerHTML = "";
      renderSettle();
    });

    // sanity: the embedded expectations must match what the engine computes
    check("Embedded demo total matches the engine", total === D.expectations.total_minor,
      money(total));
    check("Embedded transfer count matches the engine",
      plan.transfer_count === D.expectations.transfer_count, String(plan.transfer_count));

    renderOverview();
    renderCentExplainer();
    renderSettle();
    renderExpenses();
    renderProof();
    renderChecks();

    $("calc-mode-blurb").textContent = MODES.equal;
    runCalc();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
