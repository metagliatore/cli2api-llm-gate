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
curl "http://localhost:8000/ask?prompt=write+a+bash+one-liner+to+find+large+files"
# → {"job_id": "abc123", "status": "queued", "model_requested": null}

# With explicit model
curl "http://localhost:8000/ask?prompt=explain+quicksort&model=gpt-4o"
```

---

### `GET /result/{job_id}`

Polls the status and result of a job.

```bash
curl "http://localhost:8000/result/abc123"
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
curl "http://localhost:8000/models"
```

---

### `GET /health`

Liveness check.

```bash
curl "http://localhost:8000/health"
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
```

`GITHUB_TOKEN` is **not** needed in `.env` — it is injected automatically from your host `gh` auth at container startup.

---

## Integration Guide

This API is **OpenAI-compatible** (same JSON schema). Services that support a custom endpoint will work out of the box.

### Open WebUI (Ollama-style UI)

[Open WebUI](https://github.com/open-webui/open-webui) supports custom OpenAI-compatible endpoints:

1. Open WebUI → Settings → Connections → OpenAI API
2. Set **API Base URL**: `http://localhost:8000`  
   *(or `http://host.docker.internal:8000` if Open WebUI is itself in Docker)*
3. Set **API Key**: any non-empty string (e.g. `copilot`)
4. The model list will be populated from `/models`

> **Note:** Open WebUI expects the standard `/v1/chat/completions` path.  
> You can add a thin `/v1/chat/completions` proxy endpoint to `api.py` (see below).

---

### Adding an OpenAI-compatible `/v1/chat/completions` endpoint

To make this API a **drop-in replacement** for Ollama or any OpenAI-compatible client, add this to `api.py`:

```python
from fastapi import Body
from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[dict]
    stream: bool = False

@app.post("/v1/chat/completions")
def chat_completions(body: ChatRequest = Body(...)):
    """
    OpenAI-compatible synchronous endpoint.
    Useful for clients like Open WebUI, LangChain, LlamaIndex, Continue.dev, etc.
    """
    prompt = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in body.messages
    )
    job = queue.enqueue(ask_copilot, prompt, body.model, job_timeout=120)

    # Wait for result (synchronous, up to 30 s)
    import time
    for _ in range(60):
        job.refresh()
        if job.is_finished:
            break
        time.sleep(0.5)

    result = job.result or {}
    return {
        "id": job.id,
        "object": "chat.completion",
        "model": result.get("model", body.model),
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": result.get("content", ""),
            },
            "finish_reason": "stop",
        }],
        "usage": result.get("usage", {}),
    }
```

---

### LangChain / LlamaIndex

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="copilot",          # any non-empty string
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
      "apiBase": "http://localhost:8000/v1",
      "apiKey": "copilot"
    }
  ]
}
```

---

### curl (raw OpenAI-compatible call)

```bash
curl http://localhost:8000/v1/chat/completions \
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
