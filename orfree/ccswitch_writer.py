"""Creating a provider card and routing Codex to it."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from . import catalog, ccswitch


@dataclass
class ImportResult:
    """What an import produced."""

    provider_id: str
    backup_folder: str


def build_model_specs(
    models: list[Any],
    default_context_window: int = 128_000,
) -> list[dict[str, Any]]:
    """Turn fetched models into CC Switch ``modelCatalog`` specs."""
    specs: list[dict[str, Any]] = []
    for model in models:
        # Codex only knows text and image; drop everything else (OpenRouter
        # also advertises video for some models).
        modalities = ["text"]
        if "image" in getattr(model, "input_modalities", []):
            modalities.append("image")
        display = getattr(model, "display_name", "") or model.id
        spec: dict[str, Any] = {
            "model": model.id,
            "displayName": display,
            "contextWindow": getattr(model, "context_length", 0) or default_context_window,
            "inputModalities": modalities,
        }
        if getattr(model, "supports_reasoning", False):
            spec["reasoningLevels"] = ["low", "medium", "high", "xhigh", "max"]
        specs.append(spec)
    return specs


def current_routed_specs(app: str = "codex") -> list[dict[str, Any]]:
    """The model list of the provider Codex is routed to right now."""
    route = ccswitch.routing_for(app).proxy_route
    if not route:
        return []
    row = ccswitch.provider_row(route, app)
    if not row:
        return []
    return row["settings_config"].get("modelCatalog", {}).get("models", [])


def merge_specs(
    base: list[dict[str, Any]],
    extra: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine two model lists, keeping the first occurrence of each id."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in list(base) + list(extra):
        model_id = spec.get("model")
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        merged.append(spec)
    return merged


def provider_config_toml(
    model: str,
    endpoint: str,
    reasoning_effort: str = "high",
) -> str:
    """The Codex ``config.toml`` text stored inside a provider row.

    CC Switch projects this into the live ``config.toml`` when the provider is
    routed, so it must be a complete, valid Codex configuration.
    """
    lines = [
        'model_provider = "custom"',
        'model = "%s"' % model,
        'model_reasoning_effort = "%s"' % reasoning_effort,
        "disable_response_storage = true",
        "",
        "[model_providers.custom]",
        'name = "Custom"',
        'base_url = "%s"' % endpoint,
        'wire_api = "responses"',
        "requires_openai_auth = true",
    ]
    return "\n".join(lines) + "\n"


def create_provider(
    name: str,
    models: list[Any],
    api_key: str,
    endpoint: str = ccswitch.OPENROUTER_ENDPOINT,
    icon: str = "openrouter",
    icon_color: str = "#6566F1",
    website_url: str = "https://openrouter.ai",
    notes: str | None = None,
    app: str = "codex",
    backup: bool = True,
    include_current: bool = True,
    default_model: str | None = None,
) -> ImportResult:
    """Add a new provider row and return its id.

    The row is written the way CC Switch's own import writes it, so the app
    treats it as an ordinary provider.  A backup of the files this changes is
    taken first unless ``backup`` is false.
    """
    specs = build_model_specs(models)
    if include_current:
        # Keep the models that work today, so switching to the new card does
        # not take the current model away mid-session.
        specs = merge_specs(current_routed_specs(app), specs)
    if not specs:
        raise ccswitch.CcSwitchError("no models selected")
    # Default to the model Codex uses right now, so switching the route does
    # not change what the current session is talking to.
    available = [spec["model"] for spec in specs]
    default = default_model or live_config_model()
    if default not in available:
        # The configured model is not on this card; naming it would send
        # Codex to a model the card does not publish.
        default = next(
            (model for model in available if model.endswith(":free")), available[0]
        )

    settings_config = {
        "auth": {"OPENAI_API_KEY": api_key},
        "config": provider_config_toml(default, endpoint),
        "modelCatalog": {"models": specs},
    }
    meta = {
        "commonConfigEnabled": False,
        "endpointAutoSelect": True,
        # OpenRouter speaks Chat Completions, so Codex's Responses requests are
        # converted on the way out.  This matches the working OpenRouter card
        # exactly, and it is also what selects the classic catalog template.
        "apiFormat": "openai_chat",
        "usage_script": {
            "enabled": True,
            "language": "javascript",
            "code": "",
            "timeout": 10,
            "templateType": "balance",
            "autoQueryInterval": 5,
        },
    }

    provider_id = str(uuid.uuid4())
    created_at = int(time.time() * 1000)
    sort_index = ccswitch.next_sort_index(app)

    backup_folder = ccswitch.backup_state("before-import") if backup else ""

    connection = ccswitch.connect()
    try:
        connection.execute(
            "INSERT INTO providers ("
            " id, app_type, name, settings_config, website_url, category,"
            " created_at, sort_index, notes, icon, icon_color, meta,"
            " is_current, in_failover_queue"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (
                provider_id,
                app,
                name,
                json.dumps(settings_config, ensure_ascii=False),
                website_url,
                "aggregator",
                created_at,
                sort_index,
                notes,
                icon,
                icon_color,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        connection.execute(
            "INSERT INTO provider_endpoints (provider_id, app_type, url, added_at)"
            " VALUES (?, ?, ?, ?)",
            (provider_id, app, endpoint, created_at),
        )
        connection.commit()
    finally:
        connection.close()
    return ImportResult(provider_id=provider_id, backup_folder=backup_folder)


def delete_provider(provider_id: str, app: str = "codex") -> None:
    """Remove a provider card created by this tool."""
    connection = ccswitch.connect()
    try:
        connection.execute(
            "DELETE FROM provider_endpoints WHERE provider_id = ? AND app_type = ?",
            (provider_id, app),
        )
        connection.execute(
            "DELETE FROM providers WHERE id = ? AND app_type = ?", (provider_id, app)
        )
        connection.commit()
    finally:
        connection.close()


def set_route(provider_id: str, app: str = "codex") -> None:
    """Point an app's routing at a provider."""
    state = ccswitch.live_state()
    entry = state.setdefault("apps", {}).setdefault(app, {})
    entry["proxy_route"] = provider_id
    entry["attached"] = True
    entry["mode"] = "proxy"
    ccswitch.write_json_atomic(ccswitch.LIVE_STATE, state)


def write_codex_catalog(specs: list[dict[str, Any]]) -> None:
    """Write the Codex model catalog CC Switch would write for these specs."""
    document = catalog.dump_provider_catalog(specs)
    os.makedirs(ccswitch.CODEX_DIR, exist_ok=True)
    temporary = ccswitch.CODEX_CATALOG + ".orfree.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(document)
    os.replace(temporary, ccswitch.CODEX_CATALOG)


def set_codex_model(model: str) -> None:
    """Point Codex's live configuration at one model of the routed card."""
    if not os.path.exists(ccswitch.CODEX_CONFIG):
        return
    with open(ccswitch.CODEX_CONFIG, encoding="utf-8") as handle:
        text = handle.read()
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            # Past this point we are inside a table, where a "model" key
            # belongs to that table, not to the top level.
            break
        stripped = line.strip()
        if stripped.startswith("model ") and "=" in stripped:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = 'model = "%s"%s' % (model, newline)
            break
    else:
        lines.insert(0, 'model = "%s"\n' % model)
    temporary = ccswitch.CODEX_CONFIG + ".orfree.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    os.replace(temporary, ccswitch.CODEX_CONFIG)


def route_to(provider_id: str, app: str = "codex") -> None:
    """Route an app to a provider and publish its model list to Codex."""
    row = ccswitch.provider_row(provider_id, app)
    if not row:
        raise ccswitch.CcSwitchError("provider %s not found" % provider_id)
    specs = row["settings_config"].get("modelCatalog", {}).get("models", [])
    set_route(provider_id, app)
    if app == "codex" and specs:
        write_codex_catalog(specs)
        default_model = _config_model(row["settings_config"].get("config", ""))
        set_codex_model(default_model or specs[0]["model"])


def _config_model(config_text: str) -> str:
    """The ``model`` declared in a Codex config text."""
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("model ") and "=" in stripped:
            _, _, value = stripped.partition("=")
            return value.strip().strip('"')
    return ""


def set_config_model(config_text: str, model: str) -> str:
    """Return the config text with its top-level ``model`` set."""
    lines = config_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            break
        stripped = line.strip()
        if stripped.startswith("model ") and "=" in stripped:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = 'model = "%s"%s' % (model, newline)
            return "".join(lines)
    return 'model = "%s"\n%s' % (model, config_text)


def live_config_model() -> str:
    """The model Codex is configured to use right now."""
    if not os.path.exists(ccswitch.CODEX_CONFIG):
        return ""
    try:
        with open(ccswitch.CODEX_CONFIG, encoding="utf-8") as handle:
            return _config_model(handle.read())
    except OSError:
        return ""
