#!/usr/bin/env python3
"""Generate sample_habits.json — a deterministic demo dataset.

Run: python make_sample_data.py
"""
import datetime as dt
import json
import random
import sys

OUT = "data/sample_habits.json"
rng = random.Random(7)
today = dt.date.today()


def main() -> int:
    habits = [
        {"habit_id": "meditate", "name": "Meditate", "category": "mindfulness",
         "target_per_week": 7, "color": "#ffab40", "created": (today - dt.timedelta(days=40)).isoformat(), "archived": False},
        {"habit_id": "read-20-pages", "name": "Read 20 pages", "category": "learning",
         "target_per_week": 5, "color": "#00e676", "created": (today - dt.timedelta(days=40)).isoformat(), "archived": False},
        {"habit_id": "workout", "name": "Workout", "category": "fitness",
         "target_per_week": 4, "color": "#ff5252", "created": (today - dt.timedelta(days=30)).isoformat(), "archived": False},
        {"habit_id": "drink-water", "name": "Drink water", "category": "health",
         "target_per_week": 7, "color": "#00e5ff", "created": (today - dt.timedelta(days=14)).isoformat(), "archived": False},
    ]
    logs = []
    profile = {"meditate": 0.95, "read-20-pages": 0.8, "workout": 0.6, "drink-water": 0.9}
    for h in habits:
        hid = h["habit_id"]
        created = dt.date.fromisoformat(h["created"])
        for back in range((today - created).days, -1, -1):
            d = today - dt.timedelta(days=back)
            if hid == "workout" and d.weekday() >= 5 and rng.random() < 0.65:
                continue
            if rng.random() < profile[hid]:
                logs.append({"habit_id": hid, "date": d.isoformat(), "note": ""})
    data = {"habits": habits, "logs": logs}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"wrote {OUT}: {len(habits)} habits, {len(logs)} logs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
