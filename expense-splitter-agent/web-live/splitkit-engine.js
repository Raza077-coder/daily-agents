/* SplitKit browser engine — a faithful port of splitkit/money.py, splits.py,
 * ledger.py and settle.py.
 *
 * The whole point of porting rather than faking: the numbers on the demo page
 * must be the numbers the Python CLI prints. All arithmetic is done in integer
 * minor units, exactly like the Python side, with a rational allocator that
 * cannot lose a cent.
 *
 * Deliberately dependency-free and network-free: no fetch, no XHR, no CDN. The
 * demo data is embedded by build_web_data.py. Works from file:// too.
 */
(function (global) {
  "use strict";

  /* ---------------------------------------------------------------- money */

  var EXPONENTS = {
    USD: 2, EUR: 2, GBP: 2, PKR: 2, INR: 2, AED: 2, SAR: 2, CAD: 2, AUD: 2,
    CHF: 2, SGD: 2, MYR: 2, BDT: 2, TRY: 2, ZAR: 2, BRL: 2, MXN: 2, PHP: 2,
    THB: 2, IDR: 2, NGN: 2, EGP: 2, QAR: 2, CNY: 2, HKD: 2, NZD: 2, SEK: 2,
    NOK: 2, DKK: 2, PLN: 2, CZK: 2, HUF: 2, RUB: 2, UAH: 2, LKR: 2, NPR: 2,
    KES: 2, GHS: 2, MAD: 2, DZD: 2, ILS: 2,
    KWD: 3, BHD: 3, OMR: 3, JOD: 3, TND: 3, IQD: 3, LYD: 3,
    JPY: 0, KRW: 0, CLP: 0, ISK: 0, PYG: 0, VND: 0, XOF: 0, XAF: 0, RWF: 0,
    UGX: 0, DJF: 0, GNF: 0, KMF: 0, MGA: 0
  };

  var SYMBOLS = {
    USD: "$", EUR: "\u20ac", GBP: "\u00a3", JPY: "\u00a5", CNY: "\u00a5",
    INR: "\u20b9", PKR: "\u20a8", BDT: "\u09f3", TRY: "\u20ba", NGN: "\u20a6",
    PHP: "\u20b1", THB: "\u0e3f", KRW: "\u20a9", CHF: "Fr", CAD: "C$",
    AUD: "A$", NZD: "NZ$", SGD: "S$", HKD: "HK$", BRL: "R$", MXN: "MX$",
    IDR: "Rp", MYR: "RM", ILS: "\u20aa", PLN: "z\u0142", CZK: "K\u010d",
    SEK: "kr", NOK: "kr", DKK: "kr", HUF: "Ft", RUB: "\u20bd", UAH: "\u20b4",
    LKR: "Rs", NPR: "Rs", KES: "KSh", GHS: "GH\u20b5", ZAR: "R", VND: "\u20ab"
  };

  function exponentFor(code) {
    if (typeof code !== "string") throw new Error("currency code must be a string");
    return EXPONENTS.hasOwnProperty(code.toUpperCase()) ? EXPONENTS[code.toUpperCase()] : 2;
  }

  function formatAmount(minor, exponent, opts) {
    opts = opts || {};
    if (!Number.isInteger(minor)) throw new Error("formatAmount needs an integer of minor units");
    var negative = minor < 0;
    var digits = String(Math.abs(minor));
    var whole, frac;
    if (exponent === 0) {
      whole = digits; frac = "";
    } else {
      var divisor = Math.pow(10, exponent);
      var w = Math.floor(Math.abs(minor) / divisor);
      var f = Math.abs(minor) % divisor;
      whole = String(w);
      frac = String(f);
      while (frac.length < exponent) frac = "0" + frac;
    }
    if (opts.grouping) whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    var body = exponent ? whole + "." + frac : whole;
    // Zero is not positive: a "+" on an empty balance would read as a credit.
    var sign = negative ? "-" : (opts.plus && minor > 0 ? "+" : "");
    var prefix = opts.symbol || "";
    var suffix = opts.code ? " " + opts.code : "";
    return sign + prefix + body + suffix;
  }

  function formatFor(code, minor, opts) {
    opts = opts || {};
    var exp = exponentFor(code);
    return formatAmount(minor, exp, {
      symbol: opts.symbol === false ? null : SYMBOLS[code.toUpperCase()],
      code: opts.symbol === false ? code : null,
      grouping: opts.grouping !== false,
      plus: !!opts.plus
    });
  }

  /* Exact allocation. Python uses fractions.Fraction; JS gets exact rationals
   * from BigInt numerator/denominator pairs. Largest-remainder method, ties
   * broken by index so the result is fully deterministic. */
  function allocate(total, weights) {
    if (!Number.isInteger(total)) throw new Error("allocate needs an integer total");
    if (!weights || !weights.length) throw new Error("allocate needs at least one weight");

    var fr = weights.map(function (w, i) {
      if (typeof w === "boolean") throw new Error("weight " + i + " is a boolean");
      var n, d;
      if (typeof w === "number") {
        if (!Number.isFinite(w)) throw new Error("weight " + i + " is not finite");
        // Convert the decimal literal exactly (0.1 -> 1/10, not the binary value).
        var s = String(w), dot = s.indexOf(".");
        if (dot >= 0) {
          var decimals = s.length - dot - 1;
          d = BigInt(Math.pow(10, decimals));
          n = BigInt(Math.round(w * Math.pow(10, decimals)));
        } else {
          n = BigInt(s); d = 1n;
        }
      } else if (typeof w === "string") {
        var t = w.trim();
        var slash = t.indexOf("/");
        if (slash >= 0) { n = BigInt(t.slice(0, slash)); d = BigInt(t.slice(slash + 1)); }
        else {
          var dot2 = t.indexOf(".");
          if (dot2 >= 0) {
            var dec2 = t.length - dot2 - 1;
            d = BigInt(Math.pow(10, dec2));
            n = BigInt(t.replace(".", ""));
          } else { n = BigInt(t); d = 1n; }
        }
      } else {
        throw new Error("weight " + i + " (" + w + ") is not a number");
      }
      if (d === 0n) throw new Error("weight " + i + " has a zero denominator");
      if (n < 0n || d < 0n) throw new Error("weight " + i + " is negative");
      return { n: n, d: d };
    });

    var sumD = 1n;
    fr.forEach(function (x) { sumD = lcm(sumD, x.d); });
    var weightSum = fr.reduce(function (a, x) { return a + x.n * (sumD / x.d); }, 0n);
    if (weightSum <= 0n) throw new Error("weights sum to zero - nothing to allocate by");

    if (total === 0) return weights.map(function () { return 0; });

    var sign = total < 0 ? -1 : 1;
    var magnitude = BigInt(Math.abs(total));

    var parts = [], remainders = [];
    fr.forEach(function (x) {
      // share = magnitude * w / weightSum  as an exact rational
      var num = magnitude * x.n * (sumD / x.d);
      var den = weightSum;
      var floor = num / den;
      parts.push(floor);
      remainders.push({ num: num - floor * den, den: den, idx: parts.length - 1 });
    });

    var shortfall = magnitude - parts.reduce(function (a, b) { return a + b; }, 0n);
    if (shortfall > 0n) {
      // Largest fractional remainder first; ties by lowest index.
      var order = remainders.slice().sort(function (a, b) {
        var left = a.num * b.den, right = b.num * a.den; // cross-multiply
        if (left > right) return -1;
        if (left < right) return 1;
        return a.idx - b.idx;
      });
      for (var k = 0; k < Number(shortfall); k++) parts[order[k % order.length].idx] += 1n;
    }

    var out = parts.map(function (p) { return sign * Number(p); });
    var check = out.reduce(function (a, b) { return a + b; }, 0);
    if (check !== total) {
      throw new Error("allocation failed to conserve money: " + check + " != " + total);
    }
    return out;
  }

  function gcd(a, b) { while (b) { var t = a % b; a = b; b = t; } return a < 0n ? -a : a; }
  function lcm(a, b) { return (a / gcd(a, b)) * b; }

  function splitEvenly(total, count) {
    if (count < 1) throw new Error("cannot split " + total + " across " + count + " people");
    return allocate(total, new Array(count).fill(1));
  }

  /* --------------------------------------------------------------- splits */

  function resolveSplit(spec, amount, allMembers) {
    spec = spec || {};
    var mode = (spec.mode || "equal").toLowerCase();
    if (amount === 0) return {};

    if (mode === "equal") {
      var people = participants(spec, allMembers);
      var parts = splitEvenly(amount, people.length);
      var out = {};
      people.forEach(function (p, i) { if (parts[i] !== 0) out[p] = parts[i]; });
      return out;
    }

    if (mode === "exact") {
      var amounts = mapping(spec, "amounts", mode);
      checkKnown(Object.keys(amounts), allMembers, "the split");
      var values = {}, total = 0;
      Object.keys(amounts).forEach(function (who) {
        var v = amounts[who];
        values[who] = Number.isInteger(v) ? v : parseAmount(String(v));
        total += values[who];
      });
      if (total !== amount) throw new Error("exact amounts sum to " + total + " but the expense is " + amount);
      return compact(values);
    }

    if (mode === "shares") {
      var shares = mapping(spec, "shares", mode);
      checkKnown(Object.keys(shares), allMembers, "the split");
      var people2 = Object.keys(shares);
      var weights = people2.map(function (p) { return shares[p]; });
      var parts2 = allocate(amount, weights);
      var out2 = {};
      people2.forEach(function (p, i) { if (parts2[i] !== 0) out2[p] = parts2[i]; });
      return out2;
    }

    if (mode === "percent") {
      var percents = mapping(spec, "percents", mode);
      checkKnown(Object.keys(percents), allMembers, "the split");
      // Parse as exact rationals and compare against exactly 100.
      var totalPct = { n: 0n, d: 1n };
      var parsed = {};
      Object.keys(percents).forEach(function (who) {
        var f = parseRational(String(percents[who]));
        parsed[who] = f;
        totalPct = addRational(totalPct, f);
      });
      if (!(totalPct.n === 100n * totalPct.d)) {
        throw new Error("percents must sum to exactly 100% (got " + rationalToString(totalPct) + "%)");
      }
      var people3 = Object.keys(percents);
      var parts3 = allocateExact(amount, people3.map(function (p) { return parsed[p]; }));
      var out3 = {};
      people3.forEach(function (p, i) { if (parts3[i] !== 0) out3[p] = parts3[i]; });
      return out3;
    }

    if (mode === "itemized") return resolveItemized(spec, amount, allMembers);

    if (mode === "adjustment") {
      var peopleA = participants(spec, allMembers);
      var baseParts = splitEvenly(amount, peopleA.length);
      var base = {};
      peopleA.forEach(function (p, i) { base[p] = baseParts[i]; });
      var adjSpec = spec.adjustments || {};
      var adj = {}, adjSum = 0;
      Object.keys(adjSpec).forEach(function (who) {
        var key = who.toLowerCase();
        var v = adjSpec[who];
        adj[key] = Number.isInteger(v) ? v : parseAmount(String(v));
        adjSum += adj[key];
      });
      checkKnown(Object.keys(adj), allMembers, "the adjustments");
      if (adjSum !== 0) throw new Error("adjustments sum to " + adjSum + " - they must net to zero");
      Object.keys(adj).forEach(function (who) {
        if (!(who in base)) throw new Error("adjustment for " + who + " but they are not in participants");
        base[who] += adj[who];
      });
      Object.keys(base).forEach(function (who) {
        if (base[who] < 0) throw new Error("adjustments push " + who + " below zero");
      });
      return compact(base);
    }

    throw new Error("unknown split mode " + mode);
  }

  function parseRational(text) {
    var t = String(text).trim().replace("%", "").trim();
    var dot = t.indexOf(".");
    if (dot >= 0) {
      var dec = t.length - dot - 1;
      return { n: BigInt(t.replace(".", "")), d: BigInt(Math.pow(10, dec)) };
    }
    return { n: BigInt(t), d: 1n };
  }

  function addRational(a, b) {
    return { n: a.n * b.d + b.n * a.d, d: a.d * b.d };
  }

  function rationalToString(f) {
    if (f.d === 1n) return String(f.n);
    return (Number(f.n) / Number(f.d)).toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }

  /* Allocation driven by exact rationals (for percent mode). */
  function allocateExact(total, rationals) {
    var common = 1n;
    rationals.forEach(function (r) { common = lcm(common, r.d); });
    var ints = rationals.map(function (r) { return r.n * (common / r.d); });
    var weightSum = ints.reduce(function (a, b) { return a + b; }, 0n);
    if (weightSum <= 0n) throw new Error("percent weights sum to zero");

    var sign = total < 0 ? -1 : 1;
    var magnitude = BigInt(Math.abs(total));
    var parts = [], rems = [];
    ints.forEach(function (w, i) {
      var num = magnitude * w;
      var floor = num / weightSum;
      parts.push(floor);
      rems.push({ num: num - floor * weightSum, idx: i });
    });
    var shortfall = magnitude - parts.reduce(function (a, b) { return a + b; }, 0n);
    if (shortfall > 0n) {
      var order = rems.slice().sort(function (a, b) {
        if (a.num > b.num) return -1;
        if (a.num < b.num) return 1;
        return a.idx - b.idx;
      });
      for (var k = 0; k < Number(shortfall); k++) parts[order[k % order.length].idx] += 1n;
    }
    return parts.map(function (p) { return sign * Number(p); });
  }

  function resolveItemized(spec, amount, allMembers) {
    var items = spec.items;
    if (!items || !items.length) throw new Error("an 'itemized' split needs a non-empty 'items' list");
    var totals = {};
    var subtotal = 0;
    items.forEach(function (item, idx) {
      var label = item.label || "item " + (idx + 1);
      var value = Number.isInteger(item.amount) ? item.amount : parseAmount(String(item.amount));
      if (value < 0) throw new Error("item " + label + " has a negative amount");
      var people = item.participants && item.participants.length ? item.participants : allMembers.slice();
      if (item.participants && item.participants.length) checkKnown(people, allMembers, "item " + label);
      var shares = splitEvenly(value, people.length);
      people.forEach(function (who, i) { totals[who] = (totals[who] || 0) + shares[i]; });
      subtotal += value;
    });

    var tax = resolveExtra(spec.tax);
    var tip = resolveExtra(spec.tip);
    if (subtotal + tax + tip !== amount) {
      throw new Error("items + tax + tip = " + (subtotal + tax + tip) + " but the expense is " + amount);
    }

    [["tax", tax, spec.tax_mode], ["tip", tip, spec.tip_mode]].forEach(function (pair) {
      var pot = pair[1];
      if (!pot) return;
      var mode = (pair[2] || "proportional").toLowerCase();
      var people = Object.keys(totals).filter(function (p) { return totals[p] > 0; });
      if (!people.length) people = allMembers.slice();
      var weights;
      if (mode === "equal") weights = people.map(function () { return 1; });
      else weights = people.map(function (p) { return totals[p]; });
      var parts = allocate(pot, weights);
      people.forEach(function (who, i) { totals[who] += parts[i]; });
    });
    return compact(totals);
  }

  function resolveExtra(raw) {
    if (raw === undefined || raw === null || raw === 0 || raw === "0") return 0;
    var v = Number.isInteger(raw) ? raw : parseAmount(String(raw));
    if (v < 0) throw new Error("tax/tip cannot be negative");
    return v;
  }

  function participants(spec, allMembers) {
    var raw = spec.participants;
    if (raw === undefined || raw === null) return allMembers.slice();
    if (typeof raw === "string") throw new Error("'participants' must be a list, not a single string");
    var ids = raw.map(function (p) { return String(p).trim().toLowerCase(); });
    if (!ids.length) throw new Error("'participants' is empty");
    checkKnown(ids, allMembers, "the split");
    return ids;
  }

  function mapping(spec, key, mode) {
    var raw = spec[key];
    if (!raw) throw new Error("a '" + mode + "' split needs a '" + key + "' object");
    return raw;
  }

  function checkKnown(ids, known, what) {
    var set = {};
    known.forEach(function (k) { set[k] = true; });
    var unknown = ids.filter(function (i) { return !set[i]; });
    if (unknown.length) throw new Error(what + " references " + unknown.join(", ") + ", not in the group");
  }

  function compact(obj) {
    var out = {};
    Object.keys(obj).forEach(function (k) { if (obj[k] !== 0) out[k] = obj[k]; });
    return out;
  }

  function parseAmount(text, exponent) {
    exponent = exponent === undefined ? 2 : exponent;
    if (Number.isInteger(text)) return text;
    if (typeof text === "number") throw new Error("refusing the float " + text + " as an amount");
    var s = String(text).trim();
    var neg = false;
    if (s[0] === "(" && s[s.length - 1] === ")") { neg = true; s = s.slice(1, -1).trim(); }
    s = s.replace(/[^0-9.\-+]/g, "");
    if (!s) throw new Error("amount contains no digits");
    var sign = 1;
    if (s[0] === "-") { sign = -1; s = s.slice(1); }
    else if (s[0] === "+") { s = s.slice(1); }
    var dot = s.indexOf(".");
    var intPart = dot >= 0 ? s.slice(0, dot) : s;
    var fracPart = dot >= 0 ? s.slice(dot + 1) : "";
    if (fracPart.length > exponent) {
      var extra = fracPart.slice(exponent);
      if (/[^0]/.test(extra)) throw new Error("more decimal places than the currency allows");
      fracPart = fracPart.slice(0, exponent);
    }
    while (fracPart.length < exponent) fracPart += "0";
    var value = parseInt(intPart || "0", 10) * Math.pow(10, exponent) + parseInt(fracPart || "0", 10);
    return (neg ? -1 : 1) * sign * value;
  }

  /* --------------------------------------------------------------- ledger */

  function breakdowns(group) {
    var ids = group.members.map(function (m) { return m.id; });
    return group.expenses.map(function (e) {
      var shares = resolveSplit(e.split, e.amount, ids);
      var total = Object.keys(shares).reduce(function (a, k) { return a + shares[k]; }, 0);
      if (total !== e.amount) {
        throw new Error("expense " + e.id + " resolved to " + total + " but is recorded as " + e.amount);
      }
      return { expense: e, shares: shares };
    });
  }

  function paidTotals(group) {
    var t = {};
    group.members.forEach(function (m) { t[m.id] = 0; });
    group.expenses.forEach(function (e) { t[e.paid_by] = (t[e.paid_by] || 0) + e.amount; });
    return t;
  }

  function owedTotals(group) {
    var t = {};
    group.members.forEach(function (m) { t[m.id] = 0; });
    breakdowns(group).forEach(function (bd) {
      Object.keys(bd.shares).forEach(function (who) { t[who] = (t[who] || 0) + bd.shares[who]; });
    });
    return t;
  }

  function settledTotals(group) {
    var t = {};
    group.members.forEach(function (m) { t[m.id] = 0; });
    (group.settlements || []).forEach(function (s) {
      t[s.from] = (t[s.from] || 0) + s.amount;
      t[s.to] = (t[s.to] || 0) - s.amount;
    });
    return t;
  }

  function balances(group) {
    var paid = paidTotals(group), owed = owedTotals(group), settled = settledTotals(group);
    var net = {};
    group.members.forEach(function (m) {
      net[m.id] = (paid[m.id] || 0) + (settled[m.id] || 0) - (owed[m.id] || 0);
    });
    var total = Object.keys(net).reduce(function (a, k) { return a + net[k]; }, 0);
    if (total !== 0) throw new Error("ledger does not balance: sum is " + total + ", expected 0");
    return net;
  }

  function balanceTable(group) {
    var net = balances(group), paid = paidTotals(group), owed = owedTotals(group);
    var code = group.currency;
    return group.members.map(function (m) {
      var bal = net[m.id] || 0;
      return {
        id: m.id,
        name: m.name,
        paid_minor: paid[m.id] || 0,
        paid_display: formatFor(code, paid[m.id] || 0, { symbol: false }),
        share_minor: owed[m.id] || 0,
        share_display: formatFor(code, owed[m.id] || 0, { symbol: false }),
        balance_minor: bal,
        balance_display: formatFor(code, bal, { symbol: false, plus: true }),
        state: bal > 0 ? "owed" : (bal < 0 ? "owes" : "square")
      };
    });
  }

  function categoryTotals(group) {
    var t = {};
    group.expenses.forEach(function (e) { t[e.category] = (t[e.category] || 0) + e.amount; });
    return Object.keys(t).sort(function (a, b) { return t[b] - t[a] || (a < b ? -1 : 1); })
      .reduce(function (acc, k) { acc[k] = t[k]; return acc; }, {});
  }

  /* --------------------------------------------------------------- settle */

  function greedyTransfers(net) {
    var creditors = Object.keys(net).filter(function (m) { return net[m] > 0; })
      .map(function (m) { return [m, net[m]]; })
      .sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
    var debtors = Object.keys(net).filter(function (m) { return net[m] < 0; })
      .map(function (m) { return [m, net[m]]; })
      .sort(function (a, b) { return a[1] - b[1] || (a[0] < b[0] ? -1 : 1); });

    var transfers = [];
    var guard = 0, limit = 4 * (creditors.length + debtors.length) + 16;
    while (creditors.length && debtors.length) {
      if (++guard > limit) throw new Error("settle-up failed to converge");
      var ci = 0, di = 0;
      for (var i = 1; i < creditors.length; i++) {
        if (creditors[i][1] > creditors[ci][1] ||
            (creditors[i][1] === creditors[ci][1] && creditors[i][0] < creditors[ci][0])) ci = i;
      }
      for (var j = 1; j < debtors.length; j++) {
        if (debtors[j][1] < debtors[di][1] ||
            (debtors[j][1] === debtors[di][1] && debtors[j][0] < debtors[di][0])) di = j;
      }
      var amount = Math.min(creditors[ci][1], -debtors[di][1]);
      if (amount <= 0) throw new Error("settle-up produced a non-positive transfer");
      transfers.push({ from: debtors[di][0], to: creditors[ci][0], amount: amount });
      creditors[ci][1] -= amount;
      debtors[di][1] += amount;
      creditors = creditors.filter(function (c) { return c[1] > 0; });
      debtors = debtors.filter(function (d) { return d[1] < 0; });
    }
    return transfers;
  }

  /* Minimum-transfer solve: partition into independent zero-sum groups with a
   * bitmask DP, then settle each internally (k-1 transfers per k-group). */
  function optimalTransfers(net) {
    var members = Object.keys(net).sort();
    var active = members.filter(function (m) { return net[m] !== 0; });
    if (!active.length) return { transfers: [], exact: true };
    var n = active.length;
    var LIMIT = 20;
    if (n > LIMIT) return { transfers: greedyTransfers(net), exact: false };

    var values = active.map(function (m) { return net[m]; });
    var size = 1 << n;
    var sums = new Array(size).fill(0);
    for (var mask = 1; mask < size; mask++) {
      var low = mask & -mask;
      var idx = Math.log2(low) | 0;
      sums[mask] = sums[mask ^ low] + values[idx];
    }

    var bestParts = new Array(size).fill(-1);
    var bestSub = new Array(size).fill(0);
    bestParts[0] = 0;
    for (var m2 = 1; m2 < size; m2++) {
      var lowBit = m2 & -m2;
      var sub = m2;
      while (sub) {
        if ((sub & lowBit) && sums[sub] === 0) {
          var rest = bestParts[m2 ^ sub];
          if (rest >= 0 && rest + 1 > bestParts[m2]) {
            bestParts[m2] = rest + 1;
            bestSub[m2] = sub;
          }
        }
        sub = (sub - 1) & m2;
      }
    }

    var full = size - 1;
    if (bestParts[full] <= 0) return { transfers: greedyTransfers(net), exact: false };

    var transfers = [];
    var mask2 = full;
    while (mask2) {
      var piece = bestSub[mask2];
      if (!piece) return { transfers: greedyTransfers(net), exact: false };
      var local = {};
      for (var k = 0; k < n; k++) {
        if (piece & (1 << k)) local[active[k]] = values[k];
      }
      transfers = transfers.concat(greedyTransfers(local));
      mask2 ^= piece;
    }
    transfers.sort(function (a, b) {
      return (a.from < b.from ? -1 : a.from > b.from ? 1 :
              a.to < b.to ? -1 : a.to > b.to ? 1 : a.amount - b.amount);
    });
    return { transfers: transfers, exact: true };
  }

  function lowerBound(net) {
    var c = 0, d = 0;
    Object.keys(net).forEach(function (k) { if (net[k] > 0) c++; else if (net[k] < 0) d++; });
    return Math.max(c, d);
  }

  function settle(group, strategy) {
    strategy = strategy || "optimal";
    var net = balances(group);
    var greedy = greedyTransfers(net);
    var opt = optimalTransfers(net);
    var exact = opt.exact;
    var chosen, label;
    if (strategy === "greedy") { chosen = greedy; label = "greedy"; }
    else {
      if (exact && opt.transfers.length < greedy.length) { chosen = opt.transfers; label = "optimal"; }
      else { chosen = greedy; label = "greedy"; }
    }
    var notes = [];
    if (!exact) notes.push("Group too large for the exact minimum-transfer search; greedy plan shown.");
    if (!chosen.length) notes.push("Everyone is square - nothing to settle.");

    return {
      strategy: label,
      transfers: chosen,
      transfer_count: chosen.length,
      greedy_count: greedy.length,
      optimal_count: exact ? opt.transfers.length : greedy.length,
      optimal_feasible: exact,
      transfers_saved: Math.max(greedy.length - (exact ? opt.transfers.length : greedy.length), 0),
      total_moved_minor: chosen.reduce(function (a, t) { return a + t.amount; }, 0),
      notes: notes
    };
  }

  /* Independent checker: replay the plan and confirm every balance is zero. */
  function verifyPlan(net, transfers) {
    var after = {};
    Object.keys(net).forEach(function (k) { after[k] = net[k]; });
    transfers.forEach(function (t) {
      if (t.amount <= 0) throw new Error("transfer is not positive");
      if (t.from === t.to) throw new Error("transfer to the same person");
      if ((after[t.from] || 0) >= 0) throw new Error(t.from + " is not a debtor");
      if ((after[t.to] || 0) <= 0) throw new Error(t.to + " is not a creditor");
      after[t.from] = (after[t.from] || 0) + t.amount;
      after[t.to] = (after[t.to] || 0) - t.amount;
    });
    var leftover = Object.keys(after).filter(function (k) { return after[k] !== 0; });
    if (leftover.length) throw new Error("plan left unsettled balances: " + leftover.join(", "));
    return true;
  }

  function nameOf(group, id) {
    for (var i = 0; i < group.members.length; i++) if (group.members[i].id === id) return group.members[i].name;
    return id;
  }

  global.SplitKitEngine = {
    EXPONENTS: EXPONENTS,
    SYMBOLS: SYMBOLS,
    exponentFor: exponentFor,
    formatAmount: formatAmount,
    formatFor: formatFor,
    parseAmount: parseAmount,
    allocate: allocate,
    splitEvenly: splitEvenly,
    resolveSplit: resolveSplit,
    breakdowns: breakdowns,
    balances: balances,
    balanceTable: balanceTable,
    paidTotals: paidTotals,
    owedTotals: owedTotals,
    categoryTotals: categoryTotals,
    greedyTransfers: greedyTransfers,
    optimalTransfers: optimalTransfers,
    lowerBound: lowerBound,
    settle: settle,
    verifyPlan: verifyPlan,
    nameOf: nameOf
  };
})(typeof window !== "undefined" ? window : globalThis);
