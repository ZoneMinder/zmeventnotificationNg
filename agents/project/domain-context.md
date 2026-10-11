# zmeventnotificationNg domain context

Verified project facts for writing code: API quirks, platform behavior,
and approaches that already failed. Read before working on the subsystem
it covers. New entries arrive through the self-improvement protocol
(AGENTS.md M5) when a session learns a durable fact the hard way. Entries
carry no personal data, hostnames, or addresses. Each entry cites the
commit hash behind it; the instruction gate checks the hash exists.

## Running the ES

- Run the ES and its tools as the ZoneMinder user:
  `sudo -u www-data ./zmeventnotification.pl <options>`. Read the DB,
  configs, and secrets the same way; they are not readable by other users.
- Signal handling in the ES does not survive zmdc, which manages the
  process. A previous attempt was reverted (7d47954).

## ES process model

- The ES forks one child per event. A fork holds a copy of the parent's
  memory, so a counter it increments is lost when it exits. Report the
  change over the job pipe and let the parent apply it. Counting badges in
  the parent without an event id double counted overlapping events and was
  reverted (3ea5154); the version that landed sends the event id and the
  parent counts each event once (f7360cc).
- A fork must release the token-file lock before it writes to the job pipe.
  A full pipe blocks the fork while the parent waits for the lock
  (3f0dc92).
- The token file is read and written by the parent and the forks at once.
  Writes go through a temp file and a rename, under a lock (5440f15). A
  token FCM rejects is removed from the file, from the parent's list, and
  from any websocket connection holding it, or it comes back (e035515,
  37bebb1).
- Right after the ES starts, ZoneMinder shared memory can still point at a
  monitor's previous event. With no processed event id yet, an event whose
  EndDateTime is set is skipped (255cfcf).

## Networking

- The plain (non-SSL) websocket listener binds all IPv4 interfaces and
  ignores network.address. Honoring the address broke proxy setups and was
  reverted (9ba14b1); the ES now warns when an address is set without SSL
  (592b810).

## Perl

- Tests run with warnings on, and an uninitialized value in a log line has
  been fixed three times (703783d, 4a067f8, 5594eb3). Guard every value
  that can be undef before interpolating it into a log string.

## install.sh

- install.sh must work under dash. A bash-only source guard made
  `sh install.sh` exit 0 having done nothing (df9855a).
- Never delete a directory the user named unless it is provably ours. The
  venv setup once wiped any ZM_VENV path that lacked bin/python; it now
  recreates only a directory holding pyvenv.cfg (9f5de8a).
- Files holding secrets are never world-readable: secrets.yml installs at
  mode 640 (e4e61fc), and config upgrade backups keep the source's mode
  without the world bits (7c4bb09).
