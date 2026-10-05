# cli2api-ai

> **Transform Host CLI AI Tools (Google Antigravity & GitHub Copilot) into a Multi-Provider, OpenAI-Compatible HTTP Gateway.**

A minimal, production-ready microservice that bridges **Google Antigravity (`agy`)** and **GitHub Copilot / GitHub Models** into a unified, **OpenAI-compatible HTTP API**.

It allows external applications (Home Assistant, Open WebUI, Continue.dev, Dify, n8n, custom bots) to consume your host AI capabilities over secure HTTPS without paying for separate cloud API tokens.

### ✨ Key Features
- **Multi-Provider Routing**: Seamlessly route prompts to **Google Antigravity** (Gemini 3.8 Flash, Gemini 3.1 Pro, Claude Sonnet 4.6 Thinking, Claude Opus 4.6 Thinking) or **GitHub Copilot / Models** (GPT-4o, GPT-4o-mini, Llama 3.1, etc.).
- **OpenAI Compatible (`/v1/chat/completions`)**: Direct drop-in replacement for OpenAI/Ollama clients with up to 180s timeout polling for deep reasoning models.
- **Asynchronous Task Queue (Redis RQ)**: Handles heavy jobs, long prompts, and image processing (`/ask`, `/ask-image`, `/result/{job_id}`).
- **Transparent Model Aliasing**: Client applications sending legacy model names (e.g. `gpt-4o-mini`) are automatically and gracefully mapped to the active provider default without errors.
- **Zero-Credential Overhead**: Automatically mounts and reuses host CLI credentials (`~/.gemini` for Antigravity, `~/.config/gh` for Copilot).
- **Production Hardened**: Caddy reverse proxy with automated TLS certificates (Let's Encrypt / ZeroSSL), multi-token Bearer authorization, and Redis rate limiting.

## Architecture

```
                                  Client / External Apps
                                            │
                                            ▼
                             Caddy Reverse Proxy (:80 / :443)
                                 (Automated TLS Certificates)
                                            │
                                            ▼
                              FastAPI (:8000 internal network)
                                 - Multi-Bearer token auth
                                 - Redis rate limiting
                                 - Dynamic provider routing
                                            │
                                            ▼
                                  Redis Queue 7 (RQ)
                                            │
                                            ▼
                                        RQ Worker
                                            │
              ┌─────────────────────────────┴─────────────────────────────┐
              │                                                           │
              ▼                                                           ▼
     [Provider: antigravity]                                     [Provider: copilot]
   Google Antigravity CLI (`agy`)                              GitHub Models / Copilot API
 (Gemini 3.8, Claude 4.6, GPT-OSS)                                (GPT-4o, Llama 3.1, etc.)
 (Mounted ~/.gemini credentials)                                 (Mounted ~/.config/gh auth)
```

Public traffic reaches only Caddy (80/443). FastAPI and Redis stay internal to the Docker network.

---

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose
- **For GitHub Copilot**: `gh` CLI installed and authenticated on the host (`gh auth login`).
- **For Google Antigravity**: Antigravity CLI (`agy`) installed and authenticated on host (`~/.gemini`).

### 2. Clone and start

```bash
git clone <this-repo> cli2api-ai
cd cli2api-ai

# Create your .env from the template
cp .env.example .env

# Edit .env and configure:
# PROVIDER=copilot           # or PROVIDER=antigravity
# API_KEYS=token_client1,token_client2
# DOMAIN=api.your-domain.com

docker compose up --build
```

The containers automatically pick up:
- Host `gh` credentials (`~/.config/gh`) for Copilot.
- Host Antigravity session (`~/.gemini`) for Antigravity.

---

## API Reference

### `GET /ask`

Enqueues a prompt and returns a `job_id` immediately (non-blocking).

| Query param | Type   | Required | Description |
|---|---|---|---|
| `prompt`    | string | ✅        | Your question / instruction |
| `model`     | string | ❌        | Override model (e.g. `gpt-4o`, `gemini-3.8-flash-high`, `claude-sonnet-4-6`) |
| `provider`  | string | ❌        | Override provider: `copilot` or `antigravity` (auto-detected if model is specific) |

**Examples:**
```bash
# Default provider & model
curl -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/ask?prompt=write+a+bash+one-liner+to+find+large+files"

# Ask Antigravity with Gemini 3.8
curl -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/ask?prompt=explain+quicksort&model=gemini-3.8-flash-high"

# Ask Antigravity with Claude Sonnet 4.6 Thinking
curl -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/ask?prompt=solve+this+math+puzzle&model=claude-sonnet-4-6"
```

---

### `GET /result/{job_id}`

Polls the status and result of a job.

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/result/abc123"
```

**Finished response:**
```json
{
  "job_id": "abc123",
  "status": "finished",
  "result": {
    "content": "Here is the explanation...",
    "model": "gemini-3.8-flash-high",
    "provider": "antigravity",
    "usage": {
      "prompt_tokens": 18400,
      "completion_tokens": 150,
      "total_tokens": 18550
    }
  }
}
```

---

### `POST /ask-image`

Upload an image and ask a question about it.

```bash
curl -X POST "https://$DOMAIN/ask-image" \
  -H "Authorization: Bearer $API_KEY" \
  -F 'prompt=Describe this diagram' \
  -F 'model=gemini-3.8-flash-high' \
  -F 'provider=antigravity' \
  -F 'file=@/path/to/diagram.png'
```

---

### `POST /v1/chat/completions` (OpenAI-Compatible & Agent Harness Support)

Drop-in replacement for OpenAI / Ollama clients and **coding agent harnesses** (**Pi Coding Agent**, **Aider**, **Continue.dev**, **Open WebUI**, **LangChain**, **AutoGen**, etc.).

Supports:
- **Real-Time Streaming** (`stream: true` via Server-Sent Events `text/event-stream`).
- **Tool / Function Calling** (`tools`, `tool_choice`, `tool_calls`).
- **Multi-turn Context** (`system`, `user`, `assistant`, `tool` roles).
- **Both Providers**: Antigravity (`agy`) and GitHub Copilot / Models.

#### 1. Standard Chat Completion
```bash
curl https://$DOMAIN/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

#### 2. Real-Time Streaming (`stream: true`)
```bash
curl -N https://$DOMAIN/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "messages": [{"role": "user", "content": "Write a python fibonacci generator"}],
    "stream": true
  }'
```

#### 3. Tool / Function Calling
```bash
curl https://$DOMAIN/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "messages": [{"role": "user", "content": "What is the weather in Rome?"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }
    }]
  }'
```

#### 4. Official OpenAI Python SDK Integration
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.your-domain.com/v1",
    api_key="your_secret_api_key",
)

# Streaming
stream = client.chat.completions.create(
    model="gemini-3.8-flash-high",
    messages=[{"role": "user", "content": "Explain async/await in Python"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

### `GET /v1/models` & `GET /v1/models/{model_id}`

Returns the list of available models in standard OpenAI format for dynamic model discovery in agent harnesses:

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/v1/models"
```

---

### `GET /models`

Returns the list of available models split by provider (legacy endpoint):

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/models"
```

---

### `GET /health`

Liveness check (`{"status": "ok", "provider": "copilot"}`).

```bash
curl "https://$DOMAIN/health"
```

---

## Model Selection & Providers

### Provider 1: Google Antigravity (`PROVIDER=antigravity`)

Uses your authenticated Antigravity account from `~/.gemini`:

| Model | Description |
|---|---|
| `gemini-3.8-flash-high` | **Default.** Super fast, 1M+ context window, reasoning. |
| `gemini-3.8-flash-medium` | Balanced latency and speed. |
| `gemini-3.8-flash-low` | Ultra fast, lowest latency. |
| `gemini-3.7-flash-high` | High capability Gemini 3.7. |
| `gemini-3.1-pro-high` | Deep reasoning and large context. |
| `claude-sonnet-4-6` | Anthropic Claude Sonnet 4.6 (Thinking). |
| `claude-opus-4-6-thinking`| Anthropic Claude Opus 4.6 (Thinking). |
| `gpt-oss-120b-medium` | Open weights 120B model. |

### Provider 2: GitHub Copilot / Models (`PROVIDER=copilot`)

| Model | Backend | Cost / Quota |
|---|---|---|
| `gpt-4o-mini` | GitHub Models | **$0** (rate-limited, default) |
| `gpt-4o` | GitHub Models | **$0** (rate-limited) |
| `meta-llama-3.1-8b-instruct` | GitHub Models | **$0** |
| `meta-llama-3.1-70b-instruct` | GitHub Models | **$0** |
| `phi-4` | GitHub Models | **$0** |
| `gpt-4o-mini` | Copilot API | Copilot subscription |
| `claude-sonnet-4-5` | Copilot API | Copilot subscription |

---

## Configuration (`.env`)

```env
# Choose default provider: 'copilot' or 'antigravity'
PROVIDER=copilot

# Antigravity default model
ANTIGRAVITY_MODEL=gemini-3.8-flash-high

# Copilot default backend and model
COPILOT_BASE_URL=https://models.inference.ai.azure.com
COPILOT_MODEL=gpt-4o-mini

# Security
API_KEYS=token_home_assistant,token_n8n,token_phone
RATE_LIMIT_RPM=60
PUBLIC_HEALTH=true
DOMAIN=api.example.com
```

---

## Project Structure

```
.
├── api.py              # FastAPI app (endpoints, rate limiting, routing)
├── worker.py           # RQ worker entry point
├── tasks.py            # Task execution (Copilot OpenAI client & Antigravity agy CLI)
├── entrypoint.sh       # Injects GITHUB_TOKEN and checks Antigravity agy CLI
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── Caddyfile
├── MEMORY.md           # Project architecture and technical documentation
└── .env.example
```
