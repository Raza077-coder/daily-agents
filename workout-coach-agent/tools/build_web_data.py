#!/usr/bin/env python3
"""Generate `web-live/forge-data.js` from the Python engine.

The browser demo must be genuinely self-contained: no `fetch`, no XHR, no
runtime data loading — which means the exercise library and the programme
templates have to be *embedded* in a JS file at build time.

Single source of truth: the data comes from `forge.exercises` and
`forge.program`, the same modules the CLI and API use. Re-running this script
after editing the library regenerates the browser data, so the two can never
drift apart.

Usage:
    python3 tools/build_web_data.py [--out web-live/forge-data.js]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.exercises import ALL_EQUIPMENT, EXERCISES, MAJOR_MUSCLES  # noqa: E402
from forge.program import (  # noqa: E402
    EXPERIENCE_WEEKS,
    GOAL_SCHEMES,
    SESSION_TEMPLATES,
    SPLITS,
    WEEKLY_SET_CAP,
)


def build_payload() -> dict:
    """Assemble the data object the browser engine consumes."""
    exercises = []
    for ex in sorted(EXERCISES.values(), key=lambda e: e.name):
        exercises.append(
            {
                "id": ex.id,
                "name": ex.name,
                "primary": ex.primary,
                "secondary": list(ex.secondary),
                "equipment": ex.equipment,
                "pattern": ex.pattern,
                "kind": ex.kind,
                "level": ex.level,
                "ratio": ex.strength_ratio,
                "increment": ex.increment,
                "unilateral": ex.unilateral,
            }
        )

    return {
        "version": "1.0.0",
        "agent": "FORGE — Workout Coach",
        "disclaimer": (
            "FORGE is an informational fitness tool. It is not medical advice. "
            "Consult a qualified physician or physiotherapist before starting a "
            "new training programme, especially if you have an injury, are "
            "pregnant, or have a cardiovascular condition."
        ),
        "muscles": MAJOR_MUSCLES,
        "equipment": ALL_EQUIPMENT,
        "exercises": exercises,
        "splits": {
            k: {"split": v["split"], "days": v["days"], "sessions": v["sessions"]}
            for k, v in SPLITS.items()
        },
        "sessionTemplates": SESSION_TEMPLATES,
        "goalSchemes": GOAL_SCHEMES,
        "experienceWeeks": EXPERIENCE_WEEKS,
        "weeklySetCap": WEEKLY_SET_CAP,
    }


def render_js(payload: dict) -> str:
    """Wrap the payload as a global with a banner explaining where it came from.

    The preamble resolves a root object that works in both the browser and
    Node, so the same generated file can be exercised by the parity test script
    without any DOM shim.
    """
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return (
        "/* FORGE — generated data file. DO NOT EDIT BY HAND.\n"
        " *\n"
        " * Regenerate with:  python3 tools/build_web_data.py\n"
        " *\n"
        " * Embedded deliberately (rather than fetched) so the browser demo runs\n"
        " * with zero network calls and works straight from file://.\n"
        f" * Exercises: {len(payload['exercises'])}  ·  Splits: {len(payload['splits'])}\n"
        " */\n"
        "(function (root) {\n"
        f"  root.FORGE_DATA = {body};\n"
        "  if (typeof module !== 'undefined' && module.exports) {\n"
        "    module.exports = root.FORGE_DATA;\n"
        "  }\n"
        "})(typeof window !== 'undefined' ? window : globalThis);\n"
    )



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "web-live" / "forge-data.js"))
    args = ap.parse_args()

    payload = build_payload()
    js = render_js(payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(js, encoding="utf-8")

    size_kb = len(js.encode("utf-8")) / 1024
    print(f"wrote {out} — {len(payload['exercises'])} exercises, {size_kb:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
