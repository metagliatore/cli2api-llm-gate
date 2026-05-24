# GitHub Copilot HTTP API

A minimal, production-ready service that exposes **GitHub Copilot / GitHub Models** as a standard OpenAI-compatible HTTP API.

## Architecture

```
Client
  │
  ▼
FastAPI  :8000          ←  GET /ask?prompt=…&model=…
  │                        GET /result/{job_id}
  │ enqueue job             GET /models
  ▼
Redis Queue (RQ)
  │
  ▼
RQ Worker
  │  openai SDK
  ▼
GitHub Models API  ─── free tier, rate-limited, $0
── or ──
GitHub Copilot API ─── included with Copilot subscription
```

Public traffic reaches only Caddy (80/443). API and Redis stay internal to Docker network.

---

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose
- `gh` CLI installed and authenticated on the host:

```bash
# Install gh (Debian/Ubuntu)
sudo apt install gh

# Authenticate (one-time)
gh auth login
# → GitHub.com → HTTPS → Login with a browser
```

### 2. Clone and start

```bash
git clone <this-repo>
cd docker-copilot-api

# Optional: override backend or model (sensible defaults are already set)
cp .env.example .env

# REQUIRED in .env
# API_KEY=<long-random-secret>
# DOMAIN=api.your-domain.com

docker compose up --build
```

The containers automatically pick up your host `gh` auth — no token copy-paste needed.

---

## API Reference

### `GET /ask`

Enqueues a prompt and returns a `job_id` immediately (non-blocking).

| Query param | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `prompt`    | string | ✅        | Your question / instruction |
| `model`     | string | ❌        | Override the default model for this request |

**Example:**
```bash
curl -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/ask?prompt=write+a+bash+one-liner+to+find+large+files"
# → {"job_id": "abc123", "status": "queued", "model_requested": null}

# With explicit model
curl -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/ask?prompt=explain+quicksort&model=gpt-4o"
```

---

### `GET /result/{job_id}`

Polls the status and result of a job.

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/result/abc123"
```

**Possible `status` values:**

| Status     | Meaning |
|------------|---------|
| `queued`   | Waiting for a worker |
| `started`  | Worker is processing |
| `finished` | Done — `result` field is populated |
| `failed`   | Error — `error` field is populated |

**Finished response:**
```json
{
  "job_id": "abc123",
  "status": "finished",
  "result": {
    "content": "Here is the bash one-liner: ...",
    "model": "gpt-4o-mini",
    "usage": {
      "prompt_tokens": 18,
      "completion_tokens": 42,
      "total_tokens": 60
    }
  }
}
```

---

### `GET /models`

Returns the list of available models split by backend.

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/models"
```

---

### `GET /health`

Liveness check.

```bash
curl "https://$DOMAIN/health"
# → {"status": "ok"}
```

---

## Model Selection & Cost

The default model is `gpt-4o-mini`. Change it globally via `.env` or per-request via `?model=`.

| Model | Backend | Cost | Notes |
|-------|---------|------|-------|
| `gpt-4o-mini` | GitHub Models | **$0** (rate-limited) | **Default. Recommended.** |
| `gpt-4o` | GitHub Models | **$0** (rate-limited) | Better quality |
| `meta-llama-3.1-8b-instruct` | GitHub Models | **$0** | Open source, fast |
| `meta-llama-3.1-70b-instruct` | GitHub Models | **$0** | Open source, larger |
| `mistral-small` | GitHub Models | **$0** | |
| `phi-4` | GitHub Models | **$0** | Microsoft small model |
| `gpt-4o-mini` | Copilot API | **$0** (base model, no premium quota) | Needs Copilot subscription |
| `claude-sonnet-4-5` | Copilot API | Uses premium requests | Needs Copilot subscription |

**Rate limits (GitHub Models free tier, approximate):**

| Model tier | Requests/min | Requests/day |
|------------|-------------|--------------|
| Small (gpt-4o-mini, llama-8b) | ~15 | ~500 |
| Large (gpt-4o, llama-70b) | ~5 | ~50 |

---

## Configuration (`.env`)

```env
# API backend — choose one:
COPILOT_BASE_URL=https://models.inference.ai.azure.com   # GitHub Models, free tier
# COPILOT_BASE_URL=https://api.githubcopilot.com         # Copilot API, needs subscription

# Default model (overridable per-request)
COPILOT_MODEL=gpt-4o-mini

# Required security settings for public deployment
API_KEY=replace-with-long-random-secret
RATE_LIMIT_RPM=60
PUBLIC_HEALTH=true

# Domain handled by Caddy TLS
DOMAIN=api.example.com
```

`GITHUB_TOKEN` is optional in `.env` — by default it is injected from host `gh` auth at container startup.

## VPS Deployment (Secure)

1. Prepare DNS: create an A/AAAA record for `DOMAIN` pointing to the VPS IP.
2. Firewall: open only `80/tcp` and `443/tcp`; do not expose `6379`.
3. Install Docker + Compose plugin on the VPS.
4. Copy project, then set `.env` with strong `API_KEY`, valid `DOMAIN`, model/backend.
5. On VPS, run `gh auth login` (or set `GITHUB_TOKEN` in `.env`) so worker can call GitHub APIs.
6. Start with `docker compose up -d --build`.
7. Test:

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/health"
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/models"
```

Security defaults in this stack:
- API key auth required on all API endpoints except optional public `/health`.
- Per-key rate limit (`RATE_LIMIT_RPM`) enforced in API.
- Redis not published to host network.
- TLS termination and HTTPS certificates handled by Caddy.

### Portainer compatibility (`:8000`)

If your VPS already exposes Portainer on `:8000`, this setup is compatible as-is:

- This stack exposes only `80/443` via Caddy.
- The FastAPI container is internal only (no host `8000` bind).
- Redis is internal only.

So there is no conflict with Portainer on port `8000`.

### Installation checklist (copy/paste)

```bash
# 1) Clone project
git clone <your-repo-url>
cd docker-copilot-api

# 2) Prepare env
cp .env.example .env
# Edit .env and set at least:
# API_KEY=<long-random-secret>
# DOMAIN=api.yourdomain.com

# 3) (Option A) Use gh auth from host (recommended)
gh auth login

# 4) Start stack
docker compose up -d --build

# 5) Verify
curl "https://api.yourdomain.com/health"
curl -H "Authorization: Bearer <API_KEY>" "https://api.yourdomain.com/models"
```

If you don't want `gh auth login` on VPS, you can set `GITHUB_TOKEN` directly in `.env`.

---

## Integration Guide

This API is **OpenAI-compatible** (same JSON schema). Services that support a custom endpoint will work out of the box.

### Open WebUI (Ollama-style UI)

[Open WebUI](https://github.com/open-webui/open-webui) supports custom OpenAI-compatible endpoints:

1. Open WebUI → Settings → Connections → OpenAI API
2. Set **API Base URL**: `https://$DOMAIN/v1`
3. Set **API Key**: your `API_KEY`
4. The model list will be populated from `/models`

> **Note:** `/v1/chat/completions` is already implemented in this project.

---

### LangChain / LlamaIndex

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
  base_url="https://api.example.com/v1",
  api_key="YOUR_API_KEY",
    model="gpt-4o-mini",
)

response = llm.invoke("Explain Docker networking in one paragraph")
print(response.content)
```

---

### Continue.dev (VS Code / JetBrains AI coding assistant)

In `~/.continue/config.json`:

```json
{
  "models": [
    {
      "title": "GitHub Copilot API (gpt-4o-mini)",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "apiBase": "https://api.example.com/v1",
      "apiKey": "YOUR_API_KEY"
    }
  ]
}
```

---

### curl (raw OpenAI-compatible call)

```bash
curl https://api.example.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello, who are you?"}]
  }'
```

---

## Scaling Workers

```bash
# Run 3 parallel workers
docker compose up --scale worker=3
```

---

## Project Structure

```
.
├── api.py              # FastAPI app (endpoints)
├── worker.py           # RQ worker entry point
├── tasks.py            # ask_copilot() task (calls GitHub API)
├── entrypoint.sh       # Injects GITHUB_TOKEN from mounted gh auth
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```
