# llm-gateway-proxy

A small FastAPI server that puts **one OpenAI-compatible upstream** behind **two client-facing endpoints**:

- `POST /v1/chat/completions` — OpenAI Chat Completions format, forwarded as-is.
- `POST /messages` — Anthropic Messages format (what Claude Code and the Anthropic SDKs speak), translated to/from OpenAI format on the fly.

Both endpoints share the same upstream connection, including **mutual TLS (mTLS)** client-certificate authentication for upstreams that sit behind a corporate network. Streaming, tool/function calling, image inputs, and request cancellation are supported on both endpoints.

```
                         ┌─────────────────────────┐
 OpenAI-style client ───▶│  /v1/chat/completions   │───┐
                         └─────────────────────────┘   │
                                                        ├──▶  upstream OpenAI-compatible API
                         ┌─────────────────────────┐   │      (mTLS + bearer token)
 Claude Code / Anthropic▶│  /messages              │───┘
 SDK client              │  (translated to OpenAI) │
                         └─────────────────────────┘
```

## Features

- **Two endpoints, one process.** No separate translation service to run alongside your OpenAI-compatible proxy.
- **Mutual TLS to the upstream.** Client certificate + key (or a combined PEM), plus an optional custom CA bundle — for upstreams reachable only from inside a corporate network.
- **Claude ⇄ OpenAI translation.** Full support for streaming, tool use, image inputs, and system prompts.
- **No model-name games.** Both endpoints forward the `model` field you send straight to the upstream — `GET /v1/models` tells you what's actually available.
- **Request cancellation.** Upstream requests are cancelled when the client disconnects.
- **Custom header injection.** Add arbitrary headers to upstream requests via `CUSTOM_HEADER_*` env vars.
- **Optional client auth.** Gate access to the proxy itself with a shared `PROXY_API_KEY`.

## Quick Start (Docker)

```bash
cp .env.example .env
# edit .env: set OPENAI_API_KEY and OPENAI_BASE_URL at minimum

# if your upstream requires mTLS, drop your cert/key into ./certs
# and point CLIENT_CERT_PATH / CLIENT_KEY_PATH at them in .env

docker compose up --build
```

The proxy listens on `http://localhost:8082` (or whatever `PORT` you set).

## Quick Start (local Python)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env

python start_proxy.py
```

## Configuration

All configuration is via environment variables — see [.env.example](.env.example) for the full, commented list. The essentials:

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | Token sent upstream as `Authorization: Bearer <value>` | *(required)* |
| `OPENAI_BASE_URL` | Upstream base URL, including any path prefix (e.g. `/v1`) | `https://api.openai.com/v1` |
| `AZURE_API_VERSION` | Set only when the upstream is Azure OpenAI | unset |
| `PORT` / `HOST` | Where the proxy listens | `8082` / `0.0.0.0` |
| `PROXY_API_KEY` | Shared secret clients must send to use this proxy | unset (disabled) |

### Mutual TLS

For upstreams that require a client certificate:

| Variable | Description |
|---|---|
| `CLIENT_CERT_PATH` | Client certificate. If `CLIENT_KEY_PATH` is unset, this file must contain both the certificate and the unencrypted private key (a combined PEM). |
| `CLIENT_KEY_PATH` | Client private key, when kept in a separate file from the certificate. |
| `CA_BUNDLE_PATH` | Custom CA bundle to verify the upstream's server certificate (e.g. an internal corporate CA). Omit to use the system/default CA store. |
| `SSL_VERIFY` | Set to `false` to disable upstream certificate verification. Local debugging only — never disable this in production. |

## Using it

Neither endpoint remaps model names — whatever `model` you send goes upstream unchanged, so it must be an ID the upstream actually serves:

```bash
curl http://localhost:8082/v1/models
```

### With Claude Code

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_MODEL=<a-model-id-from-/v1/models> claude
```

Claude Code defaults to Anthropic model names (`claude-3-5-sonnet-...`), which this proxy won't recognize — point `ANTHROPIC_MODEL` (and `ANTHROPIC_SMALL_FAST_MODEL`, if used) at a real upstream model ID.

### With an OpenAI-compatible client

Point the client's base URL at `http://localhost:8082/v1` (most OpenAI SDKs append `/chat/completions` themselves).

### Directly with curl

```bash
# OpenAI-compatible endpoint
curl http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "<a-model-id-from-/v1/models>", "messages": [{"role": "user", "content": "Hello"}]}'

# Claude-compatible endpoint
curl http://localhost:8082/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "<a-model-id-from-/v1/models>", "max_tokens": 100, "messages": [{"role": "user", "content": "Hello"}]}'
```

Other endpoints: `GET /v1/models` (or `/models`, lists what the upstream serves), `GET /health` (liveness + config summary), `GET /test-connection` (verifies upstream connectivity, mTLS included), `POST /messages/count_tokens` (rough estimate, ~4 chars/token).

## Testing

`tests/smoke_test.py` is a manual end-to-end script — it talks to a running instance (and your real upstream), so it needs a valid `.env` and a real model ID:

```bash
python start_proxy.py &
PROXY_TEST_MODEL=<a-model-id-from-/v1/models> python tests/smoke_test.py
```

## Deployment

- **Docker** (recommended): `docker compose up -d --build`.
- **systemd**: see [deploy/llm-gateway-proxy.service.example](deploy/llm-gateway-proxy.service.example) for running the proxy directly on a Linux host.

## Project layout

```
src/
├── api/endpoints.py          FastAPI routes for both endpoint families
├── conversion/                Claude <-> OpenAI request/response translation
├── core/
│   ├── client.py               OpenAI SDK client, configured for mTLS
│   ├── config.py                Environment-variable configuration
│   └── logging.py
└── models/claude.py           Pydantic models for the Claude Messages API
```

## Acknowledgments

The Claude ⇄ OpenAI translation layer builds on [claude-code-proxy](https://github.com/fuergaosi233/claude-code-proxy) (MIT licensed). The mTLS reverse-proxy design builds on an internal corporate-network proxy.

## License

MIT — see [LICENSE](LICENSE).
