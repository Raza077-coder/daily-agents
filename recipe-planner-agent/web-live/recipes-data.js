/*!
 * PANTRY - data layer for the browser HUD.
 *
 * The recipe library is not duplicated here: the HUD reads the canonical
 * pantry/data/recipes.json (same file the Python engine loads), so the live
 * demo can never drift from the shipped library. The sample pantry is small
 * enough to inline.
 *
 * Synchronous on purpose: index.html loads this before pantry-engine.js and
 * app.js with plain <script> tags, so the globals are ready when they parse.
 *
 * Exposes: window.PANTRY_RECIPES, window.PANTRY_SAMPLE_PANTRY
 */
(function (global) {
  "use strict";

  var PATHS = [
    "../pantry/data/recipes.json",
    "pantry/data/recipes.json",
    "/daily-agents/recipe-planner-agent/pantry/data/recipes.json"
  ];

  var data = null;
  for (var i = 0; i < PATHS.length && !data; i++) {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", PATHS[i], false);
      xhr.send(null);
      if (xhr.status === 200 || xhr.status === 0) {
        var parsed = JSON.parse(xhr.responseText);
        data = parsed.recipes || parsed;
      }
    } catch (err) {
      data = null;
    }
  }

  global.PANTRY_RECIPES = data && data.length ? data : [];
  global.PANTRY_SAMPLE_PANTRY = ["200 g spaghetti","olive oil","salt","black pepper","garlic","2 piece onion","1 piece carrot","1 piece lemon","rice","400 g canned tomato","soy sauce","cumin","paprika","turmeric","honey","flour","baking powder","stock","6 piece egg","250 ml milk","100 g butter","150 g cheddar","200 g yogurt","1 piece cucumber","2 piece tomato","1 piece bell pepper","300 g mushroom","1 piece zucchini","150 g spinach"];
  global.PANTRY_DATA_ERROR = !global.PANTRY_RECIPES.length;
})(window);
