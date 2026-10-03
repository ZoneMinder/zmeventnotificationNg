package ZmEventNotification::HookProcessor;
use strict;
use warnings;
use Exporter 'import';
use JSON;
use POSIX qw(strftime);
use Time::HiRes ();
use Fcntl qw(:flock O_RDWR O_CREAT);
use ZmEventNotification::Constants qw(:all);
use ZmEventNotification::Config qw(:all);
use ZmEventNotification::Util qw(getConnectionIdentity isInList getInterval parseDetectResults buildPictureUrl appendImagePath getFrameId);
use ZmEventNotification::FCM qw(sendOverFCM);
use ZmEventNotification::MQTT qw(sendOverMQTTBroker);
use ZmEventNotification::Rules qw(isAllowedInRules);
use ZmEventNotification::DB qw(updateEventinZmDB getNotesFromEventDB tagEventObjects);
use ZmEventNotification::WebSocketHandler qw(getNotificationStatusEsControl);

our @EXPORT_OK = qw(
  processNewAlarmsInFork
  sendEvent
  isAllowedChannel
  shouldSendEventToConn
  sendOverWebSocket
  hookLimitReached
);
our %EXPORT_TAGS = ( all => \@EXPORT_OK );

sub sendOverWebSocket {
  my $alarm      = shift;
  my $ac         = shift;
  my $event_type = shift;
  my $resCode    = shift;

  my $eid = $alarm->{EventId};

  my $frame_id = getFrameId($alarm);

  # the alarm object is shared with the other clients of this event: change a copy
  $alarm = {%$alarm};
  if ( $notify_config{picture_url} && $notify_config{include_picture} ) {
    $alarm->{Picture} = buildPictureUrl($eid, $alarm->{Cause}, $resCode, 'websocket', $frame_id);
  }

  $alarm->{Cause} = 'End:'.$alarm->{Cause} if $event_type eq 'event_end';
  my $json = encode_json(
    { event  => 'alarm',
      type   => '',
      status => 'Success',
      events => [$alarm]
    }
  );
  main::Debug(2, 'Child: posting job to send out message to id:'
      . $ac->{id} . '->'
      . $ac->{conn}->ip() . ':'
      . $ac->{conn}->port());
  print main::WRITER 'message--TYPE--' . $ac->{id} . '--SPLIT--' . $json . "\n";
}

sub sendEvent {
  my $alarm      = shift;
  my $ac         = shift;
  my $event_type = shift;
  my $resCode    = shift;    # 0 = on_success, 1 = on_fail

  my $id   = $alarm->{MonitorId};
  my $name = $alarm->{Name};

  if ( ( !$notify_config{send_event_end_notification} ) && ( $event_type eq 'event_end' ) ) {
    main::Info(
      'Not sending event end notification as send_event_end_notification is no'
    );
    return;
  }

  if ( ( !$notify_config{send_event_start_notification} ) && ( $event_type eq 'event_start' ) ) {
    main::Info(
      'Not sending event start notification as send_event_start_notification is no'
    );
    return;
  }

  my $hook = $event_type eq 'event_start' ? $hooks_config{event_start_hook} : $hooks_config{event_end_hook};

  my $str = encode_json(
    { event  => 'alarm',
      type   => '',
      status => 'Success',
      events => [$alarm]
    }
  );

  my $send;

  if ( $ac->{type} == FCM
    && $ac->{pushstate} ne 'disabled'
    && $ac->{state} != PENDING_AUTH
    && $ac->{state} != PENDING_DELETE
    )
  {
    # only send if fcm is an allowed channel
    if ( isAllowedChannel( $event_type, 'fcm', $resCode )
      || !$hook
      || !$hooks_config{enabled} )
    {
      main::Info("Sending $event_type notification over FCM");
      $send = \&sendOverFCM;
    } else {
      main::Info(
        "Not sending over FCM as notify filters are on_success:$hooks_config{event_start_notify_on_hook_success} and on_fail:$hooks_config{event_end_notify_on_hook_fail}"
      );
    }
  } elsif ( $ac->{type} == WEB
    && $ac->{state} == VALID_CONNECTION
    && exists $ac->{conn} )
  {

    if ( isAllowedChannel( $event_type, 'web', $resCode )
      || !$hook
      || !$hooks_config{enabled} )
    {
      main::Info( "Sending $event_type notification for EID:"
          . $alarm->{EventId}
          . 'over web' );
      $send = \&sendOverWebSocket;
    } else {
      main::Info(
        "Not sending over Web as notify filters are on_success:$hooks_config{event_start_notify_on_hook_success} and on_fail:$hooks_config{event_start_notify_on_hook_fail}"
      );
    }

  } elsif ( $ac->{type} == MQTT ) {
    if ( isAllowedChannel( $event_type, 'mqtt', $resCode )
      || !$hook
      || !$hooks_config{enabled} )
    {
      main::Info( "Sending $event_type notification for EID:"
          . $alarm->{EventId}
          . ' over MQTT' );
      $send = \&sendOverMQTTBroker;
    } else {
      main::Info(
        "Not sending over MQTT as notify filters are on_success:$hooks_config{event_start_notify_on_hook_success} and on_fail:$hooks_config{event_start_notify_on_hook_fail}"
      );
    }
  }

  return unless $send;

  # The interval applies to start notifications only. It is checked again
  # here, under the lock, because another fork may have sent since
  # shouldSendEventToConn looked.
  if ( $event_type eq 'event_start' ) {
    my $forced = $escontrol_config{enabled}
      && getNotificationStatusEsControl( $alarm->{MonitorId} ) == ESCONTROL_FORCE_NOTIFY;
    my $mint = $forced ? 0 : getInterval( $ac->{intlist}, $ac->{monlist}, $alarm->{MonitorId} );
    return unless _claimSend( $ac, $alarm->{MonitorId}, $mint );
  }
  $send->( $alarm, $ac, $event_type, $resCode );
}

# Last-sent times are shared through a file, not kept in active_connections:
# each event is handled in its own fork, and a fork's copy of
# active_connections is stale as soon as another fork sends.
# Returns ($fh, \%times) with $fh locked, or () if the file cannot be opened.
sub _openLastSent {
  my $lock = shift;
  my $file = $server_config{base_data_path} . '/push/last_sent.json';
  my $fh;
  if ( !sysopen( $fh, $file, O_RDWR | O_CREAT, 0600 ) ) {
    main::Error("Cannot open $file: $!. Notification intervals are not enforced");
    return;
  }
  flock( $fh, $lock );
  my $raw = do { local $/; <$fh> };
  my $times = eval { decode_json($raw) } // {};
  return ( $fh, $times );
}

# Device tokens are stable; other connections are keyed by their
# per-connection id, prefixed so they can be pruned.
sub _lastSentKey {
  my $ac = shift;
  return $ac->{token} ? $ac->{token} : 'conn-' . ( $ac->{id} // '' );
}

sub _lastSentTime {
  my ( $ac, $mid ) = @_;
  my ( $fh, $times ) = _openLastSent(LOCK_SH);
  return undef if !$fh;
  close($fh);
  return $times->{ _lastSentKey($ac) }->{$mid};
}

# Records the send time unless one was recorded within the last $mint
# seconds. Check and record happen under one lock, so parallel forks
# cannot both pass. Returns 1 if the caller should send.
sub _claimSend {
  my ( $ac, $mid, $mint ) = @_;
  my ( $fh, $times ) = _openLastSent(LOCK_EX);
  return 1 if !$fh;
  my $now  = time();
  my $key  = _lastSentKey($ac);
  my $last = $times->{$key}->{$mid};
  if ( $last && ( $now - $last ) < ( $mint // 0 ) ) {
    close($fh);
    main::Debug(1, "Monitor $mid: last notification was "
        . ( $now - $last )
        . "s ago, within interval of $mint. Not sending");
    return 0;
  }
  $times->{$key}->{$mid} = $now;

  # Connection ids change on every reconnect, so their entries are dropped
  # after a day without a send. Device tokens are kept. A websocket client
  # connected for over a day with a longer interval may get one early send.
  foreach my $k ( grep { /^conn-/ } keys %$times ) {
    my $mids = $times->{$k};
    delete $mids->{$_} for grep { $now - $mids->{$_} > 86400 } keys %$mids;
    delete $times->{$k} if !%$mids;
  }

  seek( $fh, 0, 0 );
  truncate( $fh, 0 );
  print $fh encode_json($times);
  close($fh);
  return 1;
}

sub isAllowedChannel {
  my $event_type = shift;
  my $channel    = shift;
  my $rescode    = shift;

  main::Debug(2, "isAllowedChannel: got type:$event_type resCode:$rescode");

  my $key;
  if ( $event_type eq 'event_start' ) {
    $key = $rescode == 0 ? 'event_start_notify_on_hook_success' : 'event_start_notify_on_hook_fail';
  } elsif ( $event_type eq 'event_end' ) {
    $key = $rescode == 0 ? 'event_end_notify_on_hook_success' : 'event_end_notify_on_hook_fail';
  } else {
    main::Error("Invalid event_type:$event_type sent to isAllowedChannel()");
    return 0;
  }

  my %allowed = map { $_ => 1 } split(/\s*,\s*/, lc($hooks_config{$key} // ''));
  return exists($allowed{$channel}) || exists($allowed{all});
}

sub shouldSendEventToConn {
  my $alarm  = shift;
  my $ac     = shift;
  my $retVal = 0;

  my $monlist   = $ac->{monlist};
  my $intlist   = $ac->{intlist};

  if ($escontrol_config{enabled}) {
    my $id   = $alarm->{MonitorId};
    my $name = $alarm->{Name};
    if ( getNotificationStatusEsControl($id) == ESCONTROL_FORCE_NOTIFY ) {
      main::Debug(1, "ESCONTROL: Notifications are force enabled for Monitor:$name($id), returning true");
      return 1;
    }

    if ( getNotificationStatusEsControl($id) == ESCONTROL_FORCE_MUTE ) {
      main::Debug(1, "ESCONTROL: Notifications are muted for Monitor:$name($id), not sending");
      return 0;
    }
  }

  my $id     = getConnectionIdentity($ac);
  my $connId = $ac->{id};
  main::Debug(1, 'Checking alarm conditions for '.$id);

  if ( isInList( $monlist, $alarm->{MonitorId} ) ) {
    my $mint = getInterval( $intlist, $monlist, $alarm->{MonitorId} );
    my $last_sent = _lastSentTime( $ac, $alarm->{MonitorId} );
    if ( $last_sent ) {
      my $elapsed = time() - $last_sent;
      if ( $elapsed >= $mint ) {
        main::Debug(1, 'Monitor '
            . $alarm->{MonitorId}
            . " event: should send out as  $elapsed is >= interval of $mint");
        $retVal = 1;
      } else {
        main::Debug(1, 'Monitor '
            . $alarm->{MonitorId}
            . " event: should NOT send this out as $elapsed is less than interval of $mint");
        $retVal = 0;
      }
    } else {
      main::Debug(1, 'Monitor '.$alarm->{MonitorId}.' event: last time not found, so should send');
      $retVal = 1;
    }
  } else {
    main::Debug(1, 'should NOT send alarm as Monitor '.$alarm->{MonitorId}.' is excluded');
    $retVal = 0;
  }

  return $retVal;
}

sub _tag_detected_objects {
  my ($eid, $resJsonString, $label) = @_;
  return unless $hooks_config{tag_detected_objects} && $resJsonString;
  eval {
    my $det = decode_json($resJsonString);
    return unless defined($det);
    my @labels;
    if (ref($det) eq 'HASH' && ref($det->{labels}) eq 'ARRAY') {
      @labels = @{$det->{labels}};
    } elsif (ref($det) eq 'ARRAY') {
      for my $item (@$det) {
        next unless ref($item) eq 'HASH';
        push @labels, $item->{label} if $item->{label};
      }
    }
    tagEventObjects($eid, \@labels) if @labels;
  };
  main::Error("tagEventObjects ($label): $@") if $@;
}

# Hook output is not trusted: invalid detection JSON is logged and treated
# as no detections ([]) instead of killing the fork.
# Returns (decoded, json string to pass on).
sub _decode_detect_json {
  my $str = shift;
  my $ref = eval { decode_json($str) };
  return ( $ref, $str ) if !$@;
  main::Error("Could not parse hook detection JSON [$str]: $@");
  return ( [], '[]' );
}

sub _build_alarm_obj {
  my ($mname, $mid, $eid, $cause, $detectJson, $rulesObject) = @_;
  return {
    Name          => $mname,
    MonitorId     => $mid,
    EventId       => $eid,
    Cause         => $cause,
    DetectionJson => $detectJson || [],
    RulesObject   => $rulesObject
  };
}

# A hook that produced no detection text is a failure even if it exited 0.
# Pure; used at both event-start and event-end in the fork state machine.
# Locked by t/21.
sub _effective_hook_result {
  my ($exit_code, $res_txt) = @_;
  return 1 if !defined($res_txt) || $res_txt eq '';
  return $exit_code;
}

# Whether/why the event-end notification should be suppressed. Returns
# 'start_failed' (event_end_notify_if_start_success is on and the start hook
# failed), 'rules' (rules checks disallow it), or '' (send it). Pure; locked
# by t/21.
sub _end_notify_skip_reason {
  my ($notify_if_start_success, $start_hook_result, $rules_allowed) = @_;
  return 'start_failed' if $notify_if_start_success && $start_hook_result != 0;
  return 'rules' if !$rules_allowed;
  return '';
}

# Runs a configured command (hook, user script, api push script) with the
# event values as separate arguments. The configured command line is still
# parsed by the shell, so it may carry quotes or its own arguments. The
# values (monitor name, cause, detection text/JSON) reach it through "$@"
# and are never parsed by the shell. Appends the event path when
# hook_pass_image_path is on. Returns (stdout, exit code) like backticks
# and $? >> 8.
# With hook_timeout > 0 the command runs in its own process group. If its
# stdout is not closed within hook_timeout seconds, the whole group gets TERM,
# then KILL after HOOK_KILL_GRACE seconds, and ('', 1) is returned. The group
# kill matters: the hook's children (e.g. python zm_detect.py) hold stdout.
use constant HOOK_KILL_GRACE => 2;

sub _run_cmd {
  my ( $label, $cmd, $eid, @args ) = @_;
  appendImagePath( \@args, $eid ) if $hooks_config{hook_pass_image_path};
  main::Debug(1, "$label:$cmd " . join( ' ', map {"\"$_\""} @args ));
  my $timeout = $hooks_config{hook_timeout} // 0;
  return _run_cmd_timeout( $label, $cmd, $timeout, @args ) if $timeout > 0;
  open( my $fh, '-|', '/bin/sh', '-c', $cmd . ' "$@"', 'sh', @args )
    or do { main::Error("$label: could not run $cmd: $!"); return ( '', 1 ); };
  my $out = do { local $/; <$fh> } // '';
  close($fh);
  return ( $out, $? >> 8 );
}

sub _run_cmd_timeout {
  my ( $label, $cmd, $timeout, @args ) = @_;
  my $pid = open( my $fh, '-|' );
  if ( !defined($pid) ) {
    main::Error("$label: could not run $cmd: $!");
    return ( '', 1 );
  }
  if ( !$pid ) {
    setpgrp( 0, 0 );
    { exec( '/bin/sh', '-c', $cmd . ' "$@"', 'sh', @args ) };
    POSIX::_exit(127);
  }
  setpgrp( $pid, $pid );    # also here, so the kill below cannot race the child's
  my $deadline = Time::HiRes::time() + $timeout;
  my $out = '';
  my $rin = '';
  vec( $rin, fileno($fh), 1 ) = 1;
  while ( ( my $left = $deadline - Time::HiRes::time() ) > 0 ) {
    next if select( my $rout = $rin, undef, undef, $left ) <= 0;
    my $n = sysread( $fh, $out, 65536, length($out) );
    next if !defined($n) && $!{EINTR};
    if ( !$n ) {
      close($fh);
      return ( $out, $? >> 8 );
    }
  }
  main::Error("$label: $cmd timed out after ${timeout}s, killing its process group");
  kill( 'TERM', -$pid );
  my $grace_end = Time::HiRes::time() + HOOK_KILL_GRACE;
  while ( Time::HiRes::time() < $grace_end ) {
    waitpid( $pid, POSIX::WNOHANG() );
    last if !kill( 0, -$pid );
    select( undef, undef, undef, 0.1 );
  }
  kill( 'KILL', -$pid ) if kill( 0, -$pid );
  close($fh);
  return ( '', 1 );
}

sub _run_api_push {
  my ($temp_alarm_obj, $eid, $mid, $event_type, $hookResult) = @_;
  return unless $push_config{enabled} && $push_config{script};

  if ($event_type eq 'event_end' && !$notify_config{send_event_end_notification}) {
    main::Debug(1, 'Not sending event_end push over API as send_event_end_notification is no');
    return;
  }

  my $hook_key = $event_type eq 'event_start' ? 'event_start_hook' : 'event_end_hook';

  if ( isAllowedChannel( $event_type, 'api', $hookResult )
    || !$hooks_config{$hook_key}
    || !$hooks_config{enabled} )
  {
    main::Info("Sending push over API as it is allowed for $event_type");
    main::Info("Executing API script command for $event_type: $push_config{script}");

    my ( $api_res, $retcode ) = _run_cmd( "Executing API script command for $event_type",
      $push_config{script}, $eid, $eid, $mid, $temp_alarm_obj->{Name},
      $temp_alarm_obj->{Cause}, $event_type );
    main::Debug(1, "API push script returned ($event_type): $retcode");
  } else {
    main::Info("Not sending push over API as it is not allowed for $event_type");
  }
}

# Parent, before forking for each new event of a tick. A child reports its
# running hook ('add' on the job pipe) only by the next tick, so start hooks
# forked earlier in this tick are counted in $$forked_ref.
# Returns 1 if max_parallel_hooks is reached and the event must be dropped.
# ponytail: per-tick count; a child slower than one tick to report its 'add'
# is still missed. Count per child pid if that matters.
sub hookLimitReached {
  my ( $running, $forked_ref, $mid ) = @_;
  my $max = $hooks_config{max_parallel_hooks};
  return 1 if $max && ( $running + $$forked_ref ) >= $max;
  my %skip_hooks = map { $_ => 1 } split( ',', $hooks_config{hook_skip_monitors} // '' );
  $$forked_ref++ if $hooks_config{event_start_hook} && $hooks_config{enabled} && !$skip_hooks{$mid};
  return 0;
}

sub processNewAlarmsInFork {
  my $newEvent       = shift;
  my $alarm          = $newEvent->{Alarm};
  my $monitor        = $newEvent->{MonitorObj};
  my $mid            = $alarm->{MonitorId};
  my $eid            = $alarm->{EventId};
  my $mname          = $alarm->{MonitorName};
  my $doneProcessing = 0;

  my $hookResult      = 0;
  my $startHookResult = $hookResult;
  my $hookString = '';

  my $endProcessed = 0;
  my %skip_hooks = map { $_ => 1 } split(',', $hooks_config{hook_skip_monitors} // '');

  my $start_time = time();

  while (!$doneProcessing and !$main::es_terminate) {

    my $now = time();
    if ( $now - $start_time > 3600 ) {
      main::Info('Thread alive for an hour, bailing...');
      $doneProcessing = 1;
    }

    if ( $alarm->{Start}->{State} eq 'pending' ) {
      if ( $skip_hooks{$mid} ) {
        main::Info("$mid is in hook skip list, not using hooks");
        $alarm->{Start}->{State} = 'ready';
        $hookResult = 0;
      } else {
        if ( $hooks_config{event_start_hook} && $hooks_config{enabled} ) {
          print main::WRITER "update_parallel_hooks--TYPE--add\n";
          ( my $res, $hookResult ) = _run_cmd( 'Invoking hook on event start',
            $hooks_config{event_start_hook}, $eid, $eid, $mid,
            $alarm->{MonitorName}, $alarm->{Start}->{Cause} );

          print main::WRITER "update_parallel_hooks--TYPE--del\n";

          chomp($res);
          my ( $resTxt, $resJsonString ) = parseDetectResults($res);
          $hookResult = _effective_hook_result($hookResult, $resTxt);
          $startHookResult = $hookResult;

          main::Debug(1, "hook start returned with text:$resTxt json:$resJsonString exit:$hookResult");

          if ($hooks_config{event_start_hook_notify_userscript}) {
            _run_cmd( 'invoking user start notification script',
              $hooks_config{event_start_hook_notify_userscript}, $eid,
              $hookResult, $eid, $mid, $alarm->{MonitorName}, $resTxt, $resJsonString );
          } # user notify script

          if ( $hooks_config{use_hook_description} && $hookResult == 0 ) {
            $alarm->{Start}->{Cause} = $resTxt . ' ' . $alarm->{Start}->{Cause};
            ( $alarm->{Start}->{DetectionJson}, $resJsonString ) = _decode_detect_json($resJsonString);

            print main::WRITER 'active_event_update--TYPE--'
              . $mid
              . '--SPLIT--'
              . $eid
              . '--SPLIT--' . 'Start'
              . '--SPLIT--' . 'Cause'
              . '--SPLIT--'
              . $alarm->{Start}->{Cause}
              . '--JSON--'
              . $resJsonString . "\n";

            print main::WRITER 'event_description--TYPE--'
              . $mid
              . '--SPLIT--'
              . $eid
              . '--SPLIT--'
              . $resTxt . "\n";

            $hookString = $resTxt;
          }

          _tag_detected_objects($eid, $resJsonString, 'event_start') if $hookResult == 0;
        } else {
          main::Info(
            'use hooks/start hook not being used, going to directly send out a notification if checks pass'
          );
          $hookResult = 0;
        }

        $alarm->{Start}->{State} = 'ready';
      }
    } elsif ( $alarm->{Start}->{State} eq 'ready' ) {

      my ( $rulesAllowed, $rulesObject ) = isAllowedInRules($alarm);
      if ( !$rulesAllowed ) {
        main::Debug(1, 'rules: Not processing start notifications as rules checks failed');
      } else {
        my $temp_alarm_obj = _build_alarm_obj(
          $mname, $mid, $eid, $alarm->{Start}->{Cause},
          $alarm->{Start}->{DetectionJson}, $rulesObject
        );

        _run_api_push($temp_alarm_obj, $eid, $mid, 'event_start', $hookResult);
        main::Debug(1, 'Matching alarm to connection rules...');
        my %fcm_token_duplicates = ();
        foreach (@main::active_connections) {
          if ($_->{token} && $fcm_token_duplicates{$_->{token}}) {
            main::Debug(1, '...'.substr($_->{token},-10).' occurs mutiples times. NOT USUAL, ignoring');
            next;
          }
          if ( shouldSendEventToConn( $temp_alarm_obj, $_ ) ) {
            main::Debug(1, 'token is unique, shouldSendEventToConn returned true, so calling sendEvent');
            sendEvent( $temp_alarm_obj, $_, 'event_start', $hookResult );
            $fcm_token_duplicates{$_->{token}}++ if $_->{token};
          }
        }
      }
      $alarm->{Start}->{State} = 'done';
    }
    elsif ( ($alarm->{End}->{State} // '') eq 'pending' ) {
      if ( $skip_hooks{$mid} ) {
        main::Info("$mid is in hook skip list, not using hooks");
        $alarm->{End}->{State} = 'ready';
        $hookResult = 0;
      }
      else {
      if ( $alarm->{Start}->{State} ne 'done' ) {
        main::Debug(2, 'Not yet sending out end notification as start hook/notify is not done');

      } else {
        my $notes = getNotesFromEventDB($eid);
        if ($hookString) {
          if ( index( $notes, 'detected:' ) == -1 ) {
            main::Debug(1, "ZM overwrote detection DB, current notes: [$notes], adding detection notes back into DB [$hookString]");

            # This will be prefixed, so no need to add old notes back
            updateEventinZmDB( $eid, $hookString );
            $notes = $hookString . " " . $notes;
          } else {
            main::Debug(2, "DB Event notes contain detection text, all good");
          }
        }

        if ( $hooks_config{event_end_hook} && $hooks_config{enabled} ) {

          print main::WRITER "update_parallel_hooks--TYPE--add\n";
          ( my $res, $hookResult ) = _run_cmd( 'Invoking hook on event end',
            $hooks_config{event_end_hook}, $eid, $eid, $mid,
            $alarm->{MonitorName}, $notes );

          print main::WRITER "update_parallel_hooks--TYPE--del\n";

          chomp($res);
          my ( $resTxt, $resJsonString ) = parseDetectResults($res);
          $hookResult = _effective_hook_result($hookResult, $resTxt);

          $alarm->{End}->{State} = 'ready';
          main::Debug(1, "hook end returned with text:$resTxt  json:$resJsonString exit:$hookResult");

          $alarm->{End}->{Cause}         = $resTxt;
          ( $alarm->{End}->{DetectionJson}, $resJsonString ) = _decode_detect_json($resJsonString);

          if ($hooks_config{event_end_hook_notify_userscript}) {
            _run_cmd( 'invoking user end notification script',
              $hooks_config{event_end_hook_notify_userscript}, $eid,
              $hookResult, $eid, $mid, $alarm->{MonitorName}, $resTxt, $resJsonString );
          } # user notify script

          if ($hooks_config{use_hook_description} &&
              ($hookResult == 0) && (index($resTxt,'detected:') != -1)) {
            main::Debug(1, "Event end: overwriting notes with $resTxt");
            $alarm->{End}->{Cause} = $resTxt . ' ' . $alarm->{End}->{Cause};
            ( $alarm->{End}->{DetectionJson}, $resJsonString ) = _decode_detect_json($resJsonString);

            print main::WRITER 'active_event_update--TYPE--'
              . $mid
              . '--SPLIT--'
              . $eid
              . '--SPLIT--' . 'End'
              . '--SPLIT--' . 'Cause'
              . '--SPLIT--'
              . $alarm->{End}->{Cause}
              . '--JSON--'
              . $resJsonString . "\n";

            print main::WRITER 'event_description--TYPE--'
              . $mid
              . '--SPLIT--'
              . $eid
              . '--SPLIT--'
              . $resTxt . "\n";

            $hookString = $resTxt;
          }

          _tag_detected_objects($eid, $resJsonString, 'event_end') if $hookResult == 0;
        } else {
          main::Info(
            'end hooks/use hooks not being used, going to directly send out a notification if checks pass'
          );
          $hookResult = 0;
        }

        $alarm->{End}->{State} = 'ready';
      }
      }
    }
    elsif ( ($alarm->{End}->{State} // '') eq 'ready' ) {

      my ( $rulesAllowed, $rulesObject ) = isAllowedInRules($alarm);

      my $end_skip = _end_notify_skip_reason(
        $hooks_config{event_end_notify_if_start_success}, $startHookResult, $rulesAllowed);
      if ( $end_skip eq 'start_failed' ) {
        main::Info(
          'Not sending event end alarm, as we did not send a start alarm for this, or start hook processing failed'
        );
      } elsif ( $end_skip eq 'rules' ) {
        main::Debug(1, 'rules: Not processing end notifications as rules checks failed for start notification');
      } else {
        my $temp_alarm_obj = _build_alarm_obj(
          $mname, $mid, $eid, $alarm->{End}->{Cause},
          $alarm->{End}->{DetectionJson}, $rulesObject
        );

        _run_api_push($temp_alarm_obj, $eid, $mid, 'event_end', $hookResult);

        main::Debug(1, 'Matching alarm to connection rules...');
        # Same mute as the start path (shouldSendEventToConn)
        my $muted = $escontrol_config{enabled}
          && getNotificationStatusEsControl($mid) == ESCONTROL_FORCE_MUTE;
        main::Debug(1, "ESCONTROL: Notifications are muted for Monitor:$mname($mid), not sending end notification")
          if $muted;
        foreach ( $muted ? () : @main::active_connections ) {
          if ( isInList( $_->{monlist}, $temp_alarm_obj->{MonitorId} ) ) {
            sendEvent( $temp_alarm_obj, $_, 'event_end', $hookResult );
          } else {
            main::Debug(1, 'Skipping FCM notification as Monitor:'
                . $temp_alarm_obj->{Name} . '('
                . $temp_alarm_obj->{MonitorId}
                . ') is excluded from zmNinjaNG monitor list');
          }
        }
      }

      $alarm->{End}->{State} = 'done';
    }
    elsif ( ($alarm->{End}->{State} // '') eq 'done' ) {
      $doneProcessing = 1;
    }

    if ( !main::zmMemVerify($monitor) ) {
      main::Error('SHM failed, re-validating it');
      if (!main::loadMonitor($monitor)) {
        main::loadMonitors();
      }
    } else {
      my $state   = main::zmGetMonitorState($monitor);
      my $shm_eid = main::zmGetLastEvent($monitor);

      if ( ( $state == main::STATE_IDLE() || $state == main::STATE_TAPE() || $shm_eid != $eid )
        && !$endProcessed ) {
        main::Debug(2, "For $mid ($mname), SHM says: state=$state, eid=$shm_eid");
        main::Info("Event $eid for Monitor $mid has finished");
        $endProcessed = 1;

        $alarm->{End} = {
          State => 'pending',
          Time  => time(),
          # Notes can be NULL; fall back to what the start notification said
          Cause => getNotesFromEventDB($eid) // $alarm->{Start}->{Cause}
        };

        main::Debug(2, 'Event end object is: state=>'
            . $alarm->{End}->{State}
            . ' with cause=>'
            . $alarm->{End}->{Cause});
      }
    }
    sleep(2);
  } # end while loop

  main::Debug(1, 'exiting');
  print main::WRITER 'active_event_delete--TYPE--' . $mid . '--SPLIT--' . $eid . "\n";
  close(main::WRITER);
}

1;
