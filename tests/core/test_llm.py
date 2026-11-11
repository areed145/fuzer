import json
from types import SimpleNamespace

import pytest

from fuzer.core import db, llm, settings


class FakeOpenAI:
    def __init__(self, content='{"items":[]}'):
        self.content = content
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content, tool_calls=[]),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=4)),
        )


@pytest.mark.parametrize("provider", ["openai", "azure_openai", "ollama"])
def test_openai_compatible_structured_calls(provider, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake = FakeOpenAI()
    schema = {"type": "object", "required": ["items"], "additionalProperties": False,
              "properties": {"items": {"type": "array", "items": {"type": "string"}}}}
    with settings.Overrides({"llm.provider": provider, "llm.model": "qwen2.5vl",
                             "llm.base_url": "http://localhost:9000/v1"}):
        response = llm.call(fake, {
            "model": "qwen2.5vl", "max_tokens": 500,
            "system": [{"type": "text", "text": "Read the sheet.", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "Read this."}],
            "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        })

    sent = fake.requests[0]
    assert sent["model"] == "qwen2.5vl" and sent["max_tokens"] == 500
    assert sent["messages"][0]["content"].startswith("Read the sheet.")
    assert sent["response_format"]["type"] == ("json_object" if provider == "ollama" else "json_schema")
    assert "cache_control" not in json.dumps(sent)
    assert response.stop_reason == "end_turn"
    assert llm.usage(response) == {"input_tokens": 16, "output_tokens": 5, "cache_read_tokens": 4}


def test_ollama_vision_model_receives_images_and_non_vision_model_fails():
    fake = FakeOpenAI()
    request = {"model": "qwen2.5vl", "max_tokens": 100,
               "messages": [{"role": "user", "content": [
                   {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}}
               ]}]}
    with settings.Overrides({"llm.provider": "ollama"}):
        llm.call(fake, request)
        assert fake.requests[0]["messages"][0]["content"][0]["image_url"]["url"].endswith("aGVsbG8=")
        with pytest.raises(ValueError, match="does not support vision"):
            llm.call(fake, {**request, "model": "qwen2.5"})


@pytest.mark.parametrize("model", ["o3", "o3-2025-04-16", "o4-mini"])
def test_openai_reasoning_vision_models_are_allowed(model):
    fake = FakeOpenAI()
    request = {"model": model, "max_tokens": 100,
               "messages": [{"role": "user", "content": [
                   {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}}
               ]}]}
    with settings.Overrides({"llm.provider": "openai"}):
        llm.call(fake, request)
    assert fake.requests


def test_openai_json_schema_is_validated():
    schema = {"type": "object", "required": ["items"],
              "properties": {"items": {"type": "array", "items": {"type": "string"}}}}
    fake = FakeOpenAI('{"items":[3]}')
    with settings.Overrides({"llm.provider": "openai"}), pytest.raises(ValueError, match="must be string"):
        llm.call(fake, {"model": "gpt-4o", "max_tokens": 100, "messages": [{"role": "user", "content": "x"}],
                        "output_config": {"format": {"type": "json_schema", "schema": schema}}})


def test_effort_is_mapped_to_openai_reasoning_options():
    fake = FakeOpenAI()
    with settings.Overrides({"llm.provider": "openai"}):
        llm.call(fake, {"model": "gpt-5", "max_tokens": 100,
                        "messages": [{"role": "user", "content": "think"}],
                        "output_config": {"effort": "max"}})
    assert fake.requests[0]["reasoning_effort"] == "high"
    assert fake.requests[0]["max_completion_tokens"] == 100


def test_json_schema_validates_union_types():
    union = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    llm._validate(None, union)
    with pytest.raises(ValueError, match="anyOf"):
        llm._validate(1, union)


def test_local_cost_is_zero_and_openai_cost_is_provider_specific():
    from fuzer.core.pricing import cost

    assert cost("qwen2.5vl", 1_000_000, 1_000_000, provider="ollama") == (0.0, True)
    assert cost("gpt-4o", 1_000_000, 1_000_000, provider="openai") == (12.5, True)
    assert cost("unlisted", 1_000_000, 1_000_000, provider="openai") == (0.0, False)


@pytest.mark.parametrize(("endpoint", "client_type"), [
    ("https://resource.openai.azure.com", "AzureOpenAI"),
    ("https://resource.openai.azure.com/openai/v1", "OpenAI"),
])
def test_azure_endpoint_client_modes(endpoint, client_type, monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    with settings.Overrides({"llm.provider": "azure_openai", "llm.base_url": endpoint}):
        configured = llm.client()
    assert type(configured).__name__ == client_type
    configured.close()


def test_non_anthropic_batch_uses_concurrent_adapter_calls(monkeypatch):
    fake = FakeOpenAI()
    completed = []
    totals = {"failed_requests": 0}
    errors = []
    scope = db.Scope("kestrel", "proj_kestrel")

    def provider(scope="llm"):
        assert db.scope().project == "kestrel"
        return "openai"

    monkeypatch.setattr(llm, "provider", provider)
    with db.use(scope), settings.Overrides({"llm.provider": "openai"}):
        llm.run_batch(fake, {"one": {"model": "gpt-4o", "max_tokens": 10,
                                     "messages": [{"role": "user", "content": "one"}]}},
                      lambda key, response: completed.append((key, response.stop_reason)), totals, errors)
    assert completed == [("one", "end_turn")]
    assert totals["failed_requests"] == 0 and not errors
