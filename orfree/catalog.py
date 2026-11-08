"""Model-catalog generation, byte-compatible with CC Switch v4.0.17.

CC Switch writes ``~/.codex/cc-switch-model-catalog.json`` from the routed
provider's ``modelCatalog`` so Codex's picker can list them.  This module
reproduces that file exactly (verified byte-for-byte against the live file),
which is what lets a new provider take over routing without hand-editing
Codex's configuration.

Ported from CC Switch (MIT licensed) ``src-tauri/src/codex_config.rs``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Iterable

# Codex reasoning efforts it understands, with the exact descriptions CC Switch
# uses (they come from the bundled gpt5_5 template).
CANONICAL_EFFORTS: list[tuple[str, str]] = [
    ("none", "Disable Thinking"),
    ("minimal", "Minimal reasoning"),
    ("low", "Fast responses with lighter reasoning"),
    ("medium", "Balances speed and reasoning depth for everyday tasks"),
    ("high", "Greater reasoning depth for complex problems"),
    ("xhigh", "Extra high reasoning depth for complex problems"),
    ("max", "Maximum reasoning depth for the hardest problems"),
    ("ultra", "Ultra reasoning depth"),
]

_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resources", "gpt5_5_template.json"
)


def load_template() -> dict[str, Any]:
    """The bundled classic-tool template every third-party row is cloned from."""
    for path in _template_candidates():
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
    raise FileNotFoundError(
        "the model template is missing: %s" % _TEMPLATE_PATH
    )


def _template_candidates() -> list[str]:
    """Where the template may live, including inside a packaged app."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = getattr(sys, "_MEIPASS", "")
    candidates = [os.path.join(here, "resources", "gpt5_5_template.json")]
    if root:
        candidates.append(
            os.path.join(root, "orfree", "resources", "gpt5_5_template.json")
        )
        candidates.append(os.path.join(root, "resources", "gpt5_5_template.json"))
    return candidates


def _canonical_efforts(levels: Iterable[str]) -> list[str]:
    wanted = set(levels)
    return [effort for effort, _ in CANONICAL_EFFORTS if effort in wanted]


def _apply_reasoning_levels(entry: dict[str, Any], levels: list[str] | None) -> None:
    """Mirror CC Switch's ``apply_codex_reasoning_level_override``."""
    if not levels:
        return
    canonical = _canonical_efforts(levels)
    if not canonical:
        return
    descriptions = dict(CANONICAL_EFFORTS)
    entry["supported_reasoning_levels"] = [
        {"effort": effort, "description": descriptions[effort]} for effort in canonical
    ]
    # The template's default is "medium"; fall back to the highest supported
    # level when "medium" is not among them.
    entry["default_reasoning_level"] = (
        "medium" if "medium" in canonical else canonical[-1]
    )


def _find_official_model(model: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match a model against Codex's own list, by prefix or by namespace.

    An OpenRouter id such as ``openai/gpt-6-luna`` should use Codex's
    ``gpt-6-luna`` row so the model keeps its official tool setup.
    """

    def longest_prefix(text: str) -> dict[str, Any] | None:
        best: tuple[int, dict[str, Any]] | None = None
        for candidate in candidates:
            slug = candidate.get("slug")
            if not slug or not text.startswith(slug):
                continue
            if best is None or len(slug) > best[0]:
                best = (len(slug), candidate)
        return best[1] if best else None

    found = longest_prefix(model)
    if found:
        return found

    namespace, separator, suffix = model.partition("/")
    if not separator or not suffix or "/" in suffix:
        return None
    if not namespace or not all(c.isalnum() or c in "_-" for c in namespace):
        return None
    return longest_prefix(suffix)


def _build_official_entry(
    official: dict[str, Any],
    model: str,
    index: int,
    context_window: int | None,
) -> dict[str, Any]:
    """Build a catalog row from Codex's own model entry."""
    entry = json.loads(json.dumps(official))
    template = load_template()
    for key in PARSER_REQUIRED_FIELDS:
        if key not in entry and key in template:
            entry[key] = template[key]
    if not isinstance(entry.get("base_instructions"), str):
        instructions = (entry.get("model_messages") or {}).get("instructions_template")
        if isinstance(instructions, str):
            entry["base_instructions"] = instructions
    if entry.get("slug") != model:
        entry["display_name"] = model
    entry["slug"] = model
    entry["priority"] = 1000 + index
    if context_window:
        entry["context_window"] = context_window
        entry["max_context_window"] = context_window
    entry["visibility"] = "list"
    entry["service_tiers"] = []
    entry["additional_speed_tiers"] = []
    entry["availability_nux"] = None
    entry["upgrade"] = None
    entry["use_responses_lite"] = False
    # Chat gateways reject the "original" image detail.
    entry["supports_image_detail_original"] = False
    return entry


def _input_modalities(spec: dict[str, Any]) -> list[str]:
    """Mirror CC Switch's ``codex_catalog_input_modalities``."""
    declared = spec.get("inputModalities") or ["text", "image"]
    supports_image = any(str(item).strip().lower() == "image" for item in declared)
    return ["text", "image"] if supports_image else ["text"]


PARSER_REQUIRED_FIELDS = ("supports_reasoning_summaries", "supports_parallel_tool_calls")


def load_official_models() -> list[dict[str, Any]]:
    """Codex's own model list, used for models whose id matches a Codex slug.

    ``codex debug models --bundled`` is the source CC Switch reads; the
    ``models_cache.json`` next to the Codex config is the fallback.
    """
    import subprocess

    codex_dir = os.environ.get(
        "ORFREE_CODEX_DIR", os.path.join(os.path.expanduser("~"), ".codex")
    )

    candidates = []
    for name in ("codex.exe", "codex"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    for candidate in candidates:
        try:
            output = subprocess.run(
                [candidate, "debug", "models", "--bundled"],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if output.returncode != 0:
            continue
        try:
            payload = json.loads(output.stdout.decode("utf-8"))
        except ValueError:
            continue
        models = payload.get("models")
        if isinstance(models, list) and models:
            return [row for row in models if isinstance(row, dict)]

    cache = os.path.join(codex_dir, "models_cache.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as handle:
                models = json.load(handle).get("models", [])
            return [row for row in models if isinstance(row, dict)]
        except (OSError, ValueError):
            return []
    return []


def build_entry(
    template: dict[str, Any],
    spec: dict[str, Any],
    index: int,
    default_context_window: int = 128_000,
    official: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one catalog row for a model spec."""
    if official:
        match = _find_official_model(spec["model"], official)
        if match:
            window = spec.get("contextWindow") or default_context_window
            return _build_official_entry(match, spec["model"], index, window)

    entry = json.loads(json.dumps(template))

    display_name = spec.get("displayName") or spec["model"]
    context_window = spec.get("contextWindow") or default_context_window

    entry["slug"] = spec["model"]
    entry["visibility"] = "list"
    entry["display_name"] = display_name
    entry["description"] = display_name
    entry["context_window"] = context_window
    entry["max_context_window"] = context_window
    entry["priority"] = 1000 + index
    entry["additional_speed_tiers"] = []
    entry["service_tiers"] = []
    entry["availability_nux"] = None
    entry["upgrade"] = None
    entry["input_modalities"] = _input_modalities(spec)
    # CC Switch forbids the "original" image detail on Chat gateways.
    entry["supports_image_detail_original"] = False
    # Third-party rows get a compaction threshold at 90% of the window.
    entry["auto_compact_token_limit"] = (context_window * 9) // 10

    _apply_reasoning_levels(entry, spec.get("reasoningLevels"))
    return entry


def build_catalog(
    specs: list[dict[str, Any]],
    default_context_window: int = 128_000,
    official: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the full catalog document for a provider's model list."""
    template = load_template()
    return {
        "models": [
            build_entry(template, spec, index, default_context_window, official)
            for index, spec in enumerate(specs)
        ]
    }


def dump_catalog(catalog: dict[str, Any]) -> str:
    """Serialize exactly the way CC Switch writes the file.

    ``serde_json`` stores maps sorted by key, and the live catalog file is
    pretty-printed with two spaces and no trailing newline.
    """
    return json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True)


def dump_provider_catalog(specs: list[dict[str, Any]]) -> str:
    """The catalog file for a provider's models, as CC Switch would write it."""
    return dump_catalog(build_catalog(specs, official=load_official_models()))
