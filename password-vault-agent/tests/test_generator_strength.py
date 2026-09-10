"""Generator and strength-estimator tests."""

from __future__ import annotations

import random
import string

import pytest

from vaultguard.generator import (
    ABSOLUTE_MIN_LENGTH,
    DEFAULT_WORDLIST,
    GeneratorError,
    generate_passphrase,
    generate_password,
    generate_pin,
    suggest_for,
)
from vaultguard.strength import (
    COMMON_PASSWORDS,
    character_pool,
    estimate_strength,
    humanise_duration,
    score_password,
)


class TestPasswordGeneration:
    def test_default_length(self):
        assert len(generate_password()) == 20

    def test_respects_requested_length(self):
        for length in (8, 12, 24, 64):
            assert len(generate_password(length)) == length

    def test_rejects_too_short(self):
        with pytest.raises(GeneratorError):
            generate_password(ABSOLUTE_MIN_LENGTH - 1)

    def test_includes_every_selected_class(self):
        for _ in range(25):
            password = generate_password(16)
            assert any(c.islower() for c in password)
            assert any(c.isupper() for c in password)
            assert any(c.isdigit() for c in password)
            assert any(c in "!@#$%^&*()-_=+[]{};:,.?/" for c in password)

    def test_single_class_pool_is_honoured(self):
        password = generate_password(12, use_upper=False, use_digits=False, use_symbols=False)
        assert set(password) <= set(string.ascii_lowercase)

    def test_symbols_can_be_disabled(self):
        password = generate_password(20, use_symbols=False)
        assert not any(c in "!@#$%^&*" for c in password)

    def test_ambiguous_characters_excluded(self):
        from vaultguard.generator import AMBIGUOUS

        password = generate_password(30, avoid_ambiguous=True)
        assert not (set(password) & AMBIGUOUS)

    def test_exclude_list_is_respected(self):
        password = generate_password(30, exclude="abcdefghijklmnopqrstuvwxyz")
        assert not any(c.islower() for c in password)

    def test_single_excluded_vowel_only_affects_its_own_case(self):
        password = generate_password(40, exclude="aA")
        assert "a" not in password and "A" not in password

    def test_impossible_class_combination_raises(self):
        with pytest.raises(GeneratorError):
            generate_password(2, require_each_class=True)

    def test_all_classes_disabled_raises(self):
        with pytest.raises(GeneratorError):
            generate_password(
                20, use_lower=False, use_upper=False, use_digits=False, use_symbols=False
            )

    def test_seeded_rng_is_reproducible(self):
        first = generate_password(24, rng=random.Random(42))
        second = generate_password(24, rng=random.Random(42))
        assert first == second

    def test_outputs_differ_without_a_seed(self):
        assert len({generate_password(24) for _ in range(20)}) == 20

    def test_suggest_for_matches_password_generator(self):
        assert len(suggest_for(18)) == 18


class TestPassphraseGeneration:
    def test_default_word_count(self):
        assert len(generate_passphrase().split("-")) == 4

    def test_custom_word_count(self):
        assert len(generate_passphrase(6).split("-")) == 6

    def test_rejects_too_few_words(self):
        with pytest.raises(GeneratorError):
            generate_passphrase(2)

    def test_custom_separator(self):
        phrase = generate_passphrase(4, separator=".")
        assert phrase.count(".") == 3

    def test_capitalisation_option(self):
        phrase = generate_passphrase(4, capitalize=True)
        assert all(word[0].isupper() for word in phrase.split("-"))

    def test_add_number_appends_suffix(self):
        phrase = generate_passphrase(3, add_number=True)
        assert phrase.split("-")[-1].isdigit()

    def test_words_come_from_the_supplied_list(self):
        pool = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
        phrase = generate_passphrase(4, wordlist=pool)
        assert all(word in pool for word in phrase.split("-"))

    def test_small_wordlist_rejected(self):
        with pytest.raises(GeneratorError):
            generate_passphrase(3, wordlist=["a", "b"])

    def test_default_wordlist_has_no_duplicates(self):
        assert len(DEFAULT_WORDLIST) == len(set(DEFAULT_WORDLIST))

    def test_seeded_rng_is_reproducible(self):
        first = generate_passphrase(5, rng=random.Random(7))
        second = generate_passphrase(5, rng=random.Random(7))
        assert first == second


class TestPinGeneration:
    def test_default_length(self):
        assert len(generate_pin()) == 6

    def test_numeric_only(self):
        assert generate_pin(8).isdigit()

    def test_rejects_short_pin(self):
        with pytest.raises(GeneratorError):
            generate_pin(3)

    def test_rejects_excessive_length(self):
        with pytest.raises(GeneratorError):
            generate_pin(20)

    def test_seeded_rng_is_reproducible(self):
        assert generate_pin(6, rng=random.Random(3)) == generate_pin(6, rng=random.Random(3))


class TestCharacterPool:
    def test_detects_all_classes(self):
        pool, classes = character_pool("aB3!")
        assert pool == 26 + 26 + 10 + len("!@#$%^&*()-_=+[]{};:,.?/\\|~`'\"<>")
        assert set(classes) == {"lowercase", "uppercase", "digits", "symbols"}

    def test_lowercase_only(self):
        pool, classes = character_pool("abcdef")
        assert pool == 26
        assert classes == ["lowercase"]

    def test_empty_password_yields_minimum_pool(self):
        pool, classes = character_pool("")
        assert pool == 1
        assert classes == []


class TestStrengthEstimation:
    def test_empty_password_scores_zero(self):
        report = estimate_strength("")
        assert report.score == 0
        assert report.grade == "F"
        assert report.label == "empty"

    def test_common_password_is_flagged_and_weak(self):
        report = estimate_strength("password")
        assert report.is_common
        assert report.score < 30
        assert any(p["reason"] == "dictionary word" for p in report.penalties)

    def test_long_random_password_scores_high(self):
        report = estimate_strength("Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6")
        assert report.score >= 80
        assert report.grade in ("A", "A+")

    def test_repetition_is_penalised(self):
        repeated = estimate_strength("aaaaaaaaaaaaaaaa")
        varied = estimate_strength("aQ7#mZ2$kL9!pR4v")
        assert repeated.score < varied.score
        assert any(p["reason"] == "repeated characters" for p in repeated.penalties)

    def test_sequence_is_penalised(self):
        report = estimate_strength("abcdefghijkl")
        assert any(p["reason"] == "predictable sequence" for p in report.penalties)

    def test_keyboard_walk_is_penalised(self):
        report = estimate_strength("qwertyuiop")
        assert any(p["reason"] == "predictable sequence" for p in report.penalties)

    def test_date_fragment_is_penalised(self):
        report = estimate_strength("Summer2024!")
        assert any(p["reason"] == "date-like fragment" for p in report.penalties)

    def test_reuse_halves_the_score(self):
        fresh = estimate_strength("Xk9#mQ2vLp7$Zw4Rt8Nb", reused=False)
        reused = estimate_strength("Xk9#mQ2vLp7$Zw4Rt8Nb", reused=True)
        assert reused.score < fresh.score
        assert reused.reused is True

    def test_stale_password_is_penalised_and_suggests_rotation(self):
        report = estimate_strength("Xk9#mQ2vLp7$Zw4Rt8Nb", age_days=400)
        assert report.age_days == 400
        assert any("Rotate" in s for s in report.suggestions)

    def test_suggestions_are_always_populated(self):
        assert estimate_strength("").suggestions
        assert estimate_strength("Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6").suggestions

    def test_deterministic_for_identical_input(self):
        first = estimate_strength("Test1234!", age_days=10, reused=True).to_dict()
        second = estimate_strength("Test1234!", age_days=10, reused=True).to_dict()
        assert first == second

    def test_crack_time_ordering(self):
        weak = estimate_strength("abc123")
        strong = estimate_strength("Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6")
        assert strong.crack_time_seconds > weak.crack_time_seconds

    def test_linear_score_accessor(self):
        assert score_password("password") < score_password("Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6")

    def test_score_is_clamped_to_100(self):
        assert estimate_strength("X" * 200 + "!" * 50 + "9" * 50).score <= 100

    def test_to_dict_shape(self):
        payload = estimate_strength("Test1234!").to_dict()
        for key in ("score", "grade", "label", "entropy_bits", "penalties", "suggestions", "crack_time_human"):
            assert key in payload

    def test_all_common_passwords_are_flagged(self):
        for candidate in COMMON_PASSWORDS:
            assert estimate_strength(candidate).is_common, candidate


class TestHumaniseDuration:
    def test_sub_second_is_instant(self):
        assert humanise_duration(0.4) == "instantly"

    def test_seconds(self):
        assert "second" in humanise_duration(5)

    def test_minutes(self):
        assert "minute" in humanise_duration(120)

    def test_hours(self):
        assert "hour" in humanise_duration(3600 * 3)

    def test_days(self):
        assert "day" in humanise_duration(86400 * 5)

    def test_years(self):
        assert "year" in humanise_duration(86400 * 365 * 5)

    def test_centuries(self):
        assert "centur" in humanise_duration(86400 * 365 * 100 * 3)
