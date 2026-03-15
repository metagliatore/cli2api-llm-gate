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


def ask_copilot(prompt: str, model: str | None = None) -> dict:
    """
    Send *prompt* to the GitHub Copilot / Models API and return the response.
    Executed inside the RQ worker process.
    Env vars are read here (not at module level) so entrypoint-injected
    values are always picked up.
    """
    github_token = os.getenv("GITHUB_TOKEN", "")
    base_url = os.getenv("COPILOT_BASE_URL", "https://models.inference.ai.azure.com")
    default_model = os.getenv("COPILOT_MODEL", "gpt-4o-mini")

    if not github_token:
        return {
            "error": "GITHUB_TOKEN environment variable is not set.",
            "content": None,
            "model": None,
        }

    client = OpenAI(
        base_url=base_url,
        api_key=github_token,
    )

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
