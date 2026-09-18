"""Policy and miniyaml tests.

The YAML subset is small on purpose. These tests pin the boundary: what the
parser accepts, and — more importantly — that it *refuses* everything outside the
subset rather than silently mis-parsing it. A config that half-loads is a
security bug, not a convenience problem.
"""

from __future__ import annotations

import pytest

from veil import demo
from veil.errors import PolicyError
from veil.miniyaml import parse
from veil.models import ACTION_KEEP, ACTIONS, ALL_ENTITIES, MASK_CHAR
from veil.policy import (
    DEFAULT_ACTIONS,
    Policy,
    apply_env,
    from_dict,
    from_yaml,
    load,
)


class TestParseScalars:
    def test_empty_input(self):
        assert parse("") == {}

    def test_blank_lines_and_comments(self):
        assert parse("# just a comment\n\n# another") == {}

    def test_trailing_comment_is_stripped(self):
        assert parse("name: strict  # the strict one") == {"name": "strict"}

    def test_hash_inside_quotes_is_kept(self):
        assert parse('mask_char: "#"') == {"mask_char": "#"}

    @pytest.mark.parametrize("text,expected", [
        ("a: 1", {"a": 1}),
        ("a: -2", {"a": -2}),
        ("a: true", {"a": True}),
        ("a: false", {"a": False}),
        ("a: null", {"a": None}),
        ("a: ~", {"a": None}),
        ("a: 1.5", {"a": 1.5}),
        ("a: hello", {"a": "hello"}),
        ("a: 'quoted'", {"a": "quoted"}),
        ('a: "double"', {"a": "double"}),
        ("a:", {"a": None}),
    ])
    def test_scalar_types(self, text, expected):
        assert parse(text) == expected

    def test_numeric_string_that_is_not_a_number(self):
        assert parse("a: 1.2.3") == {"a": "1.2.3"}


class TestParseStructures:
    def test_nested_mapping(self):
        assert parse("a:\n  b: 1\n  c: 2") == {"a": {"b": 1, "c": 2}}

    def test_sequence_of_scalars(self):
        assert parse("items:\n  - one\n  - two") == {"items": ["one", "two"]}

    def test_sequence_at_same_indent_as_key(self):
        assert parse("items:\n- one\n- two") == {"items": ["one", "two"]}

    def test_inline_sequence(self):
        assert parse("items: [a, b, c]") == {"items": ["a", "b", "c"]}

    def test_empty_inline_sequence(self):
        assert parse("items: []") == {"items": []}

    def test_sequence_of_mappings(self):
        text = "people:\n  - name: amir\n    role: dev\n  - name: sara\n    role: qa"
        assert parse(text) == {"people": [
            {"name": "amir", "role": "dev"},
            {"name": "sara", "role": "qa"},
        ]}

    def test_nested_map_inside_sequence_entry(self):
        text = "rows:\n  - key: x\n    opts:\n      deep: 1"
        assert parse(text) == {"rows": [{"key": "x", "opts": {"deep": 1}}]}

    def test_deeply_nested(self):
        text = "a:\n  b:\n    c:\n      d: 1"
        assert parse(text) == {"a": {"b": {"c": {"d": 1}}}}


class TestParserRefusals:
    """Outside the documented subset, the parser must refuse, not guess."""

    def test_tab_indentation_is_rejected(self):
        with pytest.raises(Exception, match="tabs are not allowed"):
            parse("a:\n\tb: 1")

    def test_anchors_are_rejected(self):
        with pytest.raises(Exception, match="anchors"):
            parse("a: &anchor value")

    def test_aliases_are_rejected(self):
        with pytest.raises(Exception, match="anchors|aliases"):
            parse("a: *anchor")

    def test_block_scalars_are_rejected(self):
        with pytest.raises(Exception, match="multi-line block"):
            parse("a: |\n  text")

    def test_flow_mapping_is_rejected(self):
        with pytest.raises(Exception, match="flow mappings"):
            parse("a: {b: 1}")

    def test_multi_document_is_rejected(self):
        with pytest.raises(Exception, match="multi-document"):
            parse("---\na: 1")

    def test_missing_colon_is_rejected(self):
        with pytest.raises(Exception, match="no ':'"):
            parse("just some text")

    def test_unterminated_inline_sequence_is_rejected(self):
        with pytest.raises(Exception, match="unterminated"):
            parse("a: [1, 2")

    def test_over_indentation_is_rejected(self):
        with pytest.raises(Exception, match="unexpected indent"):
            parse("a: 1\n   b: 2")


class TestPolicyDefaults:
    def test_default_policy_scans_everything_but_person(self):
        policy = Policy()
        assert "PERSON" not in policy.entities
        assert "EMAIL" in policy.entities

    def test_default_actions_cover_every_entity(self):
        for entity in ALL_ENTITIES:
            assert entity in DEFAULT_ACTIONS, f"{entity} has no default action"

    def test_secrets_default_to_remove(self):
        assert Policy().action_for("SECRET") == "remove"

    def test_default_mask_char_is_one_character(self):
        assert len(Policy().mask_char) == 1
        assert Policy().mask_char == MASK_CHAR


class TestPolicyFromDict:
    def test_name_is_applied(self):
        assert from_dict({"name": "custom"}).name == "custom"

    def test_entities_list_is_honoured(self):
        policy = from_dict({"entities": ["EMAIL", "PHONE"]})
        assert policy.entities == ["EMAIL", "PHONE"]

    def test_all_keyword_expands(self):
        policy = from_dict({"entities": ["ALL"]})
        assert set(policy.entities) == set(ALL_ENTITIES)

    def test_bang_prefix_removes(self):
        policy = from_dict({"entities": ["ALL", "!PERSON"]})
        assert "PERSON" not in policy.entities

    def test_lowercase_entity_is_normalised(self):
        assert from_dict({"entities": ["email"]}).entities == ["EMAIL"]

    def test_actions_merge_over_defaults(self):
        policy = from_dict({"actions": {"EMAIL": "redact"}})
        assert policy.action_for("EMAIL") == "redact"
        assert policy.action_for("SECRET") == "remove"

    def test_unknown_entity_is_rejected(self):
        with pytest.raises(PolicyError, match="unknown entity"):
            from_dict({"entities": ["NOPE"]})

    def test_unknown_action_is_rejected(self):
        with pytest.raises(PolicyError, match="unknown action"):
            from_dict({"actions": {"EMAIL": "shred"}})

    def test_unknown_key_is_rejected(self):
        with pytest.raises(PolicyError, match="unknown policy key"):
            from_dict({"nonsense": 1})

    def test_empty_entity_set_is_rejected(self):
        with pytest.raises(PolicyError, match="empty set"):
            from_dict({"entities": ["ALL", "!PERSON", "!EMAIL", "!PHONE",
                                    "!CREDIT_CARD", "!IBAN", "!SSN", "!NATIONAL_ID",
                                    "!IPV4", "!IPV6", "!MAC", "!URL", "!SECRET",
                                    "!DATE_OF_BIRTH"]})

    def test_multicharacter_mask_char_is_rejected(self):
        with pytest.raises(PolicyError, match="exactly one character"):
            from_dict({"mask_char": "ab"})

    def test_negative_keep_first_is_rejected(self):
        with pytest.raises(PolicyError, match=">= 0"):
            from_dict({"keep_first": -1})

    def test_non_integer_keep_last_is_rejected(self):
        with pytest.raises(PolicyError, match="must be an integer"):
            from_dict({"keep_last": "many"})

    def test_boolean_keep_first_is_rejected(self):
        with pytest.raises(PolicyError, match="must be an integer"):
            from_dict({"keep_first": True})

    def test_bad_token_prefix_is_rejected(self):
        with pytest.raises(PolicyError, match="token_prefix"):
            from_dict({"token_prefix": "has spaces"})

    def test_non_mapping_actions_is_rejected(self):
        with pytest.raises(PolicyError, match="actions must be a mapping"):
            from_dict({"actions": ["EMAIL"]})

    def test_allowlist_accepts_a_single_string(self):
        assert from_dict({"allowlist": "a@b.co"}).allowlist == ["a@b.co"]

    def test_allowlist_rejects_nested_structures(self):
        with pytest.raises(PolicyError, match="must be scalars"):
            from_dict({"allowlist": [{"a": 1}]})

    def test_actions_must_be_a_scalar_value(self):
        with pytest.raises(PolicyError, match="valid actions|unknown action"):
            from_dict({"actions": {"EMAIL": ["redact"]}})


class TestPolicyFromYaml:
    def test_shipped_policies_load(self):
        for name, body in demo.POLICIES.items():
            policy = from_yaml(body)
            assert policy.name == name.replace(".yaml", "")

    def test_strict_redacts_everything(self):
        policy = from_yaml(demo.POLICIES["strict.yaml"])
        assert all(policy.action_for(e) in ("redact", "remove") for e in ALL_ENTITIES)

    def test_pseudonymize_tokenizes_entities(self):
        policy = from_yaml(demo.POLICIES["pseudonymize.yaml"])
        assert policy.action_for("EMAIL") == "tokenize"
        assert policy.action_for("CREDIT_CARD") == "tokenize"

    def test_shareable_carries_the_allowlist(self):
        policy = from_yaml(demo.POLICIES["shareable.yaml"])
        assert "support@brightpath-consulting.com" in policy.allowlist


class TestPolicyLoadFromFile:
    def test_load_from_disk(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("name: from-file\nentities: [EMAIL]\n", encoding="utf-8")
        assert load(str(path)).name == "from-file"

    def test_missing_file_names_the_path(self, tmp_path):
        from veil.errors import ConfigError
        with pytest.raises(ConfigError, match="not found"):
            load(str(tmp_path / "absent.yaml"))


class TestEnvironmentOverrides:
    def test_entity_list_override(self):
        policy = apply_env(Policy(), {"VEIL_ENTITIES": "EMAIL,PHONE"})
        assert policy.entities == ["EMAIL", "PHONE"]

    def test_all_keyword_in_environment(self):
        policy = apply_env(Policy(), {"VEIL_ENTITIES": "ALL"})
        assert set(policy.entities) == set(ALL_ENTITIES)

    def test_bang_removal_in_environment(self):
        policy = apply_env(Policy(), {"VEIL_ENTITIES": "ALL,!PERSON"})
        assert "PERSON" not in policy.entities

    def test_action_override(self):
        policy = apply_env(Policy(), {"VEIL_ACTION_EMAIL": "redact"})
        assert policy.action_for("EMAIL") == "redact"

    def test_every_entity_has_an_env_action_variable(self):
        for entity in ALL_ENTITIES:
            policy = apply_env(Policy(), {f"VEIL_ACTION_{entity}": "keep"})
            assert policy.action_for(entity) == ACTION_KEEP

    def test_invalid_env_action_is_rejected(self):
        with pytest.raises(PolicyError, match="not a valid action"):
            apply_env(Policy(), {"VEIL_ACTION_EMAIL": "obliterate"})

    def test_numeric_overrides(self):
        policy = apply_env(Policy(), {"VEIL_KEEP_FIRST": "2", "VEIL_KEEP_LAST": "3"})
        assert (policy.keep_first, policy.keep_last) == (2, 3)

    def test_mask_char_override(self):
        assert apply_env(Policy(), {"VEIL_MASK_CHAR": "*"}).mask_char == "*"

    def test_multicharacter_mask_char_is_rejected(self):
        with pytest.raises(PolicyError, match="one character"):
            apply_env(Policy(), {"VEIL_MASK_CHAR": "**"})

    def test_salt_override(self):
        assert apply_env(Policy(), {"VEIL_HASH_SALT": "s3cret"}).hash_salt == "s3cret"

    def test_list_overrides(self):
        policy = apply_env(Policy(), {
            "VEIL_ALLOWLIST": "a@b.co,c@d.co",
            "VEIL_DENYLIST": "internal-ref",
            "VEIL_NAMES": "Zubair",
        })
        assert policy.allowlist == ["a@b.co", "c@d.co"]
        assert policy.denylist == ["internal-ref"]
        assert policy.names == ["Zubair"]

    def test_source_records_what_was_overridden(self):
        policy = apply_env(Policy(), {"VEIL_KEEP_FIRST": "1"})
        assert "env(" in policy.source

    def test_empty_entity_override_is_rejected(self):
        with pytest.raises(PolicyError, match="empty set"):
            apply_env(Policy(), {"VEIL_ENTITIES": "  "})

    def test_no_overrides_leaves_the_policy_alone(self):
        base = Policy()
        assert apply_env(base, {}).entities == base.entities


class TestPolicySerialisation:
    def test_to_dict_carries_every_knob(self):
        data = Policy().to_dict()
        for key in ("name", "entities", "actions", "keep_first", "keep_last",
                    "mask_char", "hash_salt", "token_prefix", "allowlist",
                    "denylist", "names", "case_sensitive_denylist"):
            assert key in data

    def test_round_trips_through_from_dict(self):
        original = from_yaml(demo.POLICIES["shareable.yaml"])
        rebuilt = from_dict(original.to_dict())
        assert rebuilt.entities == original.entities
        assert rebuilt.actions == original.actions
        assert rebuilt.allowlist == original.allowlist

    def test_all_actions_declared_are_selectable(self):
        for action in ACTIONS:
            policy = from_dict({"actions": {e: action for e in ALL_ENTITIES}})
            assert policy.action_for("EMAIL") == action
