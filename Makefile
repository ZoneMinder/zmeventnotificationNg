# zmeventnotification (ES) developer test gate.
#
# Run `make hooks` once after cloning to install the pre-push gate.
# `make gate`         -> Tier-1: perl + hook (not e2e) + tools. No models/ZM.
# `make release-gate` -> Tier-1 + real e2e (models + real pyzm), require mode.
#
# ES's hook code leans on the pyzm library, which is a source checkout (not
# pip-installed). PYZM_SRC points the gate at it so the pyzm<->ES contract
# test can import the REAL pyzm. Override if your checkout is elsewhere:
#   make gate PYZM_SRC=/path/to/pyzmNg

PY ?= python3
PYZM_SRC ?= $(HOME)/fiddle/pyzmNg

.PHONY: gate release-gate perl hook tools e2e hooks help test-all test-all-e2e

help:
	@echo "make gate          - pre-push gate (perl + hook + tools)"
	@echo "make release-gate  - full gate incl. real-pyzm e2e (needs models)"
	@echo "make test-all      - run BOTH repos (ES + pyzm), unit/integration"
	@echo "make test-all-e2e  - BOTH repos incl. e2e (needs models + live ZM)"
	@echo "make hooks         - install the git pre-push hook (run once)"

# The pre-push gate. PYZM_SRC on PYTHONPATH lets the pyzm<->ES contract test
# import real pyzm and catch cross-repo drift on every push; the rest of the
# hook suite still runs against the stub in tests/conftest.py.
gate: perl hook tools

# Run BOTH repos' test suites: ES (perl + hook + tools) and pyzm (unit incl.
# local<->remote parity). Override the pyzm checkout with PYZM_SRC=/path.
test-all: gate
	$(MAKE) -C $(PYZM_SRC) gate

# Everything, incl. e2e: ES release-gate (needs models) + pyzm release-gate
# (needs models AND a live ZM). This runs the real-model local<->remote parity.
test-all-e2e: release-gate
	$(MAKE) -C $(PYZM_SRC) release-gate

perl:
	prove -I t/lib -I . -r t/

hook:
	cd hook && PYTHONPATH=$(PYZM_SRC) $(PY) -m pytest tests/ -m "not e2e" -q

tools:
	$(PY) -m pytest tools/tests/ -q

# Pre-release: real e2e with real pyzm + models. ZM_E2E_REQUIRE=1 turns a
# missing prerequisite (models, real pyzm) into a FAILURE, not a silent skip.
release-gate: gate e2e

e2e:
	cd hook && PYTHONPATH=$(PYZM_SRC) ZM_E2E_REQUIRE=1 $(PY) -m pytest tests/test_e2e/ -q

hooks:
	git config core.hooksPath .githooks
	@echo "pre-push gate installed (core.hooksPath -> .githooks)"
