#!/usr/bin/env perl
use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
use File::Temp qw(tempfile tempdir);
use JSON;
use POSIX ();

require StubZM;

use ZmEventNotification::Config qw(:all);
use ZmEventNotification::Constants qw(:all);

# Stub out heavy deps that FCM.pm imports
for my $pkg (qw(
    ZmEventNotification::MQTT
    ZmEventNotification::DB
    ZmEventNotification::WebSocketHandler
)) {
    (my $file = $pkg) =~ s{::}{/}g;
    $INC{"$file.pm"} = 1;
    no strict 'refs';
    *{"${pkg}::import"} = sub { 1 };
}

# Need LWP/HTTP stubs for FCM module compilation
for my $pkg (qw(LWP::UserAgent HTTP::Request)) {
    (my $file = $pkg) =~ s{::}{/}g;
    $INC{"$file.pm"} = 1;
    no strict 'refs';
    *{"${pkg}::new"} = sub { bless {}, $_[0] };
    *{"${pkg}::import"} = sub { 1 };
}

use_ok('ZmEventNotification::FCM');
ZmEventNotification::FCM->import(':all');

# Helpers
sub _write_file {
    my ($path, $content) = @_;
    open(my $fh, '>', $path) or die "Cannot open $path: $!";
    print $fh $content;
    close($fh);
}

sub _read_file {
    my $path = shift;
    open(my $fh, '<', $path) or die "Cannot read $path: $!";
    local $/;
    my $data = <$fh>;
    close($fh);
    return $data;
}

my $tmpdir = tempdir(CLEANUP => 1);

# ===== initFCMTokens =====

{
    # Creates file if missing
    my $tf = "$tmpdir/init_create.txt";
    local $fcm_config{token_file} = $tf;
    ok(!-f $tf, 'token file does not exist before init');
    initFCMTokens();
    ok(-f $tf, 'initFCMTokens creates file if missing');
    my $data = decode_json(_read_file($tf));
    is_deeply($data, { tokens => {} }, 'new file contains empty tokens hash');
}

{
    # Loads valid JSON, populates active_connections
    my $tf = "$tmpdir/init_load.txt";
    my $tokens = {
        tokens => {
            'tok_abc123' => {
                monlist   => '1,2',
                intlist   => '0,0',
                platform  => 'android',
                pushstate => 'enabled',
                appversion => '2.0',
                invocations => { count => 5, at => 3 }
            }
        }
    };
    _write_file($tf, encode_json($tokens));
    local $fcm_config{token_file} = $tf;
    @main::active_connections = ();
    initFCMTokens();
    is(scalar @main::active_connections, 1, 'one connection loaded');
    is($main::active_connections[0]{token}, 'tok_abc123', 'correct token');
    is($main::active_connections[0]{platform}, 'android', 'correct platform');
    is($main::active_connections[0]{type}, FCM, 'type is FCM');
    is($main::active_connections[0]{state}, INVALID_CONNECTION, 'state is INVALID_CONNECTION');
    is($main::active_connections[0]{monlist}, '1,2', 'correct monlist');
}

{
    # Migrates legacy colon-separated format to JSON
    my $tf = "$tmpdir/init_legacy.txt";
    _write_file($tf, "tok_legacy:1,2:0,0:ios:enabled\n");
    local $fcm_config{token_file} = $tf;
    @main::active_connections = ();
    initFCMTokens();
    my $data = decode_json(_read_file($tf));
    ok(exists $data->{tokens}{'tok_legacy'}, 'legacy token migrated to JSON');
    is($data->{tokens}{'tok_legacy'}{platform}, 'ios', 'platform preserved after migration');
    is(scalar @main::active_connections, 1, 'one connection after migration');
}

{
    # Each loaded token gets its own id; the parent routes child jobs by id (#55)
    my $tf = "$tmpdir/init_ids.txt";
    _write_file($tf, encode_json({ tokens => { map { ("tok_$_" => {}) } 1..3 } }));
    local $fcm_config{token_file} = $tf;
    @main::active_connections = ();
    initFCMTokens();
    my %ids = map { $_->{id} => 1 } @main::active_connections;
    is(scalar keys %ids, 3, 'tokens loaded together get distinct ids');
}

# ===== saveFCMTokens =====

{
    # saveFCMTokens writes new token entry
    my $tf = "$tmpdir/save_new.txt";
    _write_file($tf, '{"tokens":{}}');
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('tok_new', '1,2', '0,0', 'android', 'enabled', undef, '3.0');
    my $data = decode_json(_read_file($tf));
    ok(exists $data->{tokens}{'tok_new'}, 'new token written');
    is($data->{tokens}{'tok_new'}{platform}, 'android', 'platform stored');
    is($data->{tokens}{'tok_new'}{appversion}, '3.0', 'appversion stored');
}

{
    # saveFCMTokens skips empty token
    my $tf = "$tmpdir/save_empty.txt";
    _write_file($tf, '{"tokens":{}}');
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('', '1', '0', 'ios', 'enabled', undef);
    my $data = decode_json(_read_file($tf));
    is_deeply($data, { tokens => {} }, 'empty token not saved');
}

{
    # saveFCMTokens preserves existing tokens
    my $tf = "$tmpdir/save_preserve.txt";
    my $existing = { tokens => { 'tok_old' => {
        monlist => '3', intlist => '0', platform => 'ios',
        pushstate => 'enabled', invocations => { count => 1, at => 0 }
    }}};
    _write_file($tf, encode_json($existing));
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('tok_new2', '5', '10', 'android', 'enabled', undef);
    my $data = decode_json(_read_file($tf));
    ok(exists $data->{tokens}{'tok_old'}, 'old token preserved');
    ok(exists $data->{tokens}{'tok_new2'}, 'new token added');
}

{
    # saveFCMTokens with monlist=-1 does not overwrite stored monlist
    my $tf = "$tmpdir/save_minus1.txt";
    my $existing = { tokens => { 'tok_m1' => {
        monlist => '1,2', intlist => '0,0', platform => 'android',
        pushstate => 'enabled', invocations => { count => 0, at => 0 }
    }}};
    _write_file($tf, encode_json($existing));
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('tok_m1', '-1', '-1', 'android', 'enabled', undef);
    my $data = decode_json(_read_file($tf));
    is($data->{tokens}{'tok_m1'}{monlist}, '1,2', 'monlist=-1 did not overwrite');
    is($data->{tokens}{'tok_m1'}{intlist}, '0,0', 'intlist=-1 did not overwrite');
}

{
    # saveFCMTokens stores profile field
    my $tf = "$tmpdir/save_profile.txt";
    _write_file($tf, '{"tokens":{}}');
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('tok_prof', '1', '0', 'android', 'enabled', undef, '3.0', 'Home Server');
    my $data = decode_json(_read_file($tf));
    is($data->{tokens}{'tok_prof'}{profile}, 'Home Server', 'profile stored in token file');
}

{
    # saveFCMTokens with no profile omits field
    my $tf = "$tmpdir/save_no_profile.txt";
    _write_file($tf, '{"tokens":{}}');
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('tok_noprof', '1', '0', 'android', 'enabled', undef, '3.0');
    my $data = decode_json(_read_file($tf));
    ok(!exists $data->{tokens}{'tok_noprof'}{profile}, 'no profile key when not provided');
}

{
    # initFCMTokens loads profile from token data
    my $tf = "$tmpdir/init_profile.txt";
    my $tokens = {
        tokens => {
            'tok_with_profile' => {
                monlist   => '1',
                intlist   => '0',
                platform  => 'android',
                pushstate => 'enabled',
                appversion => '2.0',
                profile   => 'Office Server',
                invocations => { count => 0, at => 0 }
            }
        }
    };
    _write_file($tf, encode_json($tokens));
    local $fcm_config{token_file} = $tf;
    @main::active_connections = ();
    initFCMTokens();
    is($main::active_connections[0]{profile}, 'Office Server', 'profile loaded from token file');
}

# ===== deleteFCMToken =====
# deleteFCMToken reports to the parent over the job pipe; give it one
open( *main::WRITER, '>', \my $job_pipe ) or die "job pipe: $!";

{
    # deleteFCMToken removes token from file
    my $tf = "$tmpdir/del.txt";
    my $tokens = { tokens => {
        'tok_keep' => { monlist => '1', intlist => '0', platform => 'ios', pushstate => 'enabled' },
        'tok_del'  => { monlist => '2', intlist => '0', platform => 'android', pushstate => 'enabled' },
    }};
    _write_file($tf, encode_json($tokens));
    local $fcm_config{token_file} = $tf;
    @main::active_connections = (
        { token => 'tok_del', state => VALID_CONNECTION, type => FCM },
        { token => 'tok_keep', state => VALID_CONNECTION, type => FCM },
    );
    deleteFCMToken('tok_del');
    my $data = decode_json(_read_file($tf));
    ok(!exists $data->{tokens}{'tok_del'}, 'deleted token removed from file');
    ok(exists $data->{tokens}{'tok_keep'}, 'other token preserved');
}

{
    # deleteFCMToken marks the matching connection PENDING_DELETE so the rest
    # of this event (e.g. the end push) skips it; INVALID_CONNECTION is the
    # normal state of a push-only token and does not stop FCM sends
    my $tf = "$tmpdir/del_state.txt";
    _write_file($tf, '{"tokens":{"tok_inv":{}}}');
    local $fcm_config{token_file} = $tf;
    @main::active_connections = (
        { token => 'tok_inv', state => VALID_CONNECTION },
    );
    deleteFCMToken('tok_inv');
    is($main::active_connections[0]{state}, PENDING_DELETE, 'connection marked PENDING_DELETE');
}

{
    # deleteFCMToken handles missing file gracefully
    local $fcm_config{token_file} = "$tmpdir/nonexistent_file.txt";
    @main::active_connections = ();
    # Should not die
    eval { deleteFCMToken('tok_none') };
    is($@, '', 'no error on missing file');
}

{
    # deleteFCMToken tells the parent, which holds the authoritative list
    my $tf = "$tmpdir/del_notify.txt";
    _write_file($tf, '{"tokens":{"tok_gone":{}}}');
    local $fcm_config{token_file} = $tf;
    @main::active_connections = ();
    my $pipe = '';
    open(my $w, '>', \$pipe) or die;
    local *main::WRITER = $w;
    deleteFCMToken('tok_gone');
    close($w);
    is($pipe, "fcm_token_delete--TYPE--tok_gone\n", 'parent notified over the job pipe');
}

# ===== saveTokenInvocations =====

{
    my $tf = "$tmpdir/inv.txt";
    _write_file($tf, encode_json({ tokens => {
        tok_a => { platform => 'ios', monlist => '1', invocations => { count => 1, at => 2 } },
    }}));
    local $fcm_config{token_file} = $tf;
    @main::active_connections = (
        { type => FCM, token => 'tok_a', invocations => { count => 9, at => 2 } },
        { type => FCM, token => 'tok_deleted', invocations => { count => 3, at => 2 } },
        { type => WEB, id => 'w' },
    );
    saveTokenInvocations();
    my $data = decode_json(_read_file($tf));
    is_deeply($data->{tokens}{tok_a}, { platform => 'ios', monlist => '1', invocations => { count => 9, at => 2 } },
        'counter of a token in the file updated, other fields kept');
    ok(!exists $data->{tokens}{tok_deleted}, 'token deleted from the file is not recreated');
}

# ===== writeTokenFile =====

{
    # Rewrite keeps the file's permission bits and exact JSON format
    my $tf = "$tmpdir/mode.txt";
    _write_file($tf, '{"tokens":{}}');
    chmod 0640, $tf;
    local $fcm_config{token_file} = $tf;
    writeTokenFile({ tokens => { a => { platform => 'ios' } } });
    is((stat $tf)[2] & 07777, 0640, 'mode preserved');
    is(_read_file($tf), '{"tokens":{"a":{"platform":"ios"}}}', 'content is plain encode_json');
}

{
    # The new content replaces the file by rename (new inode), so a reader
    # never sees it truncated; no temp file is left behind.
    my $dir = "$tmpdir/atomic";
    mkdir $dir;
    my $tf = "$dir/tokens.txt";
    _write_file($tf, '{"tokens":{"old":{}}}');
    my $ino = (stat $tf)[1];
    local $fcm_config{token_file} = $tf;
    writeTokenFile({ tokens => { new => {} } });
    isnt((stat $tf)[1], $ino, 'file replaced, not rewritten in place');
    is(_read_file($tf), '{"tokens":{"new":{}}}', 'new content');
    opendir(my $dh, $dir) or die;
    my @left = grep { !/^\.\.?$/ && $_ ne 'tokens.txt' } readdir $dh;
    is_deeply(\@left, [], 'no temp file left behind');
}

{
    # A token file that does not exist yet gets the mode open() would give
    my $tf = "$tmpdir/fresh_tokens.txt";
    local $fcm_config{token_file} = $tf;
    my $old = umask 022;
    writeTokenFile({ tokens => {} });
    umask $old;
    is((stat $tf)[2] & 07777, 0644, 'new file is 0644 under umask 022');
}

{
    # A reader polling during repeated writes never sees an empty or
    # partial file
    my $tf = "$tmpdir/reader.txt";
    my $big = { tokens => { map { ("tok_$_" => { platform => 'android', monlist => '1,2,3' }) } 1 .. 300 } };
    local $fcm_config{token_file} = $tf;
    writeTokenFile($big);
    my $pid = fork();
    die "fork: $!" if !defined $pid;
    if (!$pid) {
        writeTokenFile($big) for 1 .. 300;
        exit 0;
    }
    my $bad = 0;
    while (waitpid($pid, POSIX::WNOHANG()) == 0) {
        my $c = _read_file($tf);
        $bad++ if !eval { decode_json($c); 1 };
    }
    is($bad, 0, 'no truncated or partial reads during writes');
}

SKIP: {
    skip 'root ignores directory permissions', 1 if $> == 0;
    # A writable token file in a read-only directory is still updated
    my $ro = "$tmpdir/ro";
    mkdir $ro;
    my $tf = "$ro/tokens.txt";
    _write_file($tf, '{"tokens":{}}');
    chmod 0555, $ro;
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    saveFCMTokens('tok_ro', '1', '0', 'ios', 'enabled', undef);
    chmod 0755, $ro;
    ok(exists decode_json(_read_file($tf))->{tokens}{tok_ro}, 'token saved in place');
}

{
    # Parallel read-modify-write (parent saving a token while event forks
    # update the file) must not lose entries or expose a truncated file.
    my $tf = "$tmpdir/race.txt";
    _write_file($tf, '{"tokens":{}}');
    local $fcm_config{token_file} = $tf;
    local $fcm_config{enabled} = 1;
    my @pids;
    for my $w (1 .. 3) {
        my $pid = fork();
        die "fork: $!" if !defined $pid;
        if (!$pid) {
            saveFCMTokens("tok_${w}_$_", '1', '0', 'android', 'enabled', undef) for 1 .. 40;
            exit 0;
        }
        push @pids, $pid;
    }
    waitpid($_, 0) for @pids;
    my $data = decode_json(_read_file($tf));
    is(scalar(keys %{ $data->{tokens} }), 120, 'all 120 tokens saved by 3 parallel writers');
}

done_testing();
