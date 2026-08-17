"""llm-gateway-proxy

A single FastAPI service that exposes an OpenAI-compatible endpoint and a
Claude-compatible endpoint, forwarding both to one OpenAI-compatible upstream
over mutual TLS.
"""

from dotenv import load_dotenv

load_dotenv()

__version__ = "1.0.0"
