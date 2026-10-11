# Documentation playbook

Read before editing user or developer documentation.

## Scope

- Docs are reStructuredText under `docs/`, built on Read the Docs.
- User-visible behavior changes update the guide that covers them
  (`docs/guides/`). A changed or new config key updates
  `docs/guides/config.rst` and the example config (Config keys contract).
- Hook behavior that comes from pyzmNg is checked against pyzmNg source
  before it is documented.
- `docs/guides/testing.rst` maps every test file to what it covers.
- Never edit `CHANGELOG.md`; it is generated at release.

## Style

- Write and edit with the slop-mop skill. The rules below add what it does
  not cover.
- Write like a developer explaining to a colleague. No headline-style
  headings and no news-article cadence.
- Examples must grep-hit the codebase unless marked simplified. Cite
  symbols, never line numbers.
- Developer docs cite AGENTS.md rule IDs or AGENTS.project.md contract
  names instead of copying process rules.
