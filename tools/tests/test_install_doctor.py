"""Tests for install_doctor.py — PURE diagnostic functions only.

Only functions that operate on plain cfg dicts / paths are tested here. Checks
that shell out (perl/subprocess) or need face_recognition are NOT exercised.
For check_opencv_version we inject a fake `cv2` module into sys.modules so the
OpenCV-version comparison is deterministic and independent of what's installed.
"""

import importlib.util
import os
import sys
import types

import pytest

spec = importlib.util.spec_from_file_location(
    "install_doctor",
    os.path.join(os.path.dirname(__file__), "..", "install_doctor.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

collect_enabled_models = mod.collect_enabled_models
check_opencv_version = mod.check_opencv_version
check_model_files = mod.check_model_files
resolve_path = mod.resolve_path


@pytest.fixture
def fake_cv2():
    """Install a fake cv2 with a settable __version__; restore afterwards."""
    fake = types.ModuleType("cv2")
    saved = sys.modules.get("cv2")

    def set_version(v):
        fake.__version__ = v
        sys.modules["cv2"] = fake

    yield set_version

    if saved is not None:
        sys.modules["cv2"] = saved
    else:
        sys.modules.pop("cv2", None)


# ── resolve_path ────────────────────────────────────────────────────────

class TestResolvePath:
    def test_dollar_brace_substitution(self):
        assert resolve_path("${base_data_path}/models/x", "/base") == "/base/models/x"

    def test_double_brace_substitution(self):
        assert resolve_path("{{base_data_path}}/y", "/base") == "/base/y"

    def test_empty_returns_raw(self):
        assert resolve_path("", "/base") == ""

    def test_none_returns_none(self):
        assert resolve_path(None, "/base") is None

    def test_absolute_unchanged(self):
        assert resolve_path("/abs/path", "/base") == "/abs/path"

    def test_non_string_stringified(self):
        assert resolve_path(123, "/base") == "123"


# ── collect_enabled_models ──────────────────────────────────────────────

class TestCollectEnabledModels:
    def test_only_enabled_across_sections(self):
        cfg = {
            "ml": {
                "ml_sequence": {
                    "object": {
                        "sequence": [
                            {"name": "yolo", "enabled": "yes"},
                            {"name": "off", "enabled": "no"},
                        ]
                    },
                    "face": {"sequence": [{"name": "f1", "enabled": "true"}]},
                    "alpr": {"sequence": [{"name": "a1", "enabled": "1"}]},
                }
            }
        }
        result = collect_enabled_models(cfg)
        assert result == [
            ("object", {"name": "yolo", "enabled": "yes"}),
            ("face", {"name": "f1", "enabled": "true"}),
            ("alpr", {"name": "a1", "enabled": "1"}),
        ]

    def test_none_cfg(self):
        assert collect_enabled_models(None) == []

    def test_no_ml_section(self):
        assert collect_enabled_models({"general": {}}) == []

    def test_default_disabled_when_no_enabled_key(self):
        cfg = {"ml": {"ml_sequence": {"object": {"sequence": [{"name": "x"}]}}}}
        assert collect_enabled_models(cfg) == []


# ── check_opencv_version ────────────────────────────────────────────────

def _models():
    return [
        ("object", {"name": "m26", "object_weights": "/x/yolo26s.onnx"}),
        ("object", {"name": "m11", "object_weights": "/x/yolo11n.onnx"}),
        ("object", {"name": "YOLOv4", "object_weights": "/x/yolov4.weights"}),
    ]


class TestCheckOpencvVersion:
    def test_recent_opencv_no_warnings(self, fake_cv2):
        fake_cv2("4.13.5")  # parses to (4, 13) -> satisfies v26/v11/v4
        assert check_opencv_version(_models()) == []

    def test_mid_opencv_warns_onnx_only(self, fake_cv2):
        fake_cv2("4.5.0")  # (4, 5): too old for v26/v11, but fine for v4
        warnings = check_opencv_version(_models())
        assert len(warnings) == 2
        assert "YOLOv26" in warnings[0]
        assert "m26" in warnings[0]
        assert "YOLOv11" in warnings[1]
        assert "m11" in warnings[1]
        assert not any("YOLOv4 models" in w for w in warnings)

    def test_old_opencv_warns_all_three(self, fake_cv2):
        fake_cv2("4.3.0")  # (4, 3): too old for everything
        warnings = check_opencv_version(_models())
        assert len(warnings) == 3
        assert any("YOLOv4 models" in w for w in warnings)

    def test_version_string_reported(self, fake_cv2):
        fake_cv2("4.5.9")
        warnings = check_opencv_version(_models())
        # cv_ver_str is the major.minor of the parsed version.
        assert "OpenCV 4.5 detected" in warnings[0]

    def test_v26_classification_by_name(self, fake_cv2):
        fake_cv2("4.5.0")
        models = [("object", {"name": "myYOLOV26", "object_weights": "/x/m.dat"})]
        warnings = check_opencv_version(models)
        assert len(warnings) == 1
        assert "YOLOv26" in warnings[0]


# ── check_model_files ───────────────────────────────────────────────────

class TestCheckModelFiles:
    def test_existing_file_no_warning(self, tmp_path):
        weights = tmp_path / "yolo.weights"
        weights.write_text("x")
        models = [
            ("object", {"name": "m", "object_weights": "${base_data_path}/yolo.weights"})
        ]
        assert check_model_files(models, str(tmp_path)) == []

    def test_missing_file_warns(self, tmp_path):
        models = [
            ("object", {"name": "m", "object_weights": "${base_data_path}/missing.weights"})
        ]
        warnings = check_model_files(models, str(tmp_path))
        assert len(warnings) == 1
        assert "does not exist" in warnings[0]
        assert str(tmp_path / "missing.weights") in warnings[0]

    def test_no_file_keys_no_warning(self, tmp_path):
        models = [("object", {"name": "m", "enabled": "yes"})]
        assert check_model_files(models, str(tmp_path)) == []

    def test_multiple_keys_checked(self, tmp_path):
        (tmp_path / "w.dat").write_text("x")  # object_weights exists
        models = [
            (
                "object",
                {
                    "name": "m",
                    "object_weights": "${base_data_path}/w.dat",
                    "object_config": "${base_data_path}/cfg.cfg",  # missing
                },
            )
        ]
        warnings = check_model_files(models, str(tmp_path))
        assert len(warnings) == 1
        assert "object_config" in warnings[0]


check_secrets_file = mod.check_secrets_file


# ── check_secrets_file (readability diagnostic; security-relevant) ──────────

class TestCheckSecretsFile:
    def test_no_secrets_key_returns_none(self):
        assert check_secrets_file({"general": {}}, "www-data") is None

    def test_none_cfg_returns_none(self):
        assert check_secrets_file(None, "www-data") is None

    def test_missing_secrets_file_warns(self, tmp_path):
        cfg = {"general": {"secrets": str(tmp_path / "nope.yml")}}
        warn = check_secrets_file(cfg, "www-data")
        assert warn is not None and "not found" in warn

    def test_owner_readable_no_warning(self, tmp_path, monkeypatch):
        sf = tmp_path / "secrets.yml"
        sf.write_text("x"); sf.chmod(0o600)
        # web user owns the file -> readable -> no warning
        monkeypatch.setattr(mod, "uid_for_user", lambda u: os.getuid())
        assert check_secrets_file({"general": {"secrets": str(sf)}}, "www-data") is None

    def test_not_owner_and_not_world_readable_warns(self, tmp_path, monkeypatch):
        sf = tmp_path / "secrets.yml"
        sf.write_text("x"); sf.chmod(0o600)  # no other-read
        monkeypatch.setattr(mod, "uid_for_user", lambda u: os.getuid() + 12345)
        warn = check_secrets_file({"general": {"secrets": str(sf)}}, "www-data")
        assert warn is not None and "may not be readable" in warn

    def test_world_readable_no_warning_even_if_not_owner(self, tmp_path, monkeypatch):
        sf = tmp_path / "secrets.yml"
        sf.write_text("x"); sf.chmod(0o604)  # other-read set
        monkeypatch.setattr(mod, "uid_for_user", lambda u: os.getuid() + 12345)
        assert check_secrets_file({"general": {"secrets": str(sf)}}, "www-data") is None
