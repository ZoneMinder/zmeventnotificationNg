# zmeventnotificationNg Project Instructions

Read `AGENTS.md` first. Contracts, project rules, verification, playbooks.
The sanctioned path is the only path; a bypass is a bug even when it works.

Two code bases share this repo: the Event Server (ES), Perl, in
`zmeventnotification.pl` and `ZmEventNotification/`; and the object
detection hook, Python, in `hook/`. Install and migration tools are in
`tools/` and `install.sh`.

## Architecture contracts

### FCM token store
Owns: the FCM token file, shared by the ES parent and its event forks.
Path: `readTokenFile`, `writeTokenFile` (temp file plus rename), and
`_lockTokenFile` around every read-modify-write (`ZmEventNotification/FCM.pm`).
Never: writing the token file with a plain `open`, which a concurrent reader
sees truncated; a read-modify-write without the lock, which loses a fork's
update.
Gate: `t/12-fcm-token-file.t`; the ratchet
holds fcm_token_file_raw_writes; review for the lock.

### Token masking in logs
Owns: how FCM tokens appear in log output.
Path: substr($token, -10) in Perl; `token_suffix` in
`hook/zmes_hook_helpers/push.py`.
Never: a full FCM token in a log line. Logs get attached to public issues.
Gate: the ratchet holds unmasked_token_logs (Perl); review for Python.

### Fork IPC
Owns: every state change an event fork reports to the ES parent.
Path: the fork prints a <job>--TYPE--<field>--SPLIT--<field> line to
`WRITER`; the parent parses with `parse_job_line`
(`ZmEventNotification/Util.pm`) in `processJobs` (`./zmeventnotification.pl`).
Never: a fork changing parent-held state (badge, monthly count, token list,
active events) in its own memory, because the fork holds a copy and the
change is lost (f7360cc, revert 3ea5154); splitting `--TYPE--` outside
`parse_job_line`.
Gate: `t/20-job-pipe-contract.t`; `t/23-process-jobs.t`;
`tools/tests/test_instruction_gate.py` (no --TYPE-- split outside
`ZmEventNotification/Util.pm`).

### Config keys
Owns: every key the ES and the hook read from their YAML configs.
Path: `config_get_val` (`ZmEventNotification/Config.pm`) for the ES;
`config_vals` (`hook/zmes_hook_helpers/common_params.py`) for the hook.
Never: a key read by code but missing from its example config
(zmeventnotification.example.yml, hook/objectconfig.example.yml) or from
docs/guides/config.rst; a flat hook key read from g.config without a
config_vals entry.
Gate: `scripts/gates/config_key_drift.py`; the ratchet holds
config_keys_missing_from_examples; review for undeclared hook keys.

### pyzm interface
Owns: the shape of pyzmNg that the hook consumes (`Detector`, `ZMClient`,
`StreamConfig`, `DetectionResult`, `Zone`).
Path: imports in `hook/zm_detect.py`; the real pyzmNg source at PYZM_SRC
(default ~/fiddle/pyzmNg) is the source of truth.
Never: guessing pyzm behavior instead of reading its source; a test mock of a
pyzm class whose attributes, return shapes, or signatures differ from the
real one; consuming a new pyzm field without adding it to the contract test.
Gate: `hook/tests/test_pyzm_contract.py` against real pyzm (CI sets
ZM_E2E_REQUIRE=1 so it cannot skip); review for mock fidelity.

## Project rules

- Run commands from the repo root. Run `make hooks` once per clone so the
  pre-commit and pre-push gates exist.
- Issues and PRs go to `ZoneMinder/zmeventnotificationNg` (origin), with a
  label. Gate: review.
- Never edit `CHANGELOG.md`; it is generated at release. Gate: the
  `pr-acceptance` CI job.
- Never commit plan files (`PLAN.md`, `*.plan.md`, `docs/plans/`). Gate:
  `tools/tests/test_instruction_gate.py`.
- Every test file is listed in `docs/guides/testing.rst`. Gate: the ratchet
  holds test_files_missing_from_test_map.
- A pyzm version bump updates the pin in `hook/setup.py`. Gate: review.
- Run `make release-gate` before a release. Gate: review.
- Match the style of the file being edited. Gate: review.
- Prose people read (docs, commit bodies, PR and issue bodies, review
  comments) is written with the slop-mop skill; prose reviews run its
  detect mode on the harness's top coding model.
- A `feat` PR links its spec or says in its `## Spec` section why it
  needs none. Gate: the `pr-acceptance` CI job.
- GitHub comments by an agent end with `Posted by <agent>, assisting
  @<login>.` where `<login>` comes from `gh api user --jq .login`. Add
  comments; never edit anyone else's.

## Verification

```
make gate            # perl + hook (not e2e) + tools + instruction gate + ratchet
make release-gate    # gate + real-pyzm e2e with ZM_E2E_REQUIRE=1; needs models
make test-all        # this gate and pyzmNg's gate
```

Per commit, run what the change touches; `make gate` before push or PR.
`make gate` reads pyzmNg from `PYZM_SRC` (default `~/fiddle/pyzmNg`).
Ratchet baselines lower with `sh scripts/gates/ratchet.sh --update`, which
refuses to write a rise; raising one is a hand edit to `.ratchet-baseline`
with a reason in the commit message (C7). CI also runs proven red (P2,
`scripts/gates/proven-red.sh`), the mutation smoke, and `pr-acceptance` (PR
body needs `## Acceptance` content, `## Spec` on a `feat`, and no
`CHANGELOG.md` edit). State completed checks in handoff.

## Playbooks

Read each listed playbook before work in that area.

| Work | Read first |
|---|---|
| Multi-agent or long-running work | `agents/generic/agent-workflows.md` |
| Naming, briefs, docs, proposing work | `agents/project/glossary.md`, `agents/project/out-of-scope.md` |
| Tests | `agents/project/testing.md` |
| Developer or user documentation | `agents/project/documentation.md` |
| ES runtime, FCM, hooks, ZoneMinder quirks | `agents/project/domain-context.md` |

Portable playbooks live in `agents/generic/`; project ones in
`agents/project/`.
