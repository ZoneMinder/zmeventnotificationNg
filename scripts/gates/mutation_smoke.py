#!/usr/bin/env python3
"""Mutation smoke (AGENTS.md P2, M2): break one line in each risky module and
require that module's tests to fail. Proves the existing tests can catch a
wrong value, which proven red cannot show for tests that were already there.

    python3 scripts/gates/mutation_smoke.py

Each target replaces one exact line fragment, runs its tests, and restores the
file whether or not they failed. Exit 0 every mutant was caught, 1 a mutant
survived, 2 a target no longer matches the source (update the target).
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYZM_SRC = os.environ.get("PYZM_SRC", str(Path.home() / "fiddle/pyzmNg"))
PROVE = ["prove", "-I", "t/lib", "-I", "."]

# (file, from, to, test command, cwd relative to the repo)
TARGETS = [
    # FCM token store: the rewrite drops the file's mode
    ("ZmEventNotification/FCM.pm", "chmod( $st[2] & 07777, $tmp )", "chmod( 0600, $tmp )",
     PROVE + ["t/12-fcm-token-file.t"], "."),
    # Fork IPC: the parent misreads the job type
    ("ZmEventNotification/Util.pm", "split('--TYPE--', $txt)", "split('--SPLIT--', $txt)",
     PROVE + ["t/20-job-pipe-contract.t"], "."),
    # Config keys: a !secret reference resolves to its own name
    ("ZmEventNotification/Config.pm", "$final_val = $secret_val;", "$final_val = $token;",
     PROVE + ["t/01-config-get-val.t"], "."),
    # Hook secrets: the same, on the Python side
    ("hook/zmes_hook_helpers/utils.py", "return secrets_dict[token]", "return val",
     [sys.executable, "-m", "pytest", "-q", "-x", "tests/test_utils_config.py"], "hook"),
]


def main():
    survived = []
    for rel, old, new, cmd, cwd in TARGETS:
        path = REPO / rel
        src = path.read_text()
        if src.count(old) != 1:
            print(f"mutation-smoke: {rel}: '{old}' found {src.count(old)} times, expected 1; update the target")
            return 2
        path.write_text(src.replace(old, new))
        try:
            env = dict(os.environ, PYTHONPATH=PYZM_SRC)
            r = subprocess.run(cmd, cwd=REPO / cwd, env=env, capture_output=True, text=True)
        finally:
            path.write_text(src)
        if r.returncode == 0:
            survived.append(f"{rel}: '{old}' -> '{new}'")
            print(f"SURVIVED {rel}: tests still pass")
        else:
            print(f"caught   {rel}")
    if survived:
        print("mutation-smoke: these mutants survived; the tests cannot catch them:\n  " + "\n  ".join(survived))
        return 1
    print(f"mutation-smoke: {len(TARGETS)} of {len(TARGETS)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
