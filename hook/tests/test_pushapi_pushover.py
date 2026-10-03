"""Tests for pushapi_plugins/pushapi_pushover.py.

The plugin is a top-level script, so each test executes the real file as
__main__ with sys.argv set, requests.post recorded, and the hardcoded
/etc/zm/secrets.yml redirected to a temp file. ``param_dict`` edits are made
by rewriting the "MODIFY THESE" lines, the way a user configures the script.
"""
import builtins
import os
import sys

import pytest
import requests
import yaml

PLUGIN = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'pushapi_plugins', 'pushapi_pushover.py'))
SECRETS = '/etc/zm/secrets.yml'


class _Resp:
    text = '{"status":1}'


class Harness:
    def __init__(self, monkeypatch, tmp_path):
        self.posts = []
        self.secrets_reads = 0
        self.secrets_path = tmp_path / 'secrets.yml'
        self.secrets_path.write_text(yaml.safe_dump({'secrets': {
            'PUSHOVER_APP_TOKEN': 'sec-token', 'PUSHOVER_USER_KEY': 'sec-user'}}))

        def post(url, data=None, files=None, **kw):
            self.posts.append({'url': url, 'data': dict(data), 'files': files, 'kw': kw})
            return _Resp()

        monkeypatch.setattr(requests, 'post', post)

        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if path == SECRETS:
                self.secrets_reads += 1
                path = str(self.secrets_path)
            return real_open(path, *a, **kw)

        monkeypatch.setattr(builtins, 'open', fake_open)
        self.monkeypatch = monkeypatch

    def run(self, argv, token=None, user=None):
        with open(PLUGIN) as f:
            src = f.read()
        if token:
            src = src.replace("'token': None,", "'token': {!r},".format(token), 1)
        if user:
            src = src.replace("'user' : None,", "'user' : {!r},".format(user), 1)
        self.monkeypatch.setattr(sys, 'argv', [PLUGIN] + argv)
        exec(compile(src, PLUGIN, 'exec'), {'__name__': '__main__'})
        return self.posts[-1]


@pytest.fixture
def h(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


ARGS = ['42', '3', 'Front', '[a] detected:person', 'event_start']


def test_credentials_read_from_secrets_by_default(h):
    post = h.run(ARGS)
    assert post['url'] == 'https://api.pushover.net/1/messages.json'
    assert post['data']['token'] == 'sec-token'
    assert post['data']['user'] == 'sec-user'
    assert post['data']['title'] == 'Front Alarm (42)'
    assert post['data']['message'].startswith('[a] detected:person at ')
    assert post['files'] is None


def test_token_in_script_user_from_secrets(h):
    # was `if not token or user:` -> secrets skipped, user sent as None
    post = h.run(ARGS, token='file-token')
    assert post['data']['token'] == 'file-token'
    assert post['data']['user'] == 'sec-user'


def test_event_end_title(h):
    post = h.run(ARGS[:4] + ['event_end'])
    assert post['data']['title'] == 'Ended:Front Alarm (42)'


def test_objdetect_image_attached(h, tmp_path):
    (tmp_path / 'objdetect.jpg').write_bytes(b'jpg')
    post = h.run(ARGS + [str(tmp_path)])
    name, fh, ctype = post['files']['attachment']
    assert name == 'image.jpg'
    assert fh.name == str(tmp_path / 'objdetect.jpg')
    assert ctype == 'image/jpeg'


def test_both_credentials_in_script_skip_secrets(h):
    post = h.run(ARGS, token='file-token', user='file-user')
    assert h.secrets_reads == 0
    assert post['data']['token'] == 'file-token'
    assert post['data']['user'] == 'file-user'


def test_post_has_timeout(h):
    post = h.run(ARGS)
    assert post['kw'].get('timeout')
