#!/usr/bin/env python3
"""Config keys contract (AGENTS.project.md): list keys the code reads that are
missing from their example config or from docs/guides/config.rst.

    python3 scripts/gates/config_key_drift.py           list the gaps
    python3 scripts/gates/config_key_drift.py --count   print the number only

ES keys come from config_get_val() calls in ZmEventNotification/Config.pm;
hook keys from config_vals in hook/zmes_hook_helpers/common_params.py. A key
counts as present in an example config when it appears as `key:`, commented
out or not. Exits 2 when it finds too few keys to have measured anything (M2).
"""
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Legacy alias read only as a fallback for event_start_hook; not offered to users.
ES_ALIASES = {"hook_script"}


def es_keys():
    pm = (REPO / "ZmEventNotification/Config.pm").read_text()
    return {k for k in re.findall(r"config_get_val\(\s*\$\w+\s*,\s*'\w+'\s*,\s*'(\w+)'", pm)} - ES_ALIASES


def hook_keys():
    tree = ast.parse((REPO / "hook/zmes_hook_helpers/common_params.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "config_vals" for t in node.targets):
            return {k.value for k in node.value.keys}
    return set()


def in_yaml(key, text):
    return re.search(rf"^\s*#?\s*{re.escape(key)}\s*:", text, re.M) is not None


def gaps():
    rst = (REPO / "docs/guides/config.rst").read_text()
    out = []
    for keys, example in ((es_keys(), "zmeventnotification.example.yml"),
                          (hook_keys(), "hook/objectconfig.example.yml")):
        if len(keys) < 20:
            print(f"config_key_drift: only {len(keys)} keys read for {example}; the parser is broken", file=sys.stderr)
            sys.exit(2)
        yml = (REPO / example).read_text()
        out += [f"{k}: missing from {example}" for k in sorted(keys) if not in_yaml(k, yml)]
        out += [f"{k}: missing from docs/guides/config.rst" for k in sorted(keys) if not re.search(rf"\b{re.escape(k)}\b", rst)]
    return out


if __name__ == "__main__":
    found = gaps()
    if sys.argv[1:] == ["--count"]:
        print(len(found))
    else:
        print("\n".join(found) or "no config key gaps")
