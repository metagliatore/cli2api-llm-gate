import os
import re
import json
import time
import uuid
import base64
import logging
import mimetypes
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("files_store")

# Storage directory: priority to FILES_DIR, then /data/uploads (Docker), then local fallback
DEFAULT_STORAGE_DIR = os.getenv(
    "FILES_DIR",
    "/data/uploads" if os.path.exists("/data") else os.path.expanduser("~/.cli2api_files")
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# In-memory / lazy redis client
_redis_client = None


def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try:
            from redis import Redis
            _redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
            _redis_client.ping()
        except Exception as e:
            logger.warning("Redis not available for files metadata caching (%s); using filesystem fallback.", e)
            _redis_client = False
    return _redis_client if _redis_client is not False else None


def get_storage_dir() -> str:
    storage_dir = os.getenv("FILES_DIR", DEFAULT_STORAGE_DIR)
    os.makedirs(storage_dir, exist_ok=True)
    os.makedirs(os.path.join(storage_dir, "temp"), exist_ok=True)
    return storage_dir


def detect_mime_type(data: bytes, filename: Optional[str] = None) -> str:
    """Detect MIME type from magic bytes or file extension."""
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data.startswith(b"BM"):
        return "image/bmp"

    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed

    # Check if text
    try:
        data[:1024].decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        pass

    return "application/octet-stream"


def sanitize_filename(filename: str) -> str:
    name = os.path.basename(filename or "file")
    clean = re.sub(r"[^\w\.\-\_]", "_", name)
    return clean or "file.bin"


def save_file(
    content: bytes,
    filename: str,
    purpose: str = "assistants",
    mime_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Save a file to persistent storage and register its metadata.
    Returns OpenAI-compatible file object dictionary.
    """
    storage_dir = get_storage_dir()
    file_id = f"file-{uuid.uuid4().hex[:16]}"
    clean_name = sanitize_filename(filename)
    detected_mime = mime_type or detect_mime_type(content, clean_name)
    now = int(time.time())

    file_dir = os.path.join(storage_dir, file_id)
    os.makedirs(file_dir, exist_ok=True)
    file_path = os.path.join(file_dir, clean_name)

    with open(file_path, "wb") as f:
        f.write(content)

    meta = {
        "id": file_id,
        "object": "file",
        "bytes": len(content),
        "created_at": now,
        "filename": clean_name,
        "purpose": purpose or "assistants",
        "status": "processed",
        "status_details": None,
        "mime_type": detected_mime,
        "path": file_path,
    }

    # Save metadata json on disk alongside the file
    meta_path = os.path.join(file_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # Cache in Redis if available
    r = get_redis_client()
    if r:
        try:
            r.set(f"file:{file_id}", json.dumps(meta))
            r.sadd("files:all", file_id)
        except Exception as e:
            logger.warning("Failed to cache file metadata in Redis: %s", e)

    return format_openai_file_response(meta)


def format_openai_file_response(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Return public OpenAI file representation (exclude internal filesystem path)."""
    return {
        "id": meta.get("id"),
        "object": "file",
        "bytes": meta.get("bytes", 0),
        "created_at": meta.get("created_at", int(time.time())),
        "filename": meta.get("filename", "file"),
        "purpose": meta.get("purpose", "assistants"),
        "status": meta.get("status", "processed"),
        "status_details": meta.get("status_details"),
    }


def get_file_metadata(file_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve internal file metadata including path."""
    r = get_redis_client()
    if r:
        try:
            cached = r.get(f"file:{file_id}")
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    # Filesystem lookup
    storage_dir = get_storage_dir()
    meta_path = os.path.join(storage_dir, file_id, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return meta
        except Exception as e:
            logger.error("Error reading meta.json for %s: %s", file_id, e)
    return None


def get_file(file_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve public OpenAI file object."""
    meta = get_file_metadata(file_id)
    if not meta:
        return None
    return format_openai_file_response(meta)


def get_file_content(file_id: str) -> Optional[Tuple[bytes, str, str]]:
    """Retrieve raw file content: (bytes, filename, mime_type)."""
    meta = get_file_metadata(file_id)
    if not meta:
        return None
    path = meta.get("path")
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    return data, meta.get("filename", "file"), meta.get("mime_type", "application/octet-stream")


def list_files(purpose: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all registered files, optionally filtered by purpose."""
    files: List[Dict[str, Any]] = []
    r = get_redis_client()
    file_ids: set[str] = set()

    if r:
        try:
            members = r.smembers("files:all")
            if members:
                file_ids.update(members)
        except Exception:
            pass

    # Also scan directory on disk
    storage_dir = get_storage_dir()
    if os.path.exists(storage_dir):
        for entry in os.listdir(storage_dir):
            if entry.startswith("file-") and os.path.isdir(os.path.join(storage_dir, entry)):
                file_ids.add(entry)

    for fid in file_ids:
        meta = get_file_metadata(fid)
        if meta:
            if purpose and meta.get("purpose") != purpose:
                continue
            files.append(format_openai_file_response(meta))

    files.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return files


def delete_file(file_id: str) -> bool:
    """Delete file from disk and Redis."""
    meta = get_file_metadata(file_id)
    storage_dir = get_storage_dir()
    file_dir = os.path.join(storage_dir, file_id)

    deleted = False
    if os.path.exists(file_dir):
        import shutil
        shutil.rmtree(file_dir, ignore_errors=True)
        deleted = True

    r = get_redis_client()
    if r:
        try:
            r.delete(f"file:{file_id}")
            r.srem("files:all", file_id)
        except Exception:
            pass

    return deleted or (meta is not None)


def save_data_url_to_temp_file(data_url: str, filename_prefix: str = "inline") -> Optional[Tuple[str, str]]:
    """
    Decode a base64 data URL and save it to a managed temp file.
    Returns (local_file_path, mime_type) or None if invalid.
    """
    if not data_url.startswith("data:"):
        return None

    try:
        header, b64_data = data_url.split(",", 1)
        mime_match = re.search(r"data:([^;]+);base64", header)
        mime_type = mime_match.group(1).strip() if mime_match else "application/octet-stream"
        raw_bytes = base64.b64decode(b64_data)

        ext = mimetypes.guess_extension(mime_type) or ".bin"
        if "pdf" in mime_type:
            ext = ".pdf"
        elif "jpeg" in mime_type or "jpg" in mime_type:
            ext = ".jpg"
        elif "png" in mime_type:
            ext = ".png"
        elif "webp" in mime_type:
            ext = ".webp"

        temp_dir = os.path.join(get_storage_dir(), "temp")
        file_hash = uuid.uuid4().hex[:12]
        file_path = os.path.join(temp_dir, f"{filename_prefix}_{file_hash}{ext}")

        with open(file_path, "wb") as f:
            f.write(raw_bytes)

        return file_path, mime_type
    except Exception as e:
        logger.error("Failed to decode data URL to temp file: %s", e)
        return None


def extract_file_for_ai(file_id_or_path: str) -> Dict[str, Any]:
    """
    Inspect, parse, and extract representations of a file for multimodal AI processing.
    Handles images (base64, dimensions), PDFs (text extraction + scanned page images), and text/code.
    """
    # Check if input is a registered file_id or a direct path
    meta = None
    if file_id_or_path.startswith("file-"):
        meta = get_file_metadata(file_id_or_path)
        if meta and meta.get("path") and os.path.exists(meta["path"]):
            file_path = meta["path"]
            filename = meta.get("filename", os.path.basename(file_path))
            mime_type = meta.get("mime_type")
        else:
            return {"error": f"File '{file_id_or_path}' not found"}
    else:
        file_path = file_id_or_path
        if not os.path.exists(file_path):
            return {"error": f"Path '{file_path}' does not exist"}
        filename = os.path.basename(file_path)
        mime_type = None

    with open(file_path, "rb") as f:
        content = f.read()

    detected_mime = mime_type or detect_mime_type(content, filename)

    result: Dict[str, Any] = {
        "file_id": meta.get("id") if meta else None,
        "filename": filename,
        "path": os.path.abspath(file_path),
        "mime_type": detected_mime,
        "bytes": len(content),
    }

    # 1. Images
    if detected_mime.startswith("image/"):
        b64_str = base64.b64encode(content).decode("ascii")
        result["is_image"] = True
        result["base64"] = b64_str
        result["data_url"] = f"data:{detected_mime};base64,{b64_str}"
        return result

    # 2. PDF Documents (Native CLI inspection)
    if detected_mime == "application/pdf":
        result["is_pdf"] = True
        return result

    # 3. Plain text / Code / JSON / CSV
    if detected_mime.startswith("text/") or detected_mime in ("application/json", "application/javascript", "application/xml"):
        result["is_text"] = True
        try:
            result["text"] = content.decode("utf-8")
        except UnicodeDecodeError:
            result["text"] = content.decode("latin-1", errors="replace")
        return result

    # 4. Generic binary
    result["is_binary"] = True
    return result

