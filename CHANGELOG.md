# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

### Fixed

- `/test-connection` now requires `PROXY_API_KEY` like every other functional endpoint (previously unauthenticated despite making a real upstream call).
- `PROXY_API_KEY` comparison uses a constant-time check (`hmac.compare_digest`) to avoid a timing side-channel.
- `/v1/chat/completions` strips SDK transport-control fields (`extra_headers`, `extra_query`, `extra_body`, `timeout`) from client-supplied request bodies before forwarding, so a client can't override headers injected server-side (like the upstream bearer token).
- Streaming responses close the upstream connection explicitly instead of leaving it for garbage collection; `httpcore`'s internal trace logging is quieted so `LOG_LEVEL=DEBUG` doesn't produce confusing (but harmless) `GeneratorExit` log lines.

### Removed

- All Ollama-compatible shim endpoints (`/api/tags`, `/api/show`, `/api/version`) from the original corporate proxy — out of scope for this service.

<!--
Once this repo has a GitHub remote, replace this comment with compare-view
links for each version, e.g.:
[Unreleased]: https://github.com/<owner>/<repo>/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/<owner>/<repo>/releases/tag/v1.0.0
-->
