import os
import base64
import time
import hashlib
from typing import Optional, Union, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Body, UploadFile, File, Form, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from redis import Redis
from rq import Queue
from rq.job import Job, NoSuchJobError

from tasks import (
    dispatch_ask,
    dispatch_ask_vision,
    dispatch_chat_completion,
    dispatch_chat_stream,
    resolve_provider,
    ANTIGRAVITY_MODELS,
    COPILOT_MODELS,
    get_available_models,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
API_KEY = os.getenv("API_KEY", "")
API_KEYS = os.getenv("API_KEYS", "")
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))
PUBLIC_HEALTH = os.getenv("PUBLIC_HEALTH", "true").lower() == "true"
DEFAULT_PROVIDER = os.getenv("PROVIDER", "copilot").lower()

app = FastAPI(
    title="cli2api-ai – Multi-Provider AI HTTP API (Copilot & Antigravity)",
    version="2.2.0",
    description="OpenAI-compatible and asynchronous API for GitHub Copilot / Models and Google Antigravity.",
)

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
    try:
        current = redis_conn.incr(limiter_key)
        if current == 1:
            redis_conn.expire(limiter_key, 61)
        if current > RATE_LIMIT_RPM:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    except HTTPException:
        raise
    except Exception:
        # Fail-open if Redis is temporarily unavailable
        pass

    request.state.api_key_fingerprint = key_fingerprint


# ── Asynchronous Queue Endpoints ─────────────────────────────────────────────

@app.get("/ask")
def ask(
    _: None = Depends(auth_and_rate_limit),
    prompt: str = Query(..., description="Natural-language prompt"),
    model: str | None = Query(
        None,
        description=(
            "Model to use. Examples: gpt-4o-mini, gpt-4o (Copilot); "
            "gemini-3.8-flash-high, claude-sonnet-4-6, claude-opus-4-6-thinking (Antigravity)"
        ),
    ),
    provider: str | None = Query(
        None,
        description="Override provider: 'copilot' or 'antigravity'. Defaults to server PROVIDER env.",
    ),
):
    """Enqueue an AI prompt job and return the job_id immediately."""
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    target_provider = resolve_provider(model=model, provider=provider)
    job = queue.enqueue(dispatch_ask, prompt, model, target_provider, job_timeout=180)
    status_str = job.get_status()
    if hasattr(status_str, "value"):
        status_str = status_str.value
    else:
        status_str = str(status_str)

    return {
        "job_id": job.id,
        "status": status_str,
        "model_requested": model,
        "provider": target_provider,
    }


@app.get("/result/{job_id}")
def result(job_id: str, _: None = Depends(auth_and_rate_limit)):
    """Poll the status and result of a previously enqueued job."""
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    status = job.get_status()
    status_val = status.value if hasattr(status, "value") else str(status)
    response = {"job_id": job_id, "status": status_val}

    if status_val == "finished":
        response["result"] = job.result
    elif status_val == "failed":
        response["error"] = str(job.exc_info)

    return response


# ── Models & Health ──────────────────────────────────────────────────────────

@app.get("/v1/models")
def list_v1_models(_: None = Depends(auth_and_rate_limit)):
    """List available models in OpenAI-compatible schema."""
    return {
        "object": "list",
        "data": get_available_models(),
    }


@app.get("/v1/models/{model_id}")
def get_v1_model(model_id: str, _: None = Depends(auth_and_rate_limit)):
    """Retrieve model info in OpenAI-compatible schema."""
    all_models = {m["id"]: m for m in get_available_models()}
    if model_id in all_models:
        return all_models[model_id]

    return {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "cli2api-ai",
        "permission": [],
        "root": model_id,
        "parent": None,
    }


@app.get("/models")
def models(_: None = Depends(auth_and_rate_limit)):
    """Return the list of suggested available models across providers (legacy endpoint)."""
    return {
        "active_default_provider": DEFAULT_PROVIDER,
        "antigravity_models": ANTIGRAVITY_MODELS,
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
        "note": (
            "Set PROVIDER=copilot or PROVIDER=antigravity in .env, or specify "
            "?provider= per request. Antigravity models are also automatically detected from model names."
        ),
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
    return {"status": "ok", "provider": DEFAULT_PROVIDER}


# ── Vision Endpoint ──────────────────────────────────────────────────────────

SUPPORTED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB


@app.post("/ask-image")
async def ask_image(
    _: None = Depends(auth_and_rate_limit),
    prompt: str = Form(..., description="Question or instruction about the image"),
    model: str | None = Form(None, description="Vision model to use (e.g. gpt-4o or gemini-3.8-flash-high)"),
    provider: str | None = Form(None, description="Override provider: 'copilot' or 'antigravity'"),
    file: UploadFile = File(..., description="Image file (JPEG, PNG, WEBP, GIF)"),
):
    """
    Upload an image and ask a question about it.
    Returns a job_id — poll /result/{job_id} for the answer.
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
    target_provider = resolve_provider(model=model, provider=provider)

    job = queue.enqueue(
        dispatch_ask_vision,
        prompt,
        image_b64,
        mime_type,
        model,
        target_provider,
        job_timeout=240,
    )
    status_str = job.get_status()
    if hasattr(status_str, "value"):
        status_str = status_str.value
    else:
        status_str = str(status_str)

    return {
        "job_id": job.id,
        "status": status_str,
        "model_requested": model,
        "provider": target_provider,
    }


# ── OpenAI-Compatible Chat Completions Endpoint ───────────────────────────────
# Seamless integration with Pi Coding Agent, LangChain, Aider, Open WebUI, AutoGen, etc.

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Optional[Union[str, List[Union[Dict[str, Any], Any]]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: Optional[str] = None
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    stream_options: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    response_format: Optional[Dict[str, Any]] = None
    provider: Optional[str] = None


@app.post("/v1/chat/completions")
def chat_completions(body: ChatRequest = Body(...), _: None = Depends(auth_and_rate_limit)):
    """
    OpenAI-compatible chat completions endpoint.
    Supports real-time streaming (SSE text/event-stream), tool/function calling,
    multi-turn conversations, and both Antigravity and Copilot providers.
    """
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages list must not be empty")

    effective_max_tokens = body.max_tokens or body.max_completion_tokens

    if body.stream:
        return StreamingResponse(
            dispatch_chat_stream(
                messages=body.messages,
                model=body.model,
                provider=body.provider,
                tools=body.tools,
                tool_choice=body.tool_choice,
                temperature=body.temperature,
                max_tokens=effective_max_tokens,
                stop=body.stop,
                stream_options=body.stream_options,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = dispatch_chat_completion(
        messages=body.messages,
        model=body.model,
        provider=body.provider,
        tools=body.tools,
        tool_choice=body.tool_choice,
        temperature=body.temperature,
        max_tokens=effective_max_tokens,
        stop=body.stop,
    )

    if "error" in result and result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])

    return result
