// Dev-only parity check: JS engine vs the Python engine's expectations.
// Not part of the shipped app.
require("../web-live/pantry-engine.js");
const fs = require("fs");
const path = require("path");

const P = globalThis.PantryEngine;
const root = path.join(__dirname, "..");
const recipes = JSON.parse(fs.readFileSync(path.join(root, "pantry/data/recipes.json"), "utf8")).recipes;
const lines = fs
  .readFileSync(path.join(root, "pantry/data/pantry_sample.txt"), "utf8")
  .split("\n")
  .map((s) => s.trim())
  .filter((s) => s && !s.startsWith("#"));

const e = new P.Engine(recipes, lines);

console.log("recipes        :", recipes.length);
console.log("pantry items   :", e.pantryList().length);
console.log("cookable now   :", e.cookNow({ onlyCookable: true }).length);

console.log("\ntop matches:");
e.cookNow({ limit: 5 }).forEach((m) =>
  console.log("   ", m.recipe.name.padEnd(30), Math.round(m.coverage * 100) + "%",
    "can=" + m.canCook, "missing=" + m.missing.length)
);

const pl = e.planWeek({ days: 5, slots: ["dinner"] });
console.log("\nplan           :", pl.summary.entries, "entries /", pl.summary.days, "days",
  "/ cookable", pl.summary.cookable);
console.log("plan distinct  :", pl.summary.distinctRecipes);

const sl = e.shoppingList({ days: 5, slots: ["dinner"] });
console.log("shopping lines :", sl.lines, "in", sl.aisles.length, "aisles");
console.log("covered pantry :", sl.coveredByPantry, "of", sl.subtotalNeeded);

const lo = e.leftoversPlan(4);
console.log("leftovers plan :", lo.summary.entries, "entries, all cookable =",
  lo.entries.every((x) => x.canCook));

console.log("\nparse '200 g spaghetti':", JSON.stringify(P.splitAmount("200 g spaghetti")));
console.log("parse 'olive oil'      :", JSON.stringify(P.splitAmount("olive oil")));
console.log("parse '1 1/2 cups milk':", JSON.stringify(P.splitAmount("1 1/2 cups milk")));
console.log("canon 'Spring Onions'  :", P.canonicalName("Spring Onions"));
console.log("canon 'Aubergines'     :", P.canonicalName("Aubergines"));
console.log("canon 'All-Purpose Flour':", P.canonicalName("All-Purpose Flour"));
console.log("aisle 'chicken breast' :", P.aisleFor("chicken breast"));
console.log("aisle 'spaghetti'      :", P.aisleFor("spaghetti"));

// Determinism: two engines must agree exactly.
const a = new P.Engine(recipes, lines).planWeek({ days: 5, slots: ["dinner"] });
const b = new P.Engine(recipes, lines).planWeek({ days: 5, slots: ["dinner"] });
const same = JSON.stringify(a.entries) === JSON.stringify(b.entries);
console.log("\ndeterminism    :", same ? "PASS (identical plans)" : "FAIL");
if (!same) process.exitCode = 1;
if (e.pantryList().length < 20 || e.cookNow({ onlyCookable: true }).length < 1) {
  console.log("SANITY FAIL: pantry/matching looks wrong");
  process.exitCode = 1;
}
