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


# ── main(): end-to-end behavior on realistic configs ────────────────────────

def _run_upgrade(tmp_path, monkeypatch, user_text, example_text, extra=()):
    import sys
    user = tmp_path / "user.yml"
    user.write_text(user_text)
    example = tmp_path / "example.yml"
    example.write_text(example_text)
    monkeypatch.setattr(sys, "argv", ["config_upgrade_yaml.py", "-c", str(user),
                                      "-e", str(example)] + list(extra))
    mod.main()
    return user


class TestMainCharacterization:
    """Pin what the upgrade already does right on real-shaped configs."""

    def test_python_reader_sees_same_user_values(self, tmp_path, monkeypatch):
        # The hook reads objectconfig.yml with PyYAML; existing values must
        # load identically after an upgrade that adds a key.
        import yaml
        user_text = (
            "general:\n"
            "  port: 9000\n"
            "  ratio: 0.6\n"
            "  enable: yes\n"
            "  name: 'quoted'\n"
            "  empty:\n"
            "  multi: |\n"
            "    line1\n"
            "    line2\n"
            "  seq:\n"
            "    - a\n"
            "    - 2\n"
        )
        user = _run_upgrade(tmp_path, monkeypatch, user_text,
                            "general:\n  port: 1\n  new_key: added\n")
        after = yaml.safe_load(user.read_text())
        before = yaml.safe_load(user_text)
        assert after["general"].pop("new_key") == "added"
        assert after == before

    def test_new_schema_section_added_from_example(self, tmp_path, monkeypatch):
        import yaml
        user = _run_upgrade(tmp_path, monkeypatch, "general:\n  a: 1\n",
                            "general:\n  a: 1\nmqtt:\n  enable: no\n  server: x\n")
        assert yaml.safe_load(user.read_text())["mqtt"] == {"enable": False, "server": "x"}

    def test_managed_default_replaced_end_to_end(self, tmp_path, monkeypatch):
        import yaml
        managed = tmp_path / "managed.yml"
        managed.write_text("sec:\n  fcm.key:\n    - old\n")
        user = _run_upgrade(tmp_path, monkeypatch, "fcm:\n  key: old\n",
                            "fcm:\n  key: new\n",
                            extra=["-m", str(managed), "-s", "sec"])
        assert yaml.safe_load(user.read_text()) == {"fcm": {"key": "new"}}

    def test_up_to_date_config_not_rewritten(self, tmp_path, monkeypatch):
        text = "# my comment\na: 1\n"
        user = _run_upgrade(tmp_path, monkeypatch, text, "a: 2\n")
        assert user.read_text() == text
        assert sorted(p.name for p in tmp_path.iterdir()) == ["example.yml", "user.yml"]


class TestMainBugs:
    def test_in_place_rewrite_keeps_backup_of_original(self, tmp_path, monkeypatch):
        # The rewrite drops comments, so the original must be kept.
        text = "# my precious comment\na: 1\n"
        _run_upgrade(tmp_path, monkeypatch, text, "a: 1\nb: 2\n")
        backups = list(tmp_path.glob("user.yml.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text() == text

    def test_backup_is_not_world_readable(self, tmp_path, monkeypatch):
        # secrets.yml from older installs is 0644; its backup must not be
        user = tmp_path / "user.yml"
        user.write_text("a: 1\n")
        user.chmod(0o644)
        _run_upgrade(tmp_path, monkeypatch, "a: 1\n", "a: 1\nb: 2\n")
        (backup,) = tmp_path.glob("user.yml.*.bak")
        assert backup.stat().st_mode & 0o777 == 0o640

    def test_scalars_keep_their_text_for_perl_and_python(self, tmp_path, monkeypatch):
        # The ES (Perl YAML::XS, untyped scalars) reads es_rules.yml and
        # zmeventnotification.yml. PyYAML's YAML 1.1 round-trip rewrote
        # 21:30 -> 1290, yes -> true, 0123 -> 83. BaseLoader yields the raw
        # scalar text, which is what an untyped reader sees.
        import yaml
        user_text = (
            "rules:\n"
            "  from: 21:30\n"
            "  to: 1:30:00\n"
            "  enable: yes\n"
            "  off: no\n"
            "  pin: 0123\n"
            "  hex: 0x1F\n"
            "  date: 2024-1-1\n"
            "  quoted: '800'\n"
            "  real_bool: true\n"
            "  nothing: ~\n"
        )
        user = _run_upgrade(tmp_path, monkeypatch, user_text,
                            "rules:\n  from: x\n  new_key: added\n")
        after_text = user.read_text()
        raw_after = yaml.load(after_text, Loader=yaml.BaseLoader)
        assert raw_after["rules"].pop("new_key") == "added"
        assert raw_after == yaml.load(user_text, Loader=yaml.BaseLoader)
        typed_after = yaml.safe_load(after_text)
        del typed_after["rules"]["new_key"]
        assert typed_after == yaml.safe_load(user_text)

    def test_example_monitor_entries_not_merged_into_es_rules(self, tmp_path, monkeypatch):
        import yaml
        repo = os.path.join(os.path.dirname(__file__), "..", "..")
        example = open(os.path.join(repo, "es_rules.example.yml")).read()
        user_text = (
            "notifications:\n"
            "  monitors:\n"
            "    5:\n"
            "      rules:\n"
            "        - from: '9 pm'\n"
            "          to: '1 am'\n"
            "          action: mute\n"
        )
        user = _run_upgrade(tmp_path, monkeypatch, user_text, example)
        assert yaml.safe_load(user.read_text()) == yaml.safe_load(user_text)

    def test_example_monitor_not_merged_into_objectconfig(self, tmp_path, monkeypatch):
        import yaml
        repo = os.path.join(os.path.dirname(__file__), "..", "..")
        example = open(os.path.join(repo, "hook", "objectconfig.example.yml")).read()
        # User deliberately removed the sample monitors section.
        user_text = "general:\n  base_data_path: /var/lib/zmeventnotification\n"
        user = _run_upgrade(tmp_path, monkeypatch, user_text, example)
        after = yaml.safe_load(user.read_text())
        assert "monitors" not in after
        assert "ml" in after  # real schema sections still merged


class TestMainStandardTags:
    def test_explicit_core_tags_load_the_same(self, tmp_path, monkeypatch):
        import yaml
        user_text = "a:\n  s: !!str 5\n  i: !!int '7'\n  q: !!str yes\n"
        user = _run_upgrade(tmp_path, monkeypatch, user_text, "a:\n  s: x\n  new: 1\n")
        after = yaml.safe_load(user.read_text())
        assert after["a"].pop("new") == 1
        assert after == yaml.safe_load(user_text)
