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
check_onnx_package = mod.check_onnx_package
check_model_files = mod.check_model_files
check_cv2_import = mod.check_cv2_import
check_gpu_cuda = mod.check_gpu_cuda
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


# ── check_onnx_package ──────────────────────────────────────────────────

@pytest.fixture
def onnx_installed():
    """Force `import onnx` to succeed or fail; restore afterwards. Refs #47."""
    saved = sys.modules.get("onnx")
    present = "onnx" in sys.modules

    def set_state(installed):
        if installed:
            sys.modules["onnx"] = types.ModuleType("onnx")
        else:
            sys.modules["onnx"] = None  # import raises ImportError

    yield set_state

    if present:
        sys.modules["onnx"] = saved
    else:
        sys.modules.pop("onnx", None)


class TestCheckOnnxPackage:
    def test_missing_onnx_with_onnx_model_warns(self, onnx_installed):
        onnx_installed(False)
        models = [("object", {"name": "m11", "object_weights": "/x/yolo11n.onnx"})]
        w = check_onnx_package(models)
        assert w is not None
        assert "m11" in w
        assert "pip install onnx" in w

    def test_installed_onnx_no_warning(self, onnx_installed):
        onnx_installed(True)
        models = [("object", {"name": "m11", "object_weights": "/x/yolo11n.onnx"})]
        assert check_onnx_package(models) is None

    def test_no_onnx_models_no_warning(self, onnx_installed):
        onnx_installed(False)
        models = [("object", {"name": "YOLOv4", "object_weights": "/x/yolov4.weights"})]
        assert check_onnx_package(models) is None


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


# ── cv2 import state ────────────────────────────────────────────────────

# The real numpy 1.x/2.x ABI failure, as a user sees it. Refs #50.
NUMPY_ABI_ERROR = (
    "numpy.core._multiarray_umath failed to import: a module compiled using "
    "NumPy 1.x cannot be run in NumPy 2.5.3"
)


@pytest.fixture
def cv2_state(monkeypatch):
    """Force `import cv2` into one of: ok / missing / broken.

    Patches __import__ rather than sys.modules so a *broken* cv2 raises the
    same ImportError shape the numpy ABI mismatch produces, instead of
    Python's "None in sys.modules" placeholder.
    """
    import builtins

    real_import = builtins.__import__

    def set_state(state, cuda_devices=0):
        fake = types.ModuleType("cv2")
        fake.__version__ = "4.13.0"
        fake.cuda = types.SimpleNamespace(
            getCudaEnabledDeviceCount=lambda: cuda_devices
        )

        def fake_import(name, *args, **kwargs):
            if name == "cv2":
                if state == "missing":
                    raise ModuleNotFoundError("No module named 'cv2'", name="cv2")
                if state == "broken":
                    raise ImportError(NUMPY_ABI_ERROR)
                return fake
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        return fake

    return set_state


class TestCheckCv2Import:
    def test_broken_cv2_reports_real_error(self, cv2_state):
        cv2_state("broken")
        w = check_cv2_import()
        assert w is not None
        assert "fails to import" in w
        assert "NumPy 1.x cannot be run in NumPy 2.5.3" in w
        assert "numpy in use:" in w
        # Both remedies, aimed at this interpreter's own pip.
        assert 'install "numpy<2"' in w
        assert "opencv-contrib-python" in w

    def test_missing_cv2_is_not_this_checks_problem(self, cv2_state):
        # cv2 absent entirely is reported by check_opencv_version; warning
        # here too would just be noise.
        cv2_state("missing")
        assert check_cv2_import() is None

    def test_working_cv2_no_warning(self, cv2_state):
        cv2_state("ok")
        assert check_cv2_import() is None


# ── check_gpu_cuda ──────────────────────────────────────────────────────

def _gpu_models():
    return [("object", {"name": "m3", "object_processor": "gpu"})]


class TestCheckGpuCuda:
    def test_broken_cv2_does_not_blame_cuda(self, cv2_state):
        # Refs #50: a numpy ABI break used to surface as "no CUDA devices
        # found", sending users after the wrong problem.
        cv2_state("broken")
        assert check_gpu_cuda(_gpu_models(), "/etc/objectconfig.yml") is None

    def test_no_cuda_devices_still_warns(self, cv2_state):
        cv2_state("ok", cuda_devices=0)
        w = check_gpu_cuda(_gpu_models(), "/etc/objectconfig.yml")
        assert w is not None and "no CUDA devices found" in w
        assert "m3" in w

    def test_cuda_present_no_warning(self, cv2_state):
        cv2_state("ok", cuda_devices=2)
        assert check_gpu_cuda(_gpu_models(), "/etc/objectconfig.yml") is None

    def test_missing_cv2_warns_as_before(self, cv2_state):
        # Unchanged legacy behaviour: no cv2 at all means no CUDA either.
        cv2_state("missing")
        w = check_gpu_cuda(_gpu_models(), "/etc/objectconfig.yml")
        assert w is not None and "no CUDA devices found" in w

    def test_no_gpu_models_no_warning(self, cv2_state):
        cv2_state("broken")
        models = [("object", {"name": "cpu1", "object_processor": "cpu"})]
        assert check_gpu_cuda(models, "/etc/objectconfig.yml") is None


# ── check_opencv_version: OpenCV 5 upper bound ──────────────────────────

class TestOpencvFiveDarknet:
    def test_opencv5_warns_for_darknet_weights(self, fake_cv2):
        # OpenCV 5 removed the Darknet importer; pyzm yolo_darknet.py calls
        # cv2.dnn.readNet(weights, cfg), which now raises. Refs #50.
        fake_cv2("5.1.0")
        warnings = check_opencv_version(_models())
        assert len(warnings) == 1
        assert "Darknet importer" in warnings[0]
        assert "YOLOv4" in warnings[0]
        assert "4.13" in warnings[0]

    def test_opencv5_leaves_onnx_models_alone(self, fake_cv2):
        # readNetFromONNX still exists in OpenCV 5, so ONNX models are fine.
        fake_cv2("5.1.0")
        models = [
            ("object", {"name": "m26", "object_weights": "/x/yolo26s.onnx"}),
            ("object", {"name": "m11", "object_weights": "/x/yolo11n.onnx"}),
        ]
        assert check_opencv_version(models) == []

    def test_opencv413_has_no_darknet_warning(self, fake_cv2):
        # Regression lock: the supported ceiling must stay silent.
        fake_cv2("4.13.0")
        assert check_opencv_version(_models()) == []

    def test_darknet_detected_by_extension_not_just_name(self, fake_cv2):
        fake_cv2("5.1.0")
        models = [("object", {"name": "my tiny model", "object_weights": "/x/t.weights"})]
        warnings = check_opencv_version(models)
        assert len(warnings) == 1
        assert "Darknet importer" in warnings[0]
