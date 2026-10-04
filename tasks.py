"""
tasks.py – RQ task that calls the GitHub Copilot / GitHub Models API or Google Antigravity (agy).

Backends:
---------
1. GitHub Models / Copilot:
   - Free tier: https://models.inference.ai.azure.com
   - Copilot API: https://api.githubcopilot.com
2. Antigravity (agy):
   - Local CLI binary (e.g. ~/.gemini/bin/agy or /root/.gemini/bin/agy)
   - Uses local ~/.gemini authentication
"""

import os
import json
import shutil
import base64
import tempfile
import subprocess
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

ANTIGRAVITY_MODELS = [
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_agy_bin() -> str:
    """Locate the agy CLI binary path."""
    explicit = os.getenv("AGY_PATH")
    if explicit and os.path.exists(explicit):
        return explicit
    which_agy = shutil.which("agy")
    if which_agy:
        return which_agy
    candidates = [
        os.path.expanduser("~/.gemini/bin/agy"),
        "/root/.gemini/bin/agy",
        "/root/.gemini/bin/agy",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "agy"


def is_antigravity_model(model: str | None) -> bool:
    """Check if model name matches Antigravity model patterns."""
    if not model:
        return False
    m = model.lower()
    return (
        m.startswith("gemini-")
        or m.startswith("claude-sonnet-4-6")
        or m.startswith("claude-opus-4-6")
        or m.startswith("gpt-oss-")
        or m in ANTIGRAVITY_MODELS
    )


def resolve_provider(model: str | None = None, provider: str | None = None) -> str:
    """Determine the provider ('copilot' or 'antigravity')."""
    if provider:
        return provider.lower()
    if model and is_antigravity_model(model):
        return "antigravity"
    return os.getenv("PROVIDER", "copilot").lower()


# ── Copilot / GitHub Models ───────────────────────────────────────────────────

def _get_client() -> tuple:
    """Return (client, default_model). Reads env vars at call time."""
    github_token = os.getenv("GITHUB_TOKEN", "")
    base_url = os.getenv("COPILOT_BASE_URL", "https://models.inference.ai.azure.com")
    default_model = os.getenv("COPILOT_MODEL", "gpt-4o-mini")
    if not github_token:
        return None, default_model
    return OpenAI(base_url=base_url, api_key=github_token), default_model


def _token_check() -> dict | None:
    if not os.getenv("GITHUB_TOKEN", ""):
        return {"error": "GITHUB_TOKEN environment variable is not set.", "content": None, "model": None}
    return None


def ask_copilot(prompt: str, model: str | None = None) -> dict:
    """Send *prompt* to the GitHub Copilot / Models API."""
    err = _token_check()
    if err:
        return err

    client, default_model = _get_client()
    chosen_model = model or default_model

    response = client.chat.completions.create(
        model=chosen_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    return {
        "content": response.choices[0].message.content,
        "model": response.model,
        "provider": "copilot",
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }


def ask_copilot_vision(
    prompt: str,
    image_b64: str,
    mime_type: str = "image/jpeg",
    model: str | None = None,
) -> dict:
    """Send prompt + base64 image to Copilot vision."""
    err = _token_check()
    if err:
        return err

    client, default_model = _get_client()
    chosen_model = model or "gpt-4o"
    data_url = f"data:{mime_type};base64,{image_b64}"

    response = client.chat.completions.create(
        model=chosen_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        temperature=0.7,
    )

    return {
        "content": response.choices[0].message.content,
        "model": response.model,
        "provider": "copilot",
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }


# ── Antigravity (agy) ─────────────────────────────────────────────────────────

def ask_antigravity(
    prompt: str,
    model: str | None = None,
    conversation_id: str | None = None,
    effort: str | None = None,
) -> dict:
    """Execute non-interactive prompt using the agy CLI."""
    agy_bin = _find_agy_bin()
    chosen_model = model or os.getenv("ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
    chosen_effort = effort or os.getenv("ANTIGRAVITY_EFFORT")

    cmd = [
        agy_bin,
        "-p", prompt,
        "--model", chosen_model,
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
    ]
    if chosen_effort:
        cmd.extend(["--effort", chosen_effort])
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return {
            "error": f"agy binary not found at '{agy_bin}'. Ensure Antigravity is installed/mounted.",
            "content": None,
            "model": chosen_model,
            "provider": "antigravity",
        }
    except subprocess.TimeoutExpired:
        return {
            "error": "Antigravity process timed out after 180 seconds.",
            "content": None,
            "model": chosen_model,
            "provider": "antigravity",
        }

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
        return {
            "error": f"agy execution error: {err_msg}",
            "content": None,
            "model": chosen_model,
            "provider": "antigravity",
        }

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "content": proc.stdout.strip(),
            "model": chosen_model,
            "provider": "antigravity",
            "usage": {},
        }

    usage = data.get("usage", {})
    return {
        "content": (data.get("response") or "").rstrip("\n"),
        "model": chosen_model,
        "conversation_id": data.get("conversation_id"),
        "provider": "antigravity",
        "duration_seconds": data.get("duration_seconds"),
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "thinking_tokens": usage.get("thinking_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


def ask_antigravity_vision(
    prompt: str,
    image_b64: str,
    mime_type: str = "image/jpeg",
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    """Write base64 image to a temporary file and invoke agy to analyze it."""
    ext = ".jpg"
    if "png" in mime_type:
        ext = ".png"
    elif "webp" in mime_type:
        ext = ".webp"
    elif "gif" in mime_type:
        ext = ".gif"

    temp_file = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    temp_path = temp_file.name
    try:
        temp_file.write(base64.b64decode(image_b64))
        temp_file.close()

        vision_prompt = f"Analyze this image: {temp_path}\n{prompt}"
        chosen_model = model or os.getenv("ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
        return ask_antigravity(
            prompt=vision_prompt,
            model=chosen_model,
            effort=effort,
        )
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ── Unified Dispatchers ───────────────────────────────────────────────────────

def dispatch_ask(
    prompt: str,
    model: str | None = None,
    provider: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Route ask request to the appropriate backend."""
    active_provider = resolve_provider(model=model, provider=provider)
    if active_provider == "antigravity":
        return ask_antigravity(prompt=prompt, model=model, conversation_id=conversation_id)
    return ask_copilot(prompt=prompt, model=model)


def dispatch_ask_vision(
    prompt: str,
    image_b64: str,
    mime_type: str = "image/jpeg",
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Route ask-image request to the appropriate backend."""
    active_provider = resolve_provider(model=model, provider=provider)
    if active_provider == "antigravity":
        return ask_antigravity_vision(
            prompt=prompt,
            image_b64=image_b64,
            mime_type=mime_type,
            model=model,
        )
    return ask_copilot_vision(
        prompt=prompt,
        image_b64=image_b64,
        mime_type=mime_type,
        model=model,
    )
