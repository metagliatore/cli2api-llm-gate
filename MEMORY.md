# Progetto: Multi-Provider AI HTTP API (`docker-copilot-api`)
**File di Memoria del Progetto (MEMORY.md)**  
*Data ultimo aggiornamento: Ottobre 2026*  
*Fornitori supportati: GitHub Copilot / GitHub Models e Google Antigravity (`agy`)*

---

## 1. Panoramica del Progetto

Il progetto è un microservizio containerizzato e pronto per la produzione che espone i modelli AI di **GitHub Copilot / GitHub Models** e di **Google Antigravity (`agy`)** attraverso un'interfaccia HTTP unificata, RESTful e compatibile con lo standard OpenAI (`/v1/chat/completions`).

### Obiettivi Chiave
- **Fornitori multipli selezionabili da configurazione o richiesta**:
  - `copilot`: GitHub Models (Free Tier su Azure AI Inference) o GitHub Copilot API.
  - `antigravity`: Google Antigravity tramite la CLI `agy` presente sulla macchina host (modelli Gemini 3.8 Flash, Gemini 3.1 Pro, Claude Sonnet 4.6 Thinking, Claude Opus 4.6 Thinking, ecc.).
- **Zero gestione credenziali esterne per i client**:
  - I token GitHub vengono ereditati dal mount di `$HOME/.config/gh` via `gh auth login`.
  - La sessione Antigravity viene ereditata dal mount di `$HOME/.gemini` dell'host.
  - I client esterni (Home Assistant, n8n, Open WebUI, Continue.dev, curl, mobile) usano token Bearer interni definiti in `.env` (`API_KEYS`).
- **Doppia modalità di consumo**:
  - *Asincrona con code Redis (RQ)*: per prompt lunghi o task di elaborazione visiva (`/ask`, `/ask-image`, `/result/{job_id}`).
  - *Sincrona compatibile OpenAI*: drop-in replacement immediato per qualsiasi client che supporti base URL custom (`/v1/chat/completions`).
- **Sicurezza da produzione su VPS**:
  - Reverse proxy Caddy con HTTPS/TLS automatico (porte `80`/`443`).
  - Porte interne (`8000` di FastAPI e `6379` di Redis) isolate nella rete Docker `backend` (nessun conflitto con Portainer su `:8000`).
  - Rate limiting per singolo token API su bucket Redis al minuto (`RATE_LIMIT_RPM`).
- **Retrocompatibilità trasparente al 100% per i client esistenti**:
  - Se un applicativo legacy invia modelli Copilot/OpenAI (es. `gpt-4o-mini`, `gpt-4o`) mentre il provider attivo è `antigravity`, il worker mappa automaticamente la richiesta sul modello Antigravity di default (`gemini-3.8-flash-high`) senza rompere i client esistenti.
  - L'endpoint sincrono `/v1/chat/completions` gestisce il polling fino a 180s per i modelli Antigravity con reasoning elevato.

---

## 2. Architettura del Sistema

```
                      INTERNET / CLIENTS
                              │
               (HTTPS: 443 / HTTP: 80 - TLS Automatico)
                              ▼
                     ┌──────────────────┐
                     │  Caddy Container │ (Caddy 2-alpine)
                     │ (Reverse Proxy)  │ - Headers di sicurezza
                     └────────┬─────────┘
                              │ Rete interna Docker ('backend')
                              ▼
                     ┌──────────────────┐
                     │  FastAPI (api)   │ Port :8000 interna (non bindata su host)
                     │                  │ - Autenticazione multi-Bearer (API_KEYS)
                     │                  │ - Rate-limiter su Redis (RATE_LIMIT_RPM)
                     │                  │ - Routing del provider (env o per-request)
                     └────┬────────┬────┘
                          │        │
             Enqueue jobs │        │ Rate-limit counter / fetch status
                          ▼        ▼
                     ┌──────────────────┐
                     │ Redis Container  │ (Redis 7-alpine)
                     │ (Internal only)  │ Coda: 'copilot', 'antigravity'
                     └────────▲─────────┘
                              │
                              │ Dequeue & esecuzione task
                     ┌────────┴─────────┐
                     │ RQ Worker        │ Scalabile orizzontalmente (--scale worker=N)
                     │ (worker.py)      │ Monta ~/.config/gh e ~/.gemini
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

## 3. Mappa dei File e Responsabilità

| File | Ruolo / Descrizione |
|---|---|
| [`api.py`](api.py) | **Applicazione FastAPI**: definisce gli endpoint (`/ask`, `/result/{job_id}`, `/ask-image`, `/v1/chat/completions`, `/models`, `/health`), gestisce validazione, rate-limiting e instradamento al dispatcher opportuno. |
| [`tasks.py`](tasks.py) | **Logica di elaborazione RQ**: funzioni `dispatch_ask()`, `ask_antigravity()`, `ask_antigravity_vision()`, `ask_copilot()`, `ask_copilot_vision()`. Riconosce i modelli e invoca il backend appropriato. |
| [`worker.py`](worker.py) | **Processo Worker RQ**: effettua il polling sulle code Redis `copilot` e `antigravity`, eseguendo i job accodati. |
| [`Caddyfile`](Caddyfile) | **Configurazione Caddy**: binding del dominio `${DOMAIN}`, compressione (`zstd`, `gzip`), reverse proxy su `api:8000` e hardening HTTP. |
| [`docker-compose.yml`](docker-compose.yml) | **Orchestrazione container**: 4 servizi (`caddy`, `redis`, `api`, `worker`), mount volumi di autenticazione (`$HOME/.config/gh` e `$HOME/.gemini`), network isolata `backend`. |
| [`Dockerfile`](Dockerfile) | **Immagine Docker (Python 3.11-slim)**: installa `gh` CLI, esporta `/root/.gemini/bin` in PATH, installa `requirements.txt` e configura `entrypoint.sh`. |
| [`entrypoint.sh`](entrypoint.sh) | **Script di avvio container**: verifica presenza e validità di `gh auth token` per Copilot e del binario `agy` per Antigravity. |
| [`requirements.txt`](requirements.txt) | **Dipendenze Python**: `fastapi`, `uvicorn[standard]`, `redis`, `rq`, `openai`, `python-multipart`. |
| [`.env.example`](.env.example) | Template di configurazione con parametri provider, modelli di default, chiavi di sicurezza, domini e rate limit. |
| [`.env`](.env) | Configurazione locale dell'istanza. |
| [`README.md`](README.md) | Documentazione completa per sviluppatori e guida all'integrazione con client terzi. |

---

## 4. Modalità di Selezione del Provider

La selezione del fornitore segue una logica a cascata flessibile:

1. **Parametro esplicito nella richiesta**:
   - `GET /ask?prompt=...&provider=antigravity` (o `provider=copilot`)
   - `POST /ask-image` con campo form `provider`
2. **Rilevamento automatico dal modello richiesto**:
   - Se il modello inizia con `gemini-`, `claude-sonnet-4-6`, `claude-opus-4-6` o `gpt-oss-`, il sistema instrada automaticamente verso **Google Antigravity**.
   - Se il modello è un modello GitHub/Azure noto (`gpt-4o`, `gpt-4o-mini`, `meta-llama-...`), instrada verso **Copilot**.
3. **Valore predefinito in `.env` (`PROVIDER`)**:
   - `PROVIDER=copilot` (default storico, retrocompatibile)
   - `PROVIDER=antigravity` (per usare Antigravity come fornitore predefinito dell'intera istanza).

---

## 5. Dettaglio dei Modelli e Fornitori

### Provider: Google Antigravity (`agy`)
- Esegue `/root/.gemini/bin/agy -p "<prompt>" --model <model> --output-format json --dangerously-skip-permissions --disable-slash-commands`.
- Modelli supportati:
  - `gemini-3.8-flash-high` (*Default Antigravity*, latenza ~2s, reasoning rapido, contesto 1M+)
  - `gemini-3.8-flash-medium`
  - `gemini-3.8-flash-low`
  - `gemini-3.7-flash-high`
  - `gemini-3.1-pro-high` (ragionamento approfondito)
  - `claude-sonnet-4-6` (Thinking abilitato)
  - `claude-opus-4-6-thinking` (Thinking abilitato)
  - `gpt-oss-120b-medium`
- Supporto Vision: le immagini inviate a `/ask-image` vengono salvate temporaneamente su file e passate con istruzione contestuale ad `agy`, sfruttando le capacità visive native dell'agente.

### Provider: GitHub Copilot / Models
- Esegue chiamate client OpenAI su Azure AI Inference (`COPILOT_BASE_URL`).
- Modelli supportati:
  - Free tier: `gpt-4o-mini`, `gpt-4o`, `meta-llama-3.1-8b-instruct`, `meta-llama-3.1-70b-instruct`, `phi-4`.
  - Copilot Sub: `gpt-4o-mini`, `gpt-4o`, `o1-mini`, `claude-sonnet-4-5`.

---

## 6. Sicurezza e Autenticazione Client

- **Multi-Token (`API_KEYS`)**: comma-separated tokens in `.env` (`token_ha,token_n8n,token_mobile`).
- **Single-Token (`API_KEY`)**: retrocompatibilità.
- **Header supportati**: `Authorization: Bearer <token>` oppure `X-API-Key: <token>`.
- **Rate Limit**: memorizzato in Redis come `ratelimit:<sha256_hash_16>:<minuto_unix>`. Superato `RATE_LIMIT_RPM`, restituisce HTTP 429.
- **Portainer Safe**: nessun bind su porta 8000 host.

---

## 7. Variabili d'Ambiente Principali

| Variabile | Default | Descrizione |
|---|---|---|
| `PROVIDER` | `copilot` | Fornitore attivo predefinito (`copilot` o `antigravity`) |
| `ANTIGRAVITY_MODEL` | `gemini-3.8-flash-high` | Modello di default per Antigravity |
| `ANTIGRAVITY_EFFORT` | *vuoto* | Livello di effort di ragionamento (`low`, `medium`, `high`, `xhigh`) |
| `AGY_PATH` | *auto* | Percorso opzionale al binario `agy` (default `/root/.gemini/bin/agy`) |
| `COPILOT_BASE_URL` | `https://models.inference.ai.azure.com` | Base URL inference GitHub / Copilot |
| `COPILOT_MODEL` | `gpt-4o-mini` | Modello di default per Copilot |
| `API_KEYS` | *vuoto* | Token multipli separati da virgola |
| `API_KEY` | *vuoto* | Token singolo |
| `RATE_LIMIT_RPM` | `60` | Richieste per minuto per chiave |
| `PUBLIC_HEALTH` | `true` | Rende `/health` accessibile senza Bearer |
| `DOMAIN` | `localhost` | Dominio Caddy TLS |

---

## 8. Comandi di Esempio

```bash
# Avvio dello stack
docker compose up -d --build

# Chiamata con provider Antigravity esplicito
curl -H "Authorization: Bearer <TOKEN>" \
  "https://<DOMAIN>/ask?prompt=Spiega+la+teoria+della+relativita&model=gemini-3.8-flash-high&provider=antigravity"

# Chiamata OpenAI-compatibile a Claude Sonnet tramite Antigravity
curl https://<DOMAIN>/v1/chat/completions \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Risolvi questo problema in Python..."}]
  }'

# Verifica salute e provider attivo
curl "https://<DOMAIN>/health"
# -> {"status": "ok", "provider": "copilot"}
```

