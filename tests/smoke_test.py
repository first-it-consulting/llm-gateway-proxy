"""Manual smoke test against a running llm-gateway-proxy instance.

Not a pytest suite — it exercises a live server end-to-end (including the
real upstream), so it needs valid credentials configured in .env.

Neither endpoint remaps model names, so PROXY_TEST_MODEL must be a model ID
your upstream actually serves (check GET /v1/models on the running proxy).

Usage:
    python start_proxy.py &
    PROXY_TEST_MODEL=your-model-id python tests/smoke_test.py
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


async def test_health():
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        print("health:", json.dumps(response.json(), indent=2))


async def test_list_models():
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{BASE_URL}/v1/models", headers=HEADERS)
        model_ids = [m["id"] for m in response.json().get("data", [])]
        print("\n/v1/models:", json.dumps(model_ids, indent=2))


async def test_claude_messages():
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{BASE_URL}/messages",
            headers=HEADERS,
            json={
                "model": TEST_MODEL,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
            },
        )
        print("\n/messages:", json.dumps(response.json(), indent=2))


async def test_claude_messages_streaming():
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/messages",
            headers=HEADERS,
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


async def test_openai_chat_completions():
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": TEST_MODEL,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
            },
        )
        print("\n/v1/chat/completions:", json.dumps(response.json(), indent=2))


async def test_count_tokens():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/messages/count_tokens",
            headers=HEADERS,
            json={
                "model": TEST_MODEL,
                "messages": [{"role": "user", "content": "This is a test message."}],
            },
        )
        print("\n/messages/count_tokens:", json.dumps(response.json(), indent=2))


async def main():
    print("Testing llm-gateway-proxy at", BASE_URL, "with model", TEST_MODEL)
    await test_health()
    await test_list_models()
    await test_count_tokens()
    await test_claude_messages()
    await test_claude_messages_streaming()
    await test_openai_chat_completions()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
