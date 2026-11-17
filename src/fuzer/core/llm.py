"""Provider-neutral calls for readers and other one-shot LLM requests."""

import hashlib
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from types import SimpleNamespace

from . import models, settings

DEFAULT_MODEL = models.DEFAULT_MODEL
DEFAULT_EFFORT = "medium"
_PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
}


def model() -> str:
    return settings.get(None, "llm.model")


def effort() -> str:
    return settings.get(None, "llm.effort")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def provider(scope: str = "llm") -> str:
    return settings.get(None, f"{scope}.provider")


def _config(scope: str) -> tuple[str, str, str | None]:
    selected = provider(scope)
    endpoint = settings.get(None, f"{scope}.base_url") or os.getenv(
        {"openai": "OPENAI_BASE_URL", "azure_openai": "AZURE_OPENAI_ENDPOINT",
         "ollama": "OLLAMA_BASE_URL"}.get(selected, ""), "")
    key = settings.secret(None, f"{scope}.api_key")
    key_ref = settings.get(None, f"{scope}.api_key")
    if key_ref == "env:ANTHROPIC_API_KEY" and selected != "anthropic":
        key = os.getenv(_PROVIDER_ENV.get(selected, ""), "")
    if selected == "ollama":
        endpoint = endpoint or "http://localhost:11434/v1"
        key = key or "ollama"
    return selected, endpoint, key


def has_credentials(scope: str = "llm") -> bool:
    """A provider is chosen and its key reference resolves (or needs none). Presence only: nothing is called."""
    selected, _, key = _config(scope)
    return selected == "ollama" or bool(key)


def configured(scope: str = "llm", *, person: bool = False) -> bool:
    """A provider is set up and the month's model budget isn't spent (core/usage.py): each automated feature asks this
    before calling, so a spent budget turns them off the way a missing key does. A feature that answers a person
    (`person=True`, or inside usage.person()) only needs the key: the budget caps automated use, not people."""
    if not has_credentials(scope):
        return False
    from . import usage as metering

    return person or metering.asked_by_person() or not metering.over_budget()


def _resource(obj, path: str):
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _meter(provider_client, selected: str, *, is_async: bool):
    """Record every response the client returns in fuzer.llm_usage, and refuse a call once the month's budget is
    spent. Streams are counted when they finish, from their final message."""
    from . import usage as metering

    for path in ("messages", "beta.messages", "chat.completions"):
        resource = _resource(provider_client, path)
        create = getattr(resource, "create", None) if resource is not None else None
        if create is None or getattr(create, "_fuzer_metered", False):
            continue

        if is_async:
            async def metered(*args, _create=create, **kwargs):
                metering.check()
                response = await _create(*args, **kwargs)
                if not kwargs.get("stream"):
                    metering.record(response, selected)
                return response
        else:
            def metered(*args, _create=create, **kwargs):
                metering.check()
                response = _create(*args, **kwargs)
                if not kwargs.get("stream"):
                    metering.record(response, selected)
                return response
        metered._fuzer_metered = True
        try:
            resource.create = metered
        except AttributeError:
            pass
    return provider_client


def client(scope: str = "llm", *, timeout: float | None = None):
    """Create the configured synchronous provider client, metered (core/usage.py)."""
    return _meter(_client(scope, timeout=timeout), provider(scope), is_async=False)


def _client(scope: str, *, timeout: float | None):
    selected, endpoint, key = _config(scope)
    if selected == "anthropic":
        from anthropic import Anthropic

        kwargs = {"api_key": key} if key else {}
        if endpoint:
            kwargs["base_url"] = endpoint
        if timeout:
            kwargs["timeout"] = timeout
        return Anthropic(**kwargs)
    try:
        from openai import AzureOpenAI, OpenAI
    except ImportError as e:
        raise RuntimeError("Install Fuzer's 'openai' extra to use OpenAI, Azure OpenAI, or Ollama") from e
    kwargs = {"api_key": key} if key else {}
    if selected == "ollama":
        kwargs["api_key"] = key or "ollama"
    if endpoint:
        kwargs["base_url" if selected in ("openai", "ollama") or "/openai/v1" in endpoint
               else "azure_endpoint"] = endpoint
    if timeout:
        kwargs["timeout"] = timeout
    if selected == "azure_openai" and "/openai/v1" not in endpoint:
        kwargs["api_version"] = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
        return AzureOpenAI(**kwargs)
    if selected == "azure_openai":
        kwargs["base_url"] = endpoint
    return OpenAI(**kwargs)


def async_client(scope: str = "agents", *, timeout: float | None = None):
    """Create the configured async client used by Agent Framework, metered (core/usage.py)."""
    return _meter(_async_client(scope, timeout=timeout), provider(scope), is_async=True)


def _async_client(scope: str, *, timeout: float | None):
    selected, endpoint, key = _config(scope)
    if selected == "anthropic":
        from anthropic import AsyncAnthropic

        kwargs = {"api_key": key} if key else {}
        if endpoint:
            kwargs["base_url"] = endpoint
        if timeout:
            kwargs["timeout"] = timeout
        return AsyncAnthropic(**kwargs)
    try:
        from openai import AsyncAzureOpenAI, AsyncOpenAI
    except ImportError as e:
        raise RuntimeError("Install Fuzer's 'openai' extra to use OpenAI, Azure OpenAI, or Ollama") from e
    kwargs = {"api_key": key} if key else {}
    if selected == "ollama":
        kwargs["api_key"] = key or "ollama"
    if endpoint:
        kwargs["base_url" if selected in ("openai", "ollama") or "/openai/v1" in endpoint
               else "azure_endpoint"] = endpoint
    if timeout:
        kwargs["timeout"] = timeout
    if selected == "azure_openai" and "/openai/v1" not in endpoint:
        kwargs["api_version"] = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
        return AsyncAzureOpenAI(**kwargs)
    if selected == "azure_openai":
        kwargs["base_url"] = endpoint
    return AsyncOpenAI(**kwargs)


def provider_errors(scope: str = "llm") -> tuple[type[BaseException], ...]:
    """Provider SDK errors callers can translate into an application-level response."""
    selected = provider(scope)
    try:
        if selected == "anthropic":
            from anthropic import APIError
        else:
            from openai import APIError
    except ImportError:
        return ()
    return (APIError,)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def _openai_messages(params: dict, schema: dict | None, selected: str) -> list[dict]:
    messages = []
    system = params.get("system")
    if system:
        text = _text(system)
        if selected == "ollama" and schema:
            text += "\n\nReturn JSON that follows this schema:\n" + json.dumps(schema)
        messages.append({"role": "system", "content": text})
    for message in params.get("messages", []):
        content = message["content"]
        if isinstance(content, str):
            messages.append({"role": message["role"], "content": content})
            continue
        parts = []
        for block in content:
            if block["type"] == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif block["type"] == "image":
                source = block["source"]
                if source.get("type") != "base64":
                    raise ValueError("Only base64 image input is supported by the LLM adapter")
                data = source["data"]
                parts.append({"type": "image_url", "image_url": {
                    "url": f"data:{source['media_type']};base64,{data}"}})
        messages.append({"role": message["role"], "content": parts})
    return messages


def _validate(value, schema: dict, path: str = "response") -> None:
    for alternatives, valid_count in (("anyOf", lambda n: n >= 1), ("oneOf", lambda n: n == 1)):
        if alternatives in schema:
            matches = 0
            for candidate in schema[alternatives]:
                try:
                    _validate(value, candidate, path)
                    matches += 1
                except ValueError:
                    pass
            if not valid_count(matches):
                raise ValueError(f"{path} does not match {alternatives}")
    for candidate in schema.get("allOf", []):
        _validate(value, candidate, path)
    kind = schema.get("type")
    valid = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
             "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
             "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None}
    if kind in valid and not valid[kind](value):
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if kind in ("integer", "number"):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        for bound, predicate in (("minimum", lambda v, b: v >= b), ("maximum", lambda v, b: v <= b),
                                 ("exclusiveMinimum", lambda v, b: v > b),
                                 ("exclusiveMaximum", lambda v, b: v < b)):
            if bound in schema and not predicate(value, schema[bound]):
                raise ValueError(f"{path} violates {bound}")
    if kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise ValueError(f"{path} has an invalid length")
    if kind == "object":
        missing = set(schema.get("required", ())) - value.keys()
        if missing:
            raise ValueError(f"{path} is missing required fields: {', '.join(sorted(missing))}")
        if schema.get("additionalProperties") is False and set(value) - schema.get("properties", {}).keys():
            raise ValueError(f"{path} has unexpected fields")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                _validate(value[key], subschema, f"{path}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ValueError(f"{path} has an invalid number of items")
        for index, item in enumerate(value):
            _validate(item, schema.get("items", {}), f"{path}[{index}]")


def _openai_call(client, params: dict, scope: str):
    selected, _, _ = _config(scope)
    output = params.pop("output_config", {}) or {}
    format_spec = output.get("format", {})
    schema = format_spec.get("schema") if format_spec.get("type") == "json_schema" else None
    content = {"system": params.pop("system", None), "messages": params.pop("messages", [])}
    request = {
        "model": params.pop("model"),
        "messages": _openai_messages(content, schema, selected),
        "max_tokens": params.pop("max_tokens", 4096),
    }
    effort = output.get("effort")
    model_name = request["model"].lower()
    reasoning = selected != "ollama" and any(n in model_name for n in ("o1", "o3", "o4", "gpt-5"))
    if reasoning:
        request["max_completion_tokens"] = request.pop("max_tokens")
    if effort and reasoning:
        request["reasoning_effort"] = effort if effort in ("low", "medium", "high") else "high"
    tools = params.pop("tools", None)
    if tools:
        request["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["input_schema"]}} for t in tools]
    choice = params.pop("tool_choice", None)
    if choice:
        request["tool_choice"] = ({"type": "function", "function": {"name": choice["name"]}}
                                  if choice.get("type") == "tool" else choice)
    if schema:
        request["response_format"] = ({"type": "json_object"} if selected == "ollama" else {
            "type": "json_schema", "json_schema": {"name": "fuzer_response", "strict": False, "schema": schema}})
    request.update({k: v for k, v in params.items() if k not in ("betas", "fallbacks")})
    response = client.chat.completions.create(**request)
    choice = response.choices[0]
    content = []
    refusal = getattr(choice.message, "refusal", None)
    if choice.message.content:
        if schema:
            decoded = json.loads(choice.message.content)
            _validate(decoded, schema)
        content.append(SimpleNamespace(type="text", text=choice.message.content))
    elif refusal:
        content.append(SimpleNamespace(type="text", text=refusal))
    for tool in choice.message.tool_calls or []:
        content.append(SimpleNamespace(type="tool_use", name=tool.function.name,
                                       input=json.loads(tool.function.arguments)))
    usage_data = response.usage
    prompt = getattr(usage_data, "prompt_tokens", 0) or 0
    details = getattr(usage_data, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    usage = SimpleNamespace(input_tokens=max(0, prompt - cached),
                            output_tokens=getattr(usage_data, "completion_tokens", 0) or 0,
                            cache_read_input_tokens=cached, cache_creation_input_tokens=0)
    stop = "refusal" if refusal else choice.finish_reason
    return SimpleNamespace(model=response.model, stop_reason=("max_tokens" if stop == "length" else
                                                              "refusal" if stop in ("content_filter", "refusal") else
                                                              "end_turn"), content=content, usage=usage)


def _vision_supported(selected: str, model_name: str) -> bool:
    if selected == "anthropic":
        return True
    name = model_name.lower()
    if selected == "ollama":
        return any(x in name for x in ("vision", "llava", "bakllava", "qwen2.5vl", "qwen3-vl", "gemma3"))
    return any(x in name for x in ("gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-5")) \
        or name == "o3" or name.startswith(("o3-2025", "o4-mini"))


def call(client, params: dict, *, scope: str = "llm"):
    """One synchronous request; provider-specific features are applied only where supported."""
    client = client or globals()["client"](scope)
    if hasattr(client, "beta"):
        return client.beta.messages.create(
            **params,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    if hasattr(client, "messages") and hasattr(client.messages, "create") and not hasattr(client, "chat"):
        return client.messages.create(
            **params,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    selected, _, _ = _config(scope)
    if any(b.get("type") == "image" for m in params.get("messages", [])
           for b in (m.get("content") if isinstance(m.get("content"), list) else [])):
        if not _vision_supported(selected, params.get("model", "")):
            raise ValueError(f"Model {params.get('model')!r} on {selected} does not support vision")
    return _openai_call(client, dict(params), scope)


def usage(message) -> dict:
    u = message.usage
    return {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}


def cost_report(model_name: str, totals: dict, scope: str = "llm") -> dict:
    from .pricing import cost

    selected = provider(scope)
    usd, known = cost(model_name, totals.get("input_tokens", 0), totals.get("output_tokens", 0),
                      totals.get("cache_read_tokens", 0), provider=selected)
    return {"provider": selected, "cost_usd": round(usd, 4), **({"unpriced": True} if not known else {})}


def run_batch(client, requests: dict[str, dict], on_result, totals: dict, errors: list, poll_seconds: int = 30):
    """Use Anthropic's batch API when available; otherwise make bounded concurrent calls."""
    if hasattr(client, "messages") and hasattr(client.messages, "batches"):
        from anthropic.types.message_create_params import (
            MessageCreateParamsNonStreaming,
        )
        from anthropic.types.messages.batch_create_params import Request

        from . import usage as metering

        metering.check()
        selected = provider()
        created = client.messages.batches.create(requests=[
            Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**params)) for cid, params in requests.items()
        ])
        print(f"batch {created.id}: {len(requests)} requests submitted", flush=True)
        while (b := client.messages.batches.retrieve(created.id)).processing_status != "ended":
            print(f"batch {created.id}: {b.request_counts.processing} still processing", flush=True)
            time.sleep(poll_seconds)
        for result in client.messages.batches.results(created.id):
            if result.result.type == "succeeded":
                metering.record(result.result.message, selected, batch=True)
                try:
                    on_result(result.custom_id, result.result.message)
                except Exception as e:
                    totals["failed_requests"] += 1
                    errors.append(f"{result.custom_id}: {type(e).__name__}: {e}")
            else:
                totals["failed_requests"] += 1
                errors.append(f"{result.custom_id}: {result.result.type}")
        return
    from . import usage as metering

    with metering.feature(metering.current_feature()), \
            ThreadPoolExecutor(max_workers=min(8, max(1, len(requests)))) as pool:  # the threads keep the feature
        futures = {pool.submit(copy_context().run, call, client, params): cid for cid, params in requests.items()}
        for future in as_completed(futures):
            cid = futures[future]
            try:
                on_result(cid, future.result())
            except Exception as e:
                totals["failed_requests"] += 1
                errors.append(f"{cid}: {type(e).__name__}: {e}")
