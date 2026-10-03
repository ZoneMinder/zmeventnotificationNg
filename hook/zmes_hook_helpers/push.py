"""Push notification sender for zm_detect.

Reads registered tokens from ZM's Notifications table via pyzm,
filters by monitor, checks throttle, and sends via FCM cloud function proxy.
"""

import fcntl
import json
import os
import requests
from datetime import datetime

DEFAULT_BASE_DATA_PATH = '/var/lib/zmeventnotification'
# FCM errors that mean the token itself is invalid (INVALID_ARGUMENT,
# UNREGISTERED, legacy NotRegistered).
FCM_INVALID_TOKEN_MARKERS = ('not a valid FCM', 'entity was not found', 'UNREGISTERED', 'NotRegistered')


def send_push_notifications(zm, config, monitor_id, event_id, monitor_name, cause, logger, no_match=False):
    """Send FCM push notifications to all qualifying registered tokens.

    Holds ``<base_data_path>/misc/push.lock`` from token fetch to the
    LastNotifiedAt update. Parallel zm_detect runs would otherwise all read
    LastNotifiedAt before any of them updates it, and the token interval
    would not throttle a burst of events.

    Args:
        zm: pyzm ZMClient instance (already authenticated).
        config: dict from g.config (must have 'push' key with push settings).
        monitor_id: int, the monitor that triggered the event.
        event_id: int/str, the event ID.
        monitor_name: str, human-readable monitor name.
        cause: str, detection result string (e.g. "person detected").
        logger: pyzm logger instance.
        no_match: bool, if True use fid=alarm instead of objdetect in picture URL.
    """
    lock_path = os.path.join(
        config.get('base_data_path', DEFAULT_BASE_DATA_PATH), 'misc', 'push.lock')
    try:
        lock = open(lock_path, 'a')
    except OSError as e:
        logger.Error('push: cannot open {}: {}. Token intervals are not enforced '
                     'across parallel runs'.format(lock_path, e))
        _send_push_notifications(zm, config, monitor_id, event_id, monitor_name, cause, logger, no_match)
        return
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _send_push_notifications(zm, config, monitor_id, event_id, monitor_name, cause, logger, no_match)


def _send_push_notifications(zm, config, monitor_id, event_id, monitor_name, cause, logger, no_match):
    push_cfg = config.get('push', {})
    if not push_cfg or push_cfg.get('enabled') != 'yes':
        logger.Debug(1, 'push: disabled in config, skipping')
        return

    fcm_url = push_cfg.get('fcm_v1_url')
    fcm_key = push_cfg.get('fcm_v1_key')

    if not fcm_url or not fcm_key:
        logger.Error('push: fcm_v1_url or fcm_v1_key not configured')
        return

    try:
        notifications = zm.notifications()
    except Exception as e:
        logger.Error('push: failed to fetch notifications from ZM API: {}'.format(e))
        return

    if not notifications:
        logger.Debug(1, 'push: no registered tokens found')
        return

    mid = int(monitor_id)
    sent_count = 0

    for notif in notifications:
        token_suffix = notif.token[-6:] if len(notif.token) > 6 else notif.token

        if not notif.should_notify(mid):
            logger.Debug(2, 'push: skipping token ...{} (monitor {} not in filter)'.format(token_suffix, mid))
            continue

        if notif.is_throttled():
            logger.Debug(2, 'push: skipping token ...{} (throttled, interval={}s)'.format(token_suffix, notif.interval))
            continue

        badge = notif.badge_count + 1
        title = '{} Alarm'.format(monitor_name)
        body = cause if cause else 'Event {} on {}'.format(event_id, monitor_name)

        payload = {
            'token': notif.token,
            'title': title,
            'body': body,
            'sound': 'default',
            'badge': badge,
            'data': {
                'mid': str(mid),
                'eid': str(event_id),
                'monitorName': monitor_name,
                'cause': cause or '',
                'notification_foreground': 'true',
            },
        }

        # Include profile if present on notification
        if push_cfg.get('include_profile_in_push') == 'yes' and notif.profile:
            payload['data']['profile'] = notif.profile

        # Include picture URL if configured
        if push_cfg.get('include_picture') == 'yes':
            pic_url = push_cfg.get('picture_url', '')
            if pic_url:
                image_url = pic_url.replace('EVENTID', str(event_id))
                if no_match:
                    image_url = image_url.replace('fid=objdetect', 'fid=alarm')
                pic_user = push_cfg.get('picture_portal_username', '')
                pic_pass = push_cfg.get('picture_portal_password', '')
                if pic_user:
                    image_url += '&username={}'.format(pic_user)
                if pic_pass:
                    image_url += '&password={}'.format(pic_pass)
                payload['image_url'] = image_url
                logger.Debug(1, 'push: image_url={}'.format(image_url.split('&password=')[0]))
            else:
                logger.Debug(1, 'push: include_picture=yes but no picture_url configured')

        # Platform-specific fields (proxy format, matching ES FCM.pm proxy mode)
        if notif.platform == 'android':
            payload['android'] = {
                'icon': 'ic_stat_notification',
                'priority': push_cfg.get('android_priority', 'high'),
            }
            ttl = push_cfg.get('android_ttl')
            if ttl:
                payload['android']['ttl'] = str(ttl)
            if push_cfg.get('replace_push_messages') == 'yes':
                payload['android']['tag'] = 'zmninjapush'
            if notif.app_version and notif.app_version != 'unknown':
                payload['android']['channel'] = 'zmninja'

        elif notif.platform == 'ios':
            payload['ios'] = {
                'thread_id': 'zmninja_alarm',
                'headers': {
                    'apns-priority': '10',
                    'apns-push-type': 'alert',
                },
            }
            if push_cfg.get('replace_push_messages') == 'yes':
                payload['ios']['headers']['apns-collapse-id'] = 'zmninjapush'

        # Add profile to visible notification display
        if push_cfg.get('include_profile_in_push') == 'yes' and notif.profile:
            if notif.platform == 'ios':
                payload['ios']['subtitle'] = notif.profile
            elif notif.platform == 'android':
                payload['body'] += ' \u2014 ' + notif.profile

        # Send via proxy
        try:
            logger.Debug(1, 'push: sending to token ...{} ({})'.format(token_suffix, notif.platform))

            resp = requests.post(
                fcm_url,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': fcm_key,
                },
                data=json.dumps(payload),
                timeout=10,
            )

            body_text = resp.text
            # Check for token errors in the response body.
            # The proxy may return 200 with an error in the JSON body,
            # or a non-200 status. Either way, if "Error" appears in the
            # response referencing this token, the token is invalid.
            has_token_error = 'Error' in body_text and notif.token[:8] in body_text

            if resp.ok and not has_token_error:
                logger.Debug(1, 'push: FCM proxy returned 200 for token ...{}'.format(token_suffix))
                try:
                    notif.update_last_sent(badge=badge)
                except Exception as e:
                    logger.Debug(1, 'push: failed to update LastNotifiedAt: {}'.format(e))
                sent_count += 1
            else:
                logger.Error('push: FCM proxy error for token ...{}: {}'.format(token_suffix, body_text))
                # Remove the token only when the error names it as invalid
                # (FCM.pm matches the same markers). A bare 4xx such as 401
                # (bad fcm_v1_key) or 429 applies to every token, so deleting
                # on status alone would wipe all registered devices.
                if has_token_error or any(m in body_text for m in FCM_INVALID_TOKEN_MARKERS):
                    logger.Debug(1, 'push: removing invalid token ...{}'.format(token_suffix))
                    try:
                        notif.delete()
                    except Exception as e:
                        logger.Debug(1, 'push: failed to delete invalid token: {}'.format(e))

        except Exception as e:
            logger.Error('push: exception sending to token ...{}: {}'.format(token_suffix, e))

    logger.Debug(1, 'push: sent {} notifications for event {} on monitor {}'.format(
        sent_count, event_id, monitor_name))
