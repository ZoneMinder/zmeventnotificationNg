"""E2E: Per-monitor config overrides through detection chain."""

from __future__ import annotations

import os

import pytest

from tests.test_e2e.conftest import (
    basic_ml_sequence,
    find_one_model_path,
    make_config,
    run_detect_chain,
)

import zmes_hook_helpers.common_params as g


class TestMonitorConfig:

    def test_monitor_overrides_pattern(self):
        """Per-monitor ml_sequence with restrictive pattern filters results."""
        weights, cfg, labels = find_one_model_path()

        # Global ml_sequence has broad pattern
        global_ml = basic_ml_sequence(pattern=".*")

        # Monitor 5 overrides with a restrictive pattern
        monitor_ml = {
            "general": {"model_sequence": "object"},
            "object": {
                "general": {"pattern": "^zzz_nonexistent$"},
                "sequence": [{
                    "name": "monitor-model",
                    "object_weights": weights,
                    "object_config": cfg,
                    "object_labels": labels,
                    "object_framework": "opencv",
                    "object_processor": "cpu",
                    "object_min_confidence": 0.3,
                }],
            },
        }
        monitors = {5: {"ml_sequence": monitor_ml}}
        config_path, _ = make_config(global_ml, monitors=monitors)
        try:
            matched_data, output, _ = run_detect_chain(
                config_path, monitor_id="5"
            )
            # Monitor override should produce no matches
            assert len(matched_data["labels"]) == 0
        finally:
            os.unlink(config_path)

    def test_monitor_zones_loaded(self):
        """Per-monitor zones are parsed into g.polygons during process_config."""
        ml_seq = basic_ml_sequence()
        monitors = {
            3: {
                "zones": {
                    "front_yard": {
                        "coords": "0,0 640,0 640,480 0,480",
                        "detection_pattern": "person",
                    },
                    "driveway": {
                        "coords": "100,100 500,100 500,400 100,400",
                    },
                },
            }
        }
        config_path, _ = make_config(ml_seq, monitors=monitors)
        try:
            # process_config parses per-monitor zone definitions into
            # g.polygons (str2tuple on 'coords', plus optional patterns).
            # run_detect_chain does NOT inject polygons here, so g.polygons
            # reflects exactly what config parsing produced.
            run_detect_chain(config_path, monitor_id="3")

            polygons = {p["name"]: p for p in g.polygons}
            assert set(polygons) == {"front_yard", "driveway"}, \
                f"Expected both monitor zones parsed, got {sorted(polygons)}"

            # front_yard: full-frame quad with a detection pattern
            fy = polygons["front_yard"]
            assert fy["value"] == [
                (0.0, 0.0), (640.0, 0.0), (640.0, 480.0), (0.0, 480.0)
            ], f"front_yard coords mismatch: {fy['value']}"
            assert fy["pattern"] == "person", \
                f"front_yard detection_pattern mismatch: {fy['pattern']}"

            # driveway: inner quad, no detection pattern supplied
            dw = polygons["driveway"]
            assert dw["value"] == [
                (100.0, 100.0), (500.0, 100.0), (500.0, 400.0), (100.0, 400.0)
            ], f"driveway coords mismatch: {dw['value']}"
            assert dw["pattern"] is None, \
                f"driveway should have no pattern, got {dw['pattern']}"
        finally:
            os.unlink(config_path)

    def test_monitor_overrides_config_key(self):
        """Per-monitor config keys (like show_percent) override global defaults."""
        ml_seq = basic_ml_sequence()
        monitors = {7: {"show_percent": "yes"}}
        config_path, _ = make_config(
            ml_seq,
            monitors=monitors,
            general_overrides={"show_percent": "no"},
        )
        try:
            _, output, g_config = run_detect_chain(
                config_path, monitor_id="7"
            )
            assert g_config["show_percent"] == "yes"
            if output:
                pred, _ = output.split("--SPLIT--", 1)
                assert "%" in pred
        finally:
            os.unlink(config_path)
