import sys

import uvicorn
from fastapi import FastAPI

from src.api.endpoints import router as api_router
from src.core.config import config

app = FastAPI(title="llm-gateway-proxy", version="1.0.0")
app.include_router(api_router)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(__doc__ or "llm-gateway-proxy")
        print("Usage: python start_proxy.py")
        print("See .env.example for all configuration options.")
        sys.exit(0)

    print("llm-gateway-proxy")
    print(f"  Upstream:            {config.openai_base_url}")
    print(f"  mTLS client cert:    {'enabled' if config.get_client_cert() else 'disabled'}")
    print(f"  Listening on:        {config.host}:{config.port}")
    print(f"  Client key required: {bool(config.proxy_api_key)}")

    log_level = config.log_level.split()[0].lower()
    if log_level not in ("debug", "info", "warning", "error", "critical"):
        log_level = "info"

    uvicorn.run("src.main:app", host=config.host, port=config.port, log_level=log_level, reload=False)


if __name__ == "__main__":
    main()
