"""Pytest suite for HABITOS — Habit Tracker Agent."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from habit_tracker.analytics import (  # noqa: E402
    FREQ_LABELS, compute_streaks, normalize_target, status_for, week_stats,
)
from habit_tracker.engine import HabitEngine  # noqa: E402
from habit_tracker.storage import (  # noqa: E402
    dehydrate, hydrate, iso_week_bounds, load_data, save_data, slugify,
)


@pytest.fixture()
def eng():
    """Fresh in-memory engine per test."""
    return HabitEngine(data_path="")


@pytest.fixture()
def tmpfile_eng(tmp_path):
    return HabitEngine(data_path=str(tmp_path / "data.json"))


# --------------------------------------------------------------------------- #
# slugify / storage basics
# --------------------------------------------------------------------------- #
class TestSlugify:
    def test_basic(self):
        assert slugify("Read 20 pages") == "read-20-pages"

    def test_special_chars(self):
        assert slugify("Drink Water!!!") == "drink-water"

    def test_empty(self):
        assert slugify("") == "habit"

    def test_unicode(self):
        assert slugify("Méditer") == "méditer" or slugify("Méditer") != ""


class TestIsoWeek:
    def test_monday(self):
        d = dt.date(2026, 9, 7)  # a Monday
        assert iso_week_bounds(d) == d

    def test_sunday(self):
        d = dt.date(2026, 9, 13)  # Sunday
        assert iso_week_bounds(d).isoformat() == "2026-09-07"

    def test_midweek(self):
        d = dt.date(2026, 9, 9)
        assert iso_week_bounds(d).isoformat() == "2026-09-07"


class TestPersistence:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "x.json"
        eng = HabitEngine(data_path=str(p))
        eng.add_habit("Meditate", category="mindfulness", target_per_week=7)
        eng.log("meditate")
        eng2 = HabitEngine(data_path=str(p))
        assert "meditate" in eng2.store.habits
        assert len(eng2.store.logs) == 1

    def test_load_missing_returns_empty(self, tmp_path):
        data = load_data(str(tmp_path / "nope.json"))
        assert data["habits"] == []

    def test_hydrate_dehydrate(self, eng):
        eng.add_habit("Read", category="learning", target_per_week=5)
        eng.log("read")
        blob = dehydrate(eng.store)
        store = hydrate(blob)
        assert list(store.habits) == ["read"]
        assert len(store.logs) == 1

    def test_save_then_load_file(self, tmp_path):
        p = str(tmp_path / "d.json")
        eng = HabitEngine(data_path=p)
        eng.add_habit("Workout", category="fitness")
        eng.log("workout")
        raw = json.loads(Path(p).read_text())
        assert raw["habits"][0]["name"] == "Workout"


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
class TestStreaks:
    def test_current_streak(self):
        today = dt.date(2026, 9, 9)
        dates = [(today - dt.timedelta(days=i)).isoformat() for i in range(5)]
        res = compute_streaks(dates, today)
        assert res["current"] == 5
        assert res["longest"] == 5

    def test_streak_with_today_pending(self):
        today = dt.date(2026, 9, 9)
        dates = [(today - dt.timedelta(days=i)).isoformat() for i in range(1, 5)]
        res = compute_streaks(dates, today)
        # run of 4 ending yesterday still counts as current (today pending)
        assert res["current"] == 4

    def test_streak_broken(self):
        today = dt.date(2026, 9, 9)
        dates = {(today - dt.timedelta(days=3)).isoformat(),
                 (today - dt.timedelta(days=2)).isoformat(),
                 (today - dt.timedelta(days=0)).isoformat()}
        res = compute_streaks(dates, today)
        assert res["current"] == 1
        assert res["longest"] == 2

    def test_longest_over_current(self):
        today = dt.date(2026, 9, 9)
        dates = [(today - dt.timedelta(days=10 + i)).isoformat() for i in range(6)]
        dates.append(today.isoformat())
        res = compute_streaks(dates, today)
        assert res["current"] == 1
        assert res["longest"] == 6

    def test_empty(self):
        res = compute_streaks([], dt.date(2026, 9, 9))
        assert res == {"current": 0, "longest": 0}


class TestWeekStats:
    def test_full(self):
        today = dt.date(2026, 9, 9)  # Wed
        monday = iso_week_bounds(today)
        dates = [(monday + dt.timedelta(days=i)).isoformat() for i in range(3)]
        res = week_stats(dates, 7, today)
        assert res["week_done"] == 3
        assert res["week_possible"] == 3
        assert res["completion_pct"] == 100.0

    def test_partial(self):
        today = dt.date(2026, 9, 9)
        dates = [(iso_week_bounds(today)).isoformat()]
        res = week_stats(dates, 5, today)
        assert res["week_done"] == 1
        assert res["week_possible"] == 3
        assert res["completion_pct"] == pytest.approx(33.3, abs=0.2)

    def test_none(self):
        today = dt.date(2026, 9, 9)
        res = week_stats([], 5, today)
        assert res["week_done"] == 0
        assert res["completion_pct"] == 0.0


class TestNormalizeTarget:
    def test_labels(self):
        assert normalize_target(7, "daily") == 7
        assert normalize_target(0, "weekdays") == 5
        assert normalize_target(0, "weekly") == 1
        assert normalize_target(0, "weekends") == 2

    def test_int(self):
        assert normalize_target(3) == 3
        assert normalize_target(9) == 7  # clamped

    def test_default(self):
        assert normalize_target(0) == 5


class TestStatus:
    def test_on_track(self):
        assert status_for(90, 6, 7) == "on_track"

    def test_at_risk(self):
        assert status_for(55, 2, 5) == "at_risk"

    def test_off_track(self):
        assert status_for(20, 1, 5) == "off_track"


# --------------------------------------------------------------------------- #
# Engine CRUD
# --------------------------------------------------------------------------- #
class TestEngineCrud:
    def test_add_habit(self, eng):
        h = eng.add_habit("Meditate", category="mindfulness", frequency="daily")
        assert h.habit_id == "meditate"
        assert h.target_per_week == 7
        assert h.category == "mindfulness"
        assert h.color

    def test_add_duplicate_names_unique_ids(self, eng):
        eng.add_habit("Read")
        h2 = eng.add_habit("Read")
        assert h2.habit_id != "read"
        assert len(eng.store.habits) == 2

    def test_add_requires_name(self, eng):
        with pytest.raises(ValueError):
            eng.add_habit("   ")

    def test_invalid_category_falls_back(self, eng):
        h = eng.add_habit("X", category="bogus")
        assert h.category == "general"

    def test_get_by_name(self, eng):
        eng.add_habit("Drink Water")
        assert eng.get_habit("drink-water") is not None
        assert eng.get_habit("Drink Water") is not None

    def test_delete(self, eng):
        eng.add_habit("X")
        eng.log("x")
        assert eng.delete_habit("x") is True
        assert eng.delete_habit("x") is False
        assert eng.store.logs == []

    def test_archive(self, eng):
        h = eng.add_habit("X")
        eng.log("x")
        assert eng.archive_habit("x") is not None
        assert eng.get_habit("x").archived is True
        active = eng.list_habits(include_archived=False)
        assert all(not x.archived for x in active)


class TestLogging:
    def test_log_today(self, eng):
        eng.add_habit("Meditate")
        e = eng.log("meditate")
        assert e.date == dt.date.today().isoformat()
        assert eng.done_on("meditate", e.date)

    def test_log_idempotent(self, eng):
        eng.add_habit("Meditate")
        eng.log("meditate")
        eng.log("meditate")
        assert len([l for l in eng.store.logs if l.habit_id == "meditate"]) == 1

    def test_log_backfill(self, eng):
        eng.add_habit("Read")
        eng.log("read", date="2026-09-01")
        assert eng.done_on("read", "2026-09-01")

    def test_log_unknown(self, eng):
        with pytest.raises(KeyError):
            eng.log("nope")

    def test_log_bad_date(self, eng):
        eng.add_habit("Read")
        with pytest.raises(ValueError):
            eng.log("read", date="01/09/2026")

    def test_unlog(self, eng):
        eng.add_habit("Read")
        eng.log("read")
        assert eng.unlog("read") is True
        assert eng.unlog("read") is False


# --------------------------------------------------------------------------- #
# Metrics + summary
# --------------------------------------------------------------------------- #
class TestMetrics:
    def test_metrics_refresh(self, eng):
        eng.add_habit("Meditate", target_per_week=7)
        for i in range(3):
            d = dt.date.today() - dt.timedelta(days=i)
            eng.log("meditate", date=d.isoformat())
        h = eng.get_habit("meditate")
        eng.refresh_metrics([h])
        assert h.total_done >= 3
        assert h.current_streak >= 1
        assert h.week_done >= 1
        assert h.completion_pct > 0

    def test_summary_shape(self, eng):
        eng.add_habit("A", frequency="daily")
        s = eng.summary()
        assert set(s) >= {"active_habits", "total_logs", "on_track", "at_risk", "off_track"}

    def test_habit_detail(self, eng):
        eng.add_habit("Meditate")
        eng.log("meditate")
        d = eng.habit_detail("meditate")
        assert d is not None
        assert len(d["last_7_days"]) == 7
        assert d["status"] in {"on_track", "at_risk", "off_track"}


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
class TestReports:
    def test_text_report(self, eng):
        eng.add_habit("Meditate")
        eng.log("meditate")
        r = eng.weekly_report(format="text")
        assert "HABITOS Weekly Report" in r
        assert "Meditate" in r

    def test_json_report(self, eng):
        eng.add_habit("Meditate")
        eng.log("meditate")
        r = eng.weekly_report(format="json")
        assert "summary" in r and "habits" in r

    def test_trend(self, eng):
        eng.add_habit("Read", frequency="daily")
        eng.log("read")
        t = eng.habit_trend("read", weeks=4)
        assert len(t["buckets"]) == 4
        assert t["buckets"][-1]["count"] >= 1


# --------------------------------------------------------------------------- #
# Demo dataset
# --------------------------------------------------------------------------- #
class TestDemo:
    def test_demo_seeds(self, eng):
        from habit_tracker.cli import cmd_demo

        class A:
            data = ""

        cmd_demo(eng, A())
        assert len(eng.store.habits) == 4
        assert eng.summary()["total_logs"] > 20


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
class TestCli:
    def setup_method(self):
        fd, self.data_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def _run(self, argv):
        from habit_tracker.cli import main
        return main(["--data", self.data_path] + argv)

    def test_add_and_list(self):
        assert self._run(["add", "Meditate", "--frequency", "daily"]) == 0
        assert self._run(["list"]) == 0

    def test_log(self):
        self._run(["add", "Read", "--target", "5"])
        assert self._run(["log", "read"]) == 0

    def test_log_unknown_fails(self):
        assert self._run(["log", "nope"]) == 1

    def test_report(self):
        self._run(["add", "Workout"])
        self._run(["log", "workout"])
        assert self._run(["report"]) == 0

    def test_report_json(self):
        self._run(["add", "Workout"])
        assert self._run(["report", "--json"]) == 0

    def test_delete(self):
        self._run(["add", "Temp"])
        assert self._run(["delete", "temp"]) == 0

    def test_archive(self):
        self._run(["add", "Temp2"])
        assert self._run(["archive", "temp2"]) == 0


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #
class TestApi:
    def _client(self):
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
        import api.index as mod  # type: ignore
        # point at a fresh temp file so tests are isolated
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.environ["HABITOS_DATA"] = path
        return TestClient(mod.app)

    def test_health(self):
        c = self._client()
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_routes(self):
        c = self._client()
        assert c.get("/routes").status_code == 200

    def test_empty_list(self):
        c = self._client()
        assert c.get("/habits").json()["habits"] == []

    def test_create_and_log(self):
        c = self._client()
        r = c.post("/habits", json={"name": "Meditate", "frequency": "daily"})
        assert r.status_code == 200
        hid = r.json()["habit_id"]
        r2 = c.post(f"/habits/{hid}/log", json={})
        assert r2.status_code == 200
        assert r2.json()["logged"]["habit_id"] == hid

    def test_habit_404(self):
        c = self._client()
        assert c.get("/habits/nope").status_code == 404
        assert c.post("/habits/nope/log", json={}).status_code == 404

    def test_report_endpoints(self):
        c = self._client()
        assert c.get("/report").status_code == 200
        assert c.get("/report/json").status_code == 200

    def test_demo(self):
        c = self._client()
        r = c.post("/demo")
        assert r.status_code == 200
        assert len(r.json()["habits"]) == 4

    def test_bad_create(self):
        c = self._client()
        assert c.post("/habits", json={"name": ""}).status_code == 400


class TestDeterminism:
    def test_same_input_same_output(self):
        e1 = HabitEngine(data_path="")
        e2 = HabitEngine(data_path="")
        for eng in (e1, e2):
            eng.add_habit("Meditate", frequency="daily")
            eng.log("meditate")
        assert e1.weekly_report(format="json") == e2.weekly_report(format="json")
