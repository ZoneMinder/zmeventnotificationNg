"""Integration tests that drive the real ``zm_detect.main_handler`` orchestration.

Unlike the other unit tests, these do not stop at the argparse early-exits.
They monkeypatch the symbols ``zm_detect`` imports from pyzm (``ZMClient``,
``Detector``, ``DetectionResult``, ``cv2``) and the two ``utils`` config
loaders with *recording fakes*, then run ``main_handler`` end to end.  That
exercises the genuine wiring: ZMClient construction (incl. ``conf_path``),
``--debug`` log elevation, remote-gateway injection + local fallback, the
URL-mode frame fetch through ``zm.api.request``, the ``--fakeit`` override,
note updates, object tagging, and push dispatch.

Everything is asserted on real side effects the production code produces, so a
regression that broke any of those paths fails a test here.
"""
import sys

import numpy as np
import pytest

import zmes_hook_helpers.common_params as g
import zmes_hook_helpers.utils as utils
import zmes_hook_helpers.push as push_mod
import zm_detect


# ---------------------------------------------------------------------------
# Recording fakes
# ---------------------------------------------------------------------------
class LocalLogger:
    def Debug(self, level, msg): pass
    def Info(self, msg): pass
    def Warning(self, msg): pass
    def Error(self, msg): self.errors.append(msg)
    def Fatal(self, msg): raise SystemExit(msg)
    def close(self): pass
    errors = []


class FakeApi:
    def __init__(self):
        self.portal_url = 'https://zm.example/zm'
        self.requested_urls = []
        self.response = None            # set per test

    def request(self, url):
        self.requested_urls.append(url)
        return self.response


class FakeEvent:
    def __init__(self, notes=''):
        self.notes = notes
        self.updated_notes = None
        self.tagged = None
        self.saved = None

    def update_notes(self, notes):
        self.updated_notes = notes

    def tag(self, labels):
        self.tagged = list(labels)

    def save_objdetect(self, image, metadata, path_override=None):
        self.saved = {'image': image, 'metadata': metadata, 'path_override': path_override}
        return path_override or '/tmp/objdetect.jpg'


class FakeMonitor:
    def __init__(self, name='Front Door'):
        self.name = name


class FakeZMClient:
    last = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.api = FakeApi()
        self.event_obj = FakeEvent(notes=FakeZMClient.event_notes)
        FakeZMClient.last = self

    # class-level knob so a test can seed the event notes before construction
    event_notes = ''

    def monitor(self, mid):
        return FakeMonitor()

    def event(self, eid):
        return self.event_obj


class FakeResult:
    def __init__(self, data, image=None):
        self._data = data
        self.image = image
        self.annotate_calls = []

    def to_dict(self):
        return dict(self._data)

    def annotate(self, **kw):
        self.annotate_calls.append(kw)
        return 'ANNOTATED_IMAGE'


class FakeDetector:
    """from_dict records the ml_options it was handed; detect returns a
    FakeResult built from class-level knobs.  ``fail_when_gateway`` lets a test
    simulate a remote-gateway failure that should trigger the local fallback."""
    from_dict_opts = []
    result_data = {}
    result_image = None
    fail_when_gateway = False
    last_result = None

    def __init__(self, opts):
        self._opts = opts

    @classmethod
    def from_dict(cls, opts):
        # snapshot the general block so later mutation doesn't rewrite history
        snap = dict(opts)
        snap['general'] = dict(opts.get('general', {}))
        cls.from_dict_opts.append(snap)
        return cls(opts)

    def _run(self):
        if FakeDetector.fail_when_gateway and (self._opts.get('general') or {}).get('ml_gateway'):
            raise RuntimeError('simulated remote gateway failure')
        FakeDetector.last_result = FakeResult(FakeDetector.result_data, FakeDetector.result_image)
        return FakeDetector.last_result

    def detect(self, *a, **kw):
        return self._run()

    def detect_event(self, *a, **kw):
        return self._run()


class FakeDetectionResult:
    """Stand-in for pyzm DetectionResult used by the --fakeit rebuild path."""
    last = None

    def __init__(self, data):
        self.data = data
        self.image = None
        FakeDetectionResult.last = self

    @classmethod
    def from_dict(cls, data):
        return cls(dict(data))

    def annotate(self, **kw):
        return 'ANNOTATED_IMAGE'


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def _base_config(tmp_path):
    img_dir = tmp_path / 'images'
    img_dir.mkdir(exist_ok=True)
    return {
        'base_data_path': str(tmp_path),
        'ml_sequence': {'general': {}},
        'stream_sequence': {'frame_strategy': 'first'},
        'api_portal': 'https://zm.example/zm/api',
        'user': 'u', 'password': 'p',
        'portal': 'https://zm.example/zm',
        'allow_self_signed': 'no',
        'zm_conf_path': None,
        'import_zm_zones': 'no',
        'image_path': str(img_dir),
        'wait': 0,
        'write_image_to_zm': 'no',
        'write_debug_image': 'no',
        'poly_color': '(255,255,255)',
        'poly_thickness': 1,
        'show_percent': 'no',
        'show_frame_match_type': 'yes',
        'show_models': 'no',
        'tag_detected_objects': 'no',
        'push': {},
    }


DETECTED = {
    'labels': ['dog'],
    'boxes': [[10, 20, 30, 40]],
    'frame_id': 'alarm',
    'confidences': [0.91],
    'image_dimensions': {'original': [100, 100], 'resized': [100, 100]},
}


class Harness:
    def __init__(self):
        self.captured_override = None
        self.log_name = None
        self.push_calls = []
        self.imwrite_calls = []


@pytest.fixture
def harness(monkeypatch, tmp_path):
    h = Harness()
    cfg = _base_config(tmp_path)

    # reset class-level fake state
    FakeDetector.from_dict_opts = []
    FakeDetector.result_data = dict(DETECTED)
    FakeDetector.result_image = None
    FakeDetector.fail_when_gateway = False
    FakeDetector.last_result = None
    FakeZMClient.last = None
    FakeZMClient.event_notes = ''
    LocalLogger.errors = []

    # config loaders: get_pyzm_config seeds pyzm_overrides; process_config fills the rest
    def fake_get_pyzm_config(args):
        g.config['pyzm_overrides'] = {}

    def fake_process_config(args, ctx):
        g.config.update(cfg)

    monkeypatch.setattr(utils, 'get_pyzm_config', fake_get_pyzm_config)
    monkeypatch.setattr(utils, 'process_config', fake_process_config)

    # capture the override dict handed to setup_zm_logging (proves --debug elevation)
    def fake_setup(name=None, override=None):
        h.captured_override = dict(override) if override else {}
        h.log_name = name
        return LocalLogger()

    monkeypatch.setattr(sys.modules['pyzm.log'], 'setup_zm_logging', fake_setup)

    monkeypatch.setattr(zm_detect, 'ZMClient', FakeZMClient)
    monkeypatch.setattr(zm_detect, 'Detector', FakeDetector)
    monkeypatch.setattr(zm_detect, 'DetectionResult', FakeDetectionResult)

    def fake_imwrite(path, image):
        h.imwrite_calls.append({'path': path, 'image': image})
        return True

    monkeypatch.setattr(zm_detect.cv2, 'imwrite', fake_imwrite)

    def fake_push(zm, config, mid, eid, mon_name, cause, logger, no_match=False):
        h.push_calls.append({'mid': mid, 'eid': eid, 'mon_name': mon_name,
                             'cause': cause, 'no_match': no_match})

    monkeypatch.setattr(push_mod, 'send_push_notifications', fake_push)

    h.cfg = cfg
    return h


def _run(monkeypatch, tmp_path, extra_argv):
    cfgfile = tmp_path / 'objectconfig.yml'
    cfgfile.write_text('# stub config (process_config is patched)\n')
    argv = ['zm_detect.py', '-c', str(cfgfile)] + extra_argv
    monkeypatch.setattr(sys, 'argv', argv)
    zm_detect.main_handler()


# ---------------------------------------------------------------------------
# zm_conf_path wiring  (recent feature: a086a1e)
# ---------------------------------------------------------------------------
def test_zm_conf_path_passed_to_zmclient(harness, monkeypatch, tmp_path):
    harness.cfg['zm_conf_path'] = '/etc/zm/zm.conf'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeZMClient.last is not None
    assert FakeZMClient.last.init_kwargs.get('conf_path') == '/etc/zm/zm.conf'


def test_zm_conf_path_none_when_unset(harness, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeZMClient.last.init_kwargs.get('conf_path') is None


def test_zmclient_gets_credentials_and_ssl(harness, monkeypatch, tmp_path):
    harness.cfg['allow_self_signed'] = 'yes'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    kw = FakeZMClient.last.init_kwargs
    assert kw['user'] == 'u' and kw['password'] == 'p'
    assert kw['portal_url'] == 'https://zm.example/zm'
    # allow_self_signed=yes  ->  verify_ssl=False
    assert kw['verify_ssl'] is False


# ---------------------------------------------------------------------------
# --debug log elevation  (recent fix: 6abdfbc)
# ---------------------------------------------------------------------------
def test_debug_flag_elevates_log_level(harness, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7', '-d'])
    ov = harness.captured_override
    assert ov.get('log_level_debug') == 5
    assert ov.get('log_debug') is True
    assert ov.get('dump_console') is True


def test_no_debug_flag_leaves_log_level_default(harness, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert 'log_level_debug' not in harness.captured_override


# ---------------------------------------------------------------------------
# URL-mode frame fetch through pyzm ZMAPI  (recent fix: 22bf084 / 0d696fa)
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, content):
        self.content = content


def test_url_mode_fetch_uses_zmapi_and_annotates(harness, monkeypatch, tmp_path):
    # gateway/URL mode: detector returns boxes but NO image
    harness.cfg['write_image_to_zm'] = 'yes'
    FakeDetector.result_image = None
    harness.cfg  # image absent from result_data -> triggers fetch block

    # api.request returns bytes; imdecode yields a valid array
    fake_img = np.zeros((10, 10, 3), dtype='uint8')
    monkeypatch.setattr(zm_detect.cv2, 'imdecode', lambda arr, flag: fake_img)

    # seed the response on the client the code will build
    orig_init = FakeZMClient.__init__
    def init_with_resp(self, **kw):
        orig_init(self, **kw)
        self.api.response = _Resp(b'\xff\xd8\xff\xe0jpegbytes')
    monkeypatch.setattr(FakeZMClient, '__init__', init_with_resp)

    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])

    api = FakeZMClient.last.api
    assert len(api.requested_urls) == 1
    url = api.requested_urls[0]
    assert url == 'https://zm.example/zm/index.php?view=image&eid=55555&fid=alarm'
    # image was decoded and handed to annotate + save_objdetect
    result = FakeDetector.last_result
    assert result.image is fake_img
    assert len(result.annotate_calls) == 1
    assert FakeZMClient.last.event_obj.saved is not None
    assert FakeZMClient.last.event_obj.saved['image'] == 'ANNOTATED_IMAGE'


def test_url_mode_fetch_decode_failure_logs_and_skips(harness, monkeypatch, tmp_path):
    harness.cfg['write_image_to_zm'] = 'yes'
    monkeypatch.setattr(zm_detect.cv2, 'imdecode', lambda arr, flag: None)   # undecodable

    orig_init = FakeZMClient.__init__
    def init_with_resp(self, **kw):
        orig_init(self, **kw)
        self.api.response = _Resp(b'garbage')
    monkeypatch.setattr(FakeZMClient, '__init__', init_with_resp)

    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])

    # decode returned None -> no image set, nothing saved, an error was logged
    result = FakeDetector.last_result
    assert result.image is None
    assert FakeZMClient.last.event_obj.saved is None
    assert any('could not be decoded' in e for e in LocalLogger.errors)


def test_no_fetch_when_image_already_present(harness, monkeypatch, tmp_path):
    # when the detector already returned an image, no URL fetch should happen
    harness.cfg['write_image_to_zm'] = 'yes'
    FakeDetector.result_data = dict(DETECTED, image=np.zeros((5, 5, 3), dtype='uint8'))
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeZMClient.last.api.requested_urls == []


# ---------------------------------------------------------------------------
# Remote-gateway injection + local fallback
# ---------------------------------------------------------------------------
def test_gateway_settings_injected_into_ml_options(harness, monkeypatch, tmp_path):
    harness.cfg['ml_gateway'] = 'https://gw.example/ml'
    harness.cfg['ml_user'] = 'gwu'
    harness.cfg['ml_password'] = 'gwp'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    general = FakeDetector.from_dict_opts[0]['general']
    assert general['ml_gateway'] == 'https://gw.example/ml'
    assert general['ml_user'] == 'gwu'
    assert general['ml_gateway_mode'] == 'url'   # default injected


def test_remote_failure_falls_back_to_local(harness, monkeypatch, tmp_path, capsys):
    harness.cfg['ml_gateway'] = 'https://gw.example/ml'
    harness.cfg['ml_fallback_local'] = 'yes'
    FakeDetector.fail_when_gateway = True
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    # two constructions: gateway (raises) then local (ml_gateway cleared)
    assert len(FakeDetector.from_dict_opts) == 2
    assert FakeDetector.from_dict_opts[1]['general']['ml_gateway'] is None
    # detection still produced output
    assert 'detected:dog' in capsys.readouterr().out


def test_monitor_id_injected_into_ml_options(harness, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeDetector.from_dict_opts[0]['general']['monitor_id'] == '7'


def test_image_path_injected_into_ml_options(harness, monkeypatch, tmp_path):
    harness.cfg['image_path'] = '/custom/img/path'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeDetector.from_dict_opts[0]['general']['image_path'] == '/custom/img/path'


def test_remote_failure_no_fallback_raises(harness, monkeypatch, tmp_path):
    harness.cfg['ml_gateway'] = 'https://gw.example/ml'
    harness.cfg['ml_fallback_local'] = 'no'
    FakeDetector.fail_when_gateway = True
    with pytest.raises(RuntimeError):
        _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])


# ---------------------------------------------------------------------------
# --fakeit override
# ---------------------------------------------------------------------------
def test_fakeit_overrides_labels(harness, monkeypatch, tmp_path, capsys):
    FakeDetector.result_data = {}          # detector finds nothing
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7', '--fakeit', 'dog,person'])
    out = capsys.readouterr().out
    assert 'detected:' in out
    assert 'dog' in out and 'person' in out
    assert FakeDetectionResult.last is not None
    assert FakeDetectionResult.last.data['labels'] == ['dog', 'person']


def test_fakeit_with_show_models_does_not_crash(harness, monkeypatch, tmp_path, capsys):
    """--fakeit + show_models: yes must not KeyError on model_names.

    format_detection_output reads matched_data['model_names'][idx] when
    show_models=='yes'; the fakeit override must supply model_names to match
    the fake label count.
    """
    FakeDetector.result_data = {}          # detector finds nothing (KeyError path)
    harness.cfg['show_models'] = 'yes'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7', '--fakeit', 'dog,person'])
    out = capsys.readouterr().out
    assert 'detected:' in out
    assert 'dog' in out and 'person' in out


# ---------------------------------------------------------------------------
# ZM note update
# ---------------------------------------------------------------------------
def test_notes_updated_preserving_motion(harness, monkeypatch, tmp_path):
    FakeZMClient.event_notes = 'old detection Motion:Zone1'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7', '-n'])
    updated = FakeZMClient.last.event_obj.updated_notes
    assert updated is not None
    assert updated.startswith('[a] detected:dog')
    assert updated.endswith('| Motion:Zone1')


def test_notes_not_updated_without_n_flag(harness, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeZMClient.last.event_obj.updated_notes is None


# ---------------------------------------------------------------------------
# Object tagging
# ---------------------------------------------------------------------------
def test_tagging_enabled_calls_event_tag(harness, monkeypatch, tmp_path):
    harness.cfg['tag_detected_objects'] = 'yes'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeZMClient.last.event_obj.tagged == ['dog']


def test_tagging_disabled_does_not_tag(harness, monkeypatch, tmp_path):
    harness.cfg['tag_detected_objects'] = 'no'
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert FakeZMClient.last.event_obj.tagged is None


# ---------------------------------------------------------------------------
# Push dispatch
# ---------------------------------------------------------------------------
def test_push_sent_on_match(harness, monkeypatch, tmp_path):
    harness.cfg['push'] = {'enabled': 'yes'}
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert len(harness.push_calls) == 1
    assert harness.push_calls[0]['no_match'] is False
    assert harness.push_calls[0]['eid'] == '55555'
    assert harness.push_calls[0]['mon_name'] == 'Front Door'


def test_push_skipped_when_disabled(harness, monkeypatch, tmp_path):
    harness.cfg['push'] = {'enabled': 'no'}
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert harness.push_calls == []


def test_no_match_push_sent_when_configured(harness, monkeypatch, tmp_path):
    FakeDetector.result_data = {}          # nothing detected
    harness.cfg['push'] = {'enabled': 'yes', 'send_push_on_no_match': 'yes'}
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert len(harness.push_calls) == 1
    assert harness.push_calls[0]['no_match'] is True


def test_no_match_no_push_when_not_configured(harness, monkeypatch, tmp_path):
    FakeDetector.result_data = {}
    harness.cfg['push'] = {'enabled': 'yes'}   # send_push_on_no_match absent
    _run(monkeypatch, tmp_path, ['-e', '55555', '-m', '7'])
    assert harness.push_calls == []
