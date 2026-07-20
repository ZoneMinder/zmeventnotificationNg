"""Tests for tools/config_edit.py (programmatic INI editor).

Covers parse_var / parse_vars parsing and the apply_edits core (set,
comment_out, and the _global_ cross-section application) against a real
in-memory ConfigUpdater.
"""
import importlib.util
import os

import pytest

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_config_edit():
    path = os.path.join(_TOOLS_DIR, 'config_edit.py')
    spec = importlib.util.spec_from_file_location('config_edit', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # no longer runs argparse at import
    return mod


ce = _load_config_edit()
ConfigUpdater = ce.ConfigUpdater


SAMPLE_INI = """\
[network]
address=0.0.0.0
port=9000

[general]
restart_interval=100
base_data_path=/var/lib/zm
"""


def _updater(text=SAMPLE_INI):
    u = ConfigUpdater(space_around_delimiters=False)
    u.read_string(text)
    return u


# ---------------------------------------------------------------------------
# parse_var
# ---------------------------------------------------------------------------
class TestParseVar:
    def test_section_and_key(self):
        assert ce.parse_var('network:port=9999') == ('network', 'port', '9999')

    def test_no_section_is_global(self):
        assert ce.parse_var('port=9999') == ('_global_', 'port', '9999')

    def test_value_may_contain_equals(self):
        assert ce.parse_var('general:token=a=b=c') == ('general', 'token', 'a=b=c')

    def test_whitespace_stripped_around_key_and_section(self):
        assert ce.parse_var(' network : port =9999') == ('network', 'port', '9999')

    def test_value_with_spaces_preserved(self):
        assert ce.parse_var("general:base_data_path=/my new/path") == (
            'general', 'base_data_path', '/my new/path')

    def test_missing_equals_raises_clear_error(self):
        # regression: previously raised UnboundLocalError instead of a usable message
        with pytest.raises(ValueError) as exc:
            ce.parse_var('network:address')
        assert 'KEY=VALUE' in str(exc.value)


# ---------------------------------------------------------------------------
# parse_vars
# ---------------------------------------------------------------------------
class TestParseVars:
    def test_multiple_items_grouped_by_section(self):
        result = ce.parse_vars(['network:port=9999', 'general:restart_interval=60'])
        assert result == {
            'network': {'port': '9999'},
            'general': {'restart_interval': '60'},
        }

    def test_none_returns_empty(self):
        assert ce.parse_vars(None) == {}

    def test_global_and_sectioned_mixed(self):
        result = ce.parse_vars(['port=1', 'network:port=2'])
        assert result == {'_global_': {'port': '1'}, 'network': {'port': '2'}}


# ---------------------------------------------------------------------------
# apply_edits
# ---------------------------------------------------------------------------
class TestApplyEdits:
    def test_set_existing_key(self):
        u = _updater()
        ce.apply_edits(u, {'network': {'port': '9999'}})
        assert u['network']['port'].value == '9999'

    def test_set_leaves_other_keys_untouched(self):
        u = _updater()
        ce.apply_edits(u, {'network': {'port': '9999'}})
        assert u['network']['address'].value == '0.0.0.0'
        assert u['general']['restart_interval'].value == '100'

    def test_comment_out_key(self):
        u = _updater()
        ce.apply_edits(u, {'network': {'address': 'comment_out'}})
        # the option is renamed to '#address' so it is no longer a live option
        assert not u['network'].has_option('address')
        assert '#address=0.0.0.0' in u.__str__()

    def test_global_applies_to_all_sections_with_option(self):
        text = "[a]\nport=1\n\n[b]\nport=2\n\n[c]\nother=3\n"
        u = _updater(text)
        ce.apply_edits(u, {'_global_': {'port': '8080'}})
        assert u['a']['port'].value == '8080'
        assert u['b']['port'].value == '8080'
        # section c never had 'port' so nothing is added
        assert not u['c'].has_option('port')

    def test_global_comment_out_across_sections(self):
        text = "[a]\nport=1\n\n[b]\nport=2\n"
        u = _updater(text)
        ce.apply_edits(u, {'_global_': {'port': 'comment_out'}})
        assert not u['a'].has_option('port')
        assert not u['b'].has_option('port')
