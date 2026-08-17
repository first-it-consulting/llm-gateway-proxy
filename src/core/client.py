import asyncio
import json
import ssl
from typing import Any, AsyncGenerator, Dict, Optional, Tuple, Union

from fastapi import HTTPException
from openai import AsyncAzureOpenAI, AsyncOpenAI, DefaultAsyncHttpxClient
from openai._exceptions import APIError, AuthenticationError, BadRequestError, RateLimitError

# `request` dicts passed into this client are splatted as **kwargs into the
# OpenAI SDK's `.create()` call. A few of its accepted parameter names
# control the outgoing HTTP request itself (headers/query/timeout) rather
# than the chat-completion payload. `/v1/chat/completions` forwards a raw,
# client-supplied JSON body here, so those names must be stripped — otherwise
# a client could set extra_headers to override headers we inject server-side
# (like the upstream bearer token) or extra_query/timeout to tamper with the
# request in other ways.
_RESERVED_SDK_KWARGS = frozenset({"extra_headers", "extra_query", "extra_body", "timeout"})


def _sanitize_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Drop SDK transport-control kwargs from an untrusted request dict."""
    return {k: v for k, v in request.items() if k not in _RESERVED_SDK_KWARGS}


def build_ssl_context(
    client_cert: Optional[Union[str, Tuple[str, str]]], verify: Any
) -> Union[bool, ssl.SSLContext]:
    """Build an SSLContext covering both the client cert and CA verification.

    httpx's `cert=` and a string `verify=<ca bundle path>` cannot always be
    combined directly (the exact restriction has varied across httpx
    versions), so a client certificate and a custom CA are always merged
    into one explicit SSLContext instead.
    """
    if not client_cert and not isinstance(verify, str):
        return verify  # plain bool: use httpx's own default CA handling

    if verify is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context(cafile=verify if isinstance(verify, str) else None)

    if isinstance(client_cert, tuple):
        context.load_cert_chain(certfile=client_cert[0], keyfile=client_cert[1])
    elif client_cert:
        context.load_cert_chain(certfile=client_cert)

    return context


class OpenAIClient:
    """Async OpenAI-compatible client with mTLS and cancellation support."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int = 90,
        api_version: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        client_cert: Optional[Any] = None,
        verify: Any = True,
    ):
        self.custom_headers = custom_headers or {}

        # A custom httpx client is how mTLS client certificates and a custom
        # CA bundle get plumbed into the OpenAI SDK's HTTP transport.
        http_client = DefaultAsyncHttpxClient(verify=build_ssl_context(client_cert, verify))

        if api_version:
            self.client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=base_url,
                api_version=api_version,
                timeout=timeout,
                default_headers=self.custom_headers,
                http_client=http_client,
            )
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                default_headers=self.custom_headers,
                http_client=http_client,
            )
        self.active_requests: Dict[str, asyncio.Event] = {}

    async def create_chat_completion(
        self, request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send a chat completion to the upstream API, with cancellation support."""
        request = _sanitize_request(request)
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        try:
            completion_task = asyncio.create_task(self.client.chat.completions.create(**request))

            if request_id:
                cancel_task = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    [completion_task, cancel_task], return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if cancel_task in done:
                    completion_task.cancel()
                    raise HTTPException(status_code=499, detail="Request cancelled by client")

                completion = await completion_task
            else:
                completion = await completion_task

            return completion.model_dump()

        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=self.classify_openai_error(str(e)))
        except RateLimitError as e:
            raise HTTPException(status_code=429, detail=self.classify_openai_error(str(e)))
        except BadRequestError as e:
            raise HTTPException(status_code=400, detail=self.classify_openai_error(str(e)))
        except APIError as e:
            status_code = getattr(e, "status_code", 500)
            raise HTTPException(status_code=status_code, detail=self.classify_openai_error(str(e)))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

        finally:
            if request_id:
                self.active_requests.pop(request_id, None)

    async def create_chat_completion_stream(
        self, request: Dict[str, Any], request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion from the upstream API as `data: <json>` lines."""
        request = _sanitize_request(request)
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        try:
            request["stream"] = True
            request.setdefault("stream_options", {})["include_usage"] = True

            streaming_completion = await self.client.chat.completions.create(**request)

            async for chunk in streaming_completion:
                if request_id and self.active_requests.get(request_id, asyncio.Event()).is_set():
                    raise HTTPException(status_code=499, detail="Request cancelled by client")

                chunk_json = json.dumps(chunk.model_dump(), ensure_ascii=False)
                yield f"data: {chunk_json}"

            yield "data: [DONE]"

        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=self.classify_openai_error(str(e)))
        except RateLimitError as e:
            raise HTTPException(status_code=429, detail=self.classify_openai_error(str(e)))
        except BadRequestError as e:
            raise HTTPException(status_code=400, detail=self.classify_openai_error(str(e)))
        except APIError as e:
            status_code = getattr(e, "status_code", 500)
            raise HTTPException(status_code=status_code, detail=self.classify_openai_error(str(e)))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

        finally:
            if request_id:
                self.active_requests.pop(request_id, None)

    def classify_openai_error(self, error_detail: Any) -> str:
        """Provide clearer guidance for common upstream error conditions."""
        error_str = str(error_detail).lower()

        if "unsupported_country_region_territory" in error_str or "territory not supported" in error_str:
            return "The upstream API is not available in your region."
        if "invalid_api_key" in error_str or "unauthorized" in error_str:
            return "Invalid API key. Check your OPENAI_API_KEY configuration."
        if "rate_limit" in error_str or "quota" in error_str:
            return "Rate limit exceeded. Please wait and try again, or upgrade your plan."
        if "model" in error_str and ("not found" in error_str or "does not exist" in error_str):
            return "Model not found. Check your BIG_MODEL/MIDDLE_MODEL/SMALL_MODEL configuration."
        if "billing" in error_str or "payment" in error_str:
            return "Billing issue on the upstream account."
        return str(error_detail)

    def cancel_request(self, request_id: str) -> bool:
        """Cancel an active request by request_id."""
        if request_id in self.active_requests:
            self.active_requests[request_id].set()
            return True
        return False
