"""OpenRouter model catalogue: fetching, pricing and reachability probes."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

MODELS_URL = "https://openrouter.ai/api/v1/models"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
KEY_URL = "https://openrouter.ai/api/v1/key"

USER_AGENT = "orfree/1.0 (CC Switch model helper)"


def _get_json(url: str, api_key: str | None = None, timeout: int = 30) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: int = 45,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_free(model_id: str) -> bool:
    """OpenRouter marks its free tier with a ``:free`` suffix."""
    return model_id.endswith(":free")


@dataclass
class ModelInfo:
    """One model as OpenRouter describes it."""

    id: str
    name: str
    created: int
    context_length: int
    prompt_price: float
    completion_price: float
    input_modalities: list[str]
    supports_reasoning: bool
    supports_tools: bool
    free: bool
    endpoints: list[dict[str, Any]] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or self.id

    @property
    def provider_names(self) -> list[str]:
        names: list[str] = []
        for endpoint in self.endpoints:
            name = endpoint.get("provider_name")
            if name and name not in names:
                names.append(name)
        return names

    @property
    def uptime_last_day(self) -> float | None:
        values = [
            endpoint.get("uptime_last_1d")
            for endpoint in self.endpoints
            if isinstance(endpoint.get("uptime_last_1d"), (int, float))
        ]
        if not values:
            return None
        return max(values)

    @property
    def max_completion_tokens(self) -> int | None:
        values = [
            endpoint.get("max_completion_tokens")
            for endpoint in self.endpoints
            if isinstance(endpoint.get("max_completion_tokens"), int)
        ]
        return max(values) if values else None


def _price_per_million(raw: Any) -> float:
    try:
        return float(raw) * 1_000_000
    except (TypeError, ValueError):
        return 0.0


def _parse_model(raw: dict[str, Any]) -> ModelInfo:
    architecture = raw.get("architecture") or {}
    pricing = raw.get("pricing") or {}
    parameters = raw.get("supported_parameters") or []
    modalities = architecture.get("input_modalities") or ["text"]
    return ModelInfo(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        created=int(raw.get("created") or 0),
        context_length=int(raw.get("context_length") or 0),
        prompt_price=_price_per_million(pricing.get("prompt")),
        completion_price=_price_per_million(pricing.get("completion")),
        input_modalities=[str(item) for item in modalities],
        supports_reasoning="reasoning" in parameters,
        supports_tools="tools" in parameters,
        free=is_free(raw.get("id", "")),
    )


def fetch_endpoints(
    model_id: str,
    api_key: str | None = None,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Endpoint details for one model (the upstream providers behind it)."""
    url = ENDPOINTS_URL.format(model_id=model_id.replace("/", "%2F"))
    try:
        payload = _get_json(url, api_key=api_key, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return []
    return payload.get("data", {}).get("endpoints", []) or []


def fetch_models(
    api_key: str | None = None,
    with_endpoints: bool = True,
    timeout: int = 30,
) -> list[ModelInfo]:
    """Fetch the whole catalogue, optionally enriched with endpoint details."""
    payload = _get_json(MODELS_URL, api_key=api_key, timeout=timeout)
    models = [_parse_model(raw) for raw in payload.get("data", [])]
    models = [model for model in models if model.id]
    if with_endpoints:
        for model in models:
            model.endpoints = fetch_endpoints(model.id, api_key=api_key, timeout=timeout)
    return models


@dataclass
class ProbeResult:
    """Outcome of a single live request against a model."""

    model_id: str
    ok: bool
    status: int | None = None
    reason: str = ""
    latency_ms: int = 0


def _summarize_failure(status: int | None, detail: str) -> str:
    text = (detail or "").strip()
    if status == 429:
        if "shared pool" in text or "upstream" in text.lower():
            return "429 upstream pool exhausted"
        return "429 rate limited"
    if status == 402:
        return "402 needs credits"
    if status in (401, 403):
        return "%s auth/forbidden" % status
    if status is not None:
        return ("%s %s" % (status, text[:80])) if text else str(status)
    return text[:120] or "unknown error"


def probe_model(
    model_id: str,
    api_key: str,
    timeout: int = 45,
    max_tokens: int = 8,
) -> ProbeResult:
    """Send a tiny chat request to see whether the model actually answers.

    Free models frequently sit behind a shared, exhausted pool, so the only
    trustworthy signal is a real request.
    """
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": max_tokens,
    }
    started = time.monotonic()
    try:
        _post_json(CHAT_URL, payload, api_key, timeout=timeout)
    except urllib.error.HTTPError as error:
        elapsed = int((time.monotonic() - started) * 1000)
        detail = ""
        try:
            body = json.loads(error.read().decode("utf-8"))
            detail = str(body.get("error", {}).get("message", ""))
        except Exception:
            detail = str(error.reason or "")
        return ProbeResult(
            model_id=model_id,
            ok=False,
            status=error.code,
            reason=_summarize_failure(error.code, detail),
            latency_ms=elapsed,
        )
    except (urllib.error.URLError, OSError, ValueError) as error:
        elapsed = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            model_id=model_id,
            ok=False,
            status=None,
            reason="network error: %s" % error,
            latency_ms=elapsed,
        )
    return ProbeResult(
        model_id=model_id,
        ok=True,
        status=200,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def key_info(api_key: str) -> dict[str, Any]:
    """Spending cap and remaining allowance for an OpenRouter key."""
    try:
        data = _get_json(KEY_URL, api_key=api_key).get("data", {})
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return {}
    return {
        "limit": data.get("limit"),
        "limit_remaining": data.get("limit_remaining"),
        "usage": data.get("usage"),
    }


def credits(api_key: str) -> dict[str, Any]:
    """Account credits (``/credits`` is the balance, ``/key`` is the cap)."""
    try:
        data = _get_json(CREDITS_URL, api_key=api_key).get("data", {})
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return {}
    return {
        "total_credits": data.get("total_credits"),
        "total_usage": data.get("total_usage"),
    }
