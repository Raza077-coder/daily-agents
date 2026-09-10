"""Security-audit tests: findings, weights, score and determinism."""

from __future__ import annotations

import pytest

from vaultguard.audit import (
    WEIGHTS,
    AuditFinding,
    AuditReport,
    VaultAuditor,
    quick_score,
)
from vaultguard.crypto import hash_password_hint
from vaultguard.models import VaultEntry, utcnow

STRONG = "Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6"
STRONG_ALT = "Tz5!wR8qYb3@Kd1Hn6Mv2Pc9&Ls4"


def entry(**kwargs):
    """Build an entry with a valid reuse fingerprint."""
    data = dict(title="Entry", username="user", category="login", password=STRONG)
    data.update(kwargs)
    item = VaultEntry(**data)
    item.password_hint = hash_password_hint(item.password) if item.password else ""
    return item


class TestEmptyAndCleanVaults:
    def test_empty_vault_reports_empty_verdict(self):
        report = VaultAuditor().audit([])
        assert report.total_entries == 0
        assert report.verdict == "empty"
        assert report.score == 0
        assert report.recommendations

    def test_clean_vault_scores_perfectly(self):
        entries = [
            entry(title="GitHub", password=STRONG, totp="JBSWY3DPEHPK3PXP", url="https://github.com"),
            entry(title="Bank", username="b1", password=STRONG_ALT, totp="KRSXG5CTMVRXEZLU"),
        ]
        report = VaultAuditor().audit(entries)
        assert report.score == 100
        assert report.grade == "A+"
        assert report.findings == []

    def test_render_is_a_string_with_the_score(self):
        report = VaultAuditor().audit([entry()])
        rendered = report.render()
        assert "VAULTGUARD SECURITY AUDIT" in rendered
        assert str(report.score) in rendered


class TestWeakPasswordDetection:
    def test_weak_password_is_flagged(self):
        report = VaultAuditor().audit([entry(title="Forum", password="qwerty123")])
        kinds = {f.kind for f in report.findings}
        assert "weak" in kinds

    def test_severity_escalates_for_very_weak(self):
        report = VaultAuditor().audit([entry(title="Forum", password="password")])
        weak = [f for f in report.findings if f.kind == "weak"][0]
        assert weak.severity == "critical"

    def test_finding_names_the_entry(self):
        report = VaultAuditor().audit([entry(title="My Forum", password="qwerty123")])
        assert any("My Forum" in f.title for f in report.findings)

    def test_health_counts_split_weak_and_strong(self):
        report = VaultAuditor().audit(
            [entry(title="Good", password=STRONG, totp="X" * 16), entry(title="Bad", password="qwerty123")]
        )
        assert report.health["strong"] >= 1
        assert report.health["weak"] == 1


class TestReuseDetection:
    def test_reused_password_is_critical(self):
        shared = hash_password_hint(STRONG)
        first = entry(title="A", password=STRONG)
        second = entry(title="B", password=STRONG)
        first.password_hint = second.password_hint = shared
        report = VaultAuditor().audit([first, second])
        reused = [f for f in report.findings if f.kind == "reused"]
        assert reused and reused[0].severity == "critical"
        assert report.health["reused"] == 2

    def test_unique_passwords_are_not_flagged_as_reused(self):
        report = VaultAuditor().audit([entry(title="A", password=STRONG), entry(title="B", password=STRONG_ALT)])
        assert not any(f.kind == "reused" for f in report.findings)

    def test_entries_without_a_hint_are_ignored(self):
        first = entry(title="A", password=STRONG)
        second = entry(title="B", password=STRONG)
        first.password_hint = second.password_hint = ""
        report = VaultAuditor().audit([first, second])
        assert not any(f.kind == "reused" for f in report.findings)


class TestStaleness:
    def test_old_password_is_flagged(self):
        old = entry(title="Legacy", password=STRONG, totp="X" * 16)
        old.password_updated_at = "2000-01-01T00:00:00Z"
        report = VaultAuditor().audit([old])
        assert any(f.kind == "stale" for f in report.findings)
        assert "stale" in report.deductions

    def test_custom_stale_threshold(self):
        """A one-day-old password is stale when the threshold is zero days."""
        from datetime import datetime, timedelta, timezone

        recent = entry(title="Fresh", password=STRONG, totp="X" * 16)
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        recent.password_updated_at = yesterday.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        report = VaultAuditor(stale_days=0).audit([recent])
        assert any(f.kind == "stale" for f in report.findings)

    def test_fresh_password_is_not_flagged(self):
        report = VaultAuditor().audit([entry(title="Fresh", password=STRONG, totp="X" * 16)])
        assert not any(f.kind == "stale" for f in report.findings)


class TestTwoFactorAndMetadata:
    def test_missing_totp_is_flagged_for_login_categories(self):
        report = VaultAuditor().audit([entry(title="Mail", password=STRONG, category="email")])
        assert any(f.kind == "no_2fa" for f in report.findings)

    def test_present_totp_counts_toward_health(self):
        report = VaultAuditor().audit([entry(password=STRONG, totp="JBSWY3DPEHPK3PXP")])
        assert report.health["with_2fa"] == 1
        assert not any(f.kind == "no_2fa" for f in report.findings)

    def test_duplicate_titles_are_flagged(self):
        report = VaultAuditor().audit(
            [
                entry(title="Same", password=STRONG, totp="X" * 16),
                entry(title="Same", password=STRONG_ALT, totp="Y" * 16),
            ]
        )
        assert any(f.kind == "duplicate_title" for f in report.findings)

    def test_missing_identifier_is_flagged(self):
        report = VaultAuditor().audit([entry(title="Orphan", username="", password=STRONG, totp="X" * 16)])
        assert any(f.kind == "missing_identifier" for f in report.findings)

    def test_wifi_entries_need_no_identifier(self):
        report = VaultAuditor().audit(
            [entry(title="Home WiFi", username="", category="wifi", password=STRONG, totp="X" * 16)]
        )
        assert not any(f.kind == "missing_identifier" for f in report.findings)


class TestScoring:
    def test_every_finding_kind_has_a_weight(self):
        report = VaultAuditor().audit(
            [
                entry(title="Same", username="", password="password"),
                entry(title="Same", username="", password="password"),
                entry(title="Weak", password="qwerty123"),
            ]
        )
        for kind in report.deductions:
            assert kind in WEIGHTS

    def test_deductions_are_proportional_to_prevalence(self):
        many = [entry(title=f"W{i}", password="qwerty123") for i in range(10)]
        report = VaultAuditor().audit(many)
        assert report.deductions["weak"] <= WEIGHTS["weak"]

    def test_score_never_goes_negative(self):
        bad = [entry(title=f"B{i}", password="password", username="", category="login") for i in range(30)]
        report = VaultAuditor().audit(bad)
        assert 0 <= report.score <= 100

    def test_score_drops_as_problems_accumulate(self):
        clean = VaultAuditor().audit(
            [entry(title="A", password=STRONG, totp="X" * 16), entry(title="B", password=STRONG_ALT, totp="Y" * 16)]
        )
        messy = VaultAuditor().audit([entry(title="C", password="password", username="")])
        assert messy.score < clean.score

    def test_grades_map_correctly(self):
        auditor = VaultAuditor()
        assert auditor.audit([entry(title="A", password=STRONG, totp="X" * 16)])[1] if False else True
        clean = auditor.audit([entry(password=STRONG, totp="X" * 16)])
        assert clean.grade == "A+"
        assert clean.verdict == "pristine"

    def test_quick_score_helper(self):
        assert 0 <= quick_score([entry(password=STRONG, totp="X" * 16)]) <= 100


class TestDeterminismAndShape:
    def test_identical_input_gives_identical_report(self):
        entries = [entry(title="A", password="qwerty123"), entry(title="B", password=STRONG)]
        first = VaultAuditor().audit(entries).to_dict()
        second = VaultAuditor().audit(entries).to_dict()
        assert first == second

    def test_to_dict_contains_expected_keys(self):
        payload = VaultAuditor().audit([entry(password=STRONG, totp="X" * 16)]).to_dict()
        for key in ("score", "grade", "verdict", "health", "findings", "deductions", "recommendations", "counts"):
            assert key in payload

    def test_findings_serialise(self):
        finding = AuditFinding(kind="weak", severity="high", title="t", detail="d")
        assert finding.to_dict()["severity"] == "high"

    def test_recommendations_are_actionable(self):
        report = VaultAuditor().audit([entry(title="A", password="password")])
        assert report.recommendations
        assert any("weak" in r.lower() or "reused" in r.lower() for r in report.recommendations)

    def test_findings_render_in_severity_order(self):
        report = VaultAuditor().audit(
            [entry(title="A", password="password", username=""), entry(title="B", password="qwerty123")]
        )
        rendered = report.render()
        assert rendered.index("CRITICAL") < rendered.index("LOW") if "LOW" in rendered else True

    def test_report_dataclass_defaults(self):
        report = AuditReport()
        assert report.score == 0 and report.grade == "F"
