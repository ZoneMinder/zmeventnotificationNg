"""Contract test between ES and the REAL pyzm library.

Every other hook unit test runs against the fake ``pyzm`` stub that
``tests/conftest.py`` injects into ``sys.modules``. That stub is whatever we
hand-typed -- nothing ties it to the real pyzm at ~/fiddle/pyzmNg. So a real
pyzm change (a renamed ``DetectionResult`` key, a changed ``detect_event``
signature) is invisible to the unit suite and blows up only in production.

This test strips the stub, imports the REAL pyzm, and asserts the exact shape
ES consumes. It runs whenever real pyzm is importable (the gate sets
``PYTHONPATH`` to the pyzm checkout). When pyzm cannot be imported it skips by
default and FAILS under ``ZM_E2E_REQUIRE=1`` (the release gate), so drift can
never merge silently.

Keys/signatures asserted here are derived from the real ES call sites:
  - hook/zmes_hook_helpers/utils.py + hook/zm_detect.py read these dict keys.
  - hook/zm_detect.py calls detect_event(zm, id, zones=, stream_config=) and
    detect(input, zones=).
"""

from __future__ import annotations

import inspect
import os
import sys

import pytest

# Keys ES reads by name from DetectionResult.to_dict() / matched_data.
# A missing key here is a production KeyError in format_detection_output.
ES_CONSUMED_KEYS = {
    "boxes",
    "confidences",
    "labels",
    "model_names",
    "frame_id",
    "image",
    "image_dimensions",
    "polygons",
}


def _import_real_pyzm():
    """Import real pyzm with the unit-test stub stripped. Returns the module
    or None if the genuine package is not importable."""
    import importlib

    saved = {k: v for k, v in list(sys.modules.items())
             if k == "pyzm" or k.startswith("pyzm.")}
    for k in saved:
        del sys.modules[k]
    try:
        return importlib.import_module("pyzm")
    except Exception:
        return None
    finally:
        for k in [k for k in sys.modules if k == "pyzm" or k.startswith("pyzm.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def _real_pyzm_or_skip():
    mod = _import_real_pyzm()
    if mod is not None:
        return
    msg = ("real pyzm not importable (set PYTHONPATH to the pyzm checkout, "
           "e.g. make gate)")
    if os.environ.get("ZM_E2E_REQUIRE") == "1":
        pytest.fail(msg, pytrace=False)
    pytest.skip(msg)


def test_detectionresult_to_dict_has_keys_es_consumes():
    """Real DetectionResult.to_dict() must contain every key ES reads."""
    _real_pyzm_or_skip()
    import importlib

    saved = {k: v for k, v in list(sys.modules.items())
             if k == "pyzm" or k.startswith("pyzm.")}
    for k in saved:
        del sys.modules[k]
    try:
        det_mod = importlib.import_module("pyzm.models.detection")
        BBox, Detection, DetectionResult = det_mod.BBox, det_mod.Detection, det_mod.DetectionResult
        result = DetectionResult(
            detections=[Detection("person", 0.9, BBox(0, 0, 10, 10), model_name="yolov4")],
            frame_id="snapshot",
        )
        keys = set(result.to_dict().keys())
    finally:
        for k in [k for k in sys.modules if k == "pyzm" or k.startswith("pyzm.")]:
            del sys.modules[k]
        sys.modules.update(saved)

    missing = ES_CONSUMED_KEYS - keys
    assert not missing, (
        f"pyzm DetectionResult.to_dict() no longer emits keys ES consumes: "
        f"{missing}. ES format_detection_output would KeyError in production."
    )


def test_detect_event_signature_matches_es_call():
    """Real Detector.detect_event must accept the args zm_detect.py passes:
    detect_event(zm, event_id, zones=..., stream_config=...)."""
    _real_pyzm_or_skip()
    import importlib

    saved = {k: v for k, v in list(sys.modules.items())
             if k == "pyzm" or k.startswith("pyzm.")}
    for k in saved:
        del sys.modules[k]
    try:
        detector_mod = importlib.import_module("pyzm.ml.detector")
        params = inspect.signature(detector_mod.Detector.detect_event).parameters
    finally:
        for k in [k for k in sys.modules if k == "pyzm" or k.startswith("pyzm.")]:
            del sys.modules[k]
        sys.modules.update(saved)

    for needed in ("zones", "stream_config"):
        assert needed in params, (
            f"pyzm Detector.detect_event lost the '{needed}' parameter that "
            f"zm_detect.py passes by keyword."
        )
