#!/usr/bin/env perl
# Decision logic extracted from processNewAlarmsInFork (the fork state machine).
# The full loop shells out to hooks, reads SHM, and sleeps -- not a good direct
# test target -- but its two most consequential decisions are pure and are
# locked here:
#   - _effective_hook_result: a hook that produced no detection text is a
#     failure even if it exited 0 ($hookResult = 1 if !$resTxt), used at both
#     event-start and event-end.
#   - _end_notify_skip_reason: when event_end_notify_if_start_success is on and
#     the start hook failed, the end notification must be suppressed (line 537).
use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
BEGIN { require StubZM }
use ZmEventNotification::HookProcessor;

my $effective   = \&ZmEventNotification::HookProcessor::_effective_hook_result;
my $skip_reason = \&ZmEventNotification::HookProcessor::_end_notify_skip_reason;

# ===== _effective_hook_result($exit_code, $resTxt) =====
subtest '_effective_hook_result: no detection text => failure (1)' => sub {
  is($effective->(0, ''),    1, 'exit 0 but empty text -> fail');
  is($effective->(0, undef), 1, 'exit 0 but undef text -> fail');
};
subtest '_effective_hook_result: detection text present => keep exit code' => sub {
  is($effective->(0, 'detected:person'), 0, 'exit 0 with text -> success');
  is($effective->(1, 'detected:person'), 1, 'nonzero exit preserved even with text');
};

# ===== _end_notify_skip_reason($notify_if_start_success, $startHookResult, $rulesAllowed) =====
subtest '_end_notify_skip_reason: suppressed when start hook failed' => sub {
  is($skip_reason->(1, 1, 1), 'start_failed',
     'notify_if_start_success on + start failed -> suppress');
  is($skip_reason->(1, 2, 1), 'start_failed', 'any nonzero start result suppresses');
};
subtest '_end_notify_skip_reason: not suppressed when start succeeded' => sub {
  is($skip_reason->(1, 0, 1), '', 'start succeeded -> send');
};
subtest '_end_notify_skip_reason: gate off => start result ignored' => sub {
  is($skip_reason->(0, 1, 1), '', 'gate off -> send despite start failure');
};
subtest '_end_notify_skip_reason: rules failure suppresses (when not start-gated)' => sub {
  is($skip_reason->(0, 0, 0), 'rules', 'rules disallowed -> suppress');
  is($skip_reason->(1, 0, 0), 'rules', 'start ok but rules disallowed -> suppress');
};

done_testing();
