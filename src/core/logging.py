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
