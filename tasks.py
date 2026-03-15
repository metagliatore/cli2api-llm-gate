"""
tasks.py – RQ task that calls the GitHub Copilot / GitHub Models API.

Authentication
--------------
Set GITHUB_TOKEN to a fine-grained PAT with "Copilot Requests" permission
(Copilot API) or any PAT (GitHub Models free tier).
See: https://github.com/settings/personal-access-tokens/new

Backend / Base URL
------------------
COPILOT_BASE_URL (env):
  https://models.inference.ai.azure.com   ← GitHub Models, FREE tier (default)
  https://api.githubcopilot.com           ← GitHub Copilot API (needs subscription)

Model selection
---------------
COPILOT_MODEL (env) – overrides per-request default.

Models available on the FREE GitHub Models tier:
  gpt-4o-mini                  ← cheapest, default
  gpt-4o
  meta-llama-3.1-8b-instruct
  meta-llama-3.1-70b-instruct
  mistral-small
  phi-4

Models available on the GitHub Copilot API (subscription required):
  gpt-4o-mini
  gpt-4o
  o1-mini
  claude-sonnet-4-5
"""

import os
from openai import OpenAI


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
    """
    Send *prompt* to the GitHub Copilot / Models API and return the response.
    Executed inside the RQ worker process.
    Env vars are read here (not at module level) so entrypoint-injected
    values are always picked up.
    """
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
    """
    Send a prompt + base64-encoded image to a vision-capable model.

    Supported models (both backends):
      gpt-4o          ← best quality for vision
      gpt-4o-mini     ← lighter, also supports vision

    image_b64  : base64-encoded image bytes (no data-URI prefix)
    mime_type  : e.g. "image/jpeg", "image/png", "image/webp", "image/gif"
    """
    err = _token_check()
    if err:
        return err

    client, default_model = _get_client()
    # Vision works best with gpt-4o; fall back to default if user set one explicitly
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
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }
