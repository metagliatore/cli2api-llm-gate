# Project: cli2api-ai (Multi-Provider AI HTTP Gateway)
**Project Architecture & Technical Memory (MEMORY.md)**  
*Last updated: October 2026*  
*Supported Providers: Google Antigravity (`agy`) and GitHub Copilot / GitHub Models*

---

## 1. Project Overview

**cli2api-ai** is a production-ready containerized microservice that exposes AI models from **Google Antigravity (`agy`)** and **GitHub Copilot / GitHub Models** via a unified, RESTful, and OpenAI-compatible HTTP API (`/v1/chat/completions`).

### Key Goals & Features
- **Multi-Provider Architecture**:
  - `antigravity`: Google Antigravity using the `agy` CLI binary authenticated on the host (Gemini 3.8 Flash, Gemini 3.1 Pro, Claude Sonnet 4.6 Thinking, Claude Opus 4.6 Thinking, GPT-OSS 120B).
  - `copilot`: GitHub Models (Free Tier on Azure AI Inference) or GitHub Copilot API.
- **Zero External Credential Management for Clients**:
  - GitHub tokens are extracted from the host mount of `$HOME/.config/gh` via `gh auth login`.
  - Antigravity sessions are inherited from the host mount of `$HOME/.gemini`.
  - External clients (Home Assistant, n8n, Open WebUI, Continue.dev, curl, mobile apps) use internal Bearer tokens configured in `.env` (`API_KEYS`).
- **Dual Consumption Modes**:
  - *Asynchronous with Redis Queue (RQ)*: for long-running batch prompts or image analysis (`/ask`, `/ask-image`, `/result/{job_id}`).
  - *OpenAI-Compatible & Agent Harness Gateway*: drop-in replacement for OpenAI/Ollama clients and coding agents (Pi Coding Agent, Aider, Open WebUI, Continue.dev, LangChain, AutoGen) with:
    - Real-time Server-Sent Events (SSE `stream: true`).
    - Tool / Function Calling (`tools`, `tool_choice`, `tool_calls`).
    - Multi-turn conversation support (`system`, `user`, `assistant`, `tool` roles).
    - Standard model discovery (`GET /v1/models` and `GET /v1/models/{model_id}`).
- **Production-Grade VPS Security**:
  - Caddy reverse proxy with automated TLS/HTTPS certificates (ports `80`/`443`).
  - Internal application ports (`8000` for FastAPI, `6379` for Redis) isolated within the Docker `backend` network.
  - Per-token rate limiting backed by Redis sliding minute buckets (`RATE_LIMIT_RPM`) with fail-open resilience.
- **100% Backward-Compatible Model Aliasing**:
  - If a client requests Copilot/OpenAI models (e.g. `gpt-4o-mini`, `gpt-4o`) while the active provider is `antigravity`, the system transparently aliases the request to the default Antigravity model (`gemini-3.8-flash-high`) without breaking clients.

---

## 2. System Architecture

```
                                INTERNET / CLIENTS
                                        │
                         (HTTPS: 443 / HTTP: 80 - Automated TLS)
                                        ▼
                               ┌──────────────────┐
                               │  Caddy Container │ (Caddy 2-alpine)
                               │ (Reverse Proxy)  │ - Security headers
                               └────────┬─────────┘
                                        │ Docker internal network ('backend')
                                        ▼
                               ┌──────────────────┐
                               │  FastAPI (api)   │ Port :8000 internal (not bound to host)
                               │                  │ - Multi-Bearer token authentication (API_KEYS)
                               │                  │ - Redis rate limiter (RATE_LIMIT_RPM)
                               │                  │ - Dynamic provider routing (env or per-request)
                               └────┬────────┬────┘
                                    │        │
                       Enqueue jobs │        │ Rate-limit counter / fetch status
                                    ▼        ▼
                               ┌──────────────────┐
                               │ Redis Container  │ (Redis 7-alpine)
                               │ (Internal only)  │ Queues: 'copilot', 'antigravity', 'default'
                               └────────▲─────────┘
                                        │
                                        │ Dequeue & execute tasks
                               ┌────────┴─────────┐
                               │ RQ Worker        │ Scalable horizontally (--scale worker=N)
                               │ (worker.py)      │ Mounts ~/.config/gh and ~/.gemini
                               └────────┬─────────┘
                                        │
                      ┌─────────────────┴─────────────────┐
                      │                                   │
                [PROVIDER=copilot]              [PROVIDER=antigravity]
                      │                                   │
                      ▼                                   ▼
             OpenAI Python SDK                    agy CLI Subprocess
             (GitHub Models / Copilot API)        (--output-format json)
                      │                                   │
             • gpt-4o-mini ($0)                   • gemini-3.8-flash-high (1M context)
             • gpt-4o                             • claude-sonnet-4-6 (Thinking)
             • meta-llama-3.1-70b                 • claude-opus-4-6-thinking
             • claude-sonnet-4-5 (Copilot sub)    • gemini-3.1-pro-high
```

---

## 3. File Map & Responsibilities

| File | Role / Description |
|---|---|
| [`api.py`](api.py) | **FastAPI Application**: defines endpoints (`/v1/responses`, `/v1/chat/completions`, `/v1/models`, `/v1/antigravity/usage`, `/usage`, `/ask`, `/result/{job_id}`, `/ask-image`, `/health`), manages authentication, rate limiting, and request dispatching. |
| [`tasks.py`](tasks.py) | **Task & Gateway Logic**: implements OpenAI Responses API adapter, Chat Completions streaming, prompt minification, conversation caching, Antigravity `agy` CLI subprocessing, and Copilot client dispatching. |
| [`worker.py`](worker.py) | **RQ Worker Process**: polls Redis queues `copilot`, `antigravity`, and `default`, executing enqueued jobs. |
| [`Caddyfile`](Caddyfile) | **Caddy Reverse Proxy Configuration**: binds domain `${DOMAIN}`, compression (`zstd`, `gzip`), proxies to `api:8000`, and adds HTTP security headers. |
| [`docker-compose.yml`](docker-compose.yml) | **Container Orchestration**: 4 services (`caddy`, `redis`, `api`, `worker`), volume mounts for host authentication (`$HOME/.config/gh` and `$HOME/.gemini`), isolated `backend` network. |
| [`Dockerfile`](Dockerfile) | **Docker Image (Python 3.11-slim)**: installs `gh` CLI, adds `/root/.gemini/bin` to `PATH`, installs `requirements.txt`, and configures `entrypoint.sh`. |
| [`entrypoint.sh`](entrypoint.sh) | **Container Entrypoint**: validates `gh auth token` for Copilot and detects the `agy` binary for Antigravity. |
| [`tests/`](tests/) | **Automated Test Suite**: unit tests for provider routing, Responses API converters, and auth helpers. |
| [`requirements.txt`](requirements.txt) | **Python Dependencies**: `fastapi`, `uvicorn[standard]`, `redis`, `rq`, `openai`, `python-multipart`. |
| [`.env.example`](.env.example) | Configuration template for provider settings, default models, security keys, domains, and rate limits. |
| [`.env`](.env) | Instance environment configuration (git-ignored). |
| [`README.md`](README.md) | Complete user and developer documentation for quickstart, usage, and client integration. |
| [`LICENSE`](LICENSE) | Standard MIT License. |

---

## 4. Provider Selection & Routing Logic

Provider resolution follows a flexible cascading order:

1. **Explicit Query/Form Parameter**:
   - `GET /ask?prompt=...&provider=antigravity` (or `provider=copilot`)
   - `POST /ask-image` with form field `provider`
2. **Automatic Detection from Model Name**:
   - Models starting with `gemini-`, `claude-sonnet-4-6`, `claude-opus-4-6`, or `gpt-oss-` automatically route to **Google Antigravity**.
   - Known GitHub/Azure models (`gpt-4o`, `gpt-4o-mini`, `meta-llama-...`) route according to `PROVIDER`.
3. **Default Environment Variable (`PROVIDER`)**:
   - `PROVIDER=copilot` (default for Copilot-focused instances).
   - `PROVIDER=antigravity` (default for Antigravity-focused instances).

---

## 5. Model & Provider Details

### Provider: Google Antigravity (`agy`)
- Executes `/root/.gemini/bin/agy -p "<prompt>" --model <model> --output-format json --dangerously-skip-permissions --disable-slash-commands`.
- Supported Models:
  - `gemini-3.8-flash-high` (*Default Antigravity*, ~2s latency, fast reasoning, 1M+ context window)
  - `gemini-3.8-flash-medium`
  - `gemini-3.8-flash-low`
  - `gemini-3.7-flash-high`
  - `gemini-3.1-pro-high` (deep reasoning)
  - `claude-sonnet-4-6` (Thinking enabled)
  - `claude-opus-4-6-thinking` (Thinking enabled)
  - `gpt-oss-120b-medium`
- Vision Support: images uploaded to `/ask-image` are written to temporary files and referenced in the prompt passed to `agy`, leveraging the agent's native visual analysis.

### Provider: GitHub Copilot / Models
- Executes OpenAI client calls against Azure AI Inference (`COPILOT_BASE_URL`).
- Supported Models:
  - Free Tier: `gpt-4o-mini`, `gpt-4o`, `meta-llama-3.1-8b-instruct`, `meta-llama-3.1-70b-instruct`, `phi-4`.
  - Copilot Subscription: `gpt-4o-mini`, `gpt-4o`, `o1-mini`, `claude-sonnet-4-5`.

---

## 6. Security & Client Authentication

- **Multi-Token (`API_KEYS`)**: comma-separated tokens in `.env` (e.g. `token_ha,token_n8n,token_mobile`).
- **Single-Token (`API_KEY`)**: backward compatibility.
- **Supported Headers**: `Authorization: Bearer <token>` or `X-API-Key: <token>`.
- **Rate Limiting**: stored in Redis as `ratelimit:<sha256_hash_16>:<unix_minute>`. Requests exceeding `RATE_LIMIT_RPM` receive HTTP 429.
- **Portainer-Safe**: no host port binding for port 8000; only Caddy binds `80` and `443`.

---

## 7. Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PROVIDER` | `copilot` | Active default provider (`copilot` or `antigravity`) |
| `ANTIGRAVITY_MODEL` | `gemini-3.8-flash-high` | Default model for Antigravity |
| `ANTIGRAVITY_EFFORT` | *empty* | Reasoning effort level (`low`, `medium`, `high`, `xhigh`) |
| `AGY_PATH` | *auto* | Optional custom path to `agy` binary (defaults to `~/.gemini/bin/agy` or `/root/.gemini/bin/agy`) |
| `COPILOT_BASE_URL` | `https://models.inference.ai.azure.com` | GitHub / Azure AI Inference base URL |
| `COPILOT_MODEL` | `gpt-4o-mini` | Default model for Copilot |
| `API_KEYS` | *empty* | Comma-separated API tokens for multiple clients |
| `API_KEY` | *empty* | Single legacy API token |
| `RATE_LIMIT_RPM` | `60` | Maximum requests per minute per token |
| `PUBLIC_HEALTH` | `true` | Makes `/health` accessible without authentication |
| `DOMAIN` | `localhost` | Domain name for Caddy automatic TLS certificates |

---

## 8. Example Commands

```bash
# Start the stack
docker compose up -d --build

# Ask query specifying Antigravity provider
curl -H "Authorization: Bearer <TOKEN>" \
  "https://<DOMAIN>/ask?prompt=Explain+special+relativity&model=gemini-3.8-flash-high&provider=antigravity"

# OpenAI-compatible call to Claude Sonnet via Antigravity
curl https://<DOMAIN>/v1/chat/completions \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Solve this puzzle in Python..."}]
  }'

# Health check
curl "https://<DOMAIN>/health"
# -> {"status": "ok", "provider": "antigravity"}
```
