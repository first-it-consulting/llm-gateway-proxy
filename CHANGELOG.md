# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Published container images on GHCR: pushing a `vX.Y.Z` tag now builds `ghcr.io/first-it-consulting/llm-gateway-proxy` for `linux/amd64` and `linux/arm64` and pushes `X.Y.Z`, `X.Y` and `latest`. The package is public, so no login is needed to pull. `docker-compose.yml` names that image, so `docker compose pull` works out of the box. Needs two repo secrets — `GHCR_USERNAME` and `GHCR_TOKEN` (a fine-grained PAT with package write access); until they exist the image job fails and only the GitHub Release is created.

### Fixed

- A bare `pytest` in the repo is green again. The manual smoke script lived in `tests/` as `test_*.py`, so pytest collected its six `async def` steps and reported them as failures (`async def functions are not natively supported`) even though the script itself was fine. Renamed to `tests/smoke_test_check.py`, which pytest ignores by convention, and CI now runs the whole `pytest` suite instead of one named file.

### Changed

- **Breaking:** the minimum supported Python is now 3.10 (`requires-python`). The floor was 3.9, but no current GitHub runner can provision 3.9 — the `actions/python-versions` manifest has no 3.9 build for Ubuntu 24.04 or later — so nothing was ever verifying it. The CI matrix and the declared floor now agree.

## [1.1.1] - 2026-11-18

### Fixed

- `UPSTREAM_<NAME>_PREFIX` with upper-case characters is now rejected at startup. Prefixes are matched against requested model names case-sensitively, so a mixed-case prefix would never match and its models would silently go to the default (corporate) upstream instead of the intended one.

### Added

- Continuous integration: `test` runs the unit suite on Python 3.10 and 3.12 for every pull request and every push to `main`, and is a required check on the now-protected `main` branch.

## [1.1.0] - 2026-11-18

### Added

- Multiple upstreams with model-prefix routing: `UPSTREAM_<NAME>_BASE_URL` (plus optional `_PREFIX`, `_API_KEY`) declares an extra OpenAI-compatible upstream such as a local Ollama. Requests for `<prefix><model>` go there with the prefix stripped; everything else goes to the default `OPENAI_BASE_URL` upstream unchanged. Extra upstreams never receive the default upstream's mTLS client certificate or `CUSTOM_HEADER_*` headers.
- `GET /v1/models` merges models from all upstreams (prefixed); an unreachable upstream is skipped rather than failing the request.
- `GET /health` lists configured upstreams; `GET /test-connection` reports per-upstream status.
- `tests/test_upstream_routing.py`: offline unit tests for config parsing, routing and endpoints.

## [1.0.0] - 2026-08-17

Initial release. Combines [claude-code-proxy](https://github.com/fuergaosi233/claude-code-proxy)'s Claude↔OpenAI translation layer with an internal corporate mTLS reverse proxy into a single service.

### Added

- `POST /v1/chat/completions` — OpenAI Chat Completions format, forwarded to the upstream as-is.
- `POST /messages` (+ `/v1/messages` alias) — Anthropic Messages format, translated to/from OpenAI format, with streaming, tool-use, and image-input support.
- `GET /v1/models` (+ `/models` alias) — lists models actually available on the upstream.
- Mutual TLS to the upstream: `CLIENT_CERT_PATH`/`CLIENT_KEY_PATH` (or a combined PEM), optional `CA_BUNDLE_PATH`, `SSL_VERIFY` toggle.
- `PROXY_API_KEY` — optional shared secret gating access to the proxy itself.
- `CUSTOM_HEADER_*` — inject arbitrary headers into upstream requests.
- `Dockerfile` and `docker-compose.yml`, with `CERTS_DIR` to mount an external certificate directory rather than copying certs into the repo.
- systemd unit example for bare-metal deployment.
- A `pre-commit` git hook blocking commits of certificate/key material or `.env`.

### Changed

- Both endpoints forward the requested `model` to the upstream unchanged — no Claude-tier-to-model mapping. Check `GET /v1/models` for what's actually available.
- `/messages` forwards the client's requested `max_tokens` unchanged — no `MAX_TOKENS_LIMIT`/`MIN_TOKENS_LIMIT` clamping. Models vary too widely in real capacity for a fixed global cap to make sense; let the upstream reject an out-of-range value if it needs to.

### Fixed

- `/test-connection` now requires `PROXY_API_KEY` like every other functional endpoint (previously unauthenticated despite making a real upstream call).
- `PROXY_API_KEY` comparison uses a constant-time check (`hmac.compare_digest`) to avoid a timing side-channel.
- `/v1/chat/completions` strips SDK transport-control fields (`extra_headers`, `extra_query`, `extra_body`, `timeout`) from client-supplied request bodies before forwarding, so a client can't override headers injected server-side (like the upstream bearer token).
- Streaming responses close the upstream connection explicitly instead of leaving it for garbage collection; `httpcore`'s internal trace logging is quieted so `LOG_LEVEL=DEBUG` doesn't produce confusing (but harmless) `GeneratorExit` log lines.

### Removed

- All Ollama-compatible shim endpoints (`/api/tags`, `/api/show`, `/api/version`) from the original corporate proxy — out of scope for this service.

[Unreleased]: https://github.com/first-it-consulting/llm-gateway-proxy/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/first-it-consulting/llm-gateway-proxy/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/first-it-consulting/llm-gateway-proxy/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/first-it-consulting/llm-gateway-proxy/releases/tag/v1.0.0
