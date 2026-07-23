#!/usr/bin/env perl
# Contract for the inter-process job pipe between forked children (producers:
# FCM/MQTT/HookProcessor) and processJobs (consumer, in zmeventnotification.pl).
# processJobs now parses via ZmEventNotification::Util::parse_job_line, so this
# locks the field order/count each producer emits against what the consumer
# destructures. A drift (renamed job key, reordered fields) fails here.
use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/../";
use lib "$FindBin::Bin/lib";

use Test::More;
require StubZM;

use ZmEventNotification::Util qw(parse_job_line);

# fcm_notification--TYPE--<token>--SPLIT--<badge>--SPLIT--<count>--SPLIT--<at>
{
  my ($job, @f) = parse_job_line(
    'fcm_notification--TYPE--tok123--SPLIT--5--SPLIT--2--SPLIT--2024-01-01T00:00');
  is($job, 'fcm_notification', 'fcm: job key');
  is_deeply(\@f, ['tok123', '5', '2', '2024-01-01T00:00'],
    'fcm: token,badge,count,at in order');
}

# message--TYPE--<id>--SPLIT--<message>
{
  my ($job, @f) = parse_job_line('message--TYPE--conn7--SPLIT--hello world');
  is($job, 'message', 'message: job key');
  is_deeply(\@f, ['conn7', 'hello world'], 'message: id,message');
}

# timestamp--TYPE--<id>--SPLIT--<mid>--SPLIT--<timeval>
{
  my ($job, @f) = parse_job_line('timestamp--TYPE--id9--SPLIT--3--SPLIT--1700000000');
  is($job, 'timestamp', 'timestamp: job key');
  is_deeply(\@f, ['id9', '3', '1700000000'], 'timestamp: id,mid,timeval');
}

# event_description--TYPE--<mid>--SPLIT--<eid>--SPLIT--<desc>
{
  my ($job, @f) = parse_job_line('event_description--TYPE--3--SPLIT--555--SPLIT--detected:person');
  is($job, 'event_description', 'event_description: job key');
  is_deeply(\@f, ['3', '555', 'detected:person'], 'event_description: mid,eid,desc');
}

# active_event_update--TYPE--<mid>--SPLIT--<eid>--SPLIT--<type>--SPLIT--<key>--SPLIT--<val>
{
  my ($job, @f) = parse_job_line(
    'active_event_update--TYPE--3--SPLIT--555--SPLIT--Start--SPLIT--State--SPLIT--pending');
  is($job, 'active_event_update', 'active_event_update: job key');
  is_deeply(\@f, ['3', '555', 'Start', 'State', 'pending'],
    'active_event_update: mid,eid,type,key,val');
}

# active_event_delete--TYPE--<mid>--SPLIT--<eid>
{
  my ($job, @f) = parse_job_line('active_event_delete--TYPE--3--SPLIT--555');
  is($job, 'active_event_delete', 'active_event_delete: job key');
  is_deeply(\@f, ['3', '555'], 'active_event_delete: mid,eid');
}

# mqtt_publish--TYPE--<id>--SPLIT--<topic>--SPLIT--<payload>
{
  my ($job, @f) = parse_job_line('mqtt_publish--TYPE--id9--SPLIT--zm/alarm--SPLIT--{"a":1}');
  is($job, 'mqtt_publish', 'mqtt_publish: job key');
  is_deeply(\@f, ['id9', 'zm/alarm', '{"a":1}'], 'mqtt_publish: id,topic,payload');
}

# update_parallel_hooks--TYPE--add  (no --SPLIT--)
{
  my ($job, @f) = parse_job_line('update_parallel_hooks--TYPE--add');
  is($job, 'update_parallel_hooks', 'update_parallel_hooks: job key');
  is_deeply(\@f, ['add'], 'update_parallel_hooks: single command field');
}

# Edge cases
{
  my ($job, @f) = parse_job_line('');
  is($job, '', 'empty input: empty job');
  is_deeply(\@f, [], 'empty input: no fields');

  my ($job2, @f2) = parse_job_line(undef);
  is($job2, '', 'undef input: empty job');
}

done_testing();
