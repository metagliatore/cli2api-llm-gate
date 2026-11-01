# cli2api-llm-gate

<p align="center">
  <a href="https://github.com/metagliatore/cli2api-llm-gate/actions/workflows/docker-publish.yml"><img src="https://github.com/metagliatore/cli2api-llm-gate/actions/workflows/docker-publish.yml/badge.svg" alt="CI & Docker Publish"></a>
  <a href="https://github.com/metagliatore/cli2api-llm-gate/releases"><img src="https://img.shields.io/github/v/release/metagliatore/cli2api-llm-gate?color=blue" alt="GitHub Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker Ready">
  <img src="https://img.shields.io/badge/Tests-24%20Passed-brightgreen" alt="Tests">
</p>

> **Transform Host CLI AI Tools (Google Antigravity & GitHub Copilot) into a Multi-Provider, OpenAI-Compatible HTTP Gateway.**

<p align="center">
  <img src="docs/assets/demo.gif" alt="cli2api-llm-gate terminal streaming demo" width="850">
</p>

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
- **Flexible Deployment**: Choose between **Standard Mode** (ultra-lightweight ~300 MB, reusing host CLI sessions) and **Standalone Mode** (all-in-docker ~660 MB, with `gh` and `agy` pre-installed and isolated volume storage).
- **Zero-Credential Overhead**: Automatically mounts host CLI credentials or accepts standard environment tokens (`GITHUB_TOKEN` in `.env`).
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
 (Host ~/.gemini or gemini_data volume)                          (Host ~/.config/gh or GITHUB_TOKEN)
```

Public traffic reaches only Caddy (ports 80 and 443). FastAPI and Redis stay internal to the isolated Docker network.

---

## 🚀 Quick Start & Deployment Modes

You can run `cli2api-llm-gate` in two modes depending on your infrastructure preference:

| Feature | Option A: Standard (Recommended) | Option B: Standalone |
| :--- | :--- | :--- |
| **Image Size** | **~300 MB** (ultra-lightweight) | **~660 MB** |
| **Host CLI Required** | Yes (`gh` and/or `agy` on host) | **No** (CLIs pre-installed inside container) |
| **Copilot Auth** | Mounts host `~/.config/gh` (auto-token) | Direct `GITHUB_TOKEN=gho_...` in `.env` |
| **Antigravity Auth** | Mounts host `~/.gemini` session | Stored in Docker named volume `gemini_data` |
| **Docker Compose** | `docker-compose.yml` | `docker-compose.standalone.yml` |

---

### Step 1: Clone Repository & Configure `.env`

```bash
git clone https://github.com/metagliatore/cli2api-llm-gate.git
cd cli2api-llm-gate

# Copy template environment file
cp .env.example .env
```

Edit your `.env` to configure your gateway:
```ini
# Choose active provider: 'antigravity' or 'copilot'
PROVIDER=antigravity

# Security: comma-separated list of Bearer tokens accepted by the gateway
API_KEYS=token_ha,token_pi_agent,token_mobile

# Domain name for Caddy automatic HTTPS (or 'localhost' for local testing)
DOMAIN=api.your-domain.com
```

---

### Step 2: Choose Your Deployment & Authentication Mode

#### 🔹 Option A: Standard / Lightweight (Recommended)

Uses the official pre-built lightweight image from **GHCR** (`ghcr.io/metagliatore/cli2api-llm-gate:latest`). It bridges the CLI sessions already present on your host without duplicating binaries or requiring local compilation.

1. **Host Authentication**:
   - **For GitHub Copilot**: Run `gh auth login` on the host machine.
   - **For Google Antigravity**: Ensure the `agy` CLI is installed on the host and you have run your initial Google login (`~/.gemini`).

2. **Launch the stack (Zero-Build)**:
   ```bash
   docker compose up -d
   ```
   *(To force building locally from source instead of using the GHCR image, pass `--build`)*.

*How it works*:
- The container mounts `~/.config/gh` in read-only mode; `entrypoint.sh` automatically extracts `gh auth token`.
- The container mounts `~/.gemini` directly, giving immediate access to your active Antigravity session and credentials.

---

#### 🔹 Option B: Standalone / Self-Contained (All-in-Docker)

Ideal if you prefer **not** to install `agy` or `gh` on your host, or want a completely isolated container stack. Both CLIs are pre-compiled and bundled inside the image.

1. **Authentication Configuration**:
   - **For GitHub Copilot**:
     Add your GitHub token directly into your `.env` file:
     ```ini
     GITHUB_TOKEN=gho_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
     ```
     *(No host directory mounting or local `gh` CLI required).*

   - **For Google Antigravity**:
     Perform a one-time interactive login directly inside the container:
     ```bash
     docker compose -f docker-compose.standalone.yml run --rm api agy
     ```
     A Google OAuth authorization URL will appear in your terminal. Open the URL in your browser, approve access, and the session token is automatically saved into the Docker persistent named volume `gemini_data` (`/root/.gemini/antigravity-cli/antigravity-oauth-token`).

2. **Launch the stack**:
   ```bash
   docker compose -f docker-compose.standalone.yml up -d --build
   ```

*(Note: In standalone mode, auth data in `gemini_data` persists across container restarts, stops, and rebuilds).*

> [!TIP]
> **Already running a Reverse Proxy (Nginx Proxy Manager, Traefik, Pangolin, Cloudflare Tunnel)?**  
> If ports 80 and 443 are already occupied on your host (`bind: address already in use`), you can bypass Caddy:
> 1. In `docker-compose.yml` (or `docker-compose.standalone.yml`), comment out the `caddy` service.
> 2. Expose port `8000:8000` directly under the `api` service:
>    ```yaml
>    api:
>      ports:
>        - "8000:8000"
>    ```
> 3. Point your existing reverse proxy or Cloudflare Tunnel directly to `http://<server-ip>:8000`.

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
| `/v1/chat/completions` | `POST` | Bearer / Header | **OpenAI Chat Completions** (streaming, multi-turn, tool calling, vision & files) |
| `/v1/files` | `POST`, `GET` | Bearer / Header | **OpenAI Files API** (upload scans, images, documents; list files) |
| `/v1/files/{file_id}` | `GET`, `DELETE` | Bearer / Header | Retrieve file metadata or delete file |
| `/v1/files/{file_id}/content` | `GET` | Bearer / Header | Download raw uploaded file content |
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

### 📂 `POST /v1/files` (OpenAI Files API & Multimodal Scans)

Full implementation of the OpenAI Files standard for **image analysis** and **scanned document analysis (PDFs & multi-page scans)**.

#### 1. Upload a File, Scan, or Image

```bash
curl -X POST "https://$DOMAIN/v1/files" \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@scanned_invoice.pdf" \
  -F "purpose=assistants"
```

Response:
```json
{
  "id": "file-4c91d09e598f4803",
  "object": "file",
  "bytes": 245100,
  "created_at": 1729178900,
  "filename": "scanned_invoice.pdf",
  "purpose": "assistants",
  "status": "processed",
  "status_details": null
}
```

#### 2. Analyze the Uploaded File in Chat Completions

Reference the file via `attachments` or `file_ids`:

```bash
curl -N -X POST "https://$DOMAIN/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "messages": [
      {
        "role": "user",
        "content": "Estrai numero fattura, data, fornitore e importo da questo documento scansionato:",
        "attachments": [{"file_id": "file-4c91d09e598f4803"}]
      }
    ]
  }'
```

#### 3. Direct Inline Multimodal Vision (Base64)

Directly pass base64 image or PDF data URLs in standard OpenAI format:

```bash
curl -N -X POST "https://$DOMAIN/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "Cosa c'\''è in questa scansione?"},
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,...base64_data..."}}
        ]
      }
    ]
  }'
```

#### Multi-Provider Multimodal Handling
* **Google Antigravity (`agy` / Gemini)**: Files uploaded or passed as data URLs are stored in the persistent volume and made accessible to `agy` with `--add-dir`. Gemini natively inspects local PDFs, scans, and images directly!
* **GitHub Copilot / Models**: Images are forwarded directly as standard OpenAI vision `image_url` data URLs. Text files are injected into prompt context.

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
├── api.py                      # FastAPI app (OpenAI Chat, Responses & Files API, auth, rate limiting)
├── worker.py                   # RQ worker entry point
├── tasks.py                    # Task execution (Copilot OpenAI client, Antigravity agy CLI, formatters)
├── files_store.py              # OpenAI Files storage, MIME extraction & metadata caching
├── entrypoint.sh               # Injects GITHUB_TOKEN and validates agy CLI binary
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Lightweight Python 3.11-slim container (GHCR default)
├── Dockerfile.standalone       # Self-contained container with bundled gh and agy CLIs
├── docker-compose.yml          # Default multi-service stack (Caddy, Redis, API, Worker)
├── docker-compose.standalone.yml # Standalone multi-service stack with persistent volume
├── .dockerignore               # Optimizes docker build context
├── Caddyfile                   # Caddy reverse proxy with automatic TLS & security headers
├── tests/                      # Automated test suite (21 unit tests)
│   ├── test_gateway.py         # Tests for routing, auth, converters, and binary resolution
│   └── test_files.py           # Tests for OpenAI Files API & multimodal uploads
├── docs/assets/                # Documentation assets (demo.gif)
├── MEMORY.md                   # Architecture and technical documentation
└── .env.example                # Environment variable template
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
