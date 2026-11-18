"""Unit tests for model-prefix routing across multiple upstreams.

No network: upstream clients are replaced with in-memory fakes.

    pip install -e ".[dev]"
    pytest tests/test_upstream_routing.py
"""

import os

# src.core.config reads the environment at import time.
os.environ.setdefault("OPENAI_API_KEY", "test-default-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://corp-gateway.invalid/v1")
os.environ.setdefault("UPSTREAM_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.pop("PROXY_API_KEY", None)

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import endpoints  # noqa: E402
from src.core.upstreams import (  # noqa: E402
    Route,
    UnroutableModelError,
    UpstreamRouter,
    UpstreamSpec,
    parse_extra_upstreams,
)

# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_no_extra_upstreams_by_default():
    assert parse_extra_upstreams({"OPENAI_BASE_URL": "x"}) == []


def test_parse_defaults_prefix_and_api_key():
    specs = parse_extra_upstreams({"UPSTREAM_OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1"})
    assert specs == [
        UpstreamSpec("ollama", "ollama/", "http://127.0.0.1:11434/v1", "not-needed")
    ]


def test_default_prefix_is_lowercase_even_for_uppercase_name():
    """The derived prefix must agree with the case clients are matched on."""
    specs = parse_extra_upstreams({"UPSTREAM_OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1"})
    assert specs[0].prefix == "ollama/"
    assert UpstreamRouter("D", [Route(s.name, s.prefix, "U") for s in specs]).resolve("ollama/x")[
        0
    ].name == "ollama"


def test_parse_explicit_prefix_key_and_underscored_name():
    specs = parse_extra_upstreams(
        {
            "UPSTREAM_LM_STUDIO_BASE_URL": "http://127.0.0.1:1234/v1",
            "UPSTREAM_LM_STUDIO_PREFIX": "lms:",
            "UPSTREAM_LM_STUDIO_API_KEY": "secret",
        }
    )
    assert specs == [UpstreamSpec("lm_studio", "lms:", "http://127.0.0.1:1234/v1", "secret")]


@pytest.mark.parametrize(
    "env",
    [
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_B_BASE_URL": "http://b", "UPSTREAM_B_PREFIX": "a/"},
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_A_PREFIX": ""},
        {"UPSTREAM_A_BASE_URL": "  "},
        {"UPSTREAM_DEFAULT_BASE_URL": "http://a"},
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_A_PREFIX": "  "},
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_A_API_KEY": ""},
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_A_API_KEY": "   "},
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_A_PREFIX": "A/"},
        {"UPSTREAM_A_BASE_URL": "http://a", "UPSTREAM_A_PREFIX": "Ollama/"},
    ],
    ids=[
        "duplicate-prefix",
        "empty-prefix",
        "empty-url",
        "reserved-name",
        "blank-prefix",
        "empty-api-key",
        "blank-api-key",
        "mixed-case-prefix",
        "mixed-case-prefix-matches-default-form",
    ],
)
def test_parse_rejects_invalid_config(env):
    with pytest.raises(ValueError):
        parse_extra_upstreams(env)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _router():
    return UpstreamRouter(
        "DEFAULT",
        [Route("ollama", "ollama/", "OLLAMA"), Route("ollama_big", "ollama/big/", "BIG")],
    )


def test_unprefixed_model_goes_to_default_unchanged():
    route, model = _router().resolve("gpt-4o")
    assert (route.name, route.client, model) == ("default", "DEFAULT", "gpt-4o")


def test_prefix_is_stripped():
    route, model = _router().resolve("ollama/qwen3:8b")
    assert (route.client, model) == ("OLLAMA", "qwen3:8b")


def test_longest_prefix_wins():
    route, model = _router().resolve("ollama/big/llama3")
    assert (route.client, model) == ("BIG", "llama3")


def test_bare_prefix_is_rejected():
    with pytest.raises(UnroutableModelError):
        _router().resolve("ollama/")


# ---------------------------------------------------------------------------
# Endpoints (fake upstream clients)
# ---------------------------------------------------------------------------


def _completion(model):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class _Model:
    def __init__(self, model_id):
        self.id = model_id

    def model_dump(self):
        return {"id": self.id, "object": "model", "created": 0, "owned_by": "test"}


@pytest.fixture
def upstreams(monkeypatch):
    """Patch every routed client; return {name: list of requests it received}."""
    calls = {}
    model_ids = {"default": ["gpt-4o"], "ollama": ["qwen3:8b", "llama3"]}
    for route in endpoints.router_upstreams.routes:
        calls[route.name] = []

        async def fake_create(request, request_id=None, _name=route.name):
            calls[_name].append(request)
            return _completion(request["model"])

        def fake_list(_ids=model_ids[route.name]):
            async def gen():
                for model_id in _ids:
                    yield _Model(model_id)

            return gen()

        monkeypatch.setattr(route.client, "create_chat_completion", fake_create)
        monkeypatch.setattr(route.client.client.models, "list", fake_list)
    return calls


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(endpoints.router)
    return TestClient(app)


def test_messages_routes_prefixed_model_to_extra_upstream(client, upstreams):
    resp = client.post(
        "/v1/messages",
        json={"model": "ollama/qwen3:8b", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "ollama/qwen3:8b"  # echoed as the client asked
    assert [r["model"] for r in upstreams["ollama"]] == ["qwen3:8b"]
    assert upstreams["default"] == []


def test_messages_routes_plain_model_to_default(client, upstreams):
    resp = client.post(
        "/messages",
        json={"model": "gpt-4o", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200, resp.text
    assert [r["model"] for r in upstreams["default"]] == ["gpt-4o"]
    assert upstreams["ollama"] == []


def test_chat_completions_routes_by_prefix(client, upstreams):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "ollama/llama3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200, resp.text
    assert [r["model"] for r in upstreams["ollama"]] == ["llama3"]


def test_bare_prefix_returns_400(client, upstreams):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "ollama/", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400


def test_models_lists_all_upstreams_with_prefixes(client, upstreams):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["data"]] == ["gpt-4o", "ollama/qwen3:8b", "ollama/llama3"]


def test_models_skips_a_failing_upstream(client, upstreams, monkeypatch):
    ollama = endpoints.router_upstreams.resolve("ollama/x")[0].client

    def broken():
        raise ConnectionError("ollama is down")

    monkeypatch.setattr(ollama.client.models, "list", broken)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["data"]] == ["gpt-4o"]


def test_models_skips_malformed_entries(client, upstreams, monkeypatch):
    ollama = endpoints.router_upstreams.resolve("ollama/x")[0].client

    async def malformed():
        return [{"object": "model"}, {"id": None}, {"id": "llama3", "object": "model"}]

    monkeypatch.setattr(ollama, "list_models", malformed)
    resp = client.get("/v1/models")
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["data"]] == ["gpt-4o", "ollama/llama3"]


def test_models_all_failing_names_each_upstream(client, upstreams, monkeypatch):
    async def broken():
        raise ConnectionError("connection refused")

    for route in endpoints.router_upstreams.routes:
        monkeypatch.setattr(route.client, "list_models", broken)
    resp = client.get("/v1/models")
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "default: connection refused" in detail and "ollama: connection refused" in detail
    assert "BIG_MODEL" not in detail


def test_extra_upstream_gets_no_corporate_headers():
    ollama = endpoints.router_upstreams.resolve("ollama/x")[0].client
    assert ollama.custom_headers == {}


def test_health_lists_upstreams(client):
    upstreams = client.get("/health").json()["upstreams"]
    assert upstreams == [{"name": "default", "prefix": ""}, {"name": "ollama", "prefix": "ollama/"}]
