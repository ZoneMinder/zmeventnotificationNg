"""E2E: Multiple models in objectconfig sequence."""

from __future__ import annotations

import os

import pytest

from tests.test_e2e.conftest import (
    find_one_model_path,
    make_config,
    run_detect_chain,
)


class TestMultiModelConfig:

    def test_two_models_union_strategy(self):
        """Two models with UNION strategy both contribute detections."""
        weights, config, labels = find_one_model_path()
        ml_seq = {
            "general": {
                "model_sequence": "object",
                "same_model_sequence_strategy": "union",
            },
            "object": {
                "general": {"pattern": ".*"},
                "sequence": [
                    {
                        "name": "model-a",
                        "object_weights": weights,
                        "object_config": config,
                        "object_labels": labels,
                        "object_framework": "opencv",
                        "object_processor": "cpu",
                        "object_min_confidence": 0.3,
                    },
                    {
                        "name": "model-b",
                        "object_weights": weights,
                        "object_config": config,
                        "object_labels": labels,
                        "object_framework": "opencv",
                        "object_processor": "cpu",
                        "object_min_confidence": 0.3,
                    },
                ],
            },
        }
        config_path, _ = make_config(ml_seq)
        try:
            matched_data, output, _ = run_detect_chain(config_path)
            assert len(matched_data["labels"]) > 0, \
                "Expected detections (bird.jpg must yield labels)"
            # With union strategy, BOTH models must actually contribute
            # detections to the merged result.
            model_names = set(matched_data.get("model_names", []))
            assert {"model-a", "model-b"} <= model_names, (
                "union strategy should include detections from both models; "
                f"got model_names={sorted(model_names)}"
            )
        finally:
            os.unlink(config_path)

    def test_first_strategy_uses_first_match(self):
        """FIRST strategy returns results from the first model that matches."""
        weights, config, labels = find_one_model_path()
        ml_seq = {
            "general": {
                "model_sequence": "object",
                "same_model_sequence_strategy": "first",
            },
            "object": {
                "general": {"pattern": ".*"},
                "sequence": [
                    {
                        "name": "first-model",
                        "object_weights": weights,
                        "object_config": config,
                        "object_labels": labels,
                        "object_framework": "opencv",
                        "object_processor": "cpu",
                        "object_min_confidence": 0.3,
                    },
                    {
                        "name": "second-model",
                        "object_weights": weights,
                        "object_config": config,
                        "object_labels": labels,
                        "object_framework": "opencv",
                        "object_processor": "cpu",
                        "object_min_confidence": 0.3,
                    },
                ],
            },
        }
        config_path, _ = make_config(ml_seq)
        try:
            matched_data, _, _ = run_detect_chain(config_path)
            assert matched_data.get("labels"), \
                "Expected detections (bird.jpg must yield labels)"
            # FIRST strategy: all detections come from the first model
            for mn in matched_data.get("model_names", []):
                assert mn == "first-model"
        finally:
            os.unlink(config_path)
