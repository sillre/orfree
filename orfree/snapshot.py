"""Daily snapshots of the OpenRouter catalogue.

Each run stores the model ids it saw, so the next run can tell which
models are new since yesterday.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

DATA_DIR = os.path.join(os.path.expanduser("~"), ".orfree")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")


def snapshot_path(day: str) -> str:
    """Where the snapshot for a given day lives."""
    return os.path.join(SNAPSHOT_DIR, "%s.json" % day)


def today() -> str:
    """Today's date stamp."""
    return time.strftime("%Y-%m-%d")


def previous_day(day: str) -> str:
    """The day before ``day``."""
    import datetime

    stamp = datetime.datetime.strptime(day, "%Y-%m-%d")
    return (stamp - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def load_snapshot(day: str) -> dict[str, Any]:
    """A stored snapshot, or an empty one when it does not exist."""
    path = snapshot_path(day)
    if not os.path.exists(path):
        return {"day": day, "models": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"day": day, "models": {}}


def save_snapshot(day: str, models: dict[str, Any]) -> str:
    """Store a snapshot and return its path."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = snapshot_path(day)
    payload = {"day": day, "saved_at": time.time(), "models": models}
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, path)
    return path


def model_entry(model: Any) -> dict[str, Any]:
    """The snapshot fields worth keeping for one model."""
    return {
        "name": model.display_name,
        "free": model.free,
        "context_length": model.context_length,
        "prompt_price": model.prompt_price,
        "completion_price": model.completion_price,
        "providers": model.provider_names,
    }


def new_since(catalogue: list[Any], day: str | None = None) -> set[str]:
    """Model ids that were not in the previous day's snapshot."""
    day = day or today()
    previous = load_snapshot(previous_day(day)).get("models", {})
    current = {model.id for model in catalogue}
    return {model_id for model_id in current if model_id not in previous}


def just_added(catalogue: list[Any], day: str | None = None) -> set[str]:
    """Model ids that appeared in today's snapshot but not the previous one.

    With no previous snapshot there is nothing to compare against, so nothing
    counts as newly added.
    """
    day = day or today()
    previous = load_snapshot(previous_day(day)).get("models")
    if not previous:
        return set()
    current = {model.id for model in catalogue}
    return current - set(previous)


def gone_since(catalogue: list[Any], day: str | None = None) -> set[str]:
    """Model ids present yesterday but missing today."""
    day = day or today()
    previous = set(load_snapshot(previous_day(day)).get("models") or {})
    if not previous:
        return set()
    current = {model.id for model in catalogue}
    return previous - current
