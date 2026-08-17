import logging

from src.core.config import config

_log_level = config.log_level.split()[0].upper()
if _log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    _log_level = "INFO"

logging.basicConfig(
    level=getattr(logging, _log_level),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("llm_gateway_proxy")

# Keep uvicorn's own loggers quieter than our application log level.
for uvicorn_logger in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    logging.getLogger(uvicorn_logger).setLevel(logging.WARNING)

# httpcore logs one DEBUG line per low-level socket/TLS/stream operation
# (e.g. "receive_response_body.failed exception=GeneratorExit()" whenever a
# streaming response is closed before being fully drained, which happens on
# every normal SSE request). That's noise, not a proxy error, so it's kept
# out even at LOG_LEVEL=DEBUG. httpx's own one-line-per-request summary log
# is left untouched since it's actually useful.
for quiet_logger in ("httpcore", "httpcore2", "hpack", "h11", "h2"):
    logging.getLogger(quiet_logger).setLevel(logging.WARNING)
