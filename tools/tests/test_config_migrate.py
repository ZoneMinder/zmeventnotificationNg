"""Tests for config_migrate_yaml.py — the INI -> YAML migration tool.

Covers the pure transformation functions plus a full build_yaml round-trip.
These asserts pin the *exact* migrated structure so a regression that mangles
a user's config is caught (not just "it ran without crashing").
"""

import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "config_migrate_yaml",
    os.path.join(os.path.dirname(__file__), "..", "config_migrate_yaml.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

build_yaml = mod.build_yaml
migrate_monitor = mod.migrate_monitor
migrate_section = mod.migrate_section
expand_variables = mod.expand_variables
resolve_variable_chains = mod.resolve_variable_chains
collect_variables = mod.collect_variables
safe_eval = mod.safe_eval
coerce_value = mod.coerce_value
coerce_types = mod.coerce_types
is_polygon = mod.is_polygon
strip_quotes = mod.strip_quotes
parse_ini = mod.parse_ini


def make_cp(text, tmp_path):
    """Write INI text to a temp file and parse it exactly as the tool does."""
    p = tmp_path / "sample.ini"
    p.write_text(text)
    return parse_ini(str(p))


# ── is_polygon ──────────────────────────────────────────────────────────

class TestIsPolygon:
    def test_valid_polygon(self):
        assert is_polygon("306,356 1003,341 1129,485") is True

    def test_too_few_points(self):
        # Needs at least 3 coordinate pairs.
        assert is_polygon("306,356 1003,341") is False

    def test_non_numeric_coords(self):
        assert is_polygon("a,b c,d e,f") is False

    def test_not_pairs(self):
        assert is_polygon("hello world foo") is False


# ── strip_quotes ────────────────────────────────────────────────────────

class TestStripQuotes:
    def test_double_quotes(self):
        assert strip_quotes('"hello"') == "hello"

    def test_single_quotes(self):
        assert strip_quotes("'hello'") == "hello"

    def test_no_quotes(self):
        assert strip_quotes("hello") == "hello"

    def test_mismatched_quotes_untouched(self):
        assert strip_quotes("'hello\"") == "'hello\""


# ── coerce_value / coerce_types ─────────────────────────────────────────

class TestCoerceValue:
    def test_plain_int(self):
        assert coerce_value("42") == 42
        assert isinstance(coerce_value("42"), int)

    def test_float(self):
        assert coerce_value("0.6") == 0.6
        assert isinstance(coerce_value("0.6"), float)

    def test_non_numeric_string_kept(self):
        assert coerce_value("yes") == "yes"

    def test_non_string_passthrough(self):
        assert coerce_value(True) is True
        assert coerce_value(5) == 5

    # --- Only round-tripping numbers coerce; lossy forms stay strings ---
    def test_leading_zero_kept_as_string(self):
        # "007" would lose its zeros as int 7, so it must stay a string.
        assert coerce_value("007") == "007"
        assert isinstance(coerce_value("007"), str)

    def test_underscore_grouping_kept_as_string(self):
        # Python would read "1_000" as int 1000; keep the literal string.
        assert coerce_value("1_000") == "1_000"

    def test_exponent_string_kept_as_string(self):
        # "1e3" would float to 1000.0; keep the literal string.
        assert coerce_value("1e3") == "1e3"
        assert isinstance(coerce_value("1e3"), str)

    def test_plain_numbers_still_coerce(self):
        assert coerce_value("800") == 800
        assert coerce_value("-5") == -5
        assert coerce_value("0") == 0
        assert coerce_value("0.6") == 0.6
        assert coerce_value("3.0") == 3.0

    def test_coerce_types_recurses(self):
        obj = {"a": "5", "b": ["1", "x", "0.5"], "c": {"d": "no"}}
        assert coerce_types(obj) == {"a": 5, "b": [1, "x", 0.5], "c": {"d": "no"}}


# ── safe_eval ───────────────────────────────────────────────────────────

class TestSafeEval:
    def test_dict_literal(self):
        assert safe_eval("{'a': 1, 'b': [2, 3]}") == {"a": 1, "b": [2, 3]}

    def test_list_literal(self):
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_empty_returns_none(self):
        assert safe_eval("") is None
        assert safe_eval("   ") is None

    def test_non_literal_returned_verbatim(self):
        assert safe_eval("not a literal") == "not a literal"

    def test_full_template_value_preserved(self):
        # A value that is entirely a quoted {{template}} parses and the token
        # is restored intact.
        assert safe_eval("{'w': '{{yolo_weights}}'}") == {"w": "{{yolo_weights}}"}

    def test_bare_template_only(self):
        assert safe_eval("'{{var}}'") == "{{var}}"

    def test_embedded_template_in_quoted_string(self):
        # A {{template}} embedded inside a longer quoted string parses to the
        # string with the token intact (previously this failed to parse and the
        # whole value was returned as a raw, unparsed string).
        assert safe_eval("'{{var}}/suffix'") == "{{var}}/suffix"

    def test_embedded_template_inside_dict_value(self):
        # The real-world case: an ml_sequence-style dict whose value embeds a
        # template must still parse to a dict, not collapse to a raw string.
        result = safe_eval("{'weights': '{{base_data_path}}/models/yolov4.weights'}")
        assert result == {"weights": "{{base_data_path}}/models/yolov4.weights"}

    def test_multiple_embedded_templates(self):
        assert safe_eval("['{{a}}/x', '{{b}}/y']") == ["{{a}}/x", "{{b}}/y"]


# ── variable resolution ─────────────────────────────────────────────────

class TestVariableResolution:
    def test_resolve_chained_variables(self):
        variables = {
            "base": "/var/lib/zmen",
            "models": "{{base}}/models",
            "weights": "{{models}}/yolov4.weights",
        }
        resolved = resolve_variable_chains(variables)
        assert resolved["models"] == "/var/lib/zmen/models"
        assert resolved["weights"] == "/var/lib/zmen/models/yolov4.weights"

    def test_expand_variables_marks_used(self):
        variables = {"base": "/data"}
        obj = {"path": "{{base}}/x", "other": "plain"}
        expanded, used = expand_variables(obj, variables)
        assert expanded == {"path": "/data/x", "other": "plain"}
        assert used == {"base"}

    def test_expand_unknown_variable_kept(self):
        expanded, used = expand_variables({"p": "{{missing}}/x"}, {"base": "/d"})
        assert expanded == {"p": "{{missing}}/x"}
        assert used == set()


# ── migrate_monitor ─────────────────────────────────────────────────────

class TestMigrateMonitor:
    def test_zone_coords_pattern_and_override(self, tmp_path):
        cp = make_cp(
            "[monitor-1]\n"
            "front_zone=306,356 1003,341 1129,485\n"
            "front_zone_zone_detection_pattern=(person)\n"
            "resize=800\n",
            tmp_path,
        )
        result = migrate_monitor(cp, "monitor-1")
        assert result == {
            "resize": "800",  # not yet coerced (coercion happens in build_yaml)
            "zones": {
                "front_zone": {
                    "coords": "306,356 1003,341 1129,485",
                    "detection_pattern": "(person)",
                }
            },
        }

    def test_pattern_for_undeclared_zone(self, tmp_path):
        # A detection pattern whose zone has no coords here (e.g. a ZM zone)
        # still creates a zone entry with just the pattern.
        cp = make_cp(
            "[monitor-2]\n"
            "yard_zone_detection_pattern=(car)\n",
            tmp_path,
        )
        result = migrate_monitor(cp, "monitor-2")
        assert result == {"zones": {"yard": {"detection_pattern": "(car)"}}}


# ── migrate_section ─────────────────────────────────────────────────────

class TestMigrateSection:
    def test_literal_key_evaluated(self, tmp_path):
        cp = make_cp(
            "[ml]\n"
            "ml_sequence={'object': {'sequence': []}}\n",
            tmp_path,
        )
        result = migrate_section(cp, "ml")
        assert result == {"ml_sequence": {"object": {"sequence": []}}}

    def test_legacy_key_dropped(self, tmp_path):
        cp = make_cp(
            "[general]\n"
            "use_sequence=yes\n"
            "base_data_path=/data\n",
            tmp_path,
        )
        result = migrate_section(cp, "general")
        assert result == {"base_data_path": "/data"}


# ── build_yaml (full end-to-end) ────────────────────────────────────────

SAMPLE_INI = """\
[general]
base_data_path=/var/lib/zmen
yolo_weights={{base_data_path}}/yolov4.weights
object_detection_pattern=(person|car)

[object]
object_min_confidence=0.6

[ml]
ml_sequence={'general': {'model_sequence': 'object'}, 'object': {'general': {'object_detection_pattern': '{{object_detection_pattern}}'}, 'sequence': [{'name': 'yolo', 'enabled': 'yes', 'object_weights': '{{yolo_weights}}'}]}}

[monitor-1]
front_zone=306,356 1003,341 1129,485
front_zone_zone_detection_pattern=(person)
resize=800
"""


class TestBuildYamlEndToEnd:
    def test_full_migration_structure(self, tmp_path):
        cp = make_cp(SAMPLE_INI, tmp_path)
        output, expanded, unexpanded = build_yaml(cp)

        assert output == {
            "general": {
                "base_data_path": "/var/lib/zmen",
                # chained {{base_data_path}} resolved inside yolo_weights
                "yolo_weights": "/var/lib/zmen/yolov4.weights",
                # object_detection_pattern is an INDIRECTION_ONLY key that WAS
                # referenced (expanded) in ml_sequence, so it is stripped from
                # the top level of the section.
            },
            "object": {
                # "0.6" coerced to float
                "object_min_confidence": 0.6,
            },
            "ml": {
                "ml_sequence": {
                    "general": {"model_sequence": "object"},
                    "object": {
                        # {{object_detection_pattern}} expanded in place
                        "general": {"object_detection_pattern": "(person|car)"},
                        "sequence": [
                            {
                                "name": "yolo",
                                "enabled": "yes",
                                # {{yolo_weights}} expanded (already chain-resolved)
                                "object_weights": "/var/lib/zmen/yolov4.weights",
                            }
                        ],
                    },
                }
            },
            "monitors": {
                1: {  # monitor id coerced to int
                    "resize": 800,  # "800" coerced to int
                    "zones": {
                        "front_zone": {
                            "coords": "306,356 1003,341 1129,485",
                            "detection_pattern": "(person)",
                        }
                    },
                }
            },
        }

        assert expanded == {"base_data_path", "yolo_weights", "object_detection_pattern"}
        assert unexpanded == set()

    def test_indirection_key_removed_from_top_level(self, tmp_path):
        cp = make_cp(SAMPLE_INI, tmp_path)
        output, _, _ = build_yaml(cp)
        assert "object_detection_pattern" not in output["general"]
        # but the expanded value survives nested inside ml_sequence
        nested = output["ml"]["ml_sequence"]["object"]["general"]
        assert nested["object_detection_pattern"] == "(person|car)"
