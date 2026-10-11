# Testing playbook

Read before writing or changing tests.

## Test design

- Test outcomes: changed data, the payload sent, the file written, the log
  line emitted, errors, and edge cases. Never existence or count alone (C6).
- Name the seam under test in the issue or brief before writing a test: the
  highest interface that reaches the behavior. Mock only the system
  boundary: ZoneMinder (`t/lib/StubZM.pm` for Perl), the network, and
  pyzm in hook tests (`hook/tests/conftest.py`). A pyzm mock must match the
  real class (pyzm interface contract).
- Prove red before green: run the new test against the pre-change code and
  show it fail (`sh scripts/gates/proven-red.sh <base> <head>`; CI runs it
  on every push and PR). A bug fix starts with that red, shown, before any
  code is read for a theory.
- A new gate assertion (anything under `scripts/gates/` or
  `tools/tests/test_instruction_gate.py`) is proven red against a scratch
  violation, then the scratch is removed in the same commit.
- Fork and pipe behavior is tested through the job line format
  (`t/20-job-pipe-contract.t`) and the parent's handling of it
  (`t/23-process-jobs.t`), not by forking.
- A new test file is added to `docs/guides/testing.rst` in the same commit.
- No fixed sleeps. Wait on a condition.

## Commands

```bash
make gate                                   # Tier-1: perl + hook + tools + instruction gate + ratchet
prove -I t/lib -I . t/12-fcm-token-file.t   # one Perl file
cd hook && PYTHONPATH=~/fiddle/pyzmNg python3 -m pytest tests/test_push.py -q
make release-gate                           # adds real-pyzm e2e; needs models
```

`make release-gate` sets ZM_E2E_REQUIRE=1, so a missing model or an
unimportable pyzm fails instead of skipping. Without it, the pyzm contract
test and the e2e tests skip silently when pyzm is not importable.

## Traps that have burned agents

- The date_locale test matched /^[A-Za-z]+$/, which English month names
  also match, so it passed with the override ignored. It now checks the
  Italian month name (5669636). Assert the value only the fix produces.
