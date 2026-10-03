#!/usr/bin/env perl
# The parent's per-tick fork loop in zmeventnotification.pl (on_tick) and its
# max_parallel_hooks gate. The real loop is pulled from the script source;
# fork() is overridden to count forks and take the parent branch.
use strict;
use warnings;
no warnings 'once';
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
BEGIN { require StubZM }
use PlSource;

use ZmEventNotification::Constants qw(:all);
use ZmEventNotification::Config qw(:all);
use ZmEventNotification::HookProcessor qw(:all);

our ( $parallel_hooks, $child_forks, $total_forks, $dbh );
our $forks;
our @errors;
{ no warnings 'redefine'; *main::Error = sub { push @errors, $_[0] }; }
sub zmDbConnect { 'dbh' }
BEGIN { *CORE::GLOBAL::fork = sub { $main::forks++; return 4242 } }

my $block = PlSource::extract( 'foreach (@newEvents) {', '} # for loop' );
eval "sub fork_loop { my \@newEvents = \@_; my \$forked_hooks = 0;\n$block }; 1"
    or die "compile fork loop: $@";

sub events { map { { Alarm => { MonitorId => $_, EventId => 100 + $_ }, MonitorObj => {} } } @_ }

sub run {
    my (%a) = @_;
    $forks = 0;
    @errors = ();
    $parallel_hooks = $a{running} // 0;
    $child_forks = 0;
    $total_forks = 0;
    local $hooks_config{max_parallel_hooks} = $a{max};
    local $hooks_config{enabled} = $a{enabled} // 1;
    local $hooks_config{event_start_hook} = '/usr/bin/zm_event_start.sh';
    local $hooks_config{hook_skip_monitors} = $a{skip} // '';
    fork_loop( events( @{ $a{mids} } ) );
    return $forks;
}

subtest 'unlimited (max 0): every event forks' => sub {
    is( run( max => 0, mids => [ 1, 2, 3 ] ), 3, '3 forks' );
    is( $child_forks, 3, 'child_forks counted' );
    is( $total_forks, 3, 'total_forks counted' );
};

subtest 'limit already reached: event dropped with an error' => sub {
    is( run( max => 2, running => 2, mids => [1] ), 0, 'no fork' );
    like( $errors[0], qr/max_parallel_hooks=2/, 'error logged' );
    is( $child_forks, 0, 'child_forks not counted' );
};

subtest 'below the limit: event forks' => sub {
    is( run( max => 3, running => 1, mids => [1] ), 1, '1 fork' );
};

subtest 'hooks disabled: limit never drops events' => sub {
    is( run( max => 1, enabled => 0, mids => [ 1, 2, 3 ] ), 3, '3 forks' );
};

subtest 'burst in one tick: limit applies to forks of this tick' => sub {
    is( run( max => 2, mids => [ 1, 2, 3 ] ), 2, 'only 2 of 3 forked' );
    is( scalar(@errors), 1, 'one drop logged' );
    is( run( max => 3, running => 1, mids => [ 1, 2, 3 ] ), 2, 'running hooks count too' );
};

subtest 'burst: monitors in hook_skip_monitors run no hook and are not counted' => sub {
    is( run( max => 1, skip => '1,2', mids => [ 1, 2, 3 ] ), 3, 'all forked' );
    is( run( max => 1, skip => '1', mids => [ 1, 2, 3 ] ), 2, 'monitor 1 not counted, 2 counted, 3 dropped' );
};

done_testing();
