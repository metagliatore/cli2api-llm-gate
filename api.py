import os
import base64
import time
import hashlib
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Body, UploadFile, File, Form, Depends, Header, Request
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from rq.job import Job, NoSuchJobError

from tasks import ask_copilot, ask_copilot_vision

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
API_KEY = os.getenv("API_KEY", "")
API_KEYS = os.getenv("API_KEYS", "")
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))
PUBLIC_HEALTH = os.getenv("PUBLIC_HEALTH", "true").lower() == "true"

app = FastAPI(title="GitHub Copilot API", version="2.0.0")

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue("copilot", connection=redis_conn)


def _get_valid_api_keys() -> set[str]:
    """
    Return all valid API keys.

    Priority:
    1) API_KEYS (comma-separated): key1,key2,key3
    2) API_KEY (single key, backward compatibility)
    """
    keys: set[str] = set()

    if API_KEYS:
        keys.update(k.strip() for k in API_KEYS.split(",") if k.strip())

    if API_KEY:
        keys.add(API_KEY.strip())

    return keys


def _extract_api_key(
    authorization: str | None,
    x_api_key: str | None,
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def auth_and_rate_limit(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    valid_keys = _get_valid_api_keys()
    if not valid_keys:
        raise HTTPException(
            status_code=500,
            detail="No API key configured. Set API_KEYS or API_KEY on the server",
        )

    provided_key = _extract_api_key(authorization, x_api_key)
    if not provided_key or provided_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid API key")

    key_fingerprint = hashlib.sha256(provided_key.encode()).hexdigest()[:16]
    limiter_key = f"ratelimit:{key_fingerprint}:{int(time.time() // 60)}"
    current = redis_conn.incr(limiter_key)
    if current == 1:
        redis_conn.expire(limiter_key, 61)
    if current > RATE_LIMIT_RPM:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    request.state.api_key_fingerprint = key_fingerprint


@app.get("/ask")
def ask(
    _: None = Depends(auth_and_rate_limit),
    prompt: str = Query(..., description="Natural-language prompt for Copilot"),
    model: str | None = Query(
        None,
        description=(
            "Model to use. Examples: gpt-4o-mini (default, free), gpt-4o, "
            "meta-llama-3.1-8b-instruct, claude-sonnet-4-5 (Copilot subscription)"
        ),
    ),
):
    """Enqueue a Copilot job and return the job_id immediately."""
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    job = queue.enqueue(ask_copilot, prompt, model, job_timeout=120)
    return {"job_id": job.id, "status": job.get_status(), "model_requested": model}


@app.get("/result/{job_id}")
def result(job_id: str, _: None = Depends(auth_and_rate_limit)):
    """Poll the status and result of a previously enqueued job."""
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    status = job.get_status()
    response = {"job_id": job_id, "status": str(status)}

    if status.value == "finished":
        response["result"] = job.result
    elif status.value == "failed":
        response["error"] = str(job.exc_info)

    return response


@app.get("/models")
def models(_: None = Depends(auth_and_rate_limit)):
    """Return the list of suggested available models."""
    return {
        "github_models_free_tier": [
            "gpt-4o-mini",
            "gpt-4o",
            "meta-llama-3.1-8b-instruct",
            "meta-llama-3.1-70b-instruct",
            "mistral-small",
            "phi-4",
        ],
        "github_copilot_api_subscription_required": [
            "gpt-4o-mini",
            "gpt-4o",
            "o1-mini",
            "claude-sonnet-4-5",
        ],
        "note": "Set COPILOT_BASE_URL and GITHUB_TOKEN env vars to choose the backend.",
    }


@app.get("/health")
def health(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    if not PUBLIC_HEALTH:
        valid_keys = _get_valid_api_keys()
        provided_key = _extract_api_key(authorization, x_api_key)
        if not valid_keys or not provided_key or provided_key not in valid_keys:
            raise HTTPException(status_code=401, detail="Invalid API key")
    return {"status": "ok"}


# ── Vision endpoint ───────────────────────────────────────────────────────────

SUPPORTED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB


@app.post("/ask-image")
async def ask_image(
    _: None = Depends(auth_and_rate_limit),
    prompt: str = Form(..., description="Question or instruction about the image"),
    model: str = Form("gpt-4o", description="Vision model to use (gpt-4o or gpt-4o-mini)"),
    file: UploadFile = File(..., description="Image file (JPEG, PNG, WEBP, GIF)"),
):
    """
    Upload an image and ask a question about it.
    Returns a job_id — poll /result/{job_id} for the answer.

    Example with curl:
        curl -X POST http://localhost:8000/ask-image \\
          -F 'prompt=Describe what you see in this chart' \\
          -F 'model=gpt-4o' \\
          -F 'file=@/path/to/image.png'
    """
    mime_type = file.content_type or "image/jpeg"
    if mime_type not in SUPPORTED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type '{mime_type}'. Allowed: {sorted(SUPPORTED_MIME)}",
        )

    raw = await file.read()
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 20 MB limit")

    image_b64 = base64.b64encode(raw).decode()

    job = queue.enqueue(
        ask_copilot_vision,
        prompt,
        image_b64,
        mime_type,
        model,
        job_timeout=180,
    )
    return {"job_id": job.id, "status": job.get_status(), "model_requested": model}


# ── OpenAI-compatible endpoint ────────────────────────────────────────────────
# Drop-in replacement for Ollama / OpenAI clients (LangChain, Continue.dev, etc.)

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = False

@app.post("/v1/chat/completions")
def chat_completions(body: ChatRequest = Body(...), _: None = Depends(auth_and_rate_limit)):
    """
    OpenAI-compatible synchronous chat endpoint.
    Waits up to 60 s for the worker to return a result.
    Compatible with LangChain, LlamaIndex, Continue.dev, Open WebUI, etc.
    """
    # Flatten messages into a single prompt string
    prompt = "\n".join(f"{m.role.upper()}: {m.content}" for m in body.messages)

    job = queue.enqueue(ask_copilot, prompt, body.model, job_timeout=120)

    # Poll until finished or timeout
    for _ in range(120):
        job.refresh()
        if job.is_finished or job.is_failed:
            break
        time.sleep(0.5)

    if job.is_failed:
        raise HTTPException(status_code=500, detail=str(job.exc_info))

    result = job.result or {}
    return {
        "id": f"chatcmpl-{job.id}",
        "object": "chat.completion",
        "model": result.get("model", body.model or "gpt-4o-mini"),
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
