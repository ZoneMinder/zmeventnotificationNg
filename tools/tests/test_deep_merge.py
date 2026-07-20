"""Tests for deep_merge in config_upgrade_yaml.py — the core upgrade primitive.

deep_merge(base, override) adds keys present in *base* (the example/reference)
that are missing from *override* (the user config), NEVER overwriting existing
user values, and returns the list of dotted paths that were added.
"""

import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "config_upgrade_yaml",
    os.path.join(os.path.dirname(__file__), "..", "config_upgrade_yaml.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

deep_merge = mod.deep_merge


class TestDeepMerge:
    def test_new_nested_key_added(self):
        base = {"ml": {"object": {"new_key": "default"}}}
        user = {"ml": {"object": {"existing": "keep"}}}
        added = deep_merge(base, user)
        assert added == ["ml.object.new_key"]
        assert user == {"ml": {"object": {"existing": "keep", "new_key": "default"}}}

    def test_existing_user_value_wins(self):
        base = {"fcm": {"enable": "no"}}
        user = {"fcm": {"enable": "yes"}}  # user customized
        added = deep_merge(base, user)
        assert added == []
        assert user["fcm"]["enable"] == "yes"  # untouched

    def test_top_level_new_section_added(self):
        base = {"newsection": {"a": 1, "b": 2}}
        user = {"existing": {"x": 1}}
        added = deep_merge(base, user)
        assert added == ["newsection"]
        # Whole new subtree deep-copied in.
        assert user["newsection"] == {"a": 1, "b": 2}
        # Deep copy: mutating base must not leak into user.
        base["newsection"]["a"] = 999
        assert user["newsection"]["a"] == 1

    def test_dict_vs_scalar_mismatch_user_scalar_preserved(self):
        # Example has a dict where the user set a scalar. deep_merge only
        # recurses when BOTH sides are dicts; here override[key] is not a dict,
        # so the branch is skipped and the user's scalar is preserved as-is.
        # No new keys added, nothing overwritten.
        base = {"key": {"nested": "default"}}
        user = {"key": "user_scalar"}
        added = deep_merge(base, user)
        assert added == []
        assert user == {"key": "user_scalar"}

    def test_dict_vs_scalar_mismatch_user_dict_example_scalar(self):
        # Reverse mismatch: example scalar, user dict. Key exists in user, and
        # base_val is not a dict, so it is left entirely alone.
        base = {"key": "example_scalar"}
        user = {"key": {"nested": "user_value"}}
        added = deep_merge(base, user)
        assert added == []
        assert user == {"key": {"nested": "user_value"}}

    def test_multiple_adds_dotted_paths(self):
        base = {
            "a": {"b": 1, "c": {"d": 2}},
            "top": "t",
        }
        user = {"a": {"b": 1}}  # missing a.c and top
        added = deep_merge(base, user)
        assert sorted(added) == ["a.c", "top"]
        assert user == {"a": {"b": 1, "c": {"d": 2}}, "top": "t"}

    def test_empty_base_no_changes(self):
        user = {"a": 1}
        added = deep_merge({}, user)
        assert added == []
        assert user == {"a": 1}
