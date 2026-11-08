"""The generated catalog must match what CC Switch itself writes.

CC Switch wrote the file in the backup folder before this tool's first import.
Regenerating that file from the original provider's model list must reproduce
it byte for byte; if CC Switch changes its format, this test fails loudly
instead of the tool writing a catalogue Codex cannot read.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orfree import catalog  # noqa: E402

CC_SWITCH_HOME = os.path.join(os.path.expanduser("~"), ".cc-switch")
CC_SWITCH_DB = os.path.join(CC_SWITCH_HOME, "cc-switch.db")
BACKUP_ROOT = os.path.join(CC_SWITCH_HOME, "backups")

# The provider CC Switch was routing to before this tool ran, and the
# catalogue CC Switch wrote for it.
ORIGINAL_PROVIDER = "896b19f4-21db-475e-9c56-c9dff9f56fab"


def cc_switch_own_catalog() -> str | None:
    """The catalogue file CC Switch wrote, from the import backup."""
    if not os.path.isdir(BACKUP_ROOT):
        return None
    folders = sorted(
        name
        for name in os.listdir(BACKUP_ROOT)
        if name.startswith("orfree-")
        and os.path.isdir(os.path.join(BACKUP_ROOT, name))
    )
    for name in reversed(folders):
        path = os.path.join(BACKUP_ROOT, name, "cc-switch-model-catalog.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read()
    return None


def main() -> int:
    """Run the fidelity check."""
    if not os.path.exists(CC_SWITCH_DB):
        print("SKIP: no CC Switch database at %s" % CC_SWITCH_DB)
        return 0

    reference = cc_switch_own_catalog()
    if reference is None:
        print("SKIP: no CC Switch-written catalogue to compare against")
        return 0

    connection = sqlite3.connect("file:%s?mode=ro" % CC_SWITCH_DB, uri=True)
    row = connection.execute(
        "SELECT settings_config FROM providers WHERE id = ? AND app_type = 'codex'",
        (ORIGINAL_PROVIDER,),
    ).fetchone()
    if not row:
        print("SKIP: the original provider row is gone")
        return 0
    specs = json.loads(row[0]).get("modelCatalog", {}).get("models", [])
    if not specs:
        print("SKIP: the original provider has no model list")
        return 0

    generated = catalog.dump_provider_catalog(specs)
    if generated == reference:
        print("PASS: the generated catalogue is byte-identical to CC Switch's own")
        return 0

    print("FAIL: the generated catalogue differs from CC Switch's own")
    import difflib

    diff = difflib.unified_diff(
        reference.splitlines(),
        generated.splitlines(),
        "cc-switch",
        "generated",
        lineterm="",
        n=1,
    )
    for index, line in enumerate(diff):
        print(line)
        if index > 60:
            print("... truncated")
            break
    return 1


if __name__ == "__main__":
    sys.exit(main())
