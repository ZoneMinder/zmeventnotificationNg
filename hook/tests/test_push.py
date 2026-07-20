"""Tests for send_push_notifications in zmes_hook_helpers/push.py.

These exercise the real branching logic of send_push_notifications with a
fake Notification object (mirroring pyzm's Notification interface) and a
monkeypatched requests.post so no real network calls happen.

The module is exercised through g.config (common_params) exactly as the
production caller supplies it.
"""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import zmes_hook_helpers.common_params as g  # noqa: E402
import zmes_hook_helpers.push as push  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeNotification:
    """Mimics pyzm.models.zm.Notification for the attributes push.py reads.

    should_notify / is_throttled reproduce the real pyzm semantics so the
    tests exercise genuine filtering behaviour, while delete() and
    update_last_sent() record their invocation for assertions.
    """

    def __init__(self, token='tok123456789abc', platform='android',
                 monitor_list=None, interval=0, throttled=False,
                 push_state='enabled', app_version='1.6.0', profile=None,
                 badge_count=0):
        self.token = token
        self.platform = platform
        self.monitor_list = monitor_list
        self.interval = interval
        self.push_state = push_state
        self.app_version = app_version
        self.profile = profile
        self.badge_count = badge_count
        self._throttled = throttled
        # recorders
        self.deleted = False
        self.last_sent_badge = None
        self.update_last_sent_called = False

    def monitors(self):
        if not self.monitor_list:
            return None
        return [int(m.strip()) for m in self.monitor_list.split(',') if m.strip()]

    def should_notify(self, monitor_id):
        if self.push_state != 'enabled':
            return False
        monitors = self.monitors()
        if monitors is None:
            return True
        return monitor_id in monitors

    def is_throttled(self):
        return self._throttled

    def delete(self):
        self.deleted = True

    def update_last_sent(self, badge=None):
        self.update_last_sent_called = True
        self.last_sent_badge = badge


class FakeResponse:
    def __init__(self, status_code=200, text='OK'):
        self.status_code = status_code
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300


class FakeZM:
    def __init__(self, notifications):
        self._notifications = notifications

    def notifications(self):
        return list(self._notifications)


class PostRecorder:
    """Callable stand-in for requests.post; records calls and returns a set response."""

    def __init__(self, response=None):
        self.response = response or FakeResponse(200, 'OK')
        self.calls = []

    def __call__(self, url, headers=None, data=None, timeout=None):
        payload = json.loads(data) if data is not None else None
        self.calls.append({
            'url': url,
            'headers': headers,
            'data': data,
            'payload': payload,
            'timeout': timeout,
        })
        return self.response

    @property
    def call_count(self):
        return len(self.calls)

    @property
    def last_payload(self):
        return self.calls[-1]['payload']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def base_push_cfg(**overrides):
    cfg = {
        'enabled': 'yes',
        'fcm_v1_url': 'https://fcm.example/send',
        'fcm_v1_key': 'secret-key',
    }
    cfg.update(overrides)
    return cfg


def install_post(monkeypatch, recorder):
    monkeypatch.setattr(push.requests, 'post', recorder)


def run(zm, notif=None, monitor_id=5, event_id=42, monitor_name='Front',
        cause='person detected', no_match=False):
    """Invoke send_push_notifications with g.config and g.logger from conftest."""
    return push.send_push_notifications(
        zm, g.config, monitor_id, event_id, monitor_name, cause,
        g.logger, no_match=no_match)


# ---------------------------------------------------------------------------
# Config gating
# ---------------------------------------------------------------------------
class TestConfigGating:
    def test_disabled_no_post(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(enabled='no')}
        run(FakeZM([FakeNotification()]))
        assert rec.call_count == 0

    def test_missing_push_key_no_post(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {}
        run(FakeZM([FakeNotification()]))
        assert rec.call_count == 0

    def test_missing_url_or_key_no_post(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': {'enabled': 'yes', 'fcm_v1_key': 'k'}}
        run(FakeZM([FakeNotification()]))
        assert rec.call_count == 0

    def test_no_notifications_no_post(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        run(FakeZM([]))
        assert rec.call_count == 0


# ---------------------------------------------------------------------------
# Monitor filtering (should_notify)
# ---------------------------------------------------------------------------
class TestMonitorFilter:
    def test_monitor_allowed(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(monitor_list='5,9')
        run(FakeZM([notif]), monitor_id=5)
        assert rec.call_count == 1
        assert rec.last_payload['data']['mid'] == '5'

    def test_monitor_excluded(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(monitor_list='9,10')
        run(FakeZM([notif]), monitor_id=5)
        assert rec.call_count == 0
        assert notif.update_last_sent_called is False

    def test_monitor_list_none_allows_all(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(monitor_list=None)
        run(FakeZM([notif]), monitor_id=999)
        assert rec.call_count == 1


# ---------------------------------------------------------------------------
# Throttling (is_throttled)
# ---------------------------------------------------------------------------
class TestThrottle:
    def test_throttled_suppressed(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(throttled=True, interval=60)
        run(FakeZM([notif]))
        assert rec.call_count == 0
        assert notif.update_last_sent_called is False

    def test_not_throttled_sends(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(throttled=False)
        run(FakeZM([notif]))
        assert rec.call_count == 1


# ---------------------------------------------------------------------------
# Picture URL rewrite (no_match -> fid=alarm)
# ---------------------------------------------------------------------------
class TestPictureUrl:
    PIC = 'https://portal/zm?eid=EVENTID&fid=objdetect'

    def test_no_match_rewrites_fid(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(include_picture='yes', picture_url=self.PIC)}
        run(FakeZM([FakeNotification()]), event_id=42, no_match=True)
        img = rec.last_payload['image_url']
        assert img == 'https://portal/zm?eid=42&fid=alarm'
        assert 'fid=objdetect' not in img

    def test_match_keeps_objdetect(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(include_picture='yes', picture_url=self.PIC)}
        run(FakeZM([FakeNotification()]), event_id=42, no_match=False)
        img = rec.last_payload['image_url']
        assert img == 'https://portal/zm?eid=42&fid=objdetect'

    def test_picture_credentials_appended(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(
            include_picture='yes', picture_url=self.PIC,
            picture_portal_username='admin', picture_portal_password='pw')}
        run(FakeZM([FakeNotification()]), event_id=7)
        img = rec.last_payload['image_url']
        assert img == 'https://portal/zm?eid=7&fid=objdetect&username=admin&password=pw'

    def test_no_picture_when_disabled(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        run(FakeZM([FakeNotification()]))
        assert 'image_url' not in rec.last_payload


# ---------------------------------------------------------------------------
# Platform-specific payload assembly
# ---------------------------------------------------------------------------
class TestPlatformPayload:
    def test_android_payload(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(android_priority='high', android_ttl=3600)}
        notif = FakeNotification(platform='android', app_version='1.6.0')
        run(FakeZM([notif]))
        p = rec.last_payload
        assert 'android' in p
        assert 'ios' not in p
        assert p['android']['icon'] == 'ic_stat_notification'
        assert p['android']['priority'] == 'high'
        assert p['android']['ttl'] == '3600'
        assert p['android']['channel'] == 'zmninja'

    def test_android_no_channel_when_version_unknown(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(platform='android', app_version='unknown')
        run(FakeZM([notif]))
        assert 'channel' not in rec.last_payload['android']

    def test_ios_payload(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(platform='ios')
        run(FakeZM([notif]))
        p = rec.last_payload
        assert 'ios' in p
        assert 'android' not in p
        assert p['ios']['thread_id'] == 'zmninja_alarm'
        assert p['ios']['headers']['apns-priority'] == '10'
        assert p['ios']['headers']['apns-push-type'] == 'alert'

    def test_replace_push_messages_android_tag(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(replace_push_messages='yes')}
        run(FakeZM([FakeNotification(platform='android')]))
        assert rec.last_payload['android']['tag'] == 'zmninjapush'

    def test_replace_push_messages_ios_collapse(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(replace_push_messages='yes')}
        run(FakeZM([FakeNotification(platform='ios')]))
        assert rec.last_payload['ios']['headers']['apns-collapse-id'] == 'zmninjapush'

    def test_profile_ios_subtitle(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(include_profile_in_push='yes')}
        run(FakeZM([FakeNotification(platform='ios', profile='night')]))
        p = rec.last_payload
        assert p['data']['profile'] == 'night'
        assert p['ios']['subtitle'] == 'night'

    def test_profile_android_appends_body(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg(include_profile_in_push='yes')}
        run(FakeZM([FakeNotification(platform='android', profile='night')]),
            cause='person detected')
        p = rec.last_payload
        assert p['body'] == 'person detected — night'


# ---------------------------------------------------------------------------
# Core payload / headers
# ---------------------------------------------------------------------------
class TestCorePayload:
    def test_payload_and_headers(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token='abcdef1234567890', badge_count=3)
        run(FakeZM([notif]), monitor_id=5, event_id=42,
            monitor_name='Front', cause='person detected')
        call = rec.calls[-1]
        assert call['url'] == 'https://fcm.example/send'
        assert call['headers']['Authorization'] == 'secret-key'
        assert call['headers']['Content-Type'] == 'application/json'
        assert call['timeout'] == 10
        p = call['payload']
        assert p['token'] == 'abcdef1234567890'
        assert p['title'] == 'Front Alarm'
        assert p['body'] == 'person detected'
        assert p['sound'] == 'default'
        assert p['badge'] == 4  # badge_count + 1
        assert p['data']['mid'] == '5'
        assert p['data']['eid'] == '42'
        assert p['data']['monitorName'] == 'Front'
        assert p['data']['cause'] == 'person detected'

    def test_empty_cause_falls_back_to_event_body(self, monkeypatch):
        rec = PostRecorder()
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        run(FakeZM([FakeNotification()]), event_id=99, monitor_name='Yard', cause='')
        p = rec.last_payload
        assert p['body'] == 'Event 99 on Yard'
        assert p['data']['cause'] == ''


# ---------------------------------------------------------------------------
# update_last_sent side effect (success path)
# ---------------------------------------------------------------------------
class TestUpdateLastSent:
    def test_success_updates_last_sent(self, monkeypatch):
        rec = PostRecorder(FakeResponse(200, 'OK'))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(badge_count=7)
        run(FakeZM([notif]))
        assert notif.update_last_sent_called is True
        assert notif.last_sent_badge == 8  # badge_count + 1
        assert notif.deleted is False

    def test_200_with_token_error_does_not_update(self, monkeypatch):
        # 200 OK but body carries an Error referencing this token -> treated as failure
        token = 'zzzz111122223333'
        rec = PostRecorder(FakeResponse(200, 'Error for token {}'.format(token[:8])))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token=token)
        run(FakeZM([notif]))
        assert notif.update_last_sent_called is False
        # 200 status is not 4xx and has_token_error True -> deleted
        assert notif.deleted is True


# ---------------------------------------------------------------------------
# Invalid-token deletion heuristic
# ---------------------------------------------------------------------------
class TestTokenDeletion:
    def test_4xx_with_token_error_deletes(self, monkeypatch):
        token = 'deadbeef00001111'
        rec = PostRecorder(FakeResponse(400, 'InvalidRegistration Error {}'.format(token[:8])))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token=token)
        run(FakeZM([notif]))
        assert notif.deleted is True
        assert notif.update_last_sent_called is False

    def test_4xx_without_token_error_still_deletes(self, monkeypatch):
        # 4xx alone (client error) removes the token even without body match
        rec = PostRecorder(FakeResponse(404, 'Not Found'))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token='aaaabbbbccccdddd')
        run(FakeZM([notif]))
        assert notif.deleted is True

    def test_5xx_does_not_delete(self, monkeypatch):
        # Server error is transient -> keep the token
        rec = PostRecorder(FakeResponse(503, 'Service Unavailable'))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token='aaaabbbbccccdddd')
        run(FakeZM([notif]))
        assert notif.deleted is False
        assert notif.update_last_sent_called is False

    def test_200_ok_keeps_token(self, monkeypatch):
        rec = PostRecorder(FakeResponse(200, 'OK'))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token='aaaabbbbccccdddd')
        run(FakeZM([notif]))
        assert notif.deleted is False
        assert notif.update_last_sent_called is True

    def test_token_error_needs_matching_prefix(self, monkeypatch):
        # 'Error' present but the token prefix is NOT in body -> not a token error.
        # With a 5xx this must NOT delete (proves the AND in the heuristic).
        rec = PostRecorder(FakeResponse(500, 'Error: internal server problem'))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        notif = FakeNotification(token='xxxxyyyyzzzz0000')
        run(FakeZM([notif]))
        assert notif.deleted is False


# ---------------------------------------------------------------------------
# Multiple notifications / mixed outcomes
# ---------------------------------------------------------------------------
class TestMultiple:
    def test_mixed_batch(self, monkeypatch):
        # Send to two eligible, skip one excluded, skip one throttled.
        rec = PostRecorder(FakeResponse(200, 'OK'))
        install_post(monkeypatch, rec)
        g.config = {'push': base_push_cfg()}
        allowed_a = FakeNotification(token='alloweda00000000', monitor_list='5')
        allowed_b = FakeNotification(token='allowedb00000000', monitor_list=None)
        excluded = FakeNotification(token='excluded000000000', monitor_list='9')
        throttled = FakeNotification(token='throttled00000000', throttled=True, interval=60)
        run(FakeZM([allowed_a, allowed_b, excluded, throttled]), monitor_id=5)
        assert rec.call_count == 2
        assert allowed_a.update_last_sent_called is True
        assert allowed_b.update_last_sent_called is True
        assert excluded.update_last_sent_called is False
        assert throttled.update_last_sent_called is False
