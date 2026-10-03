#!/usr/bin/env perl
# Drives processNewAlarmsInFork (the per-event fork state machine) end to end
# with a real stub hook script, and pins:
#   - the exact argv each external command receives (start/end hook, start/end
#     user scripts, api push script), including values with spaces and the
#     image path appended by hook_pass_image_path
#   - stdout/exit-code handling of the hook
#   - which notifications go out, and what the fork writes to the job pipe
# sleep() is overridden so the 2s-per-iteration loop runs instantly.
use strict;
use warnings;
no warnings 'once';
BEGIN { *CORE::GLOBAL::sleep = sub { 0 } }
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

my $fixtures = File::Spec->catdir($FindBin::Bin, 'fixtures');
my $cfg = YAML::XS::LoadFile(File::Spec->catfile($fixtures, 'test_es.yml'));
my $sec = YAML::XS::LoadFile(File::Spec->catfile($fixtures, 'test_secrets.yml'));
$ZmEventNotification::Config::secrets = $sec;
loadEsConfigSettings($cfg);

my $dir = tempdir(CLEANUP => 1);
mkdir "$dir/push" or die "mkdir: $!";
$server_config{base_data_path} = $dir;

our @sent;           # [event_type, Cause] per sendOverFCM call
our $db_notes = 'Motion All';
our $esc_status = 0;

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
    *{'ZmEventNotification::FCM::sendOverFCM'} = sub { push @main::sent, [ $_[2], $_[0]->{Cause} ] };
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
    *{'ZmEventNotification::DB::getNotesFromEventDB'} = sub { $main::db_notes };
    *{'ZmEventNotification::DB::tagEventObjects'} = sub { };
    *{'ZmEventNotification::DB::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::updateEventinZmDB"} = \&ZmEventNotification::DB::updateEventinZmDB;
        *{"${caller}::getNotesFromEventDB"} = \&ZmEventNotification::DB::getNotesFromEventDB;
        *{"${caller}::tagEventObjects"} = \&ZmEventNotification::DB::tagEventObjects;
    };
    *{'ZmEventNotification::WebSocketHandler::getNotificationStatusEsControl'} = sub { $main::esc_status };
    *{'ZmEventNotification::WebSocketHandler::import'} = sub {
        my $caller = caller;
        no strict 'refs';
        *{"${caller}::getNotificationStatusEsControl"} = \&ZmEventNotification::WebSocketHandler::getNotificationStatusEsControl;
    };
}

use_ok('ZmEventNotification::HookProcessor');

# Stub external command: logs its argv as one JSON line, prints
# $ZMT_OUT_<tag>, exits $ZMT_EXIT_<tag>. The tag is the first argument,
# which comes from the configured command line itself.
my $log  = "$dir/argv.log";
my $stub = "$dir/stub.pl";
open(my $sfh, '>', $stub) or die $!;
print $sfh <<'EOS';
use strict;
use JSON::PP;
my $tag = $ARGV[0];
open(my $f, '>>', $ENV{ZMT_LOG}) or die $!;
print $f JSON::PP->new->encode(\@ARGV), "\n";
close($f);
print $ENV{"ZMT_OUT_$tag"} // '';
exit($ENV{"ZMT_EXIT_$tag"} // 0);
EOS
close($sfh);
$ENV{ZMT_LOG} = $log;

my $cmd = "$^X $stub";
my $img = '/tmp/fake_event_path';    # StubZM ZoneMinder::Event->Path

sub argv_log {
    return {} unless -e $log;
    open(my $fh, '<', $log) or die $!;
    my %by_tag;
    while (my $l = <$fh>) {
        my $a = decode_json($l);
        push @{ $by_tag{ $a->[0] } }, $a;
    }
    close($fh);
    return \%by_tag;
}

# Runs one event through the fork state machine. Returns the job-pipe lines.
sub run_event {
    my (%a) = @_;
    unlink $log;
    unlink "$dir/push/last_sent.json";
    @sent = ();
    my $pipe = '';
    open(my $w, '>', \$pipe) or die $!;
    *main::WRITER = *$w;
    @main::active_connections = ({
        type      => FCM,
        pushstate => 'enabled',
        state     => VALID_CONNECTION,
        id        => 'fcm-1',
        token     => 'tok_aaaaaaaaaaaaaaaa',
        monlist   => '5',
        intlist   => '0',
    });
    ZmEventNotification::HookProcessor::processNewAlarmsInFork({
        Alarm => {
            MonitorId   => 5,
            EventId     => 100,
            MonitorName => $a{name} // 'Front Door',
            Start       => { State => 'pending', Cause => $a{cause} // 'Motion All' },
        },
        MonitorObj => {},
    });
    return [ split /\n/, $pipe ];
}

sub set_hooks {
    $hooks_config{enabled} = 1;
    $hooks_config{event_start_hook} = "$cmd start";
    $hooks_config{event_end_hook}   = "$cmd end";
    $hooks_config{event_start_hook_notify_userscript} = "$cmd ustart";
    $hooks_config{event_end_hook_notify_userscript}   = "$cmd uend";
    $hooks_config{hook_pass_image_path} = 1;
    $hooks_config{use_hook_description} = 1;
    $hooks_config{tag_detected_objects} = 0;
    $hooks_config{event_end_notify_if_start_success} = 1;
    $hooks_config{event_start_notify_on_hook_success} = 'all';
    $hooks_config{event_start_notify_on_hook_fail}    = 'none';
    $hooks_config{event_end_notify_on_hook_success}   = 'all';
    $hooks_config{event_end_notify_on_hook_fail}      = 'none';
    $hooks_config{hook_skip_monitors} = '';
    $push_config{enabled} = 1;
    $push_config{script}  = "$cmd api";
    $notify_config{send_event_start_notification} = 1;
    $notify_config{send_event_end_notification}   = 1;
    $escontrol_config{enabled} = 0;
    $esc_status = 0;
    $db_notes = 'Motion All';
    %ENV = (%ENV,
        ZMT_OUT_start => "detected:person--SPLIT--[]\n", ZMT_EXIT_start => 0,
        ZMT_OUT_end   => 'detected:car--SPLIT--[]',      ZMT_EXIT_end   => 0,
    );
}

subtest 'success path: exact argv for every external command' => sub {
    set_hooks();
    my $lines = run_event();
    my $log = argv_log();

    is_deeply($log->{start}, [[ 'start', 100, 5, 'Front Door', 'Motion All', $img ]],
        'start hook: eid, mid, name, cause, image path');
    is_deeply($log->{ustart}, [[ 'ustart', 0, 100, 5, 'Front Door', 'detected:person', '[]', $img ]],
        'start user script: result, eid, mid, name, text, json, image path');
    is_deeply($log->{end}, [[ 'end', 100, 5, 'Front Door', 'detected:person Motion All', $img ]],
        'end hook: notes get the start detection text prefixed');
    is_deeply($log->{uend}, [[ 'uend', 0, 100, 5, 'Front Door', 'detected:car', '[]', $img ]],
        'end user script argv');
    is_deeply($log->{api}, [
        [ 'api', 100, 5, 'Front Door', 'detected:person Motion All', 'event_start', $img ],
        [ 'api', 100, 5, 'Front Door', 'detected:car detected:car',  'event_end',   $img ],
    ], 'api push argv for start and end');

    is_deeply(\@sent, [
        [ 'event_start', 'detected:person Motion All' ],
        [ 'event_end',   'detected:car detected:car' ],
    ], 'start and end FCM notifications sent with hook descriptions');

    is(scalar(grep { $_ eq 'update_parallel_hooks--TYPE--add' } @$lines), 2, 'two hook add lines');
    is(scalar(grep { $_ eq 'update_parallel_hooks--TYPE--del' } @$lines), 2, 'two hook del lines');
    ok((grep { $_ eq 'event_description--TYPE--5--SPLIT--100--SPLIT--detected:person' } @$lines),
        'start event_description written');
    ok((grep { $_ eq 'active_event_update--TYPE--5--SPLIT--100--SPLIT--Start--SPLIT--Cause--SPLIT--detected:person Motion All--JSON--[]' } @$lines),
        'start active_event_update written');
    is($lines->[-1], 'active_event_delete--TYPE--5--SPLIT--100', 'fork ends with active_event_delete');
};

subtest 'image path not appended when hook_pass_image_path is off' => sub {
    set_hooks();
    $hooks_config{hook_pass_image_path} = 0;
    run_event();
    my $log = argv_log();
    is_deeply($log->{start}, [[ 'start', 100, 5, 'Front Door', 'Motion All' ]], 'start hook');
    is_deeply($log->{ustart}, [[ 'ustart', 0, 100, 5, 'Front Door', 'detected:person', '[]' ]], 'user script');
    is_deeply($log->{api}->[0], [ 'api', 100, 5, 'Front Door', 'detected:person Motion All', 'event_start' ], 'api push');
};

subtest 'hook exit code 1: failure passed on, no start or end push' => sub {
    set_hooks();
    local $ENV{ZMT_EXIT_start} = 1;
    run_event();
    my $log = argv_log();
    is($log->{ustart}->[0]->[1], 1, 'user script receives hook result 1');
    is_deeply(\@sent, [], 'no notification: start fail channel is none, end gated on start success');
    is_deeply($log->{api}, undef, 'api push not allowed on failure');
};

subtest 'hook exit 0 with no output is a failure' => sub {
    set_hooks();
    local $ENV{ZMT_OUT_start} = '';
    run_event();
    my $log = argv_log();
    is_deeply($log->{ustart}, [[ 'ustart', 1, 100, 5, 'Front Door', '', '[]', $img ]],
        'empty output -> result 1, empty text, [] json');
    is_deeply(\@sent, [], 'nothing sent');
};

subtest 'configured command may carry shell quoting (documented example form)' => sub {
    set_hooks();
    # zmeventnotification.example.yml quotes the user script path in single quotes
    $hooks_config{event_start_hook_notify_userscript} = "'$^X' '$stub' ustart";
    run_event();
    is_deeply(argv_log()->{ustart}, [[ 'ustart', 0, 100, 5, 'Front Door', 'detected:person', '[]', $img ]],
        'quoted command path still runs with the same argv');
};

subtest 'monitor name and cause are passed literally, never run by a shell' => sub {
    set_hooks();
    my $pwn = "$dir/pwned";
    unlink $pwn;
    my $name  = qq{Yard \$(touch $pwn) `touch $pwn` "q};
    my $cause = qq{Linked: a"b \$HOME 'x'};
    run_event(name => $name, cause => $cause);
    ok(!-e $pwn, 'no command embedded in the name was executed');
    my $log = argv_log();
    is_deeply($log->{start}, [[ 'start', 100, 5, $name, $cause, $img ]], 'start hook argv literal');
    is($log->{api}->[0]->[3], $name, 'api push gets literal name');
    is($log->{api}->[0]->[4], "detected:person $cause", 'api push gets literal cause');
};

subtest 'user script receives the detection JSON as one intact argument' => sub {
    set_hooks();
    my $json = '{"labels": ["person"], "boxes": [[1, 2, 3, 4]]}';
    local $ENV{ZMT_OUT_start} = "detected:person--SPLIT--$json";
    run_event();
    my $u = argv_log()->{ustart}->[0];
    is($u->[6], $json, 'json argument intact');
    is(scalar(@$u), 8, 'argument count unchanged');
};

subtest 'escontrol default and force-notify: start and end sent' => sub {
    set_hooks();
    $escontrol_config{enabled} = 1;
    for my $st (ESCONTROL_DEFAULT_NOTIFY, ESCONTROL_FORCE_NOTIFY) {
        $esc_status = $st;
        run_event();
        is_deeply([ map { $_->[0] } @sent ], [ 'event_start', 'event_end' ], "status $st: start and end sent");
    }
};

subtest 'escontrol force-mute: neither start nor end notification sent' => sub {
    set_hooks();
    $escontrol_config{enabled} = 1;
    $esc_status = ESCONTROL_FORCE_MUTE;
    run_event();
    is_deeply(\@sent, [], 'muted monitor sends nothing');
};

subtest 'invalid detection JSON from a hook does not kill the fork' => sub {
    set_hooks();
    local $ENV{ZMT_OUT_start} = 'detected:person--SPLIT--{not json';
    local $ENV{ZMT_OUT_end}   = 'detected:car--SPLIT--{not json';
    my $lines = eval { run_event() };
    is($@, '', 'fork did not die');
    is($lines->[-1], 'active_event_delete--TYPE--5--SPLIT--100', 'active_event_delete still sent');
    ok((grep { $_ eq 'active_event_update--TYPE--5--SPLIT--100--SPLIT--Start--SPLIT--Cause--SPLIT--detected:person Motion All--JSON--[]' } @$lines),
        'parent gets [] instead of the invalid JSON');
    is_deeply([ map { $_->[0] } @sent ], [ 'event_start', 'event_end' ], 'start and end still notified');
};

subtest 'hook_timeout 0 or unset, or a fast hook under a timeout: same argv, output, exit and pipe' => sub {
    my $run = sub {
        my ($t) = @_;
        set_hooks();
        local $hooks_config{hook_timeout} = $t;
        my %r = (lines => run_event(), argv => argv_log(), sent => [@sent]);
        local $ENV{ZMT_EXIT_start} = 3;
        $r{fail_lines} = run_event();
        $r{fail_argv}  = argv_log();
        return \%r;
    };
    my $base = $run->(undef);
    is($base->{fail_argv}{ustart}[0][1], 3, 'baseline: hook exit code 3 reaches the user script');
    is_deeply($run->(0),  $base, 'hook_timeout 0 behaves as unset');
    is_deeply($run->(30), $base, 'fast hook under hook_timeout 30 behaves as unset');
};

done_testing();
