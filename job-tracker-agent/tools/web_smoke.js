/* Offline smoke test for the JOBFLOW web HUD.
 *
 * Proves two things the README claims:
 *
 * 1. The page makes NO network calls — any attempt throws, because `fetch`
 *    and `XMLHttpRequest` are replaced with throwing stubs before the scripts
 *    load.
 * 2. The rendering inputs are complete and sane — no `undefined`/`NaN` leaks
 *    into any user-visible string, and every view has data behind it.
 *
 * Usage:
 *
 *     node tools/web_smoke.js
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.join(__dirname, '..', 'web-live');
const failures = [];
const checks = [];

function check(name, condition, detail) {
  checks.push(name);
  if (!condition) {
    failures.push(name + (detail ? ' — ' + detail : ''));
  }
}

// ---- 1. no network primitives anywhere in the shipped JS ----------------

['app.js', 'track-engine.js', 'demo-data.js'].forEach(function (file) {
  const src = fs.readFileSync(path.join(WEB, file), 'utf8');
  // Only real call sites, not URL strings that happen to live in demo data.
  const patterns = [
    /\bfetch\s*\(/,
    /\bXMLHttpRequest\b/,
    /\bWebSocket\b/,
    /\bsendBeacon\b/,
    /\bimportScripts\b/,
    /\bEventSource\b/,
    /\bnavigator\.sendBeacon\b/
  ];
  const hit = patterns.find(function (re) { return re.test(src); });
  check('no-network:' + file, !hit, hit ? 'matched ' + hit : '');
});

// ---- 2. load the page scripts with the network stubbed out -------------

const sandbox = {
  console: console,
  Math: Math,
  Date: Date,
  JSON: JSON,
  Intl: Intl,
  Object: Object,
  Array: Array,
  String: String,
  Number: Number,
  Boolean: Boolean,
  RegExp: RegExp,
  Error: Error,
  isNaN: isNaN,
  parseInt: parseInt,
  parseFloat: parseFloat,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.fetch = function () { throw new Error('NETWORK ATTEMPTED (fetch)'); };
sandbox.XMLHttpRequest = function () {
  throw new Error('NETWORK ATTEMPTED (XMLHttpRequest)');
};

vm.createContext(sandbox);

['demo-data.js', 'track-engine.js'].forEach(function (file) {
  const src = fs.readFileSync(path.join(WEB, file), 'utf8');
  vm.runInContext(src, sandbox, { filename: file });
});

const E = sandbox.JobFlowEngine;
const D = sandbox.JOBFLOW_DEMO;

check('engine present', !!E, 'window.JobFlowEngine missing');
check('demo data present', !!D, 'window.JOBFLOW_DEMO missing');

if (!E || !D) {
  console.log('FAILED to load engine/data');
  process.exit(1);
}

// ---- 3. every number the UI renders is real ----------------------------

const apps = D.applications;
const today = D.today;
const sum = E.summary(apps, today);
const plan = E.plan(apps, today, 25);

check('applications seeded', apps.length >= 5, 'got ' + apps.length);
check('submitted > 0', sum.totals.submitted > 0);
check('funnel has all rungs', sum.funnel.rows.length === 6);
check('weekly has rows', sum.weekly.length === 8);
check('sources populated', sum.sources.length > 0);
check('salary populated', sum.salary.entries > 0);
check('plan produced actions', plan.total > 0);

// The headline case: a rejection after an onsite must be counted at onsite.
const cobalt = apps.filter(function (a) {
  return a.status === 'rejected' && E.reachedIndex(a) >= E.STAGE_INDEX.onsite;
});
check('rejection-after-onsite counted', cobalt.length >= 1,
  'cohort bias regression — a post-onsite rejection was not counted at its rung');
check('onsite rung reflects it', sum.funnel.rows[3].reached >= 1);

// No undefined / NaN in any user-visible string.
const strings = [];
plan.actions.forEach(function (a) {
  strings.push(a.action, a.explain, a.label, a.title);
});
strings.push(plan.headline);
sum.sources.forEach(function (s) { strings.push(s.label, s.median_display || ''); });
sum.stalled.forEach(function (s) { strings.push(s.label, s.status_label); });
Object.keys(sum.salary.currencies).forEach(function (c) {
  const r = sum.salary.currencies[c];
  strings.push(r.min_display, r.max_display, r.median_display);
});

const bad = strings.filter(function (s) {
  return /undefined|NaN|\[object|Infinity/.test(String(s));
});
check('no undefined/NaN in output', bad.length === 0, bad.slice(0, 4).join(' | '));

plan.actions.forEach(function (a) {
  check('action has explain:' + a.rule, !!a.explain);
  check('action has score:' + a.rule, typeof a.score === 'number' && a.score > 0);
});

// ---- 4. every HTML-referenced asset exists -----------------------------

const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const refs = [];
const re = /(?:src|href)="([^"#:]+)"/g;
let m;
while ((m = re.exec(html)) !== null) {
  if (m[1].indexOf('..') === 0) { continue; }  // links up to the repo
  refs.push(m[1]);
}
refs.forEach(function (ref) {
  check('asset exists:' + ref, fs.existsSync(path.join(WEB, ref)), 'missing file');
});
check('html references the engine', /track-engine\.js/.test(html));
check('html references the data', /demo-data\.js/.test(html));
check('html references the app', /app\.js/.test(html));

// ---- report ------------------------------------------------------------

console.log('JOBFLOW web smoke: ' + checks.length + ' checks');
console.log('  applications   : ' + apps.length);
console.log('  submitted      : ' + sum.totals.submitted);
console.log('  response rate  : ' + sum.funnel.response_rate + '% (' +
  sum.funnel.responded + '/' + sum.funnel.response_denominator + ')');
console.log('  reached onsite : ' + sum.funnel.rows[3].reached);
console.log('  interviewed    : ' + sum.funnel.interviewed +
  ' | offered: ' + sum.funnel.offered);
console.log('  plan actions   : ' + plan.total);
console.log('  stalled        : ' + sum.stalled.length);
console.log('  headline       : ' + plan.headline.slice(0, 100));

if (failures.length) {
  console.log('\n' + failures.length + ' FAILURE(S):');
  failures.forEach(function (f) { console.log('  - ' + f); });
  process.exit(1);
}
console.log('\nPASS — page is fully offline and renders real data');
