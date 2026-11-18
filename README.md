# llm-gateway-proxy

A small FastAPI server that puts **one or more OpenAI-compatible upstreams** behind **two client-facing endpoints**:

- `POST /v1/chat/completions` — OpenAI Chat Completions format, forwarded as-is.
- `POST /messages` — Anthropic Messages format (what Claude Code and the Anthropic SDKs speak), translated to/from OpenAI format on the fly.

Both endpoints share the same upstream connection, including **mutual TLS (mTLS)** client-certificate authentication for upstreams that sit behind a corporate network. Streaming, tool/function calling, image inputs, and request cancellation are supported on both endpoints.

Optionally, **additional upstreams** (e.g. a local Ollama) can sit next to the default one; requests are routed by **model-name prefix** (`ollama/qwen3-coder:30b` → Ollama), so a single client endpoint — such as the one `ANTHROPIC_BASE_URL` points Claude Code at — reaches all of them.

```
                         ┌─────────────────────────┐                      ┌─▶ default upstream (OpenAI-compatible)
 OpenAI-style client ───▶│  /v1/chat/completions   │───┐   model-prefix   │   mTLS + bearer token, custom headers
                         └─────────────────────────┘   │   router         │   model "gpt-4o" (no known prefix)
                                                       ├──────────────────┤
                         ┌─────────────────────────┐   │                  │
 Claude Code / Anthropic▶│  /messages              │───┘                  └─▶ extra upstreams (optional)
 SDK client              │  (translated to OpenAI) │                          e.g. Ollama: model "ollama/qwen3:8b"
                         └─────────────────────────┘                          (prefix stripped, no corp credentials)
```

## Features

- **Two endpoints, one process.** No separate translation service to run alongside your OpenAI-compatible proxy.
- **Mutual TLS to the upstream.** Client certificate + key (or a combined PEM), plus an optional custom CA bundle — for upstreams reachable only from inside a corporate network.
- **Claude ⇄ OpenAI translation.** Full support for streaming, tool use, image inputs, and system prompts.
- **Multiple upstreams, routed by model prefix.** Add e.g. a local Ollama via `UPSTREAM_OLLAMA_BASE_URL`; `ollama/<model>` goes there, everything else to the default upstream. Extra upstreams never see the default upstream's client certificate or custom headers.
- **No model-name games.** Apart from stripping a route prefix, the `model` you send goes upstream unchanged — `GET /v1/models` lists what every upstream actually serves.
- **Request cancellation.** Upstream requests are cancelled when the client disconnects.
- **Custom header injection.** Add arbitrary headers to upstream requests via `CUSTOM_HEADER_*` env vars.
- **Optional client auth.** Gate access to the proxy itself with a shared `PROXY_API_KEY`.

## Quick Start (Docker)

```bash
cp .env.example .env
# edit .env: set OPENAI_API_KEY and OPENAI_BASE_URL at minimum

# if your upstream requires mTLS: point CERTS_DIR at the directory your
# real cert/key already live in (keeps them out of this repo entirely),
# and set CLIENT_CERT_PATH / CLIENT_KEY_PATH to /app/certs/<filename> --
# see the Mutual TLS section below.

docker compose up --build
```

The proxy listens on `http://localhost:8082` (or whatever `PORT` you set).

To run a published image instead of building it locally — multi-arch (`linux/amd64`, `linux/arm64`):

```bash
docker compose pull          # or: docker pull ghcr.io/first-it-consulting/llm-gateway-proxy:1.1.2
IMAGE_TAG=1.1.2 docker compose up -d
```

`IMAGE_TAG` defaults to `latest`; every `vX.Y.Z` tag also publishes `X.Y` and `latest`. See the note under **Releasing a new version** about logging in to pull.

## Quick Start (local Python)

Needs Python 3.10 or newer (the Docker image ships 3.12).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env

python start_proxy.py
```

> **Gotcha:** if your shell already exports `OPENAI_API_KEY` (e.g. for another CLI tool), it silently wins over `.env` — `python-dotenv` never overrides a variable that's already set in the environment. Symptom: requests fail with `401`/`502 Invalid API key` even though `.env` looks correct. Fix: `env -u OPENAI_API_KEY python start_proxy.py`, or rename the conflicting shell variable. (Not an issue with Docker — `env_file` doesn't inherit your shell's exports.)
>
> **Gotcha (mTLS):** if `.env`'s `CLIENT_CERT_PATH`/`CLIENT_KEY_PATH` are set to `/app/certs/...` (the Docker convention — see [Mutual TLS](#mutual-tls) below), running locally fails fast with `FileNotFoundError`, since that path only exists inside the container. Override both on the command line with the real path instead — a real shell env var always wins over `.env`, same mechanism as above:
>
> ```bash
> CLIENT_CERT_PATH=/real/path/to/client.cert CLIENT_KEY_PATH=/real/path/to/client.key python start_proxy.py
> ```

## Configuration

All configuration is via environment variables — see [.env.example](.env.example) for the full, commented list. The essentials:

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | Token sent upstream as `Authorization: Bearer <value>` | *(required)* |
| `OPENAI_BASE_URL` | Upstream base URL, including any path prefix (e.g. `/v1`) | `https://api.openai.com/v1` |
| `AZURE_API_VERSION` | Set only when the upstream is Azure OpenAI | unset |
| `PORT` / `HOST` | Where the proxy listens | `8082` / `0.0.0.0` |
| `PROXY_API_KEY` | Shared secret clients must send to use this proxy | unset (disabled) |
| `UPSTREAM_<NAME>_BASE_URL` | Declares an extra upstream, routed by model prefix (see below) | unset |

### Multiple upstreams (model-prefix routing)

The `OPENAI_*` settings define the **default** upstream. Additional OpenAI-compatible upstreams — e.g. a local Ollama — are declared per name:

```bash
UPSTREAM_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
# optional: UPSTREAM_OLLAMA_PREFIX (default "ollama/"), UPSTREAM_OLLAMA_API_KEY (default "not-needed")
```

A request for `ollama/qwen3-coder:30b` goes to Ollama as `qwen3-coder:30b`; anything without a known prefix goes to the default upstream unchanged. The longest matching prefix wins. On `/messages`, responses carry the model name exactly as the client sent it (prefix included); on `/v1/chat/completions` the upstream's response is passed through as-is, so its `model` field is the upstream's own (unprefixed) ID.

- `GET /v1/models` merges all upstreams, listing extra-upstream models with their prefix. An unreachable upstream is skipped (logged), not fatal.
- `GET /health` lists the configured upstreams; `GET /test-connection` checks each one.
- Extra upstreams use plain HTTPS/HTTP with default CA verification and **never** receive the default upstream's mTLS client certificate or `CUSTOM_HEADER_*` headers.
- Caveat: a default-upstream model whose ID happens to start with an extra upstream's prefix becomes unreachable — pick prefixes that can't collide.

### Mutual TLS

For upstreams that require a client certificate:

| Variable | Description |
|---|---|
| `CLIENT_CERT_PATH` | Client certificate. If `CLIENT_KEY_PATH` is unset, this file must contain both the certificate and the unencrypted private key (a combined PEM). |
| `CLIENT_KEY_PATH` | Client private key, when kept in a separate file from the certificate. |
| `CA_BUNDLE_PATH` | Custom CA bundle to verify the upstream's server certificate (e.g. an internal corporate CA). Omit to use the system/default CA store. |
| `CERTS_DIR` | Docker only. Host directory mounted read-only to `/app/certs`. Defaults to `./certs` (inside the repo, gitignored); point it at an external directory instead so cert files never need to be copied into the repo at all. |

`CLIENT_CERT_PATH`/`CLIENT_KEY_PATH`/`CA_BUNDLE_PATH` must resolve from wherever the process actually runs, which differs by how you start it:

- **`python start_proxy.py`** (no Docker): any path readable on this machine, e.g. an absolute path outside the repo.
- **`docker compose`**: the container only sees `CERTS_DIR` (or `./certs` by default) mounted at `/app/certs` — these must be `/app/certs/<filename>` regardless of where the files actually live on the host.

Prefer keeping real certs outside the repo (`CERTS_DIR` for Docker, an absolute path for local runs) over dropping them into `./certs/` — `.gitignore` and the repo's `pre-commit` hook stop them from being committed either way, but files that were never inside the working tree can't be `git add`ed by mistake in the first place.
| `SSL_VERIFY` | Set to `false` to disable upstream certificate verification. Local debugging only — never disable this in production. |

## Using it

Neither endpoint remaps model names — apart from stripping an extra upstream's route prefix, whatever `model` you send goes upstream unchanged, so it must be an ID that upstream actually serves:

```bash
curl http://localhost:8082/v1/models
```

### With Claude Code

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_MODEL=<a-model-id-from-/v1/models> claude
```

With extra upstreams configured, switch between them per session (`--model ollama/qwen3-coder:30b`) or inside a session with `/model`.

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

Other endpoints: `GET /v1/models` (or `/models`, lists what all upstreams serve, extra-upstream models prefixed), `GET /health` (liveness + config summary incl. upstreams), `GET /test-connection` (verifies connectivity to every upstream, mTLS included), `POST /messages/count_tokens` (rough estimate, ~4 chars/token).

## Testing

Unit tests (no network, upstreams are faked):

```bash
pip install -e ".[dev]"
pytest
```

The same suite runs in CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) on Python 3.10 and 3.12 for every pull request and every push to `main`; 3.12 is what the Docker image ships.

`tests/smoke_test_check.py` is a manual end-to-end script — it talks to a running instance (and your real upstream), so it needs a valid `.env` and a real model ID. It is named `*_check.py` rather than `test_*.py` on purpose, so `pytest` never collects it:

```bash
python start_proxy.py &
PROXY_TEST_MODEL=<a-model-id-from-/v1/models> python tests/smoke_test_check.py
```

## Deployment

- **Docker** (recommended): `docker compose up -d --build`.
- **systemd**: see [deploy/llm-gateway-proxy.service.example](deploy/llm-gateway-proxy.service.example) for running the proxy directly on a Linux host.

## Releasing a new version

Versioning follows [SemVer](https://semver.org/); changes are recorded in [CHANGELOG.md](CHANGELOG.md) ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

`main` is a protected branch: changes arrive through a pull request, and the `test` check has to pass before it can merge. So prepare a release on a branch rather than committing on `main` directly.

1. Move the relevant entries from `## [Unreleased]` into a new `## [X.Y.Z] - YYYY-MM-DD` section.
2. Bump `version` in [pyproject.toml](pyproject.toml) to match, and update the compare links at the bottom of [CHANGELOG.md](CHANGELOG.md).
3. Open a pull request, wait for `test` to go green, and merge it.
4. Tag the merge commit on `main` and push the tag:

   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

5. Pushing the tag triggers [.github/workflows/release.yml](.github/workflows/release.yml), which builds and pushes the multi-arch container image to GHCR and creates a GitHub Release from that version's changelog entry automatically — no need to write release notes twice.

The image job authenticates with the run's built-in `GITHUB_TOKEN`, so there is no PAT to store or rotate, and pushing the tag is all it takes — nothing waits on a manual approval.

The image pulls anonymously. The ghcr package is created automatically by the first publish but starts out **private**, so until someone flips it to Public (repo owner → Packages → `llm-gateway-proxy` → Package settings → Change visibility), pulls need a token:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <you> --password-stdin
```

See the header of `.github/workflows/release.yml` for a one-line check of whether it is public yet.

## Project layout

```
src/
├── api/endpoints.py          FastAPI routes for both endpoint families
├── conversion/                Claude <-> OpenAI request/response translation
├── core/
│   ├── client.py               OpenAI SDK client, configured for mTLS
│   ├── config.py                Environment-variable configuration
│   ├── upstreams.py             Extra upstreams (UPSTREAM_<NAME>_*) and model-prefix router
│   └── logging.py
└── models/claude.py           Pydantic models for the Claude Messages API
tests/
├── test_upstream_routing.py   Offline unit tests (routing, config, endpoints)
└── smoke_test_check.py        Manual end-to-end check against a running proxy
```

## Acknowledgments

The Claude ⇄ OpenAI translation layer builds on [claude-code-proxy](https://github.com/fuergaosi233/claude-code-proxy) (MIT licensed). The mTLS reverse-proxy design builds on an internal corporate-network proxy.

## License

MIT — see [LICENSE](LICENSE).
