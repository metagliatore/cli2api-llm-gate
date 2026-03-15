import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from rq.job import Job, NoSuchJobError

from tasks import ask_copilot

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

app = FastAPI(title="GitHub Copilot API", version="2.0.0")

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue("copilot", connection=redis_conn)


@app.get("/ask")
def ask(
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
def result(job_id: str):
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
def models():
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
def health():
    return {"status": "ok"}


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
def chat_completions(body: ChatRequest = Body(...)):
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
