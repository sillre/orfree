"""Local storage for the user's own OpenRouter API key.

This program ships with **no** API key. The key is typed once in the desktop
window (or with ``python -m orfree set-key``) and lives only on the user's
machine, in ``~/.orfree/config.json``. It is never committed, logged or sent
anywhere except to openrouter.ai as the credential of a normal API request.
"""

from __future__ import annotations

import json
import os
from typing import Any

APP_DIR = os.path.join(os.path.expanduser("~"), ".orfree")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
KEY_FIELD = "openrouter_api_key"


def config_path() -> str:
    """Where the key is kept on this machine."""
    return CONFIG_FILE


def _read() -> dict[str, Any]:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_key() -> str | None:
    """Return the saved key, or None if the user has not set one yet."""
    value = _read().get(KEY_FIELD)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def save_key(key: str) -> None:
    """Store the key in the user's home directory, outside the repository."""
    key = key.strip()
    if not key:
        raise ValueError("API Key 不能为空")
    os.makedirs(APP_DIR, exist_ok=True)
    data = _read()
    data[KEY_FIELD] = key
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)  # best effort; Windows ACLs are user-dependent
    except OSError:
        pass
    os.replace(tmp, CONFIG_FILE)


def clear_key() -> None:
    """Forget the saved key."""
    data = _read()
    if KEY_FIELD not in data:
        return
    data.pop(KEY_FIELD, None)
    with open(CONFIG_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def mask(key: str) -> str:
    """A safe-to-display form of the key."""
    if len(key) <= 12:
        return "*" * len(key)
    return "%s...%s" % (key[:8], key[-4:])
