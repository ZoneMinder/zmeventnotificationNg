#!/usr/bin/perl
use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
use YAML::XS;
use File::Spec;
use JSON;

require StubZM;

use ZmEventNotification::Config qw(:all);
use ZmEventNotification::Constants qw(:all);

# Load config so hooks_config/push_config/notify_config are populated
my $fixtures = File::Spec->catdir($FindBin::Bin, 'fixtures');
my $cfg = YAML::XS::LoadFile(File::Spec->catfile($fixtures, 'test_es.yml'));
my $sec = YAML::XS::LoadFile(File::Spec->catfile($fixtures, 'test_secrets.yml'));
$ZmEventNotification::Config::secrets = $sec;
loadEsConfigSettings($cfg);

# Spy for tagEventObjects: records [eid, \@labels] per call
our @tag_calls;

BEGIN {
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
    *{'ZmEventNotification::FCM::sendOverFCM'} = sub { };
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
    *{'ZmEventNotification::DB::tagEventObjects'} = sub { push @main::tag_calls, [ $_[0], $_[1] ]; };
    *{'ZmEventNotification::DB::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::updateEventinZmDB"} = \&ZmEventNotification::DB::updateEventinZmDB;
        *{"${caller}::getNotesFromEventDB"} = \&ZmEventNotification::DB::getNotesFromEventDB;
        *{"${caller}::tagEventObjects"} = \&ZmEventNotification::DB::tagEventObjects;
    };
    *{'ZmEventNotification::WebSocketHandler::getNotificationStatusEsControl'} = sub { 0 };
    *{'ZmEventNotification::WebSocketHandler::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::getNotificationStatusEsControl"} = \&ZmEventNotification::WebSocketHandler::getNotificationStatusEsControl;
    };
}

use_ok('ZmEventNotification::HookProcessor');
ZmEventNotification::HookProcessor->import(':all');

# These fork helpers are not exported; call them by fully-qualified name.
my $tag_detected_objects = \&ZmEventNotification::HookProcessor::_tag_detected_objects;
my $build_alarm_obj      = \&ZmEventNotification::HookProcessor::_build_alarm_obj;
my $run_api_push         = \&ZmEventNotification::HookProcessor::_run_api_push;

# ===== _build_alarm_obj =====
subtest '_build_alarm_obj constructs the alarm hash' => sub {
    my $obj = $build_alarm_obj->(
        'FrontDoor', 5, 100, 'detected:person',
        [ { label => 'person', box => [ 1, 2, 3, 4 ] } ],
        { rule => 'daytime' }
    );
    is_deeply(
        $obj,
        {
            Name          => 'FrontDoor',
            MonitorId     => 5,
            EventId       => 100,
            Cause         => 'detected:person',
            DetectionJson => [ { label => 'person', box => [ 1, 2, 3, 4 ] } ],
            RulesObject   => { rule => 'daytime' },
        },
        'all six fields mapped correctly'
    );
};

subtest '_build_alarm_obj defaults DetectionJson to [] when falsy' => sub {
    my $obj = $build_alarm_obj->('Back', 6, 101, 'motion', undef, undef);
    is_deeply($obj->{DetectionJson}, [], 'undef detectJson becomes empty arrayref');
    is($obj->{RulesObject}, undef, 'RulesObject passes undef through unchanged');
    is($obj->{Cause}, 'motion', 'Cause passed through');

    my $obj2 = $build_alarm_obj->('Back', 6, 101, 'motion', 0, {});
    is_deeply($obj2->{DetectionJson}, [], 'falsy (0) detectJson also becomes empty arrayref');
};

# ===== _tag_detected_objects =====
subtest '_tag_detected_objects - {labels:[...]} hash shape' => sub {
    local $hooks_config{tag_detected_objects} = 1;
    @tag_calls = ();

    $tag_detected_objects->(100, '{"labels":["person","car"]}', 'event_start');

    is(scalar @tag_calls, 1, 'tagEventObjects called once');
    is($tag_calls[0][0], 100, 'eid forwarded to tagEventObjects');
    is_deeply($tag_calls[0][1], [ 'person', 'car' ], 'labels extracted from {labels:[...]} shape');
};

subtest '_tag_detected_objects - [{label:...}] array shape' => sub {
    local $hooks_config{tag_detected_objects} = 1;
    @tag_calls = ();

    # third element has no "label" key and must be skipped
    $tag_detected_objects->(
        101,
        '[{"label":"dog"},{"label":"cat"},{"score":0.91}]',
        'event_end'
    );

    is(scalar @tag_calls, 1, 'tagEventObjects called once');
    is($tag_calls[0][0], 101, 'eid forwarded to tagEventObjects');
    is_deeply($tag_calls[0][1], [ 'dog', 'cat' ],
        'labels extracted from [{label:...}] shape, label-less entries skipped');
};

subtest '_tag_detected_objects - disabled by config' => sub {
    local $hooks_config{tag_detected_objects} = 0;
    @tag_calls = ();

    $tag_detected_objects->(102, '{"labels":["person"]}', 'event_start');

    is(scalar @tag_calls, 0, 'tagEventObjects NOT called when tag_detected_objects is off');
};

subtest '_tag_detected_objects - no labels means no tagging' => sub {
    local $hooks_config{tag_detected_objects} = 1;

    @tag_calls = ();
    $tag_detected_objects->(103, '{"labels":[]}', 'event_start');
    is(scalar @tag_calls, 0, 'not called for empty labels array');

    @tag_calls = ();
    $tag_detected_objects->(103, '[{"score":0.5}]', 'event_start');
    is(scalar @tag_calls, 0, 'not called when array entries carry no label');

    @tag_calls = ();
    $tag_detected_objects->(103, '', 'event_start');
    is(scalar @tag_calls, 0, 'not called for empty json string');
};

# ===== _run_api_push =====
# Point the backtick target at a harmless shell snippet that appends to a
# marker file; the trailing '#' comments out the eid/mid/name args the code
# concatenates. Presence/absence of the marker proves whether the gated
# backtick actually ran.
my $scratch = $ENV{CLAUDE_SCRATCH}
    || File::Spec->catdir(File::Spec->tmpdir(), "zmen_t19_$$");
mkdir $scratch unless -d $scratch;
my $marker = File::Spec->catfile($scratch, 'api_push_marker');
my $harmless_script = "echo ran >> '$marker' #";

my $alarm_obj = {
    Name      => 'FrontDoor',
    Cause     => 'detected:person',
    MonitorId => 5,
    EventId   => 100,
};

sub _marker_ran {
    return 0 unless -e $marker;
    open(my $fh, '<', $marker) or return 0;
    my @lines = <$fh>;
    close($fh);
    return scalar @lines;
}

subtest '_run_api_push - skipped when push disabled' => sub {
    unlink $marker;
    local $push_config{enabled} = 0;
    local $push_config{script}  = $harmless_script;

    $run_api_push->($alarm_obj, 100, 5, 'event_start', 0);

    is(_marker_ran(), 0, 'backtick command NOT executed when use_api_push is off');
};

subtest '_run_api_push - skipped when script empty' => sub {
    unlink $marker;
    local $push_config{enabled} = 1;
    local $push_config{script}  = '';

    $run_api_push->($alarm_obj, 100, 5, 'event_start', 0);

    is(_marker_ran(), 0, 'backtick command NOT executed when api_push_script is empty');
};

subtest '_run_api_push - runs when enabled and channel allows api' => sub {
    unlink $marker;
    local $push_config{enabled} = 1;
    local $push_config{script}  = $harmless_script;
    local $hooks_config{enabled} = 1;
    local $hooks_config{event_start_hook} = '/usr/bin/detect';
    local $hooks_config{hook_pass_image_path} = 0;
    local $hooks_config{event_start_notify_on_hook_success} = 'all';  # api included

    $run_api_push->($alarm_obj, 100, 5, 'event_start', 0);

    is(_marker_ran(), 1, 'backtick command executed once when channel allows api');
};

subtest '_run_api_push - skipped when channel does not allow api' => sub {
    unlink $marker;
    local $push_config{enabled} = 1;
    local $push_config{script}  = $harmless_script;
    local $hooks_config{enabled} = 1;
    local $hooks_config{event_start_hook} = '/usr/bin/detect';
    local $hooks_config{hook_pass_image_path} = 0;
    local $hooks_config{event_start_notify_on_hook_success} = 'none';  # api excluded

    $run_api_push->($alarm_obj, 100, 5, 'event_start', 0);

    is(_marker_ran(), 0, 'backtick command NOT executed when api channel is disallowed');
};

subtest '_run_api_push - runs regardless of channel when hooks disabled' => sub {
    unlink $marker;
    local $push_config{enabled} = 1;
    local $push_config{script}  = $harmless_script;
    local $hooks_config{enabled} = 0;   # forces the gate open
    local $hooks_config{event_start_hook} = '/usr/bin/detect';
    local $hooks_config{hook_pass_image_path} = 0;
    local $hooks_config{event_start_notify_on_hook_success} = 'none';

    $run_api_push->($alarm_obj, 100, 5, 'event_start', 0);

    is(_marker_ran(), 1, 'backtick command executed when hooks disabled (channel gate bypassed)');
};

subtest '_run_api_push - event_end skipped when send_event_end_notification is off' => sub {
    unlink $marker;
    local $push_config{enabled} = 1;
    local $push_config{script}  = $harmless_script;
    local $hooks_config{enabled} = 0;   # would otherwise open the gate
    local $notify_config{send_event_end_notification} = 0;

    $run_api_push->($alarm_obj, 100, 5, 'event_end', 0);

    is(_marker_ran(), 0, 'event_end push short-circuits when send_event_end_notification is no');
};

unlink $marker;

done_testing();
