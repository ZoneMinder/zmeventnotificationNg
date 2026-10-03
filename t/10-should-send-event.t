#!/usr/bin/env perl
use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
use YAML::XS;
use File::Spec;
use File::Temp qw(tempdir);
use JSON;

require StubZM;

use ZmEventNotification::Config qw(:all);
use ZmEventNotification::Constants qw(:all);

# Load config
my $fixtures = File::Spec->catdir($FindBin::Bin, 'fixtures');
my $cfg = YAML::XS::LoadFile(File::Spec->catfile($fixtures, 'test_es.yml'));
my $sec = YAML::XS::LoadFile(File::Spec->catfile($fixtures, 'test_secrets.yml'));
$ZmEventNotification::Config::secrets = $sec;
loadEsConfigSettings($cfg);

# Stub out the heavy dependencies before loading HookProcessor.
# Use a package var so the closure in BEGIN can reference it.
our $escontrol_return;
our $fcm_sends = 0;

BEGIN {
    $escontrol_return = 0;  # ESCONTROL_DEFAULT_NOTIFY = 0

    for my $pkg (qw(
        ZmEventNotification::FCM
        ZmEventNotification::MQTT
        ZmEventNotification::DB
        ZmEventNotification::WebSocketHandler
    )) {
        (my $file = $pkg) =~ s{::}{/}g;
        $INC{"$file.pm"} = 1;
    }
    no strict 'refs';
    *{'ZmEventNotification::FCM::sendOverFCM'} = sub { $fcm_sends++ };
    *{'ZmEventNotification::FCM::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::sendOverFCM"} = \&ZmEventNotification::FCM::sendOverFCM;
    };
    *{'ZmEventNotification::MQTT::sendOverMQTTBroker'} = sub { };
    *{'ZmEventNotification::MQTT::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::sendOverMQTTBroker"} = \&ZmEventNotification::MQTT::sendOverMQTTBroker;
    };
    *{'ZmEventNotification::DB::updateEventinZmDB'} = sub { };
    *{'ZmEventNotification::DB::getNotesFromEventDB'} = sub { '' };
    *{'ZmEventNotification::DB::tagEventObjects'} = sub { };
    *{'ZmEventNotification::DB::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::updateEventinZmDB"} = \&ZmEventNotification::DB::updateEventinZmDB;
        *{"${caller}::getNotesFromEventDB"} = \&ZmEventNotification::DB::getNotesFromEventDB;
        *{"${caller}::tagEventObjects"} = \&ZmEventNotification::DB::tagEventObjects;
    };
    *{'ZmEventNotification::WebSocketHandler::getNotificationStatusEsControl'} = sub { $escontrol_return };
    *{'ZmEventNotification::WebSocketHandler::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::getNotificationStatusEsControl"} = \&ZmEventNotification::WebSocketHandler::getNotificationStatusEsControl;
    };
}

use_ok('ZmEventNotification::HookProcessor');
ZmEventNotification::HookProcessor->import(':all');

# Disable escontrol by default
$escontrol_config{enabled} = 0;

# Last-sent times live in base_data_path/push/last_sent.json
my $data_dir = tempdir(CLEANUP => 1);
mkdir "$data_dir/push" or die "mkdir: $!";
$server_config{base_data_path} = $data_dir;
my $store = "$data_dir/push/last_sent.json";

sub _seed_last_sent {
    my ($key, $mid, $t) = @_;
    my $d = {};
    if (-s $store) {
        open(my $in, '<', $store) or die "read $store: $!";
        local $/;
        $d = decode_json(<$in>);
    }
    $d->{$key}{$mid} = $t;
    open(my $out, '>', $store) or die "write $store: $!";
    print $out encode_json($d);
    close($out);
}

my $alarm = { MonitorId => '1', Name => 'Front' };

# ===== Monitor in monlist, no prior send -> 1 =====
{
    my $ac = {
        monlist   => '1,2,3',
        intlist   => '0,0,0',
        type      => FCM,
        token     => 'tok_test1_1234567890',
        id        => 1,
    };
    is(shouldSendEventToConn($alarm, $ac), 1, 'monitor in list, no prior send -> 1');
}

# ===== Monitor NOT in monlist -> 0 =====
{
    my $ac = {
        monlist   => '2,3',
        intlist   => '0,0',
        type      => FCM,
        token     => 'tok_test2_1234567890',
        id        => 2,
    };
    is(shouldSendEventToConn($alarm, $ac), 0, 'monitor NOT in monlist -> 0');
}

# ===== Monitor in list, within interval -> 0 =====
{
    my $ac = {
        monlist   => '1,2',
        intlist   => '600,0',
        type      => FCM,
        token     => 'tok_test3_1234567890',
        id        => 3,
    };
    _seed_last_sent($ac->{token}, '1', time() - 10);  # 10 secs ago, interval is 600
    is(shouldSendEventToConn($alarm, $ac), 0, 'within interval -> 0');
}

# ===== Monitor in list, past interval -> 1 =====
{
    my $ac = {
        monlist   => '1,2',
        intlist   => '5,0',
        type      => FCM,
        token     => 'tok_test4_1234567890',
        id        => 4,
    };
    _seed_last_sent($ac->{token}, '1', time() - 100);  # 100 secs ago, interval is 5
    is(shouldSendEventToConn($alarm, $ac), 1, 'past interval -> 1');
}

# ===== Empty monlist (all monitors) -> 1 =====
{
    my $ac = {
        monlist   => '',
        intlist   => '',
        type      => FCM,
        token     => 'tok_test5_1234567890',
        id        => 5,
    };
    is(shouldSendEventToConn($alarm, $ac), 1, 'empty monlist -> 1');
}

# ===== monlist=-1 (all monitors) -> 1 =====
{
    my $ac = {
        monlist   => '-1',
        intlist   => '',
        type      => FCM,
        token     => 'tok_test6_1234567890',
        id        => 6,
    };
    is(shouldSendEventToConn($alarm, $ac), 1, 'monlist=-1 -> 1');
}

# ===== escontrol FORCE_NOTIFY -> 1 regardless =====
{
    local $escontrol_config{enabled} = 1;
    $escontrol_return = ESCONTROL_FORCE_NOTIFY;
    my $ac = {
        monlist   => '99',          # monitor 1 not in list
        intlist   => '0',
        type      => FCM,
        token     => 'tok_test7_1234567890',
        id        => 7,
    };
    is(shouldSendEventToConn($alarm, $ac), 1, 'FORCE_NOTIFY -> 1 regardless');
    $escontrol_return = ESCONTROL_DEFAULT_NOTIFY;
}

# ===== escontrol FORCE_MUTE -> 0 regardless =====
{
    local $escontrol_config{enabled} = 1;
    $escontrol_return = ESCONTROL_FORCE_MUTE;
    my $ac = {
        monlist   => '1,2',
        intlist   => '0,0',
        type      => FCM,
        token     => 'tok_test8_1234567890',
        id        => 8,
    };
    is(shouldSendEventToConn($alarm, $ac), 0, 'FORCE_MUTE -> 0 regardless');
    $escontrol_return = ESCONTROL_DEFAULT_NOTIFY;
}

# ===== escontrol DEFAULT -> falls through to normal logic =====
{
    local $escontrol_config{enabled} = 1;
    $escontrol_return = ESCONTROL_DEFAULT_NOTIFY;
    my $ac = {
        monlist   => '1,2',
        intlist   => '0,0',
        type      => FCM,
        token     => 'tok_test9_1234567890',
        id        => 9,
    };
    is(shouldSendEventToConn($alarm, $ac), 1, 'DEFAULT -> normal logic (in list, no prior send) -> 1');
}

# ===== Multiple monitors, correct interval selected =====
{
    my $alarm5 = { MonitorId => '5', Name => 'Side' };
    my $ac = {
        monlist   => '1,5,10',
        intlist   => '60,120,300',
        type      => FCM,
        token     => 'tok_test10_1234567890',
        id        => 10,
    };
    _seed_last_sent($ac->{token}, '5', time() - 60);  # 60 secs ago, interval for mid=5 is 120
    is(shouldSendEventToConn($alarm5, $ac), 0, 'mid=5 interval=120, elapsed=60 -> 0');

    _seed_last_sent($ac->{token}, '5', time() - 200);  # 200 secs ago > 120 interval
    is(shouldSendEventToConn($alarm5, $ac), 1, 'mid=5 interval=120, elapsed=200 -> 1');
}

# ===== Two forks holding the same stale connection copy: only one sends (#54) =====
# Each event is handled in its own fork with a copy of active_connections,
# so the interval must hold across copies.
{
    local $hooks_config{enabled} = 0;
    my %conn = (
        monlist   => '1',
        intlist   => '300',
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        token     => 'tok_race_1234567890',
        id        => 11,
    );
    my ($fork1, $fork2) = ({%conn}, {%conn});
    is(shouldSendEventToConn($alarm, $fork1), 1, 'first fork: no prior send -> 1');
    sendEvent($alarm, $fork1, 'event_start', 0);
    is(shouldSendEventToConn($alarm, $fork2), 0, 'second fork with stale copy: within interval -> 0');
}

# ===== Send blocked by notify filter does not start the interval (#54) =====
{
    local $hooks_config{enabled} = 1;
    local $hooks_config{event_start_hook} = '/usr/bin/detect';
    local $hooks_config{event_start_notify_on_hook_fail} = 'none';
    my $ac = {
        monlist   => '1',
        intlist   => '300',
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        token     => 'tok_filtered_1234567890',
        id        => 12,
    };
    sendEvent($alarm, $ac, 'event_start', 1);  # hook failed, fail channel is 'none'
    is(shouldSendEventToConn($alarm, {%$ac}), 1, 'filtered send leaves interval unstarted -> 1');
}

# ===== Two forks pass the check together: only the first to send does (#54) =====
{
    local $hooks_config{enabled} = 0;
    my %conn = (
        monlist   => '1',
        intlist   => '300',
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        token     => 'tok_together_1234567890',
        id        => 14,
    );
    my ($fork1, $fork2) = ({%conn}, {%conn});
    is(shouldSendEventToConn($alarm, $fork1), 1, 'fork1 check passes');
    is(shouldSendEventToConn($alarm, $fork2), 1, 'fork2 check passes before fork1 sends');
    $fcm_sends = 0;
    sendEvent($alarm, $fork1, 'event_start', 0);
    sendEvent($alarm, $fork2, 'event_start', 0);
    is($fcm_sends, 1, 'only one of the two forks sends');
}

# ===== escontrol FORCE_NOTIFY sends even within the interval =====
{
    local $hooks_config{enabled} = 0;
    local $escontrol_config{enabled} = 1;
    local $escontrol_return = ESCONTROL_FORCE_NOTIFY;
    my $ac = {
        monlist   => '1',
        intlist   => '300',
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        token     => 'tok_forced_1234567890',
        id        => 15,
    };
    _seed_last_sent($ac->{token}, '1', time() - 10);
    $fcm_sends = 0;
    sendEvent($alarm, $ac, 'event_start', 0);
    is($fcm_sends, 1, 'FORCE_NOTIFY sends within interval');
}

# ===== event_end notification does not move the interval =====
{
    local $hooks_config{enabled} = 0;
    my $ac = {
        monlist   => '1',
        intlist   => '300',
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        token     => 'tok_endsend_1234567890',
        id        => 16,
    };
    $fcm_sends = 0;
    sendEvent($alarm, $ac, 'event_end', 0);
    is($fcm_sends, 1, 'event_end sent');
    is(shouldSendEventToConn($alarm, {%$ac}), 1, 'event_end send leaves interval unstarted -> 1');
}

# ===== Pruning drops stale connection-id keys, never device tokens =====
{
    local $hooks_config{enabled} = 0;
    my $old = time() - 8 * 86400;
    _seed_last_sent('tok_longgone_1234567890', '1', $old);
    _seed_last_sent('conn-77', '1', $old);
    my $ac = {
        monlist   => '1',
        intlist   => '0',
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        token     => 'tok_prune_1234567890',
        id        => 17,
    };
    sendEvent($alarm, $ac, 'event_start', 0);
    open(my $fh, '<', $store) or die "read $store: $!";
    my $times = decode_json(do { local $/; <$fh> });
    is($times->{tok_longgone_1234567890}{1}, $old, 'old token time kept');
    ok(!exists $times->{'conn-77'}, 'old connection-id time pruned');
}

# ===== Unusable store does not block notifications =====
{
    local $server_config{base_data_path} = "$data_dir/missing";
    my $ac = {
        monlist => '1',
        intlist => '300',
        type    => FCM,
        token   => 'tok_nostore_1234567890',
        id      => 13,
    };
    is(shouldSendEventToConn($alarm, $ac), 1, 'store cannot be opened -> 1');
}

# ===== No interval for the monitor: a prior send does not block =====
# All-monitor connections (monlist empty or -1) and blank intlist entries
# have no interval, which counts as 0.
for my $case (
    [ '',      '',      'empty monlist' ],
    [ '-1',    '',      'monlist=-1' ],
    [ '2,1,3', '5,,3',  'blank intlist entry' ],
) {
    my ($monlist, $intlist, $label) = @$case;
    my $ac = {
        monlist => $monlist,
        intlist => $intlist,
        type    => FCM,
        token   => "tok_noint_${label}_1234567890",
        id      => 18,
    };
    _seed_last_sent($ac->{token}, '1', time() - 2);
    is(shouldSendEventToConn($alarm, $ac), 1, "$label, sent 2s ago -> 1");
    _seed_last_sent($ac->{token}, '1', time() + 100);
    is(shouldSendEventToConn($alarm, $ac), 0, "$label, last send in the future -> 0");
}

done_testing();
