"""Manual smoke test against a running llm-gateway-proxy instance.

Not a pytest suite — it exercises a live server end-to-end (including the
real upstream), so it needs valid credentials configured in .env.

Usage:
    python start_proxy.py &
    python tests/smoke_test.py
"""

import asyncio
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("PROXY_TEST_URL", "http://localhost:8082")
HEADERS = {}
if os.environ.get("PROXY_API_KEY"):
    HEADERS["x-api-key"] = os.environ["PROXY_API_KEY"]


async def test_health():
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        print("health:", json.dumps(response.json(), indent=2))


async def test_claude_messages():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/messages",
            headers=HEADERS,
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
            },
        )
        print("\n/messages:", json.dumps(response.json(), indent=2))


async def test_claude_messages_streaming():
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/messages",
            headers=HEADERS,
            json={
                "model": "claude-3-5-haiku-20241022",
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
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "gpt-4o-mini",
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
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "This is a test message."}],
            },
        )
        print("\n/messages/count_tokens:", json.dumps(response.json(), indent=2))


async def main():
    print("Testing llm-gateway-proxy at", BASE_URL)
    await test_health()
    await test_count_tokens()
    await test_claude_messages()
    await test_claude_messages_streaming()
    await test_openai_chat_completions()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
