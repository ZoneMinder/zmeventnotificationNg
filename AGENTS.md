# Development Guidelines

Read this file before work. It owns the process rules. `CLAUDE.md` holds the
project overview and pointers; where both speak, follow the stricter rule.

| Work | Read first |
|---|---|
| Any code, test, or config change | This file, then run the test gate below |
| Hook / ML detection (`hook/`) | pyzmNg source at `~/fiddle/pyzmNg` is the source of truth |
| Config keys | The config-key checklist below |
| Docs | `docs/guides/testing.rst` for the test map |

## Core rules

1. Write plain, factual prose. No marketing claims, filler, or recap sections.
2. Create or use a GitHub issue before feature or bug work, on
   `ZoneMinder/zmeventnotificationNg` (origin). Label it. A user instruction to
   use an existing issue overrides creating one.
3. **Test first.** Before any commit, run the full gate (see Verification) and
   confirm it is green. Never commit after a failed or unrun gate.
4. Every new feature or bugfix ships with a test that fails before the change
   and passes after. If you cannot write one, say why in the PR.
5. `zm_detect.py` and its helpers lean on pyzmNg. Validate what is true by
   reading pyzmNg (`~/fiddle/pyzmNg`), not by guessing. Mocks in tests must
   match the real pyzmNg interface.
6. Follow DRY. Write simple code. Match the style of the file you are editing.
7. Use conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`,
   `test:`. Scope optional (`feat(hook):`). One logical change per commit.
   Reference the issue in the body (`refs #<id>`); close only after the user
   confirms.
8. Never edit `CHANGELOG.md`. It is auto-generated.
9. Do not commit plan files (`PLAN.md`, `*.plan.md`, implementation plans).
   They are temporary; delete them when the task is done.
10. When responding to issues or PRs from others, add comments, never overwrite
    anyone's (including an AI agent's). Identify yourself as Claude assisting
    @pliablepixels.
11. Run Event Server commands as the ZM user: `sudo -u www-data ./zmeventnotification.pl <options>`.
    Access DB/configs/secrets as `sudo -u www-data`.
12. Read failures. Fix the cause. Do not blindly retry or weaken a test to make
    it pass.

## Verification (the test gate)

Run all three tiers before committing. All must pass.

```bash
# Perl (Event Server: zmeventnotification.pl + ZmEventNotification/*.pm)
prove -I t/lib -I . -r t/

# Python unit/integration (hook + tools), no models needed
cd hook && python3 -m pytest tests/ -m "not e2e" -q && cd ..
python3 -m pytest tools/tests/ -q

# Python e2e (real pyzmNg + YOLO models). Use PYTHONPATH if pyzmNg is on a
# non-standard path. In CI, add ZM_E2E_REQUIRE=1 so missing prereqs FAIL
# instead of silently skipping.
cd hook && python3 -m pytest tests/test_e2e/ -q && cd ..
```

Verification runs the real commands and reads the real output. Do not claim
green from memory. State which tiers you ran in the handoff.

The test suite is a regression net: a green gate should mean a new change broke
nothing that worked. Keep it that way. Do not add tautological tests (assertions
that pass regardless of production correctness) or tests that re-implement the
logic they claim to check. A useful test fails when the code it covers breaks.

`docs/guides/testing.rst` maps every test file to what it covers. Update it when
you add or repurpose a test file.

## Config-key checklist

When adding, removing, or changing ANY config key, update all of:

- `docs/guides/config.rst` (the "Complete Hook Config Reference" table)
- `hook/objectconfig.example.yml`
- `hook/zmes_hook_helpers/common_params.py` (for flat keys)
- Any code examples in `docs/guides/hooks.rst` referencing the key
- pyzmNg docs if the key is consumed by pyzmNg
- A test asserting the new behavior
