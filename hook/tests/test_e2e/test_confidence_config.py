"""E2E: Min confidence filtering through objectconfig."""

from __future__ import annotations

import os

import pytest

from tests.test_e2e.conftest import (
    basic_ml_sequence,
    make_config,
    run_detect_chain,
)


class TestConfidenceConfig:

    def test_high_min_confidence_filters_all(self):
        """A high min_confidence (0.99) demonstrably drops detections vs a low one."""
        low_seq = basic_ml_sequence(min_confidence=0.2)
        high_seq = basic_ml_sequence(min_confidence=0.99)
        low_path, _ = make_config(low_seq)
        high_path, _ = make_config(high_seq)
        try:
            low_data, _, _ = run_detect_chain(low_path)
            high_data, _, _ = run_detect_chain(high_path)

            # Baseline: the low threshold must actually detect the bird.
            assert len(low_data["labels"]) > 0, \
                "low threshold should yield detections (bird.jpg)"

            # Any survivor of the high threshold respects it.
            for conf in high_data.get("confidences", []):
                assert conf >= 0.99

            # The high threshold must strictly drop detections relative to low,
            # proving the filter is real and not a tautology.
            assert len(high_data["labels"]) < len(low_data["labels"]), (
                "0.99 threshold should drop detections vs 0.2: "
                f"high={len(high_data['labels'])} low={len(low_data['labels'])}"
            )
        finally:
            os.unlink(low_path)
            os.unlink(high_path)

    def test_low_min_confidence_keeps_detections(self):
        """A min_confidence of 0.01 keeps all detections."""
        ml_seq = basic_ml_sequence(min_confidence=0.01)
        config_path, _ = make_config(ml_seq)
        try:
            matched_data, _, _ = run_detect_chain(config_path)
            assert len(matched_data["labels"]) > 0
        finally:
            os.unlink(config_path)

    def test_low_vs_high_confidence(self):
        """Lower min_confidence produces >= as many detections as higher."""
        ml_seq_low = basic_ml_sequence(min_confidence=0.1)
        ml_seq_high = basic_ml_sequence(min_confidence=0.8)
        config_low, _ = make_config(ml_seq_low)
        config_high, _ = make_config(ml_seq_high)
        try:
            low_data, _, _ = run_detect_chain(config_low)
            high_data, _, _ = run_detect_chain(config_high)
            assert len(low_data["labels"]) >= len(high_data["labels"])
        finally:
            os.unlink(config_low)
            os.unlink(config_high)
