#!/usr/bin/env perl
# processJobs (zmeventnotification.pl): the parent's consumer of the job pipe
# written by event forks. The real sub (and sysreadline) is pulled from the
# script source and run against a real pipe.
use strict;
use warnings;
no warnings 'once';
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
use IO::Handle;
use IO::Select;
use Symbol qw(qualify_to_ref);
use JSON;
require StubZM;
use PlSource;

use ZmEventNotification::Constants qw(:all);
use ZmEventNotification::Config qw(:all);
use ZmEventNotification::Util qw(parse_job_line);

our ( $rin, $rout, %active_events, $child_forks, $parallel_hooks, @active_connections );
our @db_updates;
our @errors;
sub updateEventinZmDB { push @db_updates, [@_] }
{ no warnings 'redefine'; *main::Error = sub { push @errors, $_[0] }; }

pipe( READER, WRITER ) or die "pipe: $!";
WRITER->autoflush(1);
$rin = '';
vec( $rin, fileno(READER), 1 ) = 1;

my $code = PlSource::extract( 'sub sysreadline', 'sub at_eol' )
         . PlSource::extract( 'sub processJobs', '} # end sub processJobs' );
eval "$code; 1" or die "compile processJobs: $@";

sub feed {
    print WRITER "$_\n" for @_;
    processJobs();
}

{
    package MockConn;
    sub new { bless { sent => [] }, shift }
    sub ip { '10.0.0.1' }
    sub port { 9000 }
    sub send_utf8 { push @{ $_[0]{sent} }, $_[1] }
}

subtest 'message: delivered to the matching websocket connection' => sub {
    my $c = MockConn->new;
    @active_connections = ( { id => 'w1', conn => $c, state => VALID_CONNECTION }, { id => 'w2' } );
    feed('message--TYPE--w1--SPLIT--{"event":"alarm"}');
    is_deeply( $c->{sent}, ['{"event":"alarm"}'], 'sent to w1' );
};

subtest 'fcm_notification: badge and invocations updated' => sub {
    @active_connections = (
        { id => 'f1', token => 'tokA', badge => 3, invocations => { count => 7, at => 4 } },
        { id => 'f2', token => 'tokB', badge => 0, invocations => { count => 0, at => 4 } },
        { id => 'w1' },
    );
    feed('fcm_notification--TYPE--tokA--SPLIT--4--SPLIT--8--SPLIT--4');
    is( $active_connections[0]{badge}, 4, 'badge set' );
    is_deeply( $active_connections[0]{invocations}, { count => 8, at => 4 }, 'invocations set' );
    is( $active_connections[1]{badge}, 0, 'other token untouched' );
};

subtest 'fcm_notification: concurrent children do not lose counts' => sub {
    # Two forks sharing the same fork-time snapshot (badge 3, count 7)
    # both report badge 4 / count 8.
    @active_connections = ( { id => 'f1', token => 'tokA', badge => 3, invocations => { count => 7, at => 4 } } );
    feed( 'fcm_notification--TYPE--tokA--SPLIT--4--SPLIT--8--SPLIT--4',
          'fcm_notification--TYPE--tokA--SPLIT--4--SPLIT--8--SPLIT--4' );
    is( $active_connections[0]{badge}, 5, 'badge counts both' );
    is_deeply( $active_connections[0]{invocations}, { count => 9, at => 4 }, 'count counts both' );
};

subtest 'fcm_notification: month change and missing invocations take the child values' => sub {
    @active_connections = (
        { id => 'f1', token => 'tokA', badge => 3, invocations => { count => 900, at => 4 } },
        { id => 'f2', token => 'tokB', badge => 0 },
    );
    feed( 'fcm_notification--TYPE--tokA--SPLIT--4--SPLIT--1--SPLIT--5',
          'fcm_notification--TYPE--tokB--SPLIT--1--SPLIT--0--SPLIT--5' );
    is_deeply( $active_connections[0]{invocations}, { count => 1, at => 5 }, 'new month: child reset count' );
    is_deeply( $active_connections[1]{invocations}, { count => 0, at => 5 }, 'no invocations: child value' );
    is( $active_connections[1]{badge}, 1, 'badge' );
};

subtest 'fcm_token_delete: token FCM rejected is dropped from memory' => sub {
    my $c = MockConn->new;
    @active_connections = (
        { id => 'f1', type => FCM, token => 'tokDead', state => INVALID_CONNECTION },
        { id => 'f2', type => FCM, token => 'tokLive', state => INVALID_CONNECTION },
        { id => 'w1', type => FCM, token => 'tokDead', state => VALID_CONNECTION, conn => $c },
    );
    @errors = ();
    feed('fcm_token_delete--TYPE--tokDead');
    is_deeply( \@errors, [], 'job recognized' );
    is( $active_connections[0]{state}, PENDING_DELETE, 'push-only entry marked for removal' );
    is( $active_connections[1]{state}, INVALID_CONNECTION, 'other token untouched' );
    is( $active_connections[2]{state}, VALID_CONNECTION, 'live websocket connection left alone' );
};

subtest 'event_description: written to the ZM DB' => sub {
    @db_updates = ();
    feed('event_description--TYPE--3--SPLIT--555--SPLIT--detected:person');
    is_deeply( \@db_updates, [ [ 555, 'detected:person' ] ], 'updateEventinZmDB(eid, desc)' );
};

subtest 'active_event_update: State and Cause' => sub {
    %active_events = ( 3 => { 555 => { Start => { State => 'pending' } } } );
    feed('active_event_update--TYPE--3--SPLIT--555--SPLIT--Start--SPLIT--State--SPLIT--ready');
    is( $active_events{3}{555}{Start}{State}, 'ready', 'state updated' );
    feed('active_event_update--TYPE--3--SPLIT--555--SPLIT--Start--SPLIT--Cause--SPLIT--detected:car Motion--JSON--{"labels":["car"]}');
    is( $active_events{3}{555}{Start}{Cause}, 'detected:car Motion', 'cause text' );
    is_deeply( $active_events{3}{555}{Start}{DetectionJson}, { labels => ['car'] }, 'detection json' );
    feed('active_event_update--TYPE--3--SPLIT--555--SPLIT--End--SPLIT--Cause--SPLIT--Motion');
    is_deeply( $active_events{3}{555}{End}{DetectionJson}, [], 'missing json -> []' );
};

subtest 'active_event_delete: removes event and decrements forks' => sub {
    %active_events = ( 3 => { 555 => {}, 556 => {} } );
    $child_forks = 2;
    feed('active_event_delete--TYPE--3--SPLIT--555');
    ok( !exists $active_events{3}{555}, 'event removed' );
    ok( exists $active_events{3}{556}, 'other event kept' );
    is( $child_forks, 1, 'child_forks decremented' );
};

subtest 'update_parallel_hooks: add and del' => sub {
    $parallel_hooks = 0;
    feed( 'update_parallel_hooks--TYPE--add', 'update_parallel_hooks--TYPE--add', 'update_parallel_hooks--TYPE--del' );
    is( $parallel_hooks, 1, 'two adds, one del' );
};

subtest 'unknown job is logged' => sub {
    @errors = ();
    feed('bogus--TYPE--x');
    like( $errors[0], qr/not recognized/, 'error logged' );
};

subtest 'corrupted active_event_update JSON does not kill the daemon' => sub {
    %active_events = ( 3 => { 555 => { Start => { State => 'pending' } } } );
    @errors = ();
    $child_forks = 1;
    my $ok = eval {
        feed( 'active_event_update--TYPE--3--SPLIT--555--SPLIT--Start--SPLIT--Cause--SPLIT--detected:car--JSON--{"labels":["ca',
              'active_event_delete--TYPE--3--SPLIT--555' );
        1;
    };
    ok( $ok, 'processJobs did not die' ) or diag $@;
    ok( scalar(@errors), 'error logged' );
    is( $child_forks, 0, 'following job still processed' );
};

done_testing();
