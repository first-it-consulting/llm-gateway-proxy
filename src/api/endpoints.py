import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.conversion.request_converter import convert_claude_to_openai
from src.conversion.response_converter import convert_openai_streaming_to_claude, convert_openai_to_claude_response
from src.core.client import OpenAIClient
from src.core.config import config
from src.core.logging import logger
from src.core.upstreams import Route, UnroutableModelError, UpstreamRouter
from src.models.claude import ClaudeMessagesRequest, ClaudeTokenCountRequest

router = APIRouter()

openai_client = OpenAIClient(
    config.openai_api_key,
    config.openai_base_url,
    config.request_timeout,
    api_version=config.azure_api_version,
    custom_headers=config.get_custom_headers(),
    client_cert=config.get_client_cert(),
    verify=config.get_verify(),
)

# Extra upstreams (UPSTREAM_<NAME>_*) get plain HTTPS with default CA
# verification -- no client certificate and no CUSTOM_HEADER_* headers, which
# belong to the default (corporate) upstream only.
router_upstreams = UpstreamRouter(
    openai_client,
    [
        Route(
            spec.name,
            spec.prefix,
            OpenAIClient(spec.api_key, spec.base_url, config.request_timeout),
        )
        for spec in config.extra_upstreams
    ],
)


def resolve_upstream(model) -> tuple:
    """Map a client-requested model to (route, upstream model name), or 400."""
    if not isinstance(model, str) or not model:
        raise HTTPException(status_code=400, detail="Request must include a 'model'.")
    try:
        route, upstream_model = router_upstreams.resolve(model)
    except UnroutableModelError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if route.prefix:
        logger.debug("Routing model %s -> upstream %s (%s)", model, route.name, upstream_model)
    return route, upstream_model


async def validate_api_key(
    x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)
):
    """Validate a client's PROXY_API_KEY from either the x-api-key or Authorization header."""
    if not config.proxy_api_key:
        return

    client_api_key = None
    if x_api_key:
        client_api_key = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        client_api_key = authorization.removeprefix("Bearer ")

    if not client_api_key or not config.validate_client_api_key(client_api_key):
        logger.warning("Rejected request with invalid or missing API key")
        raise HTTPException(status_code=401, detail="Invalid API key.")


async def _sse_passthrough(lines):
    """Add SSE line-terminators to an already-formatted `data: ...` line generator."""
    async for line in lines:
        yield f"{line}\n\n"


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint — forwarded to the upstream mostly as-is.
# ---------------------------------------------------------------------------


@router.post("/v1/chat/completions")
async def create_chat_completion(http_request: Request, _: None = Depends(validate_api_key)):
    body = await http_request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    request_id = str(uuid.uuid4())
    route, upstream_model = resolve_upstream(body.get("model"))
    body = {**body, "model": upstream_model}
    client = route.client

    if body.get("stream"):
        stream = client.create_chat_completion_stream(body, request_id)
        return StreamingResponse(
            _sse_passthrough(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    response = await client.create_chat_completion(body, request_id)
    return JSONResponse(response)


@router.get("/v1/models")
@router.get("/models")
async def list_models(_: None = Depends(validate_api_key)):
    """List models from every upstream, in OpenAI's /v1/models format.

    Models from extra upstreams are listed with their route prefix (e.g.
    "ollama/qwen3:8b"), i.e. exactly the ID a client must request. An
    upstream that fails to answer is skipped; 502 only if all of them fail.
    """
    routes = router_upstreams.routes
    results = await asyncio.gather(
        *(r.client.list_models() for r in routes), return_exceptions=True
    )

    models, failures = [], []
    for route, result in zip(routes, results):
        if isinstance(result, BaseException):
            logger.error("Failed to list models from upstream %s: %s", route.name, result)
            failures.append(f"{route.name}: {result}")
            continue
        skipped = 0
        for model in result:
            model_id = model.get("id") if isinstance(model, dict) else None
            if not isinstance(model_id, str) or not model_id:
                skipped += 1  # malformed entry (e.g. some llama.cpp builds): don't poison the list
                continue
            if route.prefix:
                model = {**model, "id": f"{route.prefix}{model_id}"}
            models.append(model)
        if skipped:
            logger.warning("Upstream %s returned %d model(s) without an id; skipped", route.name, skipped)

    if len(failures) == len(routes):
        raise HTTPException(
            status_code=502, detail="All upstreams failed to list models: " + "; ".join(failures)
        )
    return {"object": "list", "data": models}


# ---------------------------------------------------------------------------
# Claude-compatible endpoint — translated to/from the upstream OpenAI format.
# Registered at both /messages and /v1/messages for compatibility with
# clients (like the Anthropic SDK) that always append /v1/messages.
# ---------------------------------------------------------------------------


@router.post("/messages")
@router.post("/v1/messages")
async def create_message(
    request: ClaudeMessagesRequest, http_request: Request, _: None = Depends(validate_api_key)
):
    try:
        logger.debug("Processing Claude request: model=%s, stream=%s", request.model, request.stream)
        request_id = str(uuid.uuid4())
        route, upstream_model = resolve_upstream(request.model)
        client = route.client
        openai_request = convert_claude_to_openai(request)
        openai_request["model"] = upstream_model

        if await http_request.is_disconnected():
            raise HTTPException(status_code=499, detail="Client disconnected")

        if request.stream:
            openai_stream = client.create_chat_completion_stream(openai_request, request_id)
            return StreamingResponse(
                convert_openai_streaming_to_claude(
                    openai_stream, request, logger, http_request, client, request_id
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        openai_response = await client.create_chat_completion(openai_request, request_id)
        return convert_openai_to_claude_response(openai_response, request)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error processing request")
        raise HTTPException(status_code=500, detail=openai_client.classify_openai_error(str(e)))


@router.post("/messages/count_tokens")
@router.post("/v1/messages/count_tokens")
async def count_tokens(request: ClaudeTokenCountRequest, _: None = Depends(validate_api_key)):
    """Rough token estimate (~4 characters per token). Not model-accurate."""
    total_chars = 0

    if request.system:
        if isinstance(request.system, str):
            total_chars += len(request.system)
        else:
            total_chars += sum(len(block.text) for block in request.system)

    for msg in request.messages:
        if msg.content is None:
            continue
        if isinstance(msg.content, str):
            total_chars += len(msg.content)
        else:
            total_chars += sum(len(block.text) for block in msg.content if hasattr(block, "text") and block.text)

    return {"input_tokens": max(1, total_chars // 4)}


# ---------------------------------------------------------------------------
# Operational endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "upstream_configured": bool(config.openai_api_key),
        "mtls_enabled": config.get_client_cert() is not None,
        "client_api_key_validation": bool(config.proxy_api_key),
        "upstreams": [
            {"name": r.name, "prefix": r.prefix} for r in router_upstreams.routes
        ],
    }


@router.get("/test-connection")
async def test_connection(_: None = Depends(validate_api_key)):
    """Verify connectivity to every upstream (mTLS, auth, reachability) by listing models."""
    routes = router_upstreams.routes

    results = await asyncio.gather(
        *(r.client.list_models() for r in routes), return_exceptions=True
    )
    upstreams = {}
    for route, result in zip(routes, results):
        if isinstance(result, BaseException):
            logger.error("Connectivity test failed for upstream %s: %s", route.name, result)
            upstreams[route.name] = {"status": "failed", "message": str(result)}
        else:
            upstreams[route.name] = {"status": "success", "model_count": len(result)}

    failed = [name for name, r in upstreams.items() if r["status"] == "failed"]
    if not failed:
        default = upstreams["default"]
        return {"status": "success", "model_count": default["model_count"], "upstreams": upstreams}

    return JSONResponse(
        status_code=503,
        content={
            "status": "failed",
            "message": "; ".join(f"{n}: {upstreams[n]['message']}" for n in failed),
            "upstreams": upstreams,
            "suggestions": [
                "Check OPENAI_API_KEY and OPENAI_BASE_URL",
                "If using mTLS, check CLIENT_CERT_PATH/CLIENT_KEY_PATH and CA_BUNDLE_PATH",
                "For extra upstreams, check UPSTREAM_<NAME>_BASE_URL and that the service is running",
            ],
        },
    )


@router.get("/")
async def root():
    return {
        "message": "llm-gateway-proxy",
        "status": "running",
        "endpoints": {
            "openai_chat_completions": "/v1/chat/completions",
            "models": "/v1/models",
            "claude_messages": "/messages",
            "claude_count_tokens": "/messages/count_tokens",
            "health": "/health",
            "test_connection": "/test-connection",
        },
    }
