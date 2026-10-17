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
from typing import Optional, Union, List, Dict, Any, Generator, Tuple

from files_store import (
    extract_file_for_ai,
    save_data_url_to_temp_file,
    get_file,
)

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


def get_antigravity_usage_stats() -> Dict[str, Any]:
    """
    Extract usage statistics, daily breakdown, recent conversations,
    and quota limit / error status from Antigravity's local SQLite database and logs.
    """
    db_candidates = [
        os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db"),
        "/root/.gemini/antigravity-cli/conversation_summaries.db",
    ]
    db_path = next((p for p in db_candidates if os.path.exists(p)), None)

    summary = {
        "total_conversations": 0,
        "total_step_turns": 0,
        "days_tracked": 0,
    }
    daily_usage: List[Dict[str, Any]] = []
    recent_conversations: List[Dict[str, Any]] = []

    if db_path:
        try:
            conn = sqlite3.connect(db_path, timeout=2.0)
            cur = conn.cursor()

            tot_row = cur.execute(
                "SELECT count(*), coalesce(sum(step_count), 0) FROM conversation_summaries WHERE last_modified_time > '2000'"
            ).fetchone()
            if tot_row:
                summary["total_conversations"] = tot_row[0]
                summary["total_step_turns"] = tot_row[1]

            days_rows = cur.execute(
                """
                SELECT substr(last_modified_time, 1, 10) as day, count(*), coalesce(sum(step_count), 0)
                FROM conversation_summaries
                WHERE last_modified_time > '2000'
                GROUP BY day
                ORDER BY day DESC
                LIMIT 30
                """
            ).fetchall()
            summary["days_tracked"] = len(days_rows)
            for d, c, s in days_rows:
                daily_usage.append({
                    "date": d,
                    "conversations": c,
                    "step_turns": s,
                })

            recent_rows = cur.execute(
                """
                SELECT conversation_id, title, step_count, last_modified_time
                FROM conversation_summaries
                WHERE last_modified_time > '2000'
                ORDER BY last_modified_time DESC
                LIMIT 10
                """
            ).fetchall()
            for cid, title, steps, lm in recent_rows:
                recent_conversations.append({
                    "conversation_id": cid,
                    "title": title or "Untitled",
                    "step_count": steps,
                    "last_modified": str(lm)[:19],
                })
            conn.close()
        except Exception as e:
            logger.exception("Error querying conversation_summaries.db: %s", e)

    log_candidates = [
        os.path.expanduser("~/.gemini/antigravity-cli/log"),
        "/root/.gemini/antigravity-cli/log",
    ]
    log_dir = next((p for p in log_candidates if os.path.isdir(p)), None)

    quota_status = {
        "is_rate_limited": False,
        "reset_in": None,
        "last_exhausted_error": None,
        "last_log_file": None,
    }

    if log_dir:
        import glob
        logs = sorted(glob.glob(os.path.join(log_dir, "cli-*.log")))
        for lf in reversed(logs[-10:]):
            try:
                with open(lf, "r", errors="ignore") as f:
                    content = f.read()
                    if "RESOURCE_EXHAUSTED" in content:
                        quota_status["is_rate_limited"] = True
                        m = re.search(r"Resets in ([^\n.]+)", content)
                        if m:
                            quota_status["reset_in"] = m.group(1).strip()
                        err_m = re.search(r"RESOURCE_EXHAUSTED[^\n]+", content)
                        if err_m:
                            quota_status["last_exhausted_error"] = err_m.group(0).strip()
                        quota_status["last_log_file"] = os.path.basename(lf)
                        break
            except Exception:
                pass

    return {
        "status": "ok",
        "provider": "antigravity",
        "quota_status": quota_status,
        "summary": summary,
        "daily_usage": daily_usage,
        "recent_conversations": recent_conversations,
    }


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


def extract_content_and_files_for_antigravity(
    content: Any,
    attachments: Optional[List[Any]] = None,
    file_ids: Optional[List[str]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Extract text content, inline images/data-URLs, and attached files for an Antigravity turn.
    Resolves file_ids, attachments, and inline base64 data URLs.
    Returns (formatted_text, list_of_attached_files).
    """
    parts = []
    attached_files: List[Dict[str, Any]] = []

    # 1. Attachments & file_ids
    all_fids: List[str] = []
    if attachments:
        for att in attachments:
            if isinstance(att, dict) and att.get("file_id"):
                all_fids.append(att["file_id"])
            elif isinstance(att, str):
                all_fids.append(att)
    if file_ids:
        for fid in file_ids:
            if isinstance(fid, str):
                all_fids.append(fid)

    for fid in all_fids:
        info = extract_file_for_ai(fid)
        if "error" in info:
            parts.append(f"[File Error: {fid} - {info['error']}]")
            continue
        attached_files.append(info)
        f_path = info.get("path", "")
        f_name = info.get("filename", "file")
        if info.get("is_image"):
            parts.append(f"[Attached Image: {f_path} ({f_name})]")
        elif info.get("is_pdf"):
            parts.append(f"[Attached Document: {f_path} ({f_name})]")
        elif info.get("is_text"):
            parts.append(f"[Attached File: {f_path} ({f_name})]\n{info.get('text', '')}")
        else:
            parts.append(f"[Attached File: {f_path} ({f_name})]")

    # 2. Content parts
    if content is not None:
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    p_type = item.get("type")
                    if p_type in ("text", "input_text"):
                        parts.append(item.get("text", ""))
                    elif p_type in ("image_file", "file", "file_id", "input_file"):
                        fid = (
                            item.get("file_id")
                            or item.get("id")
                            or item.get("file", {}).get("file_id")
                            or item.get("image_file", {}).get("file_id")
                        )
                        if fid:
                            info = extract_file_for_ai(fid)
                            if "error" in info:
                                parts.append(f"[File Error: {fid} - {info['error']}]")
                                continue
                            attached_files.append(info)
                            f_path = info.get("path", "")
                            f_name = info.get("filename", "file")
                            if info.get("is_image"):
                                parts.append(f"[Attached Image: {f_path} ({f_name})]")
                            elif info.get("is_pdf"):
                                parts.append(f"[Attached Document: {f_path} ({f_name})]")
                            elif info.get("is_text"):
                                parts.append(f"[Attached File: {f_path} ({f_name})]\n{info.get('text', '')}")
                            else:
                                parts.append(f"[Attached File: {f_path} ({f_name})]")
                    elif p_type in ("image_url", "input_image"):
                        img_val = item.get("image_url") or item.get("url") or item.get("image")
                        url = img_val.get("url", "") if isinstance(img_val, dict) else str(img_val or "")
                        if url.startswith("data:"):
                            temp_res = save_data_url_to_temp_file(url, "inline_attachment")
                            if temp_res:
                                temp_path, mime = temp_res
                                attached_files.append({
                                    "path": temp_path,
                                    "filename": os.path.basename(temp_path),
                                    "mime_type": mime,
                                })
                                if "pdf" in mime:
                                    parts.append(f"[Attached Document: {temp_path}]")
                                else:
                                    parts.append(f"[Attached Image: {temp_path}]")
                            else:
                                parts.append("[Image attached]")
                        elif url:
                            parts.append(f"[Image URL: {url}]")
                    else:
                        if "text" in item:
                            parts.append(item["text"])

    return "\n".join(parts), attached_files


def extract_text_from_content(content: Any) -> str:
    """Extract plain text string from string or multimodal content structure."""
    text, _ = extract_content_and_files_for_antigravity(content)
    return text


def format_messages_for_antigravity(
    messages: List[Any],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    incremental: bool = False,
    return_attached_dirs: bool = False,
) -> Union[str, Tuple[str, List[str]]]:
    """
    Format chat messages and optional tool specifications into an optimized prompt for agy.
    If incremental=True, only the latest turns since the last assistant message are sent,
    saving thousands of prompt tokens when resuming an active Antigravity conversation.
    Resolves attached images, scans, and PDFs into local file paths and instructions.
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
    all_attached_files: List[Dict[str, Any]] = []

    for msg in target_messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content")
            attachments = msg.get("attachments")
            file_ids = msg.get("file_ids")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            name = msg.get("name")
        else:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", None)
            attachments = getattr(msg, "attachments", None)
            file_ids = getattr(msg, "file_ids", None)
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            name = getattr(msg, "name", None)

        text, msg_files = extract_content_and_files_for_antigravity(
            content=content, attachments=attachments, file_ids=file_ids
        )
        if msg_files:
            all_attached_files.extend(msg_files)

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

    if all_attached_files:
        file_lines = []
        for f in all_attached_files:
            p = f.get("path")
            fn = f.get("filename", os.path.basename(p or ""))
            if p:
                file_lines.append(f"- {p} ({fn})")
        if file_lines:
            files_section = (
                "[Attached Files & Scans to Analyze]\n"
                "The following local file(s) are attached to this conversation:\n"
                + "\n".join(file_lines) + "\n"
                "Please examine and analyze these files directly to answer the user's questions."
            )
            prompt_sections.append(files_section)

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
    final_prompt = "\n\n".join(prompt_sections)

    if return_attached_dirs:
        dirs = list({
            os.path.dirname(f["path"])
            for f in all_attached_files
            if f.get("path") and os.path.exists(os.path.dirname(f["path"]))
        })
        return final_prompt, dirs

    return final_prompt


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
    """
    Clean, prepare, and resolve multimodal file attachments / data URLs for OpenAI client.
    Converts image files and scanned PDF page images to standard OpenAI vision parts (image_url),
    and injects extracted text from PDFs and text files.
    """
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

        # Check for message attachments or file_ids
        attachments = d.pop("attachments", None) or []
        file_ids = d.pop("file_ids", None) or []
        all_file_ids = []
        for att in attachments:
            if isinstance(att, dict) and att.get("file_id"):
                all_file_ids.append(att["file_id"])
            elif isinstance(att, str):
                all_file_ids.append(att)
        for fid in file_ids:
            if isinstance(fid, str):
                all_file_ids.append(fid)

        content = d.get("content")
        new_content_parts: List[Dict[str, Any]] = []

        # 1. Process files from attachments/file_ids
        for fid in all_file_ids:
            finfo = extract_file_for_ai(fid)
            if "error" in finfo:
                new_content_parts.append({"type": "text", "text": f"[Error loading file {fid}: {finfo['error']}]"})
                continue
            if finfo.get("is_image"):
                new_content_parts.append({"type": "image_url", "image_url": {"url": finfo["data_url"]}})
            elif finfo.get("is_text"):
                new_content_parts.append({
                    "type": "text",
                    "text": f"[Attached File: {finfo['filename']}]\n{finfo.get('text', '')}",
                })
            else:
                new_content_parts.append({
                    "type": "text",
                    "text": f"[Attached File: {finfo['filename']}]",
                })

        # 2. Process content
        if isinstance(content, str):
            if new_content_parts:
                new_content_parts.insert(0, {"type": "text", "text": content})
                d["content"] = new_content_parts
            else:
                d["content"] = content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    new_content_parts.append({"type": "text", "text": part})
                elif isinstance(part, dict):
                    p_type = part.get("type")
                    if p_type in ("image_file", "file", "file_id", "input_file"):
                        fid = (
                            part.get("file_id")
                            or part.get("id")
                            or part.get("file", {}).get("file_id")
                            or part.get("image_file", {}).get("file_id")
                        )
                        if fid:
                            finfo = extract_file_for_ai(fid)
                            if "error" in finfo:
                                new_content_parts.append({"type": "text", "text": f"[Error loading file {fid}: {finfo['error']}]"})
                                continue
                            if finfo.get("is_image"):
                                new_content_parts.append({"type": "image_url", "image_url": {"url": finfo["data_url"]}})
                            elif finfo.get("is_text"):
                                new_content_parts.append({
                                    "type": "text",
                                    "text": f"[Attached File: {finfo['filename']}]\n{finfo.get('text', '')}",
                                })
                            else:
                                new_content_parts.append({
                                    "type": "text",
                                    "text": f"[Attached File: {finfo['filename']}]",
                                })
                    else:
                        new_content_parts.append(part)
            d["content"] = new_content_parts

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
    prompt_res = format_messages_for_antigravity(
        messages, tools=tools, tool_choice=tool_choice, incremental=incremental, return_attached_dirs=True
    )
    if isinstance(prompt_res, tuple):
        prompt, attached_dirs = prompt_res
    else:
        prompt, attached_dirs = prompt_res, []

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
    for ad in attached_dirs:
        cmd.extend(["--add-dir", ad])
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
    prompt_res = format_messages_for_antigravity(
        messages, tools=tools, tool_choice=tool_choice, incremental=incremental, return_attached_dirs=True
    )
    if isinstance(prompt_res, tuple):
        prompt, attached_dirs = prompt_res
    else:
        prompt, attached_dirs = prompt_res, []

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
    for ad in attached_dirs:
        cmd.extend(["--add-dir", ad])
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


# ── OpenAI Responses API Compatibility Layer ─────────────────────────────────
# Supports Home Assistant v2025/2026 Core Assist (`client.responses.create`),
# `ha-openai-compatible`, and modern OpenAI Responses SDK callers.

def convert_responses_input_to_messages(
    input_data: Union[str, List[Any], None],
    instructions: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Convert OpenAI Responses API `input` parameter and `instructions` into
    standard OpenAI Chat Completions `messages` format.
    Supports text, multi-turn messages, function_call, and function_call_output items.
    """
    messages: List[Dict[str, Any]] = []

    if instructions and str(instructions).strip():
        messages.append({"role": "system", "content": str(instructions).strip()})

    if not input_data:
        return messages

    if isinstance(input_data, str):
        messages.append({"role": "user", "content": input_data})
        return messages

    if not isinstance(input_data, list):
        messages.append({"role": "user", "content": str(input_data)})
        return messages

    for item in input_data:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue

        if not isinstance(item, dict):
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            elif hasattr(item, "__dict__"):
                item = item.__dict__
            else:
                messages.append({"role": "user", "content": str(item)})
                continue

        item_type = item.get("type")

        # 1. Tool result (function_call_output) -> tool message
        if item_type == "function_call_output":
            call_id = item.get("call_id") or item.get("id") or ""
            raw_output = item.get("output", "")
            if isinstance(raw_output, (dict, list)):
                raw_output = json.dumps(raw_output)
            messages.append({
                "role": "tool",
                "tool_call_id": str(call_id),
                "content": str(raw_output),
            })
            continue

        # 2. Function call from assistant
        if item_type == "function_call":
            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            name = item.get("name", "")
            raw_args = item.get("arguments", "{}")
            if isinstance(raw_args, (dict, list)):
                raw_args = json.dumps(raw_args)
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": str(call_id),
                    "type": "function",
                    "function": {
                        "name": str(name),
                        "arguments": str(raw_args),
                    },
                }],
            })
            continue

        # 3. Message item (type="message" or standard role)
        role = item.get("role") or "user"
        if role == "developer":
            role = "system"

        content = item.get("content")
        if isinstance(content, list):
            text_parts = []
            standard_parts = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                    standard_parts.append({"type": "text", "text": part})
                elif isinstance(part, dict):
                    p_type = part.get("type")
                    if p_type in ("input_text", "text"):
                        txt = part.get("text", "")
                        text_parts.append(txt)
                        standard_parts.append({"type": "text", "text": txt})
                    elif p_type in ("input_image", "image_url"):
                        img_url = part.get("image_url") or part.get("url")
                        if isinstance(img_url, dict):
                            standard_parts.append({"type": "image_url", "image_url": img_url})
                        elif isinstance(img_url, str):
                            standard_parts.append({"type": "image_url", "image_url": {"url": img_url}})
                        elif "image" in part:
                            standard_parts.append({"type": "image_url", "image_url": {"url": part["image"]}})
                    elif p_type in ("input_file", "file", "file_id", "image_file"):
                        fid = (
                            part.get("file_id")
                            or part.get("id")
                            or (part.get("file", {}).get("file_id") if isinstance(part.get("file"), dict) else None)
                            or (part.get("image_file", {}).get("file_id") if isinstance(part.get("image_file"), dict) else None)
                        )
                        if fid:
                            standard_parts.append({"type": "file", "file_id": str(fid)})
                    else:
                        if "text" in part:
                            text_parts.append(part["text"])
                            standard_parts.append({"type": "text", "text": part["text"]})

            if standard_parts and all(p.get("type") == "text" for p in standard_parts):
                content = "".join(text_parts)
            else:
                content = standard_parts

        msg_obj: Dict[str, Any] = {"role": role, "content": content}
        if item.get("attachments"):
            msg_obj["attachments"] = item["attachments"]
        if item.get("file_ids"):
            msg_obj["file_ids"] = item["file_ids"]
        if item.get("name"):
            msg_obj["name"] = item["name"]
        if item.get("tool_calls"):
            msg_obj["tool_calls"] = item["tool_calls"]
        if item.get("tool_call_id"):
            msg_obj["tool_call_id"] = item["tool_call_id"]

        messages.append(msg_obj)

    return messages


def adapt_responses_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    Adapt tools from Responses API format (where function attributes may be top-level)
    to Chat Completions format (tool["function"]).
    """
    if not tools:
        return None
    adapted = []
    for tool in tools:
        if isinstance(tool, dict):
            if tool.get("type") == "function" and "function" not in tool and "name" in tool:
                adapted.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    },
                })
            else:
                adapted.append(tool)
        else:
            adapted.append(tool)
    return adapted


def dispatch_responses_completion(
    messages: List[Any],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[Any] = None,
    conversation_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute a non-streaming Responses API request by delegating to dispatch_chat_completion
    and shaping the result into OpenAI Responses API schema.
    """
    chat_res = dispatch_chat_completion(
        messages=messages,
        model=model,
        provider=provider,
        tools=tools,
        tool_choice=tool_choice,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
        conversation_id=conversation_id,
        **kwargs,
    )
    if "error" in chat_res and chat_res.get("error"):
        return chat_res

    resp_id = f"resp_{uuid.uuid4().hex[:12]}"
    created_ts = chat_res.get("created", int(time.time()))
    res_model = chat_res.get("model", model or "default")

    output_items: List[Dict[str, Any]] = []
    choices = chat_res.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        content_txt = msg.get("content")
        tool_calls = msg.get("tool_calls") or []

        if content_txt:
            output_items.append({
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": str(content_txt),
                    "annotations": [],
                }],
            })

        for tc in tool_calls:
            tc_fn = tc.get("function") or {}
            tc_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            tc_args = tc_fn.get("arguments", "{}")
            if isinstance(tc_args, (dict, list)):
                tc_args = json.dumps(tc_args)
            output_items.append({
                "type": "function_call",
                "id": tc_id,
                "call_id": tc_id,
                "name": tc_fn.get("name", ""),
                "arguments": str(tc_args),
                "status": "completed",
            })
    else:
        output_items.append({
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{
                "type": "output_text",
                "text": "",
                "annotations": [],
            }],
        })

    usage_dict = None
    if "usage" in chat_res and isinstance(chat_res["usage"], dict):
        u = chat_res["usage"]
        cached_tok = u.get("prompt_tokens_details", {}).get("cached_tokens", 0) if isinstance(u.get("prompt_tokens_details"), dict) else 0
        usage_dict = {
            "total_tokens": u.get("total_tokens", 0),
            "input_tokens": u.get("prompt_tokens", 0),
            "output_tokens": u.get("completion_tokens", 0),
            "input_tokens_details": {
                "cached_tokens": cached_tok,
                "cache_write_tokens": 0,
            },
            "output_tokens_details": {
                "reasoning_tokens": 0,
            },
        }

    response_payload: Dict[str, Any] = {
        "id": resp_id,
        "object": "response",
        "status": "completed",
        "created_at": created_ts,
        "model": res_model,
        "output": output_items,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    if usage_dict:
        response_payload["usage"] = usage_dict
    if "conversation_id" in chat_res:
        response_payload["conversation_id"] = chat_res["conversation_id"]

    return response_payload


def dispatch_responses_stream(
    messages: List[Any],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[Any] = None,
    stream_options: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    **kwargs,
) -> Generator[str, None, None]:
    """
    Stream responses in OpenAI Responses API SSE event format (`response.*` events),
    consumed natively by Home Assistant (v2025/2026 Core and ha-openai-compatible)
    and the latest OpenAI Python SDK (`client.responses.create(..., stream=True)`).
    """
    active_provider = resolve_provider(model=model, provider=provider)
    effective_model = model or ("gemini-3.8-flash-high" if active_provider == "antigravity" else "gpt-4o-mini")
    resp_id = f"resp_{uuid.uuid4().hex[:12]}"
    msg_item_id = f"msg_{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    seq = 0

    # 1. response.created
    resp_created_event = {
        "type": "response.created",
        "sequence_number": seq,
        "response": {
            "id": resp_id,
            "object": "response",
            "status": "in_progress",
            "created_at": created_ts,
            "model": effective_model,
            "output": [],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
        },
    }
    yield f"data: {json.dumps(resp_created_event)}\n\n"
    seq += 1

    # 2. response.output_item.added for initial message
    msg_item_added = {
        "type": "response.output_item.added",
        "sequence_number": seq,
        "output_index": 0,
        "item": {
            "id": msg_item_id,
            "type": "message",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        },
    }
    yield f"data: {json.dumps(msg_item_added)}\n\n"
    seq += 1

    chat_stream_gen = dispatch_chat_stream(
        messages=messages,
        model=model,
        provider=provider,
        tools=tools,
        tool_choice=tool_choice,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
        stream_options=stream_options,
        conversation_id=conversation_id,
        **kwargs,
    )

    full_text: List[str] = []
    output_items: List[Dict[str, Any]] = []
    tc_states: Dict[int, Dict[str, Any]] = {}
    output_index_counter = 1

    for raw_chunk in chat_stream_gen:
        if not raw_chunk or not raw_chunk.strip():
            continue
        line = raw_chunk.strip()
        if line == "data: [DONE]":
            break
        if not line.startswith("data: "):
            continue

        data_json = line[6:].strip()
        try:
            chunk = json.loads(data_json)
        except json.JSONDecodeError:
            continue

        choices = chunk.get("choices") or []
        if not choices:
            continue

        delta = choices[0].get("delta") or {}

        # Content text delta
        content_chunk = delta.get("content")
        if content_chunk:
            full_text.append(content_chunk)
            text_delta_event = {
                "type": "response.output_text.delta",
                "sequence_number": seq,
                "output_index": 0,
                "content_index": 0,
                "item_id": msg_item_id,
                "delta": content_chunk,
                "logprobs": [],
            }
            yield f"data: {json.dumps(text_delta_event)}\n\n"
            seq += 1

        # Tool calls delta
        tool_calls = delta.get("tool_calls") or []
        for tc in tool_calls:
            tc_idx = tc.get("index", 0)
            if tc_idx not in tc_states:
                tc_states[tc_idx] = {
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "name": tc.get("function", {}).get("name", ""),
                    "args": [],
                    "output_index": output_index_counter,
                    "added": False,
                }
                output_index_counter += 1

            state = tc_states[tc_idx]
            if tc.get("id"):
                state["id"] = tc["id"]
            if tc.get("function", {}).get("name"):
                state["name"] = tc["function"]["name"]

            if not state["added"]:
                tc_added_event = {
                    "type": "response.output_item.added",
                    "sequence_number": seq,
                    "output_index": state["output_index"],
                    "item": {
                        "type": "function_call",
                        "id": state["id"],
                        "call_id": state["id"],
                        "name": state["name"],
                        "arguments": "",
                        "status": "in_progress",
                    },
                }
                yield f"data: {json.dumps(tc_added_event)}\n\n"
                seq += 1
                state["added"] = True

            arg_delta = tc.get("function", {}).get("arguments")
            if arg_delta:
                state["args"].append(arg_delta)
                tc_arg_delta_event = {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": seq,
                    "output_index": state["output_index"],
                    "item_id": state["id"],
                    "delta": arg_delta,
                }
                yield f"data: {json.dumps(tc_arg_delta_event)}\n\n"
                seq += 1

    # Finalize tool calls
    for tc_idx, state in sorted(tc_states.items()):
        full_args = "".join(state["args"])
        tc_arg_done_event = {
            "type": "response.function_call_arguments.done",
            "sequence_number": seq,
            "output_index": state["output_index"],
            "item_id": state["id"],
            "arguments": full_args,
        }
        yield f"data: {json.dumps(tc_arg_done_event)}\n\n"
        seq += 1

        tc_item = {
            "type": "function_call",
            "id": state["id"],
            "call_id": state["id"],
            "name": state["name"],
            "arguments": full_args,
            "status": "completed",
        }
        tc_done_event = {
            "type": "response.output_item.done",
            "sequence_number": seq,
            "output_index": state["output_index"],
            "item": tc_item,
        }
        yield f"data: {json.dumps(tc_done_event)}\n\n"
        seq += 1
        output_items.append(tc_item)

    # Finalize message
    full_str = "".join(full_text)
    msg_item = {
        "id": msg_item_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{
            "type": "output_text",
            "text": full_str,
            "annotations": [],
        }],
    }
    msg_done_event = {
        "type": "response.output_item.done",
        "sequence_number": seq,
        "output_index": 0,
        "item": msg_item,
    }
    yield f"data: {json.dumps(msg_done_event)}\n\n"
    seq += 1
    if full_str or not output_items:
        output_items.insert(0, msg_item)

    # 5. response.completed
    resp_completed_event = {
        "type": "response.completed",
        "sequence_number": seq,
        "response": {
            "id": resp_id,
            "object": "response",
            "status": "completed",
            "created_at": created_ts,
            "model": effective_model,
            "output": output_items,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
        },
    }
    yield f"data: {json.dumps(resp_completed_event)}\n\n"
    seq += 1

    yield "data: [DONE]\n\n"
