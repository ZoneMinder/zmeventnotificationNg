#!/usr/bin/env perl
# Tests for subs that live in zmeventnotification.pl itself (checkNewEvents,
# initSocketServer, restartES). The script cannot be loaded whole (it connects
# to ZM and starts the server at file scope), so each sub's real source is
# extracted from the script and compiled into main with the script's file-scope
# lexicals declared alongside. exit/exec are overridden to record calls.
use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
require StubZM;

use ZmEventNotification::Constants qw(:all);
use ZmEventNotification::Config qw(:all);
use ZmEventNotification::Util qw(getConnFields);

# ---- stubs for what the extracted subs call ----
our (@exec_args, @logged_errors, @processed_msgs);
our %shm;    # monitor id -> { state, last_event, trigger_cause, trigger_text, alarm_cause }

sub main::STATE_ALARM () { 2 }
sub main::STATE_ALERT () { 3 }
sub main::zmMemInvalidate { }
sub main::getNotesFromEventDB { '' }
sub main::zmMemRead {
  my ($monitor, $fields) = @_;
  my $s = $shm{ $monitor->{Id} };
  return $s->{alarm_cause} if !ref $fields;
  return @{$s}{qw(state last_event trigger_cause trigger_text)};
}
{
  no warnings 'redefine', 'once';
  *main::Error = sub { push @logged_errors, $_[0] };
  *main::processIncomingMessage = sub { push @processed_msgs, $_[1] };
}

{
  package FakeWSS;
  our %last_opts;
  our $shutdown_called = 0;
  sub new      { my ($c, %o) = @_; %last_opts = %o; bless {}, 'FakeWSS' }
  sub start    { }
  sub shutdown { $shutdown_called++ }
}
{
  package FakeConn;
  sub new  { my ($c, $ip, $port) = @_; bless { ip => $ip, port => $port, h => {} }, $c }
  sub ip   { $_[0]{ip} }
  sub port { $_[0]{port} }
  sub on   { my ($s, %h) = @_; $s->{h} = { %{ $s->{h} }, %h } }
}
{
  package FakeHandshake;
  sub new    { bless {}, shift }
  sub req    { $_[0] }
  sub fields { {} }
}
{
  no warnings 'redefine', 'once';
  *Net::WebSocket::Server::new = \&FakeWSS::new;
}

{
  no warnings 'once';
  *CORE::GLOBAL::exit = sub { die 'EXIT:' . ( $_[0] // 0 ) . "\n" };
  *CORE::GLOBAL::exec = sub { @exec_args = @_; die "EXEC\n" };
}

# ---- compile the real subs out of zmeventnotification.pl ----
my $pl_file = "$FindBin::Bin/../zmeventnotification.pl";
open( my $fh, '<', $pl_file ) or die "open $pl_file: $!";
my $pl = do { local $/; <$fh> };
close $fh;

sub pl_sub {
  my $name = shift;
  my ($src) = $pl =~ /^(sub \Q$name\E\b.*?^\})/ms;
  die "sub $name not found in zmeventnotification.pl" if !$src;
  return $src;
}

my $code = join "\n",
  'package main; use strict; use warnings;',
  'use Time::HiRes qw/gettimeofday/; use POSIX qw(ceil);',
  'our ($es_terminate, %monitors, @active_connections, $wss, $dbh, @original_argv);',
  'my ($child_forks, $parallel_hooks, $total_forks) = (0, 0, 0);',
  'my ($mqtt_last_tick_time, $es_start_time) = (time(), time());',
  'my $monitor_reload_time = time();',
  'my %active_events;',
  'my $zmdc_active = 0;',
  'sub _t_set_zmdc { $zmdc_active = shift }',
  'sub _t_reset_events { %active_events = () }',
  map( { pl_sub($_) } qw(checkNewEvents initSocketServer restartES) ),
  '1;';
eval $code or die "compiling extracted subs failed: $@";

sub run_catching(&) {
  my $cb = shift;
  local $@;
  eval { $cb->(); 1 } and return '';
  return $@;
}

local $server_config{monitor_reload_interval} = 1_000_000;

# ===== checkNewEvents =====
sub alarm_on {
  my ( $mid, $eid, %o ) = @_;
  %main::monitors = ( $mid => { Id => $mid, Name => "Mon$mid" } );
  $shm{$mid} = {
    state         => STATE_ALARM(),
    last_event    => $eid,
    trigger_cause => $o{trigger_cause} // '',
    trigger_text  => '',
    alarm_cause   => $o{alarm_cause} // '',
  };
}

subtest 'checkNewEvents: alarm_cause read from SHM when read_alarm_cause on' => sub {
  _t_reset_events();
  local $notify_config{read_alarm_cause} = 1;
  alarm_on( 1, 100, alarm_cause => 'Motion: Zone1' );
  my @ev = checkNewEvents();
  is( scalar @ev, 1, 'one new event' );
  is( $ev[0]{Alarm}{EventId}, 100, 'event id' );
  is( $ev[0]{Alarm}{MonitorId}, 1, 'monitor id' );
  is( $ev[0]{Alarm}{Start}{Cause}, 'Motion: Zone1', 'cause from SHM alarm_cause' );
  is( $ev[0]{MonitorObj}{Id}, 1, 'monitor object passed' );
};

subtest 'checkNewEvents: trigger_cause fills empty alarm_cause' => sub {
  _t_reset_events();
  local $notify_config{read_alarm_cause} = 1;
  alarm_on( 1, 101, alarm_cause => '', trigger_cause => 'Forced Web' );
  my @ev = checkNewEvents();
  is( $ev[0]{Alarm}{Start}{Cause}, 'Forced Web', 'trigger_cause used' );
};

subtest 'checkNewEvents: same event reported once, older event discarded' => sub {
  _t_reset_events();
  local $notify_config{read_alarm_cause} = 1;
  alarm_on( 1, 200, alarm_cause => 'x' );
  is( scalar( my @a = checkNewEvents() ), 1, 'first sight reported' );
  is( scalar( my @b = checkNewEvents() ), 0, 'same event not re-reported' );
  alarm_on( 1, 150, alarm_cause => 'x' );
  is( scalar( my @c = checkNewEvents() ), 0, 'older event id discarded' );
  alarm_on( 1, 201, alarm_cause => 'x' );
  is( scalar( my @d = checkNewEvents() ), 1, 'newer event reported' );
};

subtest 'checkNewEvents: idle monitor reports nothing' => sub {
  _t_reset_events();
  local $notify_config{read_alarm_cause} = 1;
  alarm_on( 1, 300 );
  $shm{1}{state} = 0;
  is( scalar( my @ev = checkNewEvents() ), 0, 'no events when idle' );
};

subtest 'checkNewEvents: read_alarm_cause off uses trigger_cause' => sub {
  _t_reset_events();
  local $notify_config{read_alarm_cause} = 0;
  alarm_on( 1, 400, trigger_cause => 'Forced Web', alarm_cause => 'ignored' );
  my @ev = checkNewEvents();
  is( $ev[0]{Alarm}{Start}{Cause}, 'Forced Web', 'trigger_cause used when alarm cause not read' );
};

subtest 'checkNewEvents: trigger_cause does not stick to later events' => sub {
  _t_reset_events();
  local $notify_config{read_alarm_cause} = 0;
  alarm_on( 1, 500, trigger_cause => 'Forced Web' );
  my @a = checkNewEvents();
  is( $a[0]{Alarm}{Start}{Cause}, 'Forced Web', 'first event has trigger cause' );
  alarm_on( 2, 600, trigger_cause => '' );
  my @b = checkNewEvents();
  is( scalar @b, 1, 'second event reported' );
  ok( !$b[0]{Alarm}{Start}{Cause}, 'second event has no stale cause' )
    or diag( 'got cause: ' . $b[0]{Alarm}{Start}{Cause} );
};

# ===== initSocketServer =====
%main::monitors = ();

subtest 'initSocketServer: plain WS with default address listens on port only' => sub {
  local $ssl_config{enabled} = 0;
  local $server_config{port} = 9123;
  local $server_config{address} = DEFAULT_ADDRESS;
  local $server_config{event_check_interval} = 7;
  initSocketServer();
  is( $FakeWSS::last_opts{listen}, 9123, 'listen is the bare port' );
  is( $FakeWSS::last_opts{tick_period}, 7, 'tick_period from event_check_interval' );
  is( ref $FakeWSS::last_opts{on_connect}, 'CODE', 'on_connect handler set' );
  is( ref $FakeWSS::last_opts{on_tick}, 'CODE', 'on_tick handler set' );
};

subtest 'initSocketServer: SSL passes a configured IO::Socket::SSL listener' => sub {
  my %ssl_args;
  my $sock = bless {}, 'FakeSSLSock';
  no warnings 'redefine', 'once';
  local *IO::Socket::SSL::new = sub { my ($c, %a) = @_; %ssl_args = %a; $sock };
  local $ssl_config{enabled}   = 1;
  local $ssl_config{cert_file} = '/c.pem';
  local $ssl_config{key_file}  = '/k.pem';
  local $server_config{port}    = 9124;
  local $server_config{address} = '10.0.0.5';
  initSocketServer();
  is( $ssl_args{LocalPort}, 9124, 'LocalPort' );
  is( $ssl_args{LocalAddr}, '10.0.0.5', 'LocalAddr' );
  is( $ssl_args{SSL_cert_file}, '/c.pem', 'cert' );
  is( $ssl_args{SSL_key_file}, '/k.pem', 'key' );
  is( $FakeWSS::last_opts{listen}, $sock, 'listen is the SSL socket' );
};

subtest 'initSocketServer: SSL listener failure is fatal, no plaintext fallback' => sub {
  no warnings 'redefine', 'once';
  local *IO::Socket::SSL::new = sub { undef };
  local *IO::Socket::SSL::errstr = sub { 'bind: Address already in use' };
  local $ssl_config{enabled} = 1;
  local $server_config{port} = 9127;
  %FakeWSS::last_opts = ();
  @logged_errors = ();
  my $err = run_catching { initSocketServer() };
  is( $err, "EXIT:-1\n", 'exits with -1' );
  ok( !%FakeWSS::last_opts, 'websocket server not started' );
  like( join( '', @logged_errors ), qr/Address already in use/, 'SSL error logged' );
};

sub connect_client {
  my $conn = FakeConn->new(@_);
  $FakeWSS::last_opts{on_connect}->( undef, $conn );
  $conn->{h}{handshake}->( $conn, FakeHandshake->new );
  return $conn;
}

subtest 'initSocketServer: connect, message, disconnect handlers' => sub {
  local $ssl_config{enabled} = 0;
  local $server_config{port} = 9125;
  local $server_config{address} = DEFAULT_ADDRESS;
  @main::active_connections = ();
  @processed_msgs = ();
  initSocketServer();

  my $c = connect_client( '1.2.3.4', 5000 );
  is( scalar @main::active_connections, 1, 'handshake adds a connection' );
  my $entry = $main::active_connections[0];
  is( $entry->{state}, PENDING_AUTH, 'new connection pending auth' );
  is( $entry->{conn}, $c, 'entry holds the connection' );
  is( $entry->{type}, WEB, 'type WEB' );

  $c->{h}{utf8}->( $c, '{"event":"auth"}' );
  is_deeply( \@processed_msgs, ['{"event":"auth"}'], 'utf8 message forwarded' );

  $c->{h}{disconnect}->( $c, 1000, '' );
  is( $entry->{state}, PENDING_DELETE, 'tokenless connection marked for delete on disconnect' );

  my $c2 = connect_client( '1.2.3.5', 5001 );
  $main::active_connections[-1]{token} = 'tok';
  $main::active_connections[-1]{state} = VALID_CONNECTION;
  $c2->{h}{disconnect}->( $c2, 1000, '' );
  is( $main::active_connections[-1]{state}, INVALID_CONNECTION,
    'connection with token kept but invalidated on disconnect' );
};

subtest 'initSocketServer: disconnect only affects its own connection, not one with same ip:port' => sub {
  local $ssl_config{enabled} = 0;
  local $server_config{port} = 9130;
  local $server_config{address} = DEFAULT_ADDRESS;
  @main::active_connections = ();
  initSocketServer();
  my $old = connect_client( '127.0.0.1', 40000 );
  $main::active_connections[0]{state} = VALID_CONNECTION;
  my $new = connect_client( '127.0.0.1', 40000 );
  $new->{h}{disconnect}->( $new, 1000, '' );
  is( $main::active_connections[1]{state}, PENDING_DELETE, 'disconnected connection marked' );
  is( $main::active_connections[0]{state}, VALID_CONNECTION, 'other connection untouched' );
};

subtest 'initSocketServer: an exception while handling a message does not escape' => sub {
  local $ssl_config{enabled} = 0;
  local $server_config{port} = 9126;
  local $server_config{address} = DEFAULT_ADDRESS;
  @main::active_connections = ();
  @logged_errors = ();
  initSocketServer();
  my $c = connect_client( '1.2.3.6', 5002 );
  no warnings 'redefine';
  local *main::processIncomingMessage = sub { die "boom\n" };
  my $err = run_catching { $c->{h}{utf8}->( $c, '[1]' ) };
  is( $err, '', 'utf8 handler does not die' );
  like( join( '', @logged_errors ), qr/boom/, 'error logged' );
};

# ===== restartES =====
subtest 'restartES: under zmdc shuts down and exits 0' => sub {
  $main::wss = FakeWSS->new;
  $FakeWSS::shutdown_called = 0;
  _t_set_zmdc(1);
  my $err = run_catching { restartES() };
  is( $err, "EXIT:0\n", 'exit(0)' );
  is( $FakeWSS::shutdown_called, 1, 'server shut down' );
  _t_set_zmdc(0);
};

subtest 'restartES: standalone with no args re-execs $0' => sub {
  $main::wss = FakeWSS->new;
  @exec_args = ();
  { no warnings 'once'; @main::original_argv = (); }
  my $err = run_catching { restartES() };
  is( $err, "EXEC\n", 'exec called' );
  is_deeply( \@exec_args, [$0], 'exec($0)' );
};

subtest 'restartES: standalone re-exec keeps the original command line' => sub {
  $main::wss = FakeWSS->new;
  @exec_args = ();
  @main::original_argv = ( '--config', '/etc/zm/custom.yml', '--debug' );
  my $err = run_catching { restartES() };
  is( $err, "EXEC\n", 'exec called' );
  is_deeply( \@exec_args, [ $0, '--config', '/etc/zm/custom.yml', '--debug' ],
    'original arguments passed again' );
  @main::original_argv = ();
};

done_testing();
