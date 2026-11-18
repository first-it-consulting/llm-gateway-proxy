"""Manual smoke test against a running llm-gateway-proxy instance.

Not a pytest suite -- it exercises a live server end-to-end (including the
real upstream), so it needs valid credentials configured in .env. The file is
named *_check.py so pytest does not collect it: its async functions are meant
to be run once in sequence by main(), and pytest has no live server to talk to.

Neither endpoint remaps model names, so PROXY_TEST_MODEL must be a model ID
your upstream actually serves (check GET /v1/models on the running proxy).

Usage:
    python start_proxy.py &
    PROXY_TEST_MODEL=your-model-id python tests/smoke_test_check.py
"""

import asyncio
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("PROXY_TEST_URL", "http://localhost:8082")
TEST_MODEL = os.environ.get("PROXY_TEST_MODEL", "gpt-4o-mini")
HEADERS = {}
if os.environ.get("PROXY_API_KEY"):
    HEADERS["x-api-key"] = os.environ["PROXY_API_KEY"]


def _client(timeout=30):
    return httpx.AsyncClient(timeout=timeout, headers=HEADERS)


async def check_health():
    async with _client() as client:
        response = await client.get(f"{BASE_URL}/health")
        print("health:", json.dumps(response.json(), indent=2))


async def check_list_models():
    async with _client() as client:
        response = await client.get(f"{BASE_URL}/v1/models")
        model_ids = [m["id"] for m in response.json().get("data", [])]
        print("\n/v1/models:", json.dumps(model_ids, indent=2))


async def check_claude_messages():
    async with _client(60) as client:
        response = await client.post(
            f"{BASE_URL}/messages",
            json={
                "model": TEST_MODEL,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
            },
        )
        print("\n/messages:", json.dumps(response.json(), indent=2))


async def check_claude_messages_streaming():
    async with _client(60) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/messages",
            json={
                "model": TEST_MODEL,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Count from 1 to 5."}],
                "stream": True,
            },
        ) as response:
            print("\n/messages (streaming):")
            async for line in response.aiter_lines():
                if line.strip():
                    print(" ", line)


async def check_openai_chat_completions():
    async with _client(60) as client:
        response = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": TEST_MODEL,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
            },
        )
        print("\n/v1/chat/completions:", json.dumps(response.json(), indent=2))


async def check_count_tokens():
    async with _client() as client:
        response = await client.post(
            f"{BASE_URL}/messages/count_tokens",
            json={
                "model": TEST_MODEL,
                "messages": [{"role": "user", "content": "This is a test message."}],
            },
        )
        print("\n/messages/count_tokens:", json.dumps(response.json(), indent=2))


async def main():
    print("Testing llm-gateway-proxy at", BASE_URL, "with model", TEST_MODEL)
    await check_health()
    await check_list_models()
    await check_count_tokens()
    await check_claude_messages()
    await check_claude_messages_streaming()
    await check_openai_chat_completions()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
