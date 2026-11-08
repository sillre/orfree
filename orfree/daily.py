"""The daily job: refresh the catalogue and keep the CC Switch card current.

Run it once a day (Task Scheduler, cron, launchd).  It:

1. fetches the current OpenRouter catalogue and stores a snapshot,
2. probes the free models and remembers which ones actually answer,
3. rewrites the model list of the card this tool manages, adding newly
   appeared free models that work and dropping the ones that stopped.

The card it manages is recorded in ``~/.orfree/state.json``; the card that
was routed before the first run is never touched.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import ccswitch, ccswitch_writer, openrouter, snapshot

STATE_PATH = os.path.join(os.path.expanduser("~"), ".orfree", "state.json")
APP = "codex"


def load_state() -> dict[str, Any]:
    """The tool's own state."""
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    """Store the tool's own state."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    temporary = STATE_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, STATE_PATH)


def managed_provider_id() -> str | None:
    """The card this tool manages, if it has been created yet."""
    return load_state().get("managed_provider_id")


def refresh(
    api_key: str,
    probe: bool = True,
    dry_run: bool = False,
) -> int:
    """Refresh the catalogue and update the managed card."""
    models = openrouter.fetch_models(api_key=api_key, with_endpoints=False)
    snapshot.save_snapshot(
        snapshot.today(), {model.id: snapshot.model_entry(model) for model in models}
    )

    free = [model for model in models if model.free]
    working: list[str] = []
    for model in free:
        if not probe:
            # Without a health check every free model is kept.
            working.append(model.id)
            continue
        result = openrouter.probe_model(model.id, api_key)
        status = "ok" if result.ok else result.reason
        print("  %-4s %-48s %s" % ("OK" if result.ok else "FAIL", model.id, status))
        if result.ok:
            working.append(model.id)

    if probe:
        print("")
        print("%d/%d free models answered." % (len(working), len(free)))

    provider_id = managed_provider_id()
    if not provider_id:
        print("")
        print("No managed card yet. Run 'orfree import --route' to create one.")
        return 0

    row = ccswitch.provider_row(provider_id, APP)
    if not row:
        print("The managed card %s is gone; nothing to update." % provider_id)
        return 0

    specs = row["settings_config"].get("modelCatalog", {}).get("models", [])
    kept = [
        spec for spec in specs if not spec.get("model", "").endswith(":free")
    ]
    catalogue = {model.id: model for model in models}
    for model_id in working:
        model = catalogue.get(model_id)
        if not model:
            continue
        if any(spec.get("model") == model_id for spec in kept):
            continue
        kept.extend(ccswitch_writer.build_model_specs([model]))

    if dry_run:
        print("Dry run: %d models would be on the card." % len(kept))
        return 0

    settings = row["settings_config"]
    settings["modelCatalog"] = {"models": kept}

    # The card's own model and Codex's config must name a model that is
    # actually available, or Codex asks for a model that was just dropped.
    available = [spec["model"] for spec in kept]
    current = ccswitch_writer.live_config_model()
    if current not in available:
        current = next(
            (model for model in available if model.endswith(":free")), available[0]
        )
    settings["config"] = ccswitch_writer.set_config_model(
        settings.get("config", ""), current
    )

    connection = ccswitch.connect()
    try:
        connection.execute(
            "UPDATE providers SET settings_config = ? WHERE id = ? AND app_type = ?",
            (json.dumps(settings, ensure_ascii=False), provider_id, APP),
        )
        connection.commit()
    finally:
        connection.close()

    if ccswitch.routing_for(APP).proxy_route == provider_id:
        ccswitch_writer.write_codex_catalog(kept)
        ccswitch_writer.set_codex_model(current)
    print("Updated %s to %d models." % (provider_id, len(kept)))
    print("Codex now uses %s." % current)
    return 0
