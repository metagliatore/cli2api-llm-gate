# cli2api-llm-gate

> **Transform Host CLI AI Tools (Google Antigravity & GitHub Copilot) into a Multi-Provider, OpenAI-Compatible HTTP Gateway.**

A production-ready containerized microservice that bridges **Google Antigravity (`agy`)** and **GitHub Copilot / GitHub Models** into a unified, **OpenAI-compatible HTTP API**.

It allows external applications (**Home Assistant**, **Pi Coding Agent**, **Aider**, **Open WebUI**, **Continue.dev**, **Dify**, **n8n**, custom bots) to consume your host AI capabilities over secure HTTPS without paying for separate cloud API tokens.

---

## ✨ Key Features

- **Multi-Provider Routing**: Seamlessly route prompts to **Google Antigravity** (Gemini 3.8 Flash, Gemini 3.1 Pro, Claude Sonnet 4.6 Thinking, Claude Opus 4.6 Thinking) or **GitHub Copilot / Models** (GPT-4o, GPT-4o-mini, Llama 3.1, Phi-4, etc.).
- **OpenAI Responses API (`POST /v1/responses`)**: Native compatibility with modern OpenAI SDKs and **Home Assistant Assist** (`client.responses.create`), complete with Server-Sent Events (`response.*`), multi-turn state, and tool execution.
- **OpenAI Chat Completions (`POST /v1/chat/completions`)**: Drop-in replacement for OpenAI/Ollama clients, featuring real-time Server-Sent Events (`stream: true`), function/tool calling, and multi-turn context.
- **Agent Harness Optimized**: Engineered for coding agents (**Pi Coding Agent**, **Aider**, **Continue.dev**) with prompt minification, conversation session reuse (`X-Conversation-Id`), and zero idle token waste.
- **Asynchronous Task Queue (Redis RQ)**: Handles heavy batch jobs, long prompts, and image processing (`/ask`, `/ask-image`, `/result/{job_id}`).
- **Usage & Quota Monitoring (`/v1/antigravity/usage` & `/usage`)**: Real-time stats on daily conversation counts, step turns, and rate limit status (`RESOURCE_EXHAUSTED`).
- **Transparent Model Aliasing**: Client requests specifying legacy model names (e.g. `gpt-4o-mini`) are automatically mapped to the active provider default without breaking clients.
- **Zero-Credential Overhead**: Automatically mounts and reuses host CLI credentials (`~/.gemini` for Antigravity, `~/.config/gh` for Copilot).
- **Production Hardened**: Caddy reverse proxy with automated TLS certificates (Let's Encrypt / ZeroSSL), multi-token Bearer authorization, and Redis sliding-window rate limiting.

---

## 🏛️ Architecture

```
                                  Client / External Apps
                           (Home Assistant, Pi Agent, WebUI, curl)
                                             │
                                             ▼
                              Caddy Reverse Proxy (:80 / :443)
                                  (Automated TLS Certificates)
                                             │
                                             ▼
                              FastAPI (:8000 internal network)
                                 - Multi-Bearer token auth (API_KEYS)
                                 - Redis sliding-window rate limiting
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

Public traffic reaches only Caddy (ports 80 and 443). FastAPI and Redis stay internal to the isolated Docker network.

---

## 🚀 Quick Start

### 1. Prerequisites

- **Docker & Docker Compose** installed on the host.
- **For GitHub Copilot**: `gh` CLI installed and authenticated on the host (`gh auth login`).
- **For Google Antigravity**: Antigravity CLI (`agy`) installed and authenticated on the host (`~/.gemini`).

### 2. Setup and Launch

```bash
git clone https://github.com/metagliatore/copilot-api.git cli2api-llm-gate
cd cli2api-llm-gate

# Create your .env from template
cp .env.example .env

# Edit .env and configure your domain and secret tokens:
# PROVIDER=antigravity      # or 'copilot'
# API_KEYS=token_ha,token_pi_agent,token_mobile
# DOMAIN=api.your-domain.com

# Start the stack
docker compose up -d --build
```

The stack automatically mounts:
- Host `gh` credentials (`~/.config/gh`) for Copilot.
- Host Antigravity session (`~/.gemini`) for Antigravity.

---

## 🔌 Integration Guides

### 1. Home Assistant Assist (`ha-openai-compatible`)

Use the custom integration [`ha-openai-compatible`](https://github.com/adamjs83/ha-openai-compatible) (or Core Assist):

1. Go to **Settings > Devices & Services > Add Integration > OpenAI Compatible**.
2. Fill in the connection form:
   * **Base URL**: `https://api.your-domain.com/v1` *(no trailing slash, no `/chat/completions`)*
   * **API Key**: One of the keys defined in your `API_KEYS`
3. Add a **Conversation Agent** sub-entry:
   * **Model**: `gemini-3.8-flash-high` or `claude-sonnet-4-6` (Antigravity), or `gpt-4o-mini` (Copilot).
4. Home Assistant will natively communicate using `POST /v1/responses`, including tool calling for your home entities!

---

### 2. Pi Coding Agent / Coding Harnesses

Configure `cli2api-llm-gate` as a custom OpenAI provider in your coding agent (`~/.pi/agent/models.json` or equivalent):

```json
{
  "providers": {
    "cli2api_llm_gate": {
      "type": "openai",
      "baseUrl": "https://api.your-domain.com/v1",
      "apiKey": "your_secret_api_key",
      "models": [
        {
          "id": "gemini-3.8-flash-high",
          "name": "Gemini 3.8 Flash High (Antigravity)",
          "contextWindow": 1048576,
          "supportsStreaming": true,
          "supportsTools": true
        },
        {
          "id": "claude-sonnet-4-6",
          "name": "Claude 3.7 / 4.6 Sonnet Thinking",
          "contextWindow": 200000,
          "supportsStreaming": true,
          "supportsTools": true
        }
      ]
    }
  }
}
```

---

### 3. Official OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.your-domain.com/v1",
    api_key="your_secret_api_key",
)

# 1. Chat Completion with Streaming
stream = client.chat.completions.create(
    model="gemini-3.8-flash-high",
    messages=[{"role": "user", "content": "Explain async generator pattern in Python"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)

# 2. Modern Responses API (client.responses.create)
response = client.responses.create(
    model="gemini-3.8-flash-high",
    input="Give me 3 tips for efficient docker containers.",
)
print(response.output[0].content[0].text)
```

---

## 📡 API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/v1/responses` | `POST` | Bearer / Header | **OpenAI Responses API** (Home Assistant Assist & modern agents, SSE streaming & tools) |
| `/v1/chat/completions` | `POST` | Bearer / Header | **OpenAI Chat Completions** (streaming, multi-turn, tool calling) |
| `/v1/models` | `GET` | Bearer / Header | Standard OpenAI model listing |
| `/v1/models/{model_id}` | `GET` | Bearer / Header | Model metadata retrieval |
| `/v1/antigravity/usage` | `GET` | Bearer / Header / Query | Antigravity quota status, daily breakdown & active sessions |
| `/usage` | `GET` | Bearer / Header / Query | Quick browser alias for `/v1/antigravity/usage` |
| `/ask` | `GET` | Bearer / Header | Enqueue prompt in Redis RQ (asynchronous) |
| `/ask-image` | `POST` | Bearer / Header | Multipart image upload + question in Redis RQ |
| `/result/{job_id}` | `GET` | Bearer / Header | Poll result of enqueued Redis job |
| `/health` | `GET` | Optional | Stack liveness status (`{"status": "ok", "provider": "..."}`) |

---

### `POST /v1/responses` (OpenAI Responses API)

Native endpoint for Home Assistant and next-gen agent harnesses. Supports both non-streaming JSON and streaming Server-Sent Events (`stream: true`).

```bash
# Streaming Responses request
curl -N -X POST "https://$DOMAIN/v1/responses" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "input": [
      {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "Dimmi Ciao da Home Assistant"}]
      }
    ],
    "stream": true
  }'
```

---

### `POST /v1/chat/completions` (Chat Completions)

Drop-in replacement for OpenAI/Ollama compatible tools:

```bash
curl -N -X POST "https://$DOMAIN/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Write a python fibonacci generator"}],
    "stream": true
  }'
```

---

### `GET /v1/antigravity/usage` & `GET /usage`

Inspects real-time consumption, daily conversation/step counts, and quota status (`RESOURCE_EXHAUSTED` / rate limits):

```bash
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/v1/antigravity/usage"
# Or in browser / dashboard with query parameter:
curl "https://$DOMAIN/usage?api_key=$API_KEY"
```

**Example response:**
```json
{
  "status": "ok",
  "provider": "antigravity",
  "quota_status": {
    "is_rate_limited": false,
    "reset_in": null,
    "last_exhausted_error": null
  },
  "summary": {
    "total_conversations": 53,
    "total_step_turns": 382,
    "days_tracked": 4
  },
  "daily_usage": [
    {"date": "2026-10-11", "conversations": 12, "step_turns": 84},
    {"date": "2026-10-10", "conversations": 7, "step_turns": 38}
  ],
  "recent_conversations": [ ... ]
}
```

---

### `GET /ask` & `GET /result/{job_id}`

Asynchronous background execution:

```bash
# 1. Enqueue prompt
curl -H "Authorization: Bearer $API_KEY" \
  "https://$DOMAIN/ask?prompt=Explain+special+relativity&model=gemini-3.8-flash-high"
# Returns: {"job_id": "abc123", "status": "queued"}

# 2. Poll result
curl -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/result/abc123"
```

---

## 🤖 Supported Models

### Provider: Google Antigravity (`PROVIDER=antigravity`)

| Model | Description |
|---|---|
| `gemini-3.8-flash-high` | **Default.** Ultra fast (~2s), 1M+ context window, reasoning. |
| `gemini-3.8-flash-medium` | Balanced speed and latency. |
| `gemini-3.8-flash-low` | Ultra fast, lowest latency. |
| `gemini-3.7-flash-high` | High capability Gemini 3.7. |
| `gemini-3.1-pro-high` | Deep reasoning and large context. |
| `claude-sonnet-4-6` | Anthropic Claude Sonnet 4.6 (Thinking). |
| `claude-opus-4-6-thinking`| Anthropic Claude Opus 4.6 (Thinking). |
| `gpt-oss-120b-medium` | Open weights 120B model. |

### Provider: GitHub Copilot / Models (`PROVIDER=copilot`)

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

## ⚙️ Configuration Reference (`.env`)

```env
# Choose default provider: 'copilot' or 'antigravity'
PROVIDER=antigravity

# Antigravity default model
ANTIGRAVITY_MODEL=gemini-3.8-flash-high

# Optional: Antigravity reasoning effort level (low|medium|high|xhigh)
# ANTIGRAVITY_EFFORT=high

# Copilot default backend and model
COPILOT_BASE_URL=https://api.githubcopilot.com
COPILOT_MODEL=gpt-4o-mini

# Security: multi-client tokens (comma-separated)
API_KEYS=token_home_assistant,token_pi_agent,token_mobile

# Maximum requests per minute per token
RATE_LIMIT_RPM=60

# Keep /health accessible without token (true/false)
PUBLIC_HEALTH=true

# Domain used by Caddy for automatic TLS/HTTPS certificates
DOMAIN=api.your-domain.com
```

---

## 🧪 Testing

The test suite runs with standard Python `unittest`:

```bash
python3 -m unittest discover tests
```

---

## 📂 Project Structure

```
.
├── api.py              # FastAPI app (OpenAI Chat & Responses endpoints, auth, rate limiting)
├── worker.py           # RQ worker entry point
├── tasks.py            # Task execution (Copilot OpenAI client, Antigravity agy CLI, formatters)
├── entrypoint.sh       # Injects GITHUB_TOKEN and validates agy CLI binary
├── requirements.txt    # Python dependencies
├── Dockerfile          # Python 3.11-slim container with gh CLI and agy PATH
├── docker-compose.yml  # Multi-service stack (Caddy, Redis, API, Worker)
├── Caddyfile           # Caddy reverse proxy with TLS & security headers
├── tests/              # Automated test suite
│   └── test_gateway.py # Unit tests for routing, auth, converters, and endpoints
├── MEMORY.md           # Architecture and technical documentation
└── .env.example        # Environment variable template
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
