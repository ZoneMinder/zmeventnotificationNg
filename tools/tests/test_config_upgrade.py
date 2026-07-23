"""Tests for config_upgrade_yaml.py — resolve_dotted, apply_managed_defaults, apply_removed_keys."""

import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "config_upgrade_yaml",
    os.path.join(os.path.dirname(__file__), "..", "config_upgrade_yaml.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

resolve_dotted = mod.resolve_dotted
apply_managed_defaults = mod.apply_managed_defaults
apply_removed_keys = mod.apply_removed_keys


# ── resolve_dotted ──────────────────────────────────────────────────────

class TestResolveDotted:
    def test_simple_resolution(self):
        d = {"fcm": {"fcm_v1_key": "abc"}}
        assert resolve_dotted(d, "fcm.fcm_v1_key") == "abc"

    def test_missing_key_returns_none(self):
        d = {"fcm": {"fcm_v1_key": "abc"}}
        assert resolve_dotted(d, "fcm.no_such_key") is None

    def test_missing_parent_returns_none(self):
        d = {"fcm": {"fcm_v1_key": "abc"}}
        assert resolve_dotted(d, "no_parent.fcm_v1_key") is None


# ── apply_managed_defaults ──────────────────────────────────────────────

class TestApplyManagedDefaults:
    def test_old_default_replaced(self):
        user = {"fcm": {"fcm_v1_key": "old-key"}}
        example = {"fcm": {"fcm_v1_key": "new-key"}}
        managed = {"fcm.fcm_v1_key": ["old-key"]}
        updated = apply_managed_defaults(user, example, managed)
        assert updated == ["fcm.fcm_v1_key"]
        assert user["fcm"]["fcm_v1_key"] == "new-key"

    def test_custom_value_preserved(self):
        user = {"fcm": {"fcm_v1_key": "my-custom-key"}}
        example = {"fcm": {"fcm_v1_key": "new-key"}}
        managed = {"fcm.fcm_v1_key": ["old-key"]}
        updated = apply_managed_defaults(user, example, managed)
        assert updated == []
        assert user["fcm"]["fcm_v1_key"] == "my-custom-key"

    def test_mixed_old_key_custom_url(self):
        user = {"fcm": {"fcm_v1_key": "old-key", "url": "https://custom.example.com"}}
        example = {"fcm": {"fcm_v1_key": "new-key", "url": "https://default.example.com"}}
        managed = {
            "fcm.fcm_v1_key": ["old-key"],
            "fcm.url": ["https://old-default.example.com"],
        }
        updated = apply_managed_defaults(user, example, managed)
        assert updated == ["fcm.fcm_v1_key"]
        assert user["fcm"]["fcm_v1_key"] == "new-key"
        assert user["fcm"]["url"] == "https://custom.example.com"

    def test_missing_key_in_user_skipped(self):
        user = {"fcm": {}}
        example = {"fcm": {"fcm_v1_key": "new-key"}}
        managed = {"fcm.fcm_v1_key": ["old-key"]}
        updated = apply_managed_defaults(user, example, managed)
        assert updated == []
        assert "fcm_v1_key" not in user["fcm"]

    def test_user_already_has_current_default(self):
        user = {"fcm": {"fcm_v1_key": "new-key"}}
        example = {"fcm": {"fcm_v1_key": "new-key"}}
        managed = {"fcm.fcm_v1_key": ["old-key"]}
        updated = apply_managed_defaults(user, example, managed)
        assert updated == []
        assert user["fcm"]["fcm_v1_key"] == "new-key"

    def test_multiple_old_defaults_any_match(self):
        user = {"fcm": {"fcm_v1_key": "old-key-v2"}}
        example = {"fcm": {"fcm_v1_key": "new-key"}}
        managed = {"fcm.fcm_v1_key": ["old-key-v1", "old-key-v2", "old-key-v3"]}
        updated = apply_managed_defaults(user, example, managed)
        assert updated == ["fcm.fcm_v1_key"]
        assert user["fcm"]["fcm_v1_key"] == "new-key"


# ── apply_removed_keys ────────────────────────────────────────────────

class TestApplyRemovedKeys:
    def test_key_removed(self):
        user = {"hook": {"keep_frame_match_type": "yes", "enabled": "yes"}}
        removed = apply_removed_keys(user, ["hook.keep_frame_match_type"])
        assert removed == ["hook.keep_frame_match_type"]
        assert "keep_frame_match_type" not in user["hook"]
        assert user["hook"]["enabled"] == "yes"

    def test_missing_key_skipped(self):
        user = {"hook": {"enabled": "yes"}}
        removed = apply_removed_keys(user, ["hook.keep_frame_match_type"])
        assert removed == []

    def test_missing_parent_skipped(self):
        user = {"general": {"debug": "yes"}}
        removed = apply_removed_keys(user, ["hook.keep_frame_match_type"])
        assert removed == []

    def test_multiple_keys_removed(self):
        user = {"hook": {"keep_frame_match_type": "yes", "old_key": "val", "enabled": "yes"}}
        removed = apply_removed_keys(user, ["hook.keep_frame_match_type", "hook.old_key"])
        assert sorted(removed) == ["hook.keep_frame_match_type", "hook.old_key"]
        assert list(user["hook"].keys()) == ["enabled"]


# ── main() orchestration: the in-place write that touches user configs ──────

class TestMainInPlace:
    def _write(self, tmp_path, name, text):
        p = tmp_path / name
        p.write_text(text)
        return str(p)

    def _run_main(self, monkeypatch, argv):
        import sys
        monkeypatch.setattr(sys, "argv", ["config_upgrade_yaml.py"] + argv)
        mod.main()

    def test_dry_run_leaves_config_byte_identical(self, tmp_path, monkeypatch, capsys):
        user = self._write(tmp_path, "user.yml", "a: 1\n")
        example = self._write(tmp_path, "example.yml", "a: 1\nb: 2\n")
        before = open(user).read()
        self._run_main(monkeypatch, ["-c", user, "-e", example, "--dry-run"])
        assert open(user).read() == before          # nothing written
        assert "Dry run" in capsys.readouterr().out

    def test_real_run_adds_missing_keys_preserving_order(self, tmp_path, monkeypatch):
        # user has z first, then a -> order must be preserved, new key appended
        user = self._write(tmp_path, "user.yml", "z: 10\na: 1\n")
        example = self._write(tmp_path, "example.yml", "z: 10\na: 1\nb: 2\n")
        self._run_main(monkeypatch, ["-c", user, "-e", example])
        import yaml
        text = open(user).read()
        loaded = yaml.safe_load(text)
        assert loaded == {"z": 10, "a": 1, "b": 2}   # new key merged in
        # order preserved (sort_keys=False): z before a in the written file
        assert text.index("z:") < text.index("a:")

    def test_output_flag_does_not_touch_input(self, tmp_path, monkeypatch):
        user = self._write(tmp_path, "user.yml", "a: 1\n")
        example = self._write(tmp_path, "example.yml", "a: 1\nb: 2\n")
        out = str(tmp_path / "out.yml")
        before = open(user).read()
        self._run_main(monkeypatch, ["-c", user, "-e", example, "-o", out])
        assert open(user).read() == before          # input untouched
        assert "b" in open(out).read()               # output has merged key
