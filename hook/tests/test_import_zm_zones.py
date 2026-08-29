"""Tests for import_zm_zones using pyzm ZMClient. Ref: ZoneMinder/zmeventnotificationNg#18"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import zmes_hook_helpers.common_params as g
from pyzm.models.zm import Zone


class _FakeLogger:
    def __init__(self):
        self.error = []

    def Debug(self, *a, **kw): pass
    def Info(self, *a, **kw): pass
    def Error(self, msg): self.error.append(msg)


def _make_zone(name, coords, zone_type="Active"):
    """Helper to create a Zone with _raw metadata."""
    points = [tuple(map(float, p.split(','))) for p in coords.split()]
    return Zone(
        name=name,
        points=points,
        _raw={"Zone": {"Name": name, "Type": zone_type, "Coords": coords}},
    )


class TestImportZmZones:
    def setup_method(self):
        g.polygons = []
        g.zone_patterns = {}
        g.logger = _FakeLogger()
        g.config = {
            'only_triggered_zm_zones': 'no',
            'import_zm_zones': 'yes',
        }

    def test_basic_zone_import(self):
        """Zones are imported and names normalized."""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Front Yard", "0,0 100,0 100,100 0,100"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", None, mock_zm)
        assert len(g.polygons) == 1
        assert g.polygons[0]['name'] == 'front_yard'
        assert g.polygons[0]['value'] == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        assert g.polygons[0]['pattern'] is None

    def test_inactive_zones_skipped(self):
        """Inactive zones are not imported."""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Active Zone", "0,0 100,0 100,100 0,100", "Active"),
            _make_zone("Disabled Zone", "50,50 150,50 150,150 50,150", "Inactive"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", None, mock_zm)
        assert len(g.polygons) == 1
        assert g.polygons[0]['name'] == 'active_zone'

    def test_only_triggered_zm_zones_filters_by_reason(self):
        """When only_triggered_zm_zones is yes, only zones matching alarm reason are imported."""
        from zmes_hook_helpers.utils import import_zm_zones

        g.config['only_triggered_zm_zones'] = 'yes'

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Driveway", "0,0 100,0 100,100 0,100"),
            _make_zone("Backyard", "50,50 150,50 150,150 50,150"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", "Motion: Driveway", mock_zm)
        assert len(g.polygons) == 1
        assert g.polygons[0]['name'] == 'driveway'

    def test_no_reason_match_imports_all_when_not_triggered(self):
        """When only_triggered_zm_zones is no, reason is ignored."""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Driveway", "0,0 100,0 100,100 0,100"),
            _make_zone("Backyard", "50,50 150,50 150,150 50,150"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", "Motion: Driveway", mock_zm)
        assert len(g.polygons) == 2

    def test_float_percentage_coords(self):
        """Float/percentage coordinates are preserved. Ref: ZoneMinder/zmeventnotificationNg#18"""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Porch", "26.41,33.5 75.2,33.5 75.2,90.1 26.41,90.1"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", None, mock_zm)
        assert len(g.polygons) == 1
        assert g.polygons[0]['value'] == [(26.41, 33.5), (75.2, 33.5), (75.2, 90.1), (26.41, 90.1)]

    def test_zone_pattern_passed_through(self):
        """Zone pattern and ignore_pattern from ZM are passed through."""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        z = _make_zone("Front Yard", "0,0 100,0 100,100 0,100")
        z.pattern = "person|car"
        z.ignore_pattern = "chair"
        mock_monitor.get_zones.return_value = [z]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", None, mock_zm)
        assert len(g.polygons) == 1
        assert g.polygons[0]['pattern'] == 'person|car'
        assert g.polygons[0]['ignore_pattern'] == 'chair'

    def test_zone_no_pattern_stays_none(self):
        """Zone without pattern/ignore_pattern sets them to None."""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Back Yard", "0,0 100,0 100,100 0,100"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", None, mock_zm)
        assert g.polygons[0]['pattern'] is None
        assert g.polygons[0]['ignore_pattern'] is None

    def test_name_normalization(self):
        """Zone names: spaces to underscores, lowercased."""
        from zmes_hook_helpers.utils import import_zm_zones

        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = [
            _make_zone("Front Yard Camera 1", "0,0 100,0 100,100 0,100"),
        ]
        mock_zm.monitor.return_value = mock_monitor

        import_zm_zones("1", None, mock_zm)
        assert g.polygons[0]['name'] == 'front_yard_camera_1'


class TestConfigPatternJoin:
    """Config patterns join onto ZM geometry by zone name. Ref: #48"""

    def setup_method(self):
        g.polygons = []
        g.zone_patterns = {}
        g.logger = _FakeLogger()
        g.config = {
            'only_triggered_zm_zones': 'no',
            'import_zm_zones': 'yes',
        }

    def _client(self, zones):
        mock_zm = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_zones.return_value = zones
        mock_zm.monitor.return_value = mock_monitor
        return mock_zm

    def test_config_pattern_applied_to_imported_zone(self):
        """A config zone with patterns but no coords stamps them onto ZM's geometry."""
        from zmes_hook_helpers.utils import import_zm_zones

        g.zone_patterns = {
            'front_yard': {'pattern': '(person|car)', 'ignore_pattern': '(cat)'},
        }
        import_zm_zones("1", None, self._client([
            _make_zone("Front Yard", "0,0 100,0 100,100 0,100"),
        ]))

        assert len(g.polygons) == 1
        poly = g.polygons[0]
        assert poly['name'] == 'front_yard'
        assert poly['value'] == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        assert poly['pattern'] == '(person|car)'
        assert poly['ignore_pattern'] == '(cat)'
        assert g.logger.error == []

    def test_zone_without_config_entry_keeps_none_pattern(self):
        """A ZM zone the config says nothing about is imported unchanged."""
        from zmes_hook_helpers.utils import import_zm_zones

        g.zone_patterns = {'front_yard': {'pattern': '(person)', 'ignore_pattern': None}}
        import_zm_zones("1", None, self._client([
            _make_zone("Front Yard", "0,0 100,0 100,100 0,100"),
            _make_zone("Back Yard", "0,0 50,0 50,50 0,50"),
        ]))

        back = [p for p in g.polygons if p['name'] == 'back_yard'][0]
        assert back['pattern'] is None
        assert back['ignore_pattern'] is None

    def test_config_coords_win_and_zm_zone_not_duplicated(self):
        """A config zone with coords pins geometry; the same ZM zone is not appended."""
        from zmes_hook_helpers.utils import import_zm_zones

        g.polygons = [{
            'name': 'front_yard',
            'value': [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)],
            'pattern': '(person)',
            'ignore_pattern': None,
        }]
        g.zone_patterns = {'front_yard': {'pattern': '(person)', 'ignore_pattern': None}}
        import_zm_zones("1", None, self._client([
            _make_zone("Front Yard", "0,0 100,0 100,100 0,100"),
        ]))

        assert len(g.polygons) == 1
        assert g.polygons[0]['value'] == [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]
        assert g.logger.error == []

    def test_unmatched_config_pattern_logs_error(self):
        """A pattern-only zone matching no imported ZM zone is reported, not silent."""
        from zmes_hook_helpers.utils import import_zm_zones

        g.zone_patterns = {'no_such_zone': {'pattern': '(person)', 'ignore_pattern': None}}
        import_zm_zones("1", None, self._client([
            _make_zone("Front Yard", "0,0 100,0 100,100 0,100"),
        ]))

        assert len(g.polygons) == 1
        assert any('no_such_zone' in m for m in g.logger.error)

    def test_unmatched_config_pattern_silent_when_only_triggered(self):
        """Under only_triggered_zm_zones a non-triggered zone is legitimately absent."""
        from zmes_hook_helpers.utils import import_zm_zones

        g.config['only_triggered_zm_zones'] = 'yes'
        g.zone_patterns = {'backyard': {'pattern': '(person)', 'ignore_pattern': None}}
        import_zm_zones("1", "Motion: Driveway", self._client([
            _make_zone("Driveway", "0,0 100,0 100,100 0,100"),
            _make_zone("Backyard", "0,0 50,0 50,50 0,50"),
        ]))

        assert [p['name'] for p in g.polygons] == ['driveway']
        assert g.logger.error == []

    def test_config_pattern_beats_zm_pattern(self):
        """When ZM ever supplies a pattern, the config's still wins."""
        from zmes_hook_helpers.utils import import_zm_zones

        z = _make_zone("Front Yard", "0,0 100,0 100,100 0,100")
        z.pattern = "(dog)"
        z.ignore_pattern = "(bird)"
        g.zone_patterns = {'front_yard': {'pattern': '(person)', 'ignore_pattern': '(cat)'}}
        import_zm_zones("1", None, self._client([z]))

        assert g.polygons[0]['pattern'] == '(person)'
        assert g.polygons[0]['ignore_pattern'] == '(cat)'

