"""Action and vault tests.

The vault is the only place VEIL stores the data it is meant to remove, so it
gets the strictest treatment here: permissions, atomicity, and a refusal to load
anything that is not a vault.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from veil import demo
from veil.actions import (
    HASH_LENGTH,
    MIN_PARTIAL_LENGTH,
    Tokenizer,
    apply_action,
    hash_value,
    mask,
)
from veil.errors import ConfigError, VeilError, VaultError
from veil.models import ALL_ENTITIES
from veil.vault import (
    VAULT_FORMAT,
    Vault,
    default_vault_path,
    merge,
)


class TestMask:
    def test_keeps_prefix_and_suffix(self):
        value = "alice@example.com"
        result = mask(value, "EMAIL", 2, 4)
        assert result.startswith("al")
        assert result.endswith(".com")
        # 17 chars - 2 kept - 4 kept = 11 hidden
        assert result == "al" + "\u2022" * 11 + ".com"
        assert len(result) == len(value)

    def test_short_value_is_fully_masked(self):
        # Below MIN_PARTIAL_LENGTH, keeping characters would expose most of it.
        result = mask("abc123", "SECRET", 0, 4)
        assert result == "\u2022" * 6

    def test_all_mask_char_when_hidden_run_too_short(self):
        value = "abcdefgh"
        result = mask(value, "X", 3, 3)
        assert result == "\u2022" * len(value)

    def test_custom_mask_char(self):
        result = mask("alice@example.com", "EMAIL", 2, 4, mask_char="*")
        assert result.startswith("al")
        assert "*" in result
        assert result.endswith(".com")

    def test_never_returns_the_original(self):
        for value in ["a@b.co", "abcdefghij", "1234567890"]:
            assert mask(value, "X", 0, 4) != value

    def test_min_partial_length_is_respected(self):
        assert MIN_PARTIAL_LENGTH >= 8


class TestHashAction:
    def test_equal_values_hash_equally(self):
        assert hash_value("a@b.co", "salt") == hash_value("a@b.co", "salt")

    def test_different_values_differ(self):
        assert hash_value("a@b.co", "salt") != hash_value("c@d.co", "salt")

    def test_salt_changes_the_digest(self):
        assert hash_value("a@b.co", "one") != hash_value("a@b.co", "two")

    def test_unsalted_still_works(self):
        assert len(hash_value("a@b.co")) == HASH_LENGTH

    def test_replacement_shape(self):
        from veil.actions import hash_replacement
        result = hash_replacement("a@b.co", "EMAIL", "s")
        assert result.startswith("[EMAIL:")
        assert result.endswith("]")


class TestTokenizer:
    def test_numbers_per_entity_from_one(self):
        tokenizer = Tokenizer("VEIL")
        assert tokenizer.token_for("EMAIL", "a@b.co") == "VEIL_EMAIL_001"
        assert tokenizer.token_for("EMAIL", "c@d.co") == "VEIL_EMAIL_002"
        assert tokenizer.token_for("PHONE", "+1234") == "VEIL_PHONE_001"

    def test_same_value_reuses_its_token(self):
        tokenizer = Tokenizer("VEIL")
        first = tokenizer.token_for("EMAIL", "a@b.co")
        assert tokenizer.token_for("EMAIL", "a@b.co") == first
        assert len(tokenizer.mapping) == 1

    def test_reset_clears_state(self):
        tokenizer = Tokenizer()
        tokenizer.token_for("EMAIL", "a@b.co")
        tokenizer.reset()
        assert tokenizer.mapping == {}

    def test_custom_prefix(self):
        assert Tokenizer("REDACT").token_for("EMAIL", "a@b.co") == "REDACT_EMAIL_001"


class TestApplyAction:
    def test_keep_returns_the_value(self):
        assert apply_action("keep", "a@b.co", "EMAIL") == "a@b.co"

    def test_redact_labels_the_entity(self):
        assert apply_action("redact", "a@b.co", "EMAIL") == "[EMAIL]"

    def test_remove_returns_empty(self):
        assert apply_action("remove", "secret", "SECRET") == ""

    def test_tokenize_requires_a_tokenizer(self):
        with pytest.raises(VeilError, match="Tokenizer"):
            apply_action("tokenize", "a@b.co", "EMAIL")

    def test_unknown_action_raises(self):
        with pytest.raises(VeilError, match="unknown action"):
            apply_action("shred", "a@b.co", "EMAIL")

    def test_action_is_case_insensitive(self):
        assert apply_action("REDACT", "a@b.co", "EMAIL") == "[EMAIL]"

    def test_all_declared_actions_are_dispatchable(self):
        from veil.models import ACTIONS
        for action in ACTIONS:
            tokenizer = Tokenizer() if action == "tokenize" else None
            result = apply_action(action, "value123", "EMAIL", tokenizer=tokenizer)
            assert isinstance(result, str)


class TestVault:
    def test_round_trip_through_disk(self, tmp_path, tokenize_all_policy):
        from veil import Scanner
        result = Scanner(tokenize_all_policy).redact(demo.DOCUMENTS["app.log"])
        vault = Vault.from_mapping(result.token_map, policy_name="t")

        path = str(tmp_path / "v.veilvault.json")
        vault.save(path)
        loaded = Vault.load(path)

        assert loaded.to_mapping() == vault.to_mapping()
        assert loaded.format == VAULT_FORMAT
        assert loaded.policy == "t"

    def test_saved_file_is_owner_only(self, tmp_path):
        vault = Vault.from_mapping({"VEIL_EMAIL_001": "a@b.co"})
        path = str(tmp_path / "v.veilvault.json")
        vault.save(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"vault mode was {oct(mode)}"

    def test_missing_vault_names_the_problem(self, tmp_path):
        with pytest.raises(VaultError, match="no vault at"):
            Vault.load(str(tmp_path / "nope.json"))

    def test_non_json_is_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(VaultError, match="not valid JSON"):
            Vault.load(str(path))

    def test_wrong_format_is_rejected(self, tmp_path):
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"format": "something/else", "entries": []}),
                        encoding="utf-8")
        with pytest.raises(VaultError, match="not a VEIL vault"):
            Vault.load(str(path))

    def test_malformed_entry_is_rejected(self, tmp_path):
        path = tmp_path / "bad-entry.json"
        path.write_text(json.dumps({"format": VAULT_FORMAT, "entries": [{"token": "X"}]}),
                        encoding="utf-8")
        with pytest.raises(VaultError, match="malformed entry"):
            Vault.load(str(path))

    def test_entity_is_recovered_from_the_token(self):
        vault = Vault.from_mapping({
            "VEIL_EMAIL_001": "a@b.co",
            "VEIL_CREDIT_CARD_002": "4111111111111111",
        })
        assert vault.entity_of("VEIL_EMAIL_001") == "EMAIL"
        # Two-word entity: the middle segment must not be truncated.
        assert vault.entity_of("VEIL_CREDIT_CARD_002") == "CREDIT_CARD"

    def test_summary_counts_by_entity(self):
        vault = Vault.from_mapping({
            "VEIL_EMAIL_001": "a@b.co", "VEIL_EMAIL_002": "c@d.co",
            "VEIL_PHONE_001": "+123",
        })
        assert vault.summary() == {"EMAIL": 2, "PHONE": 1}

    def test_empty_vault_is_falsy(self):
        assert not Vault.from_mapping({})

    def test_token_map_is_sorted(self):
        vault = Vault.from_mapping({
            "VEIL_PHONE_001": "x", "VEIL_EMAIL_002": "y", "VEIL_EMAIL_001": "z",
        })
        tokens = [e["token"] for e in vault.entries]
        assert tokens == sorted(tokens)

    def test_merge_combines_and_deduplicates(self):
        first = Vault.from_mapping({"VEIL_EMAIL_001": "a@b.co"})
        second = Vault.from_mapping({"VEIL_EMAIL_001": "updated", "VEIL_PHONE_001": "p"})
        merged = merge([first, second])
        mapping = merged.to_mapping()
        assert mapping["VEIL_EMAIL_001"] == "updated"
        assert "VEIL_PHONE_001" in mapping

    def test_default_path_appends_suffix(self):
        assert default_vault_path("out.txt") == "out.txt.veilvault.json"

    def test_save_creates_missing_directories(self, tmp_path):
        path = str(tmp_path / "nested" / "deep" / "v.veilvault.json")
        Vault.from_mapping({"VEIL_EMAIL_001": "a@b.co"}).save(path)
        assert os.path.exists(path)

    def test_serialised_form_carries_the_format_marker(self):
        data = Vault.from_mapping({"VEIL_EMAIL_001": "a@b.co"}).to_dict()
        assert data["format"] == VAULT_FORMAT
        assert data["count"] == 1
        assert data["entries"][0]["entity"] == "EMAIL"


class TestVaultRoundTripThroughScanner:
    def test_every_entity_survives_a_round_trip(self, tokenize_all_policy):
        from veil import Scanner
        scanner = Scanner(tokenize_all_policy)
        for body in demo.DOCUMENTS.values():
            result = scanner.redact(body)
            vault = Vault.from_mapping(result.token_map)
            restored = scanner.detokenize(result.text, vault)
            assert restored == body

    def test_all_entities_declared_are_tokenisable(self):
        for entity in ALL_ENTITIES:
            tokenizer = Tokenizer()
            assert apply_action("tokenize", "v1234567", entity, tokenizer=tokenizer)


class TestMiniYamlThroughPolicy:
    def test_parses_the_shipped_policies(self):
        from veil.policy import from_yaml
        for name, body in demo.POLICIES.items():
            policy = from_yaml(body)
            assert policy.name
            assert policy.entities

    def test_config_error_is_a_veil_error(self):
        assert issubclass(ConfigError, VeilError)

    def test_unknown_entity_in_policy_is_rejected(self):
        from veil.errors import PolicyError
        from veil.policy import from_yaml
        with pytest.raises(PolicyError, match="unknown entity"):
            from_yaml("entities: [NOT_A_THING]")
