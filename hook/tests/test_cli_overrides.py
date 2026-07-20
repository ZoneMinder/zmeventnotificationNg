"""Tests for the -O/--override dot-notation config override subsystem.

Exercises the REAL functions in zmes_hook_helpers.utils:
    apply_cli_overrides, _coerce_value, _parse_path_segments,
    _find_by_name, _resolve_segment

apply_cli_overrides mutates g.config in place, so each test seeds g.config
(reset to {} by the autouse reset_common_params fixture in conftest.py) and
asserts the exact resulting structure.
"""
import copy

import pytest

import zmes_hook_helpers.utils as utils
import zmes_hook_helpers.common_params as g


# ---------------------------------------------------------------------------
# Helper: record Warning() calls so error/edge cases can be positively asserted
# ---------------------------------------------------------------------------
class _RecordingLogger:
    def __init__(self):
        self.warnings = []
        self.debugs = []

    def Debug(self, level, msg):
        self.debugs.append(msg)

    def Info(self, msg):
        pass

    def Warning(self, msg):
        self.warnings.append(msg)

    def Error(self, msg):
        pass

    def Fatal(self, msg):
        raise SystemExit(msg)

    def close(self):
        pass


@pytest.fixture
def rec_logger():
    logger = _RecordingLogger()
    g.logger = logger
    return logger


# ---------------------------------------------------------------------------
# _coerce_value: type coercion
# ---------------------------------------------------------------------------
def test_coerce_int():
    result = utils._coerce_value('20')
    assert result == 20
    assert isinstance(result, int)


def test_coerce_negative_int():
    result = utils._coerce_value('-7')
    assert result == -7
    assert isinstance(result, int)


def test_coerce_float():
    result = utils._coerce_value('0.5')
    assert result == 0.5
    assert isinstance(result, float)


def test_coerce_yes_stays_string():
    # 'yes'/'no' are intentionally returned as-is (strings), NOT booleans.
    result = utils._coerce_value('yes')
    assert result == 'yes'
    assert isinstance(result, str)


def test_coerce_no_stays_string():
    result = utils._coerce_value('no')
    assert result == 'no'
    assert isinstance(result, str)


def test_coerce_yes_preserves_case():
    # code lowercases only for the comparison, returns the original token
    assert utils._coerce_value('Yes') == 'Yes'


def test_coerce_plain_string():
    result = utils._coerce_value('yolov8')
    assert result == 'yolov8'
    assert isinstance(result, str)


def test_coerce_true_false_are_plain_strings():
    # There is no bool coercion: 'true'/'false' remain strings.
    assert utils._coerce_value('true') == 'true'
    assert utils._coerce_value('false') == 'false'
    assert isinstance(utils._coerce_value('true'), str)


# ---------------------------------------------------------------------------
# _parse_path_segments
# ---------------------------------------------------------------------------
def test_parse_plain_dotted_path():
    assert utils._parse_path_segments('a.b.c') == ['a', 'b', 'c']


def test_parse_bracket_int_index():
    assert utils._parse_path_segments(
        'ml_sequence.object.sequence[0].object_min_confidence'
    ) == ['ml_sequence', 'object', 'sequence', 0, 'object_min_confidence']


def test_parse_bracket_name_index():
    # non-numeric bracket content kept as string for name-based lookup
    assert utils._parse_path_segments(
        'ml_sequence.object.sequence[YOLOv11 ONNX].enabled'
    ) == ['ml_sequence', 'object', 'sequence', 'YOLOv11 ONNX', 'enabled']


# ---------------------------------------------------------------------------
# _find_by_name
# ---------------------------------------------------------------------------
def test_find_by_name_case_insensitive():
    lst = [{'name': 'Alpha', 'v': 1}, {'name': 'Beta', 'v': 2}]
    assert utils._find_by_name(lst, 'beta') is lst[1]
    assert utils._find_by_name(lst, 'ALPHA') is lst[0]


def test_find_by_name_missing_returns_none():
    lst = [{'name': 'Alpha'}]
    assert utils._find_by_name(lst, 'zzz') is None


# ---------------------------------------------------------------------------
# apply_cli_overrides: simple dotted path
# ---------------------------------------------------------------------------
def test_simple_override_changes_value():
    g.config = {'show_percent': 30}
    utils.apply_cli_overrides(['show_percent=20'])
    assert g.config == {'show_percent': 20}
    assert isinstance(g.config['show_percent'], int)


def test_simple_override_only_touches_target():
    g.config = {'show_percent': 30, 'other': 'keep'}
    utils.apply_cli_overrides(['show_percent=20'])
    assert g.config == {'show_percent': 20, 'other': 'keep'}


def test_nested_dict_override():
    g.config = {'ml_sequence': {'general': {'model_sequence': 'object'}}}
    utils.apply_cli_overrides(['ml_sequence.general.model_sequence=face'])
    assert g.config == {'ml_sequence': {'general': {'model_sequence': 'face'}}}


# ---------------------------------------------------------------------------
# apply_cli_overrides: bracket index path
# ---------------------------------------------------------------------------
def _nested_sequence_config():
    return {
        'ml_sequence': {
            'object': {
                'sequence': [
                    {'name': 'YOLOv8', 'object_min_confidence': 0.3},
                    {'name': 'YOLOv11 ONNX', 'object_min_confidence': 0.7},
                ]
            }
        }
    }


def test_bracket_index_override_changes_right_element():
    g.config = _nested_sequence_config()
    expected = copy.deepcopy(g.config)
    expected['ml_sequence']['object']['sequence'][0]['object_min_confidence'] = 0.5

    utils.apply_cli_overrides(
        ['ml_sequence.object.sequence[0].object_min_confidence=0.5']
    )

    seq = g.config['ml_sequence']['object']['sequence']
    # targeted element changed...
    assert seq[0]['object_min_confidence'] == 0.5
    assert isinstance(seq[0]['object_min_confidence'], float)
    # ...and the other element is untouched
    assert seq[1]['object_min_confidence'] == 0.7
    assert g.config == expected


def test_bracket_index_second_element():
    g.config = _nested_sequence_config()
    utils.apply_cli_overrides(
        ['ml_sequence.object.sequence[1].object_min_confidence=0.9']
    )
    seq = g.config['ml_sequence']['object']['sequence']
    assert seq[1]['object_min_confidence'] == 0.9
    assert seq[0]['object_min_confidence'] == 0.3  # untouched


# ---------------------------------------------------------------------------
# apply_cli_overrides: name-based lookup
# ---------------------------------------------------------------------------
def test_name_based_lookup_targets_matching_element():
    g.config = _nested_sequence_config()
    utils.apply_cli_overrides(
        ['ml_sequence.object.sequence[YOLOv11 ONNX].object_min_confidence=0.42']
    )
    seq = g.config['ml_sequence']['object']['sequence']
    assert seq[1]['object_min_confidence'] == 0.42   # the named element
    assert seq[0]['object_min_confidence'] == 0.3    # the other one untouched


def test_name_based_lookup_case_insensitive():
    g.config = _nested_sequence_config()
    utils.apply_cli_overrides(
        ['ml_sequence.object.sequence[yolov8].object_min_confidence=0.11']
    )
    seq = g.config['ml_sequence']['object']['sequence']
    assert seq[0]['object_min_confidence'] == 0.11
    assert seq[1]['object_min_confidence'] == 0.7


# ---------------------------------------------------------------------------
# apply_cli_overrides: error / edge cases -> Warning + no-op, never raises
# ---------------------------------------------------------------------------
def test_malformed_override_no_equals_is_noop(rec_logger):
    g.config = {'show_percent': 30}
    before = copy.deepcopy(g.config)
    utils.apply_cli_overrides(['show_percent'])   # no '='
    assert g.config == before
    assert len(rec_logger.warnings) == 1
    assert 'malformed' in rec_logger.warnings[0].lower()


def test_index_out_of_range_is_noop(rec_logger):
    # final-segment integer index beyond list length hits the explicit
    # 'Override index out of range' guard.
    g.config = _nested_sequence_config()
    before = copy.deepcopy(g.config)
    utils.apply_cli_overrides(['ml_sequence.object.sequence[5]=0.5'])
    assert g.config == before
    assert len(rec_logger.warnings) == 1
    assert 'out of range' in rec_logger.warnings[0].lower()


def test_intermediate_index_out_of_range_is_noop(rec_logger):
    # index is not the last segment: obj[5] raises IndexError, caught -> Warning
    g.config = _nested_sequence_config()
    before = copy.deepcopy(g.config)
    utils.apply_cli_overrides(
        ['ml_sequence.object.sequence[5].object_min_confidence=0.5']
    )
    assert g.config == before
    assert len(rec_logger.warnings) == 1


def test_missing_key_is_noop(rec_logger):
    g.config = {'ml_sequence': {'object': {}}}
    before = copy.deepcopy(g.config)
    utils.apply_cli_overrides(['ml_sequence.object.nonexistent=1'])
    assert g.config == before
    assert len(rec_logger.warnings) == 1
    assert 'not found' in rec_logger.warnings[0].lower()


def test_missing_name_lookup_is_noop(rec_logger):
    g.config = _nested_sequence_config()
    before = copy.deepcopy(g.config)
    utils.apply_cli_overrides(
        ['ml_sequence.object.sequence[DoesNotExist].object_min_confidence=0.5']
    )
    assert g.config == before
    assert len(rec_logger.warnings) == 1


def test_intermediate_missing_key_is_noop(rec_logger):
    # KeyError while walking parents is caught and warned, not raised
    g.config = {'ml_sequence': {}}
    before = copy.deepcopy(g.config)
    utils.apply_cli_overrides(['ml_sequence.object.sequence[0].x=1'])
    assert g.config == before
    assert len(rec_logger.warnings) == 1


def test_multiple_overrides_applied_together():
    g.config = _nested_sequence_config()
    utils.apply_cli_overrides([
        'ml_sequence.object.sequence[0].object_min_confidence=0.15',
        'ml_sequence.object.sequence[YOLOv11 ONNX].object_min_confidence=0.85',
    ])
    seq = g.config['ml_sequence']['object']['sequence']
    assert seq[0]['object_min_confidence'] == 0.15
    assert seq[1]['object_min_confidence'] == 0.85
