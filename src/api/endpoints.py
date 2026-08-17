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
from src.core.model_manager import model_manager
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
    request_id = str(uuid.uuid4())

    if body.get("stream"):
        stream = openai_client.create_chat_completion_stream(body, request_id)
        return StreamingResponse(
            _sse_passthrough(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    response = await openai_client.create_chat_completion(body, request_id)
    return JSONResponse(response)


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
        openai_request = convert_claude_to_openai(request, model_manager)

        if await http_request.is_disconnected():
            raise HTTPException(status_code=499, detail="Client disconnected")

        if request.stream:
            openai_stream = openai_client.create_chat_completion_stream(openai_request, request_id)
            return StreamingResponse(
                convert_openai_streaming_to_claude(
                    openai_stream, request, logger, http_request, openai_client, request_id
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        openai_response = await openai_client.create_chat_completion(openai_request, request_id)
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
    }


@router.get("/test-connection")
async def test_connection(_: None = Depends(validate_api_key)):
    """Send a minimal request upstream to verify connectivity (including mTLS)."""
    try:
        response = await openai_client.create_chat_completion(
            {
                "model": config.small_model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 5,
            }
        )
        return {
            "status": "success",
            "model_used": config.small_model,
            "response_id": response.get("id", "unknown"),
        }
    except Exception as e:
        logger.error("Upstream connectivity test failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "message": str(e),
                "suggestions": [
                    "Check OPENAI_API_KEY and OPENAI_BASE_URL",
                    "If using mTLS, check CLIENT_CERT_PATH/CLIENT_KEY_PATH and CA_BUNDLE_PATH",
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
            "claude_messages": "/messages",
            "claude_count_tokens": "/messages/count_tokens",
            "health": "/health",
            "test_connection": "/test-connection",
        },
    }
