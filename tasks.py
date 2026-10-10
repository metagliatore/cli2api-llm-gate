"""
tasks.py – Tasks and completion handlers for GitHub Copilot / Models and Google Antigravity (agy).

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
import re
import json
import uuid
import time
import shutil
import base64
import tempfile
import logging
import sqlite3
import subprocess
from typing import Optional, Union, List, Dict, Any, Generator

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)

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

COPILOT_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "o1-mini",
    "o1-preview",
    "claude-sonnet-4-5",
    "meta-llama-3.1-8b-instruct",
    "meta-llama-3.1-70b-instruct",
    "mistral-small",
    "phi-4",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_available_models() -> List[Dict[str, Any]]:
    """Return all models in OpenAI /v1/models list format."""
    now = int(time.time())
    seen = set()
    model_list = []

    for m in ANTIGRAVITY_MODELS:
        if m not in seen:
            seen.add(m)
            model_list.append({
                "id": m,
                "object": "model",
                "created": now,
                "owned_by": "antigravity",
                "permission": [],
                "root": m,
                "parent": None,
            })

    for m in COPILOT_MODELS:
        if m not in seen:
            seen.add(m)
            model_list.append({
                "id": m,
                "object": "model",
                "created": now,
                "owned_by": "copilot",
                "permission": [],
                "root": m,
                "parent": None,
            })

    return model_list


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
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "agy"


def _conversation_exists_in_agy(conversation_id: str) -> bool:
    """Check if conversation_id already exists in Antigravity's local DB."""
    if not conversation_id:
        return False
    candidate_dbs = [
        os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db"),
        "/root/.gemini/antigravity-cli/conversation_summaries.db",
    ]
    for db_path in candidate_dbs:
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path, timeout=1.0)
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT conversation_id FROM conversation_summaries WHERE conversation_id = ? LIMIT 1",
                    (conversation_id,),
                ).fetchone()
                conn.close()
                if row:
                    return True
            except Exception:
                pass
    return False


def _get_github_token() -> str:
    """Extract GitHub token from env, mounted gh config, or gh CLI."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token

    # Check hosts.yml in mounted or host config paths
    candidate_paths = [
        os.path.expanduser("~/.config/gh/hosts.yml"),
        "/gh-config-ro/hosts.yml",
        "/tmp/gh-config/hosts.yml",
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                try:
                    import yaml
                    with open(path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        for host, vals in data.items():
                            if isinstance(vals, dict) and "oauth_token" in vals:
                                token = vals["oauth_token"]
                                os.environ["GITHUB_TOKEN"] = token
                                return token
                except ImportError:
                    pass

                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "oauth_token:" in line:
                            token = line.split("oauth_token:", 1)[1].strip().strip('"').strip("'")
                            if token:
                                os.environ["GITHUB_TOKEN"] = token
                                return token
            except Exception as e:
                logger.warning("Error reading %s: %s", path, e)

    # Fallback: gh auth token command
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0 and proc.stdout.strip():
            token = proc.stdout.strip()
            os.environ["GITHUB_TOKEN"] = token
            return token
    except Exception:
        pass

    return ""


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


def is_copilot_model(model: str | None) -> bool:
    """Check if model name matches Copilot / GitHub model patterns."""
    if not model:
        return False
    m = model.lower()
    return (
        m.startswith("gpt-")
        or m.startswith("o1-")
        or m.startswith("o3-")
        or m.startswith("claude-sonnet-4-5")
        or m.startswith("meta-llama")
        or m.startswith("mistral")
        or m.startswith("phi-")
        or m in COPILOT_MODELS
    )


def resolve_provider(model: str | None = None, provider: str | None = None) -> str:
    """Determine the provider ('copilot' or 'antigravity')."""
    if provider:
        return provider.lower()
    if model:
        if is_antigravity_model(model):
            return "antigravity"
        if is_copilot_model(model):
            return "copilot"
    return os.getenv("PROVIDER", "copilot").lower()


def extract_text_from_content(content: Any) -> str:
    """Extract plain text string from string or multimodal content structure."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        parts.append("[Image attached]")
                    else:
                        parts.append(f"[Image URL: {url}]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def format_messages_for_antigravity(
    messages: List[Any],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    incremental: bool = False,
) -> str:
    """
    Format chat messages and optional tool specifications into an optimized prompt for agy.
    If incremental=True, only the latest turns since the last assistant message are sent,
    saving thousands of prompt tokens when resuming an active Antigravity conversation.
    """
    target_messages = messages
    if incremental and len(messages) > 1:
        last_asst_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            m = messages[idx]
            r = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if r == "assistant":
                last_asst_idx = idx
                break
        if last_asst_idx != -1 and last_asst_idx < len(messages) - 1:
            target_messages = messages[last_asst_idx + 1:]

    system_prompts: List[str] = []
    conversation_turns: List[str] = []

    for msg in target_messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            name = msg.get("name")
        else:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", None)
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            name = getattr(msg, "name", None)

        text = extract_text_from_content(content)

        if role in ("system", "developer"):
            # When incremental, system instructions are already stored in the conversation session
            if text and not incremental:
                system_prompts.append(text)
        elif role == "user":
            conversation_turns.append(f"User: {text}")
        elif role == "assistant":
            parts = []
            if text:
                parts.append(text)
            if tool_calls:
                parts.append(f"```json\n{{\"tool_calls\":{json.dumps(tool_calls, separators=(',', ':'))}}}\n```")
            conversation_turns.append(f"Assistant: {' '.join(parts) if parts else ''}")
        elif role in ("tool", "function"):
            identifier = name or tool_call_id or "tool"
            conversation_turns.append(f"Tool Result ({identifier}): {text}")
        else:
            conversation_turns.append(f"{role.capitalize()}: {text}")

    prompt_sections: List[str] = []
    if system_prompts:
        prompt_sections.append("[System Instructions]\n" + "\n\n".join(system_prompts))

    if tools and tool_choice != "none":
        # Minify tool specifications to eliminate whitespace token bloat
        tools_minified = json.dumps(tools, separators=(',', ':'))
        tool_instruction = (
            "[Available Tools]\n"
            f"{tools_minified}\n"
            "If invoking a tool, respond ONLY with:\n"
            "```json\n"
            '{"tool_calls":[{"name":"<tool_name>","arguments":{<args>}}]}\n'
            "```\n"
            "Otherwise, respond with text answer directly."
        )
        if isinstance(tool_choice, dict) and "function" in tool_choice:
            func_name = tool_choice["function"].get("name")
            if func_name:
                tool_instruction += f"\nYou MUST invoke function '{func_name}'."
        elif tool_choice == "required":
            tool_instruction += "\nYou MUST invoke at least one tool."

        prompt_sections.append(tool_instruction)

    if conversation_turns:
        prompt_sections.append("[Conversation]\n" + "\n\n".join(conversation_turns))

    prompt_sections.append("Assistant:")
    return "\n\n".join(prompt_sections)


def parse_tool_calls_from_text(text: str) -> tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
    """
    Search for a tool_calls block in the model output.
    Returns (cleaned_content, parsed_tool_calls_list).
    """
    if not text:
        return None, None

    # Try 1: Search for ```json ... ``` code blocks
    code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    for block in code_blocks:
        try:
            data = json.loads(block.strip())
            if isinstance(data, dict) and "tool_calls" in data and isinstance(data["tool_calls"], list):
                tool_calls = []
                for tc in data["tool_calls"]:
                    name = tc.get("name")
                    args = tc.get("arguments", {})
                    args_str = json.dumps(args) if isinstance(args, (dict, list)) else str(args)
                    tool_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": args_str,
                        },
                    })
                # Remove the parsed tool call block from content
                cleaned = re.sub(r"```(?:json)?\s*" + re.escape(block.strip()) + r"\s*```", "", text).strip()
                return (cleaned if cleaned else None), tool_calls
        except json.JSONDecodeError:
            continue

    # Try 2: Direct raw JSON without markdown fences
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and "tool_calls" in data and isinstance(data["tool_calls"], list):
            tool_calls = []
            for tc in data["tool_calls"]:
                name = tc.get("name")
                args = tc.get("arguments", {})
                args_str = json.dumps(args) if isinstance(args, (dict, list)) else str(args)
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args_str,
                    },
                })
            return None, tool_calls
    except json.JSONDecodeError:
        pass

    return text.strip(), None


def prepare_messages_for_openai(messages: List[Any]) -> List[Dict[str, Any]]:
    """Clean and prepare message dicts for the official OpenAI client."""
    prepared = []
    for msg in messages:
        if hasattr(msg, "model_dump"):
            d = msg.model_dump(exclude_none=True)
        elif isinstance(msg, dict):
            d = {k: v for k, v in msg.items() if v is not None}
        else:
            d = {
                "role": getattr(msg, "role", "user"),
                "content": getattr(msg, "content", ""),
            }
        prepared.append(d)
    return prepared


# ── Copilot / GitHub Models ───────────────────────────────────────────────────

def _get_client() -> tuple:
    """Return (client, default_model). Reads env vars and tokens dynamically."""
    github_token = _get_github_token()
    base_url = os.getenv("COPILOT_BASE_URL", "https://api.githubcopilot.com")
    if "models.inference.ai.azure.com" in base_url:
        base_url = "https://api.githubcopilot.com"
    default_model = os.getenv("COPILOT_MODEL", "gpt-4o-mini")
    if not github_token:
        return None, default_model
    return OpenAI(base_url=base_url, api_key=github_token), default_model


def _token_check() -> Optional[Dict[str, Any]]:
    token = _get_github_token()
    if not token:
        return {
            "error": "GITHUB_TOKEN environment variable is not set and gh auth was not found.",
            "content": None,
            "model": None,
        }
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


def chat_complete_copilot(
    messages: List[Any],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Execute chat completion using GitHub Copilot / Models API."""
    err = _token_check()
    if err:
        return err

    client, default_model = _get_client()
    chosen_model = model or default_model
    prepared_messages = prepare_messages_for_openai(messages)

    call_kwargs: Dict[str, Any] = {
        "model": chosen_model,
        "messages": prepared_messages,
    }
    if tools:
        call_kwargs["tools"] = tools
    if tool_choice:
        call_kwargs["tool_choice"] = tool_choice
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    if stop is not None:
        call_kwargs["stop"] = stop

    try:
        response = client.chat.completions.create(**call_kwargs)
        res_dict = response.model_dump()
        res_dict["provider"] = "copilot"
        if not res_dict.get("object"):
            res_dict["object"] = "chat.completion"
        if not res_dict.get("created"):
            res_dict["created"] = int(time.time())
        return res_dict
    except Exception as e:
        logger.exception("Error in Copilot chat completion: %s", e)
        return {
            "error": f"Copilot error: {str(e)}",
            "model": chosen_model,
            "provider": "copilot",
        }


def chat_stream_copilot(
    messages: List[Any],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[Any] = None,
    stream_options: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Generator[str, None, None]:
    """Stream chat completions from GitHub Copilot / Models API as Server-Sent Events."""
    err = _token_check()
    if err:
        err_chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model or "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "delta": {"content": f"\n[Error: {err.get('error')}]"},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(err_chunk)}\n\n"
        yield "data: [DONE]\n\n"
        return

    client, default_model = _get_client()
    chosen_model = model or default_model
    prepared_messages = prepare_messages_for_openai(messages)

    call_kwargs: Dict[str, Any] = {
        "model": chosen_model,
        "messages": prepared_messages,
        "stream": True,
    }
    if tools:
        call_kwargs["tools"] = tools
    if tool_choice:
        call_kwargs["tool_choice"] = tool_choice
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    if stop is not None:
        call_kwargs["stop"] = stop
    if stream_options:
        call_kwargs["stream_options"] = stream_options

    try:
        stream = client.chat.completions.create(**call_kwargs)
        for chunk in stream:
            chunk_dict = chunk.model_dump()
            if not chunk_dict.get("object"):
                chunk_dict["object"] = "chat.completion.chunk"
            if not chunk_dict.get("created"):
                chunk_dict["created"] = int(time.time())
            yield f"data: {json.dumps(chunk_dict)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.exception("Error in Copilot chat streaming: %s", e)
        err_chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": chosen_model,
            "choices": [{
                "index": 0,
                "delta": {"content": f"\n[Copilot error: {str(e)}]"},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(err_chunk)}\n\n"
        yield "data: [DONE]\n\n"


# ── Antigravity (agy) ─────────────────────────────────────────────────────────

def _get_subprocess_env() -> dict:
    """Return environment dict ensuring HOME, PATH and Antigravity dirs are set."""
    env = os.environ.copy()
    if "HOME" not in env or not env["HOME"]:
        env["HOME"] = os.path.expanduser("~") or "/root"
    gemini_bin = os.path.expanduser("~/.gemini/bin")
    current_path = env.get("PATH", "")
    if gemini_bin not in current_path:
        env["PATH"] = f"{gemini_bin}:/root/.gemini/bin:{current_path}"
    return env


def ask_antigravity(
    prompt: str,
    model: str | None = None,
    conversation_id: str | None = None,
    effort: str | None = None,
) -> dict:
    """Execute non-interactive prompt using the agy CLI."""
    agy_bin = _find_agy_bin()
    default_model = os.getenv("ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
    if model and not is_antigravity_model(model):
        logger.info(
            "Model '%s' is not a native Antigravity model; mapping to default '%s'",
            model,
            default_model,
        )
        chosen_model = default_model
    else:
        chosen_model = model or default_model
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=_get_subprocess_env())
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


def chat_complete_antigravity(
    messages: List[Any],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[Any] = None,
    effort: Optional[str] = None,
    conversation_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Execute OpenAI-compatible chat completion using Antigravity."""
    incremental = bool(conversation_id and _conversation_exists_in_agy(conversation_id))
    prompt = format_messages_for_antigravity(
        messages, tools=tools, tool_choice=tool_choice, incremental=incremental
    )
    agy_bin = _find_agy_bin()
    default_model = os.getenv("ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
    chosen_model = model if (model and is_antigravity_model(model)) else default_model
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=_get_subprocess_env())
    except FileNotFoundError:
        return {
            "error": f"agy binary not found at '{agy_bin}'. Ensure Antigravity is installed/mounted.",
            "model": chosen_model,
            "provider": "antigravity",
        }
    except subprocess.TimeoutExpired:
        return {
            "error": "Antigravity process timed out after 180 seconds.",
            "model": chosen_model,
            "provider": "antigravity",
        }

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
        return {
            "error": f"agy execution error: {err_msg}",
            "model": chosen_model,
            "provider": "antigravity",
        }

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {"response": proc.stdout.strip()}

    raw_response = (data.get("response") or "").strip()
    usage_data = data.get("usage", {})
    usage = {
        "prompt_tokens": usage_data.get("input_tokens", 0),
        "completion_tokens": usage_data.get("output_tokens", 0),
        "total_tokens": usage_data.get("total_tokens", 0),
    }
    if usage_data.get("cache_read_tokens"):
        usage["prompt_tokens_details"] = {
            "cached_tokens": usage_data.get("cache_read_tokens", 0)
        }

    content, parsed_tool_calls = parse_tool_calls_from_text(raw_response) if tools else (raw_response, None)
    finish_reason = "tool_calls" if parsed_tool_calls else "stop"

    msg: Dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if parsed_tool_calls:
        msg["tool_calls"] = parsed_tool_calls

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    active_conv_id = data.get("conversation_id") or conversation_id

    res_payload = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": chosen_model,
        "provider": "antigravity",
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": finish_reason,
        }],
        "usage": usage,
    }
    if active_conv_id:
        res_payload["conversation_id"] = active_conv_id
        res_payload["system_fingerprint"] = active_conv_id

    return res_payload


def chat_stream_antigravity(
    messages: List[Any],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[Any] = None,
    stream_options: Optional[Dict[str, Any]] = None,
    effort: Optional[str] = None,
    conversation_id: Optional[str] = None,
    **kwargs,
) -> Generator[str, None, None]:
    """Stream chat completions from Antigravity as Server-Sent Events with abort safety and session reuse."""
    incremental = bool(conversation_id and _conversation_exists_in_agy(conversation_id))
    prompt = format_messages_for_antigravity(
        messages, tools=tools, tool_choice=tool_choice, incremental=incremental
    )
    agy_bin = _find_agy_bin()
    default_model = os.getenv("ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
    chosen_model = model if (model and is_antigravity_model(model)) else default_model
    chosen_effort = effort or os.getenv("ANTIGRAVITY_EFFORT")

    cmd = [
        agy_bin,
        "-p", prompt,
        "--model", chosen_model,
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
    ]
    if chosen_effort:
        cmd.extend(["--effort", chosen_effort])
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    active_conv_id = conversation_id

    # Send initial role delta chunk
    init_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": chosen_model,
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant"},
            "logprobs": None,
            "finish_reason": None,
        }],
    }
    if active_conv_id:
        init_chunk["conversation_id"] = active_conv_id
        init_chunk["system_fingerprint"] = active_conv_id
    yield f"data: {json.dumps(init_chunk)}\n\n"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_get_subprocess_env(),
        )
    except Exception as e:
        err_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": chosen_model,
            "choices": [{
                "index": 0,
                "delta": {"content": f"\n[Error launching Antigravity: {e}]"},
                "finish_reason": "stop",
            }],
        }
        if active_conv_id:
            err_chunk["conversation_id"] = active_conv_id
            err_chunk["system_fingerprint"] = active_conv_id
        yield f"data: {json.dumps(err_chunk)}\n\n"
        yield "data: [DONE]\n\n"
        return

    full_text = []
    final_usage = None
    is_tool_mode = bool(tools and tool_choice != "none")
    buffer: List[str] = []
    detected_tool_call = False
    flushed_buffer = False
    error_msg = None

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event_data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event_data.get("conversation_id"):
                active_conv_id = event_data["conversation_id"]

            evt = event_data.get("event")
            if evt == "step_update":
                delta_text = event_data.get("step_update", {}).get("text_delta", "")
                if not delta_text:
                    continue

                full_text.append(delta_text)

                if not is_tool_mode:
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": chosen_model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": delta_text},
                            "logprobs": None,
                            "finish_reason": None,
                        }],
                    }
                    if active_conv_id:
                        chunk["conversation_id"] = active_conv_id
                        chunk["system_fingerprint"] = active_conv_id
                    yield f"data: {json.dumps(chunk)}\n\n"
                else:
                    # In tool mode: buffer until we know if it is a tool call or plain text
                    if not flushed_buffer and not detected_tool_call:
                        buffer.append(delta_text)
                        combined_buffer = "".join(buffer).lstrip()
                        if combined_buffer.startswith("```") or combined_buffer.startswith("{"):
                            detected_tool_call = True
                        elif len(combined_buffer) > 20:
                            flushed_buffer = True
                            chunk = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": chosen_model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": "".join(buffer)},
                                    "logprobs": None,
                                    "finish_reason": None,
                                }],
                            }
                            if active_conv_id:
                                chunk["conversation_id"] = active_conv_id
                                chunk["system_fingerprint"] = active_conv_id
                            yield f"data: {json.dumps(chunk)}\n\n"
                    elif flushed_buffer:
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": chosen_model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": delta_text},
                                "logprobs": None,
                                "finish_reason": None,
                            }],
                        }
                        if active_conv_id:
                            chunk["conversation_id"] = active_conv_id
                            chunk["system_fingerprint"] = active_conv_id
                        yield f"data: {json.dumps(chunk)}\n\n"

            elif evt == "result":
                res = event_data.get("result", {})
                if res.get("conversation_id"):
                    active_conv_id = res["conversation_id"]
                error_msg = res.get("error")
                raw_usage = res.get("usage", {})
                if raw_usage:
                    final_usage = {
                        "prompt_tokens": raw_usage.get("input_tokens", 0),
                        "completion_tokens": raw_usage.get("output_tokens", 0),
                        "total_tokens": raw_usage.get("total_tokens", 0),
                    }
                    if raw_usage.get("cache_read_tokens"):
                        final_usage["prompt_tokens_details"] = {
                            "cached_tokens": raw_usage.get("cache_read_tokens", 0)
                        }

        proc.wait()

    except GeneratorExit:
        logger.info("Client aborted/disconnected stream; terminating agy process PID %s", proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        raise
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # If agy reported an error and no output was produced, yield error chunk
    if (proc.returncode != 0 or error_msg) and not full_text:
        err_detail = error_msg or f"Antigravity process exited with code {proc.returncode}"
        err_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": chosen_model,
            "choices": [{
                "index": 0,
                "delta": {"content": f"\n[Antigravity Error: {err_detail}]"},
                "finish_reason": "stop",
            }],
        }
        if active_conv_id:
            err_chunk["conversation_id"] = active_conv_id
            err_chunk["system_fingerprint"] = active_conv_id
        yield f"data: {json.dumps(err_chunk)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Handle completion when tools were requested
    if is_tool_mode and not flushed_buffer:
        entire_output = "".join(full_text).strip()
        cleaned_content, parsed_tool_calls = parse_tool_calls_from_text(entire_output)
        if parsed_tool_calls:
            tc_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": chosen_model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": idx,
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "arguments": tc["function"]["arguments"],
                                },
                            }
                            for idx, tc in enumerate(parsed_tool_calls)
                        ]
                    },
                    "logprobs": None,
                    "finish_reason": None,
                }],
            }
            if active_conv_id:
                tc_chunk["conversation_id"] = active_conv_id
                tc_chunk["system_fingerprint"] = active_conv_id
            yield f"data: {json.dumps(tc_chunk)}\n\n"
            finish_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": chosen_model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "tool_calls",
                }],
            }
            if active_conv_id:
                finish_chunk["conversation_id"] = active_conv_id
                finish_chunk["system_fingerprint"] = active_conv_id
            yield f"data: {json.dumps(finish_chunk)}\n\n"
        else:
            if entire_output:
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": chosen_model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": entire_output},
                        "finish_reason": None,
                    }],
                }
                if active_conv_id:
                    chunk["conversation_id"] = active_conv_id
                    chunk["system_fingerprint"] = active_conv_id
                yield f"data: {json.dumps(chunk)}\n\n"
            finish_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": chosen_model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
            }
            if active_conv_id:
                finish_chunk["conversation_id"] = active_conv_id
                finish_chunk["system_fingerprint"] = active_conv_id
            yield f"data: {json.dumps(finish_chunk)}\n\n"
    else:
        finish_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": chosen_model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }
        if active_conv_id:
            finish_chunk["conversation_id"] = active_conv_id
            finish_chunk["system_fingerprint"] = active_conv_id
        yield f"data: {json.dumps(finish_chunk)}\n\n"

    # Optional usage chunk
    if final_usage and (stream_options and stream_options.get("include_usage")):
        usage_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": chosen_model,
            "choices": [],
            "usage": final_usage,
        }
        if active_conv_id:
            usage_chunk["conversation_id"] = active_conv_id
            usage_chunk["system_fingerprint"] = active_conv_id
        yield f"data: {json.dumps(usage_chunk)}\n\n"

    yield "data: [DONE]\n\n"


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


def dispatch_chat_completion(
    messages: List[Any],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Route chat completion to the appropriate backend."""
    active_provider = resolve_provider(model=model, provider=provider)
    if active_provider == "antigravity":
        return chat_complete_antigravity(
            messages=messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )
    return chat_complete_copilot(
        messages=messages,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        **kwargs,
    )


def dispatch_chat_stream(
    messages: List[Any],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    **kwargs,
) -> Generator[str, None, None]:
    """Route streaming chat completion to the appropriate backend."""
    active_provider = resolve_provider(model=model, provider=provider)
    if active_provider == "antigravity":
        return chat_stream_antigravity(
            messages=messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )
    return chat_stream_copilot(
        messages=messages,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        **kwargs,
    )
