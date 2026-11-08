"""Reading and writing a running CC Switch installation.

CC Switch keeps its state in a SQLite database plus a ``live-state.json``
file holding the routing pointers.  Both are read on every request, so a
change lands without restarting the app.

The provider that is currently routed is never modified by this module; a new
card is created alongside it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from . import catalog

# Both roots can be redirected, which keeps the test-suite away from a real
# CC Switch installation.
CC_SWITCH_HOME = os.environ.get(
    "ORFREE_CC_SWITCH_HOME",
    os.path.join(os.path.expanduser("~"), ".cc-switch"),
)
CC_SWITCH_DB = os.path.join(CC_SWITCH_HOME, "cc-switch.db")
LIVE_STATE = os.path.join(CC_SWITCH_HOME, "live-state.json")

CODEX_DIR = os.environ.get(
    "ORFREE_CODEX_DIR", os.path.join(os.path.expanduser("~"), ".codex")
)
CODEX_CONFIG = os.path.join(CODEX_DIR, "config.toml")
CODEX_CATALOG = os.path.join(CODEX_DIR, "cc-switch-model-catalog.json")

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1"
PROXY_PORT = 15721


class CcSwitchError(RuntimeError):
    """Raised when CC Switch cannot be read or updated safely."""


@dataclass
class RoutingState:
    """The routing pointers for one app."""

    mode: str | None
    attached: bool
    proxy_route: str | None


def read_json(path: str) -> dict[str, Any]:
    """Parse a JSON file."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    """Write JSON via a temporary file and a rename."""
    temporary = path + ".orfree.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def live_state() -> dict[str, Any]:
    """The parsed ``live-state.json``."""
    if not os.path.exists(LIVE_STATE):
        raise CcSwitchError("CC Switch state file not found: %s" % LIVE_STATE)
    return read_json(LIVE_STATE)


def routing_for(app: str) -> RoutingState:
    """Routing pointers for an app."""
    entry = live_state().get("apps", {}).get(app, {})
    return RoutingState(
        mode=entry.get("mode"),
        attached=bool(entry.get("attached")),
        proxy_route=entry.get("proxy_route"),
    )


def connect() -> sqlite3.Connection:
    """Open CC Switch's database for writing."""
    if not os.path.exists(CC_SWITCH_DB):
        raise CcSwitchError("CC Switch database not found: %s" % CC_SWITCH_DB)
    connection = sqlite3.connect(CC_SWITCH_DB, timeout=15)
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def provider_row(provider_id: str, app: str = "codex") -> dict[str, Any] | None:
    """One provider row, or ``None`` when it does not exist."""
    connection = connect()
    try:
        row = connection.execute(
            "SELECT id, name, settings_config, meta, is_current, in_failover_queue"
            " FROM providers WHERE id = ? AND app_type = ?",
            (provider_id, app),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "settings_config": json.loads(row[2]),
        "meta": json.loads(row[3] or "{}"),
        "is_current": bool(row[4]),
        "in_failover_queue": bool(row[5]),
    }


def list_providers(app: str = "codex") -> list[dict[str, Any]]:
    """All providers for an app, in CC Switch's own display order."""
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT id, name, settings_config, meta, is_current, in_failover_queue"
            " FROM providers WHERE app_type = ?"
            " ORDER BY COALESCE(sort_index, 999999), created_at ASC, id ASC",
            (app,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row[0],
            "name": row[1],
            "settings_config": json.loads(row[2]),
            "meta": json.loads(row[3] or "{}"),
            "is_current": bool(row[4]),
            "in_failover_queue": bool(row[5]),
        }
        for row in rows
    ]


def next_sort_index(app: str = "codex") -> int:
    """The sort index a new provider should take."""
    connection = connect()
    try:
        row = connection.execute(
            "SELECT MAX(sort_index) FROM providers WHERE app_type = ?", (app,)
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) + 1


def backup_state(tag: str = "") -> str:
    """Copy every file this tool may modify into a timestamped folder."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = ("-" + tag) if tag else ""
    folder = os.path.join(CC_SWITCH_HOME, "backups", "orfree-%s%s" % (stamp, suffix))
    os.makedirs(folder, exist_ok=True)
    for source in (CC_SWITCH_DB, LIVE_STATE, CODEX_CONFIG, CODEX_CATALOG):
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(folder, os.path.basename(source)))
    return folder


def restore_backup(folder: str) -> list[str]:
    """Restore the files saved in a backup folder."""
    if not folder:
        raise CcSwitchError("no backup folder given")
    if not os.path.isdir(folder):
        raise CcSwitchError("not a backup folder: %s" % folder)
    restored = []
    mapping = {
        "cc-switch.db": CC_SWITCH_DB,
        "live-state.json": LIVE_STATE,
        "config.toml": CODEX_CONFIG,
        "cc-switch-model-catalog.json": CODEX_CATALOG,
    }
    for name, destination in mapping.items():
        source = os.path.join(folder, name)
        if os.path.exists(source):
            shutil.copy2(source, destination)
            restored.append(destination)
    return restored


def latest_backup() -> str | None:
    """The most recent backup folder, if any."""
    root = os.path.join(CC_SWITCH_HOME, "backups")
    if not os.path.isdir(root):
        return None
    folders = [
        os.path.join(root, name)
        for name in os.listdir(root)
        if name.startswith("orfree-") and os.path.isdir(os.path.join(root, name))
    ]
    return max(folders) if folders else None


def proxy_health() -> bool:
    """Whether CC Switch's local proxy is answering."""
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%d/health" % PROXY_PORT, method="GET"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False
