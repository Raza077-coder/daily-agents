"""The FORGE coach voice.

Turns the engine's raw facts into the sentences a good coach would actually
say. Keeping the phrasing in one place (backed by `forge_persona.json`) means
the CLI, the REST API and the browser demo sound like the same person, and the
tone can be tuned without touching any training logic.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PERSONA_PATH = Path(__file__).resolve().parent.parent / "forge_persona.json"


def _default_persona() -> Dict[str, Any]:
    """Inline fallback so the coach still speaks if the JSON is missing."""
    return {
        "name": "FORGE",
        "messages": {
            "no_program": "No programme yet. Build one with `forge program --days <n>`.",
            "no_history": "No logged work for this lift yet.",
            "rest_day": "Rest day. Recovery is where the adaptation actually happens.",
        },
        "safety": {
            "disclaimer": "FORGE is an informational fitness tool, not medical advice. "
            "Consult a qualified professional before starting a new programme."
        },
        "voice": {"style_rules": [], "banned_phrases": []},
        "principles": [],
    }


def load_persona(path: Optional[Any] = None) -> Dict[str, Any]:
    """Load the persona JSON, falling back to a minimal inline copy."""
    target = Path(path).expanduser() if path else DEFAULT_PERSONA_PATH
    if not target.exists():
        return _default_persona()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_persona()
    base = _default_persona()
    # Shallow-merge so a partial custom persona file still works.
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


@dataclass
class Coach:
    """Formats engine output into plain-language coaching."""

    persona: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.persona is None:
            self.persona = load_persona()

    # -- helpers ------------------------------------------------------------

    @property
    def name(self) -> str:
        return str(self.persona.get("name", "FORGE"))

    @property
    def disclaimer(self) -> str:
        return str(
            self.persona.get("safety", {}).get(
                "disclaimer",
                "This is an informational fitness tool, not medical advice.",
            )
        )

    def msg(self, key: str, **kwargs: Any) -> str:
        """Fetch a message template and fill it, tolerating missing keys."""
        raw = self.persona.get("messages", {}).get(key)
        if not raw:
            return ""
        try:
            return raw.format(**kwargs)
        except (KeyError, IndexError):
            return raw

    # -- narrators ----------------------------------------------------------

    def greet(self) -> str:
        tagline = self.persona.get("tagline", "Deterministic strength coaching.")
        return f"{self.name} — {tagline}"

    def describe_prescription(self, advice: Dict[str, Any]) -> str:
        """One line an athlete can act on, plus the reasoning underneath."""
        action = advice.get("action")
        weight = advice.get("weight", 0.0)
        low = advice.get("target_reps_low", 0)
        high = advice.get("target_reps_high", 0)
        sets = advice.get("sets", 0)
        name = advice.get("name", advice.get("exercise", "the lift"))

        if action == "increase":
            head = (
                f"{name}: {sets}x{low}-{high} @ {_kg(weight)} "
                f"(up from {_kg(advice.get('previous_weight', 0))})"
            )
        elif action == "deload":
            head = (
                f"{name}: {sets}x{low}-{high} @ {_kg(weight)} — deload "
                f"(from {_kg(advice.get('previous_weight', 0))})"
            )
        elif action == "change_stimulus":
            head = f"{name}: {sets}x{low}-{high} @ {_kg(weight)} — new rep range"
        elif action == "increase_reps":
            head = f"{name}: {sets}x{low}-{high} bodyweight"
        elif action == "start":
            head = f"{name}: {sets}x{low}-{high} @ {_kg(weight)} to start"
        else:
            head = f"{name}: {sets}x{low}-{high} @ {_kg(weight)}"

        reason = advice.get("reason", "").strip()
        return f"{head}\n    {reason}" if reason else head

    def describe_prs(self, prs: List[Dict[str, Any]]) -> str:
        if not prs:
            return ""
        if len(prs) == 1:
            pr = prs[0]
            return self.msg(
                "pr_single",
                label=pr.get("label", "personal best"),
                value=_num(pr.get("value", 0)),
                previous=_num(pr.get("previous", 0)),
            )
        return self.msg("pr_multi", count=len(prs))

    def describe_session(self, result: Dict[str, Any]) -> str:
        lines = [
            f"Logged {result.get('date')} — {result.get('session') or 'session'}",
            f"  Volume: {_num(result.get('total_volume', 0))}kg across "
            f"{len(result.get('exercises', []))} exercise(s)",
        ]
        for ex in result.get("exercises", []):
            top = ex.get("top_weight", 0)
            reps = (ex.get("sets") or [{}])[0].get("reps", 0)
            lines.append(
                f"  • {ex.get('name', ex.get('exercise'))}: "
                f"{len(ex.get('sets', []))} sets, top {_kg(top)} x {reps}, "
                f"volume {_num(ex.get('volume', 0))}kg"
            )
        pr_line = self.describe_prs(result.get("prs", []))
        if pr_line:
            lines.append("")
            lines.append(f"  {pr_line}")
        return "\n".join(lines)

    def describe_progress(self, data: Dict[str, Any]) -> str:
        trend = data.get("trend", {})
        first, latest = data.get("first", {}), data.get("latest", {})
        lines = [
            f"{data.get('name')} — {data.get('sessions')} logged session(s)",
            f"  First: {first.get('date')} — {_kg(first.get('weight', 0))} x "
            f"{first.get('reps', 0)} (e1RM {_num(first.get('e1rm', 0))}kg)",
            f"  Latest: {latest.get('date')} — {_kg(latest.get('weight', 0))} x "
            f"{latest.get('reps', 0)} (e1RM {_num(latest.get('e1rm', 0))}kg)",
            f"  Change: {_signed(data.get('weight_gain', 0))}kg on the bar, "
            f"{_signed(data.get('e1rm_gain', 0))}kg on estimated 1RM "
            f"({trend.get('direction', 'flat')})",
        ]
        advice = data.get("next")
        if advice:
            lines.append("")
            lines.append("  Next session:")
            lines.append("  " + self.describe_prescription(advice))
        return "\n".join(lines)

    def describe_summary(self, data: Dict[str, Any]) -> str:
        header = self.persona.get("report_templates", {}).get(
            "header", "FORGE — training report for {date}"
        ).format(date=data.get("as_of", ""))
        lines = [header, "=" * len(header), ""]

        if not data.get("total_workouts"):
            lines.append("No sessions logged yet. Log one with `forge log <exercise> <sets>`.")
            return "\n".join(lines)

        lines.append(
            f"Lifetime: {data['total_workouts']} sessions · "
            f"{_num(data['lifetime_tonnage'])}kg total tonnage · "
            f"{data['lifetime_sets']} working sets"
        )
        window = data.get("window", {})
        lines.append(
            f"Last {data.get('window_days')} days: {window.get('sessions')} sessions, "
            f"{_num(window.get('tonnage', 0))}kg, {window.get('sets')} sets"
        )

        tw = data.get("this_week")
        if tw:
            bodyweight_sets = tw.get("bodyweight_sets", 0)
            line = self.persona.get("report_templates", {}).get(
                "tonnage_line",
                "Weekly tonnage: {tonnage}kg ({bodyweight_sets} bodyweight sets excluded).",
            ).format(tonnage=_num(tw.get("tonnage", 0)), bodyweight_sets=bodyweight_sets)
            lines.append(line)
        delta = data.get("tonnage_delta_pct", 0)
        if delta > 1:
            lines.append("  " + self.msg("volume_up", pct=_num(delta)))
        elif delta < -1:
            lines.append("  " + self.msg("volume_down", pct=_num(abs(delta))))

        streak = data.get("streak", {})
        lines.append(
            f"Streak: {streak.get('current', 0)} week(s) at "
            f"{streak.get('target')}+ sessions (best {streak.get('longest', 0)}) — "
            f"{streak.get('this_week', 0)} logged this week."
        )

        cov = data.get("coverage", {})
        if cov:
            template = self.persona.get("report_templates", {}).get(
                "coverage_line",
                "Muscle coverage: {trained}/{total} major muscles trained.",
            )
            lines.append(
                template.format(
                    trained=len(cov.get("trained", [])),
                    total=len(cov.get("trained", []))
                    + len(cov.get("light", []))
                    + len(cov.get("missed", [])),
                )
            )
            if cov.get("missed"):
                lines.append("  Not trained: " + ", ".join(cov["missed"]))

        balance = data.get("balance", {})
        if balance:
            lines.append("")
            lines.append("Balance:")
            for finding in balance.get("findings", [])[:3]:
                lines.append(f"  [{finding.get('severity')}] {finding.get('detail')}")

        vol = data.get("volume_by_muscle", {})
        if vol:
            lines.append("")
            lines.append("Volume by muscle (working sets, secondary at 0.5):")
            for muscle, row in list(vol.items())[:8]:
                target = row.get("target") or 0
                pct = row.get("pct_of_target", 0)
                bar = _meter(pct)
                suffix = f" / {target} target" if target else ""
                lines.append(
                    f"  {muscle:<14} {row['sets']:>5} sets{bar}{suffix}"
                )

        plateaus = data.get("plateaus", [])
        if plateaus:
            lines.append("")
            lines.append("Plateaus:")
            for p in plateaus[:4]:
                lines.append(
                    f"  {p['name']}: flat across {p['sessions']} sessions "
                    f"(e1RM {_num(p['e1rm'])}kg since {p['since']})"
                )
                lines.append(f"    {p['hint']}")

        return "\n".join(lines)

    def describe_today(self, data: Dict[str, Any]) -> str:
        lines = [f"{data.get('date')} ({data.get('weekday')})"]
        if data.get("rest"):
            lines.append("")
            lines.append(self.msg("rest_day") or "Rest day.")
            return "\n".join(lines)
        if not data.get("program"):
            lines.append("")
            lines.append(self.msg("no_program") or "No programme yet.")
            return "\n".join(lines)

        lines.append(f"{data.get('session')} — {data.get('program')}")
        if data.get("focus"):
            lines.append("Focus: " + ", ".join(data["focus"]))
        lines.append("")
        for ex in data.get("exercises", []):
            lines.append("  " + self.describe_prescription(ex.get("advice", {})))
            lines.append("")
        if data.get("logged"):
            lines.append(
                f"  Already logged today: {len(data['logged'])} session(s)."
            )
        return "\n".join(lines).rstrip()

    def describe_program(self, program: Any) -> str:
        d = program.to_dict() if hasattr(program, "to_dict") else dict(program)
        lines = [
            f"{d.get('name')}",
            "=" * len(str(d.get("name", ""))),
            f"Split: {d.get('split')} · {d.get('days_per_week')} days/week · "
            f"{d.get('weeks')} week block · goal: {d.get('goal')}",
            "",
        ]
        for sess in d.get("sessions", []):
            lines.append(f"{sess.get('title')} ({sess.get('day')})")
            for pe in sess.get("exercises", []):
                from .exercises import EXERCISES

                ex = EXERCISES.get(pe["exercise_id"])
                nm = ex.name if ex else pe["exercise_id"]
                load = (
                    "bodyweight"
                    if pe.get("start_weight", 0) <= 0
                    else f"{_num(pe['start_weight'])}kg"
                )
                lines.append(
                    f"  {nm:<28} {pe['sets']}x{pe['rep_low']}-{pe['rep_high']} "
                    f"@ {load}, rest {pe['rest_sec']}s"
                )
            lines.append("")
        for note in d.get("notes", []):
            lines.append(f"• {note}")
        lines.append("")
        lines.append(f"⚠  {self.disclaimer}")
        return "\n".join(lines)

    def describe_projection(self, weeks: List[Dict[str, Any]]) -> str:
        lines = ["Projected overload (double progression applied to compounds)", ""]
        for week in weeks:
            lines.append(f"Week {week['week']}")
            for sess in week.get("sessions", []):
                lifts = sess.get("lifts", [])
                if not lifts:
                    continue
                parts = [
                    f"{l['name'].split()[-1].lower()} {_num(l['weight'])}kg x {l['reps']}"
                    for l in lifts[:4]
                ]
                lines.append(f"  {sess['session']:<12} " + ", ".join(parts))
            lines.append("")
        lines.append(
            "This is the trajectory if every session is completed as prescribed — "
            "missed sessions push it out, they do not break it."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def _kg(value: Any) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "0kg"
    if v == 0:
        return "bodyweight"
    return f"{int(v)}kg" if v == int(v) else f"{v:g}kg"


def _num(value: Any) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.1f}"


def _signed(value: Any) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{v:+.1f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):+d}"


def _meter(pct: float, width: int = 12) -> str:
    """A tiny ASCII meter so set counts read at a glance."""
    filled = max(0, min(width, int(round(pct / 100.0 * width))))
    return "  [" + "#" * filled + "." * (width - filled) + f"] {int(round(pct))}%"
