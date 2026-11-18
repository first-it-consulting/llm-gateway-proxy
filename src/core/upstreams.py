"""Additional upstreams, selected per request by model-name prefix.

The upstream configured via OPENAI_BASE_URL / OPENAI_API_KEY (plus mTLS and
CUSTOM_HEADER_*) remains the *default* upstream. Extra upstreams are declared
with environment variables:

    UPSTREAM_<NAME>_BASE_URL   required -- declares the upstream
    UPSTREAM_<NAME>_PREFIX     model prefix routed here (default: "<name>/")
    UPSTREAM_<NAME>_API_KEY    Bearer token (default: "not-needed")

A request whose model starts with an upstream's prefix goes to that upstream,
with the prefix stripped (e.g. "ollama/qwen3:8b" -> "qwen3:8b"). Everything
else goes to the default upstream unchanged. Extra upstreams get neither the
default upstream's client certificate nor its CUSTOM_HEADER_* headers, so
corporate credentials never leak to them.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

_ENV_PREFIX = "UPSTREAM_"
_BASE_URL_SUFFIX = "_BASE_URL"
DEFAULT_UPSTREAM_NAME = "default"


@dataclass(frozen=True)
class UpstreamSpec:
    name: str
    prefix: str
    base_url: str
    api_key: str


def parse_extra_upstreams(environ: Mapping[str, str]) -> List[UpstreamSpec]:
    """Parse UPSTREAM_<NAME>_* variables into upstream specs (sorted by name)."""
    specs = []
    for key, base_url in environ.items():
        if not (key.startswith(_ENV_PREFIX) and key.endswith(_BASE_URL_SUFFIX)):
            continue
        raw_name = key[len(_ENV_PREFIX):-len(_BASE_URL_SUFFIX)]
        if not raw_name:
            continue
        base_url = base_url.strip()
        if not base_url:
            raise ValueError(f"{key} is set but empty")

        name = raw_name.lower()
        if name == DEFAULT_UPSTREAM_NAME:
            raise ValueError(
                f"{key}: the name '{DEFAULT_UPSTREAM_NAME}' is reserved for the "
                "OPENAI_BASE_URL upstream"
            )
        prefix = environ.get(f"{_ENV_PREFIX}{raw_name}_PREFIX", f"{name}/").strip()
        if not prefix:
            raise ValueError(
                f"{_ENV_PREFIX}{raw_name}_PREFIX must not be empty or blank -- "
                "unprefixed models always go to the default upstream"
            )
        # Unset means the upstream needs no key (e.g. Ollama). Set-but-blank is
        # almost certainly a typo, so fail at startup instead of 401ing later.
        api_key_var = f"{_ENV_PREFIX}{raw_name}_API_KEY"
        api_key = environ.get(api_key_var)
        if api_key is None:
            api_key = "not-needed"
        else:
            api_key = api_key.strip()
            if not api_key:
                raise ValueError(f"{api_key_var} is set but empty -- unset it if no key is needed")
        specs.append(UpstreamSpec(name=name, prefix=prefix, base_url=base_url, api_key=api_key))

    specs.sort(key=lambda s: s.name)
    seen: Dict[str, str] = {}
    for spec in specs:
        if spec.prefix in seen:
            raise ValueError(
                f"Upstreams '{seen[spec.prefix]}' and '{spec.name}' share the prefix '{spec.prefix}'"
            )
        seen[spec.prefix] = spec.name
    return specs


@dataclass(frozen=True)
class Route:
    name: str
    prefix: str  # "" for the default upstream
    client: Any  # OpenAIClient


class UnroutableModelError(ValueError):
    """The model is only a route prefix, with no model name after it."""


class UpstreamRouter:
    """Pick an upstream client for a requested model (longest prefix wins)."""

    def __init__(self, default_client: Any, extra_routes: Optional[List[Route]] = None):
        self.default = Route(DEFAULT_UPSTREAM_NAME, "", default_client)
        self.extra = sorted(extra_routes or [], key=lambda r: len(r.prefix), reverse=True)

    @property
    def routes(self) -> List[Route]:
        return [self.default] + sorted(self.extra, key=lambda r: r.name)

    def resolve(self, model: str) -> Tuple[Route, str]:
        """Return (route, model name to send upstream)."""
        for route in self.extra:
            if model.startswith(route.prefix):
                upstream_model = model[len(route.prefix):]
                if not upstream_model:
                    raise UnroutableModelError(
                        f"Model '{model}' names only the '{route.name}' upstream prefix, no model"
                    )
                return route, upstream_model
        return self.default, model
