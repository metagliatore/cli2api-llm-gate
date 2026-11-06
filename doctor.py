"""
doctor.py – Diagnostic & Health Check tool for cli2api-llm-gate.

Can be run via CLI:
    docker compose exec api python -m doctor
    docker compose exec api python doctor.py
    docker compose exec api python -m doctor --json

Or accessed via authenticated HTTP:
    GET /v1/doctor
    GET /v1/doctor?format=text
"""

import os
import sys
import time
import json
import shutil
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from tasks import (
    _get_github_token,
    _find_agy_bin,
    _antigravity_token_check,
    _get_subprocess_env,
)
from files_store import get_storage_dir


def check_redis(redis_url: Optional[str] = None) -> Dict[str, Any]:
    """Test Redis connectivity and measure round-trip latency."""
    url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379")
    try:
        from redis import Redis
        r = Redis.from_url(url, socket_connect_timeout=2.0, socket_timeout=2.0)
        t0 = time.perf_counter()
        r.ping()
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        return {
            "status": "ok",
            "message": "Connected",
            "latency_ms": round(latency_ms, 2),
            "url": url,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Connection failed ({e})",
            "url": url,
        }


def check_rate_limiter(redis_status: Dict[str, Any]) -> Dict[str, Any]:
    """Check rate limiter configuration and Redis backing store."""
    rpm = int(os.getenv("RATE_LIMIT_RPM", "60"))
    if redis_status.get("status") == "ok":
        return {
            "status": "ok",
            "message": f"Active ({rpm} RPM sliding-window)",
            "rpm": rpm,
        }
    return {
        "status": "warning",
        "message": f"Degraded ({rpm} RPM configured, but Redis is offline; failing open)",
        "rpm": rpm,
    }


def check_copilot() -> Dict[str, Any]:
    """Inspect GitHub Copilot token, verify authentication with GitHub API if reachable."""
    token = _get_github_token()
    base_url = os.getenv("COPILOT_BASE_URL", "https://api.githubcopilot.com")
    model = os.getenv("COPILOT_MODEL", "gpt-4o-mini")

    if not token:
        return {
            "status": "warning",
            "message": (
                "No token found. Provide GITHUB_TOKEN in .env or run: "
                "docker compose exec -it api gh auth login"
            ),
            "configured": False,
            "model": model,
            "base_url": base_url,
        }

    # Mask token for safe diagnostic output
    if len(token) > 10:
        masked = f"{token[:4]}...{token[-3:]}"
    else:
        masked = "***"

    # Attempt lightweight verification with GitHub / Copilot endpoint (3s timeout)
    verify_status = "unverified"
    verify_detail = ""
    try:
        # Check GitHub Copilot / Models or User profile endpoint
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "cli2api-llm-gate-doctor",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            if resp.status == 200:
                user_data = json.loads(resp.read().decode())
                login_user = user_data.get("login", "")
                verify_status = "verified"
                verify_detail = f"User: @{login_user}" if login_user else "GitHub OAuth OK"
    except urllib.error.HTTPError as he:
        if he.code in (401, 403):
            verify_status = "invalid"
            verify_detail = f"HTTP {he.code}: token rejected or expired"
        else:
            verify_status = "reachable"
            verify_detail = f"HTTP {he.code}"
    except Exception:
        # Network unreachable or timeout; keep status as present
        verify_status = "offline"
        verify_detail = "Offline / Timeout verifying online"

    if verify_status == "invalid":
        return {
            "status": "error",
            "message": f"Token rejected ({verify_detail}) | Token: {masked}",
            "configured": True,
            "token_masked": masked,
            "model": model,
            "base_url": base_url,
        }

    detail_str = f"Token: {masked}"
    if verify_detail:
        detail_str += f" ({verify_detail})"
    detail_str += f" | Model: {model}"

    return {
        "status": "ok",
        "message": detail_str,
        "configured": True,
        "verified": verify_status == "verified",
        "token_masked": masked,
        "model": model,
        "base_url": base_url,
    }


def check_antigravity() -> Dict[str, Any]:
    """Inspect Google Antigravity binary, session token, and model configuration."""
    model = os.getenv("ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
    agy_bin = _find_agy_bin()

    bin_exists = False
    if os.path.isabs(agy_bin):
        bin_exists = os.path.isfile(agy_bin) and os.access(agy_bin, os.X_OK)
    else:
        bin_exists = shutil.which(agy_bin) is not None

    token_err = _antigravity_token_check()

    if not bin_exists:
        return {
            "status": "warning",
            "message": (
                f"Binary '{agy_bin}' not found. Make sure ~/.gemini is mounted "
                "or use docker-compose.standalone.yml"
            ),
            "configured": False,
            "binary_path": agy_bin,
            "model": model,
        }

    if token_err:
        return {
            "status": "warning",
            "message": (
                "Session token missing. Run: docker compose exec -it api agy "
                "(or 'docker compose run --rm api agy') to complete Google login"
            ),
            "configured": False,
            "binary_path": agy_bin,
            "model": model,
        }

    # Binary and token present
    detail_str = f"Session active | Binary: {agy_bin} | Model: {model}"

    return {
        "status": "ok",
        "message": detail_str,
        "configured": True,
        "binary_path": agy_bin,
        "model": model,
    }


def check_storage() -> Dict[str, Any]:
    """Verify uploads storage directory accessibility and file count."""
    storage_dir = get_storage_dir()
    try:
        os.makedirs(storage_dir, exist_ok=True)
        writable = os.access(storage_dir, os.W_OK)
        file_count = len(os.listdir(storage_dir)) if os.path.exists(storage_dir) else 0
        if writable:
            return {
                "status": "ok",
                "message": f"Writable ({storage_dir}) | {file_count} stored file(s)",
                "path": storage_dir,
                "writable": True,
                "file_count": file_count,
            }
        return {
            "status": "error",
            "message": f"Directory not writable: {storage_dir}",
            "path": storage_dir,
            "writable": False,
            "file_count": file_count,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Storage error: {e}",
            "path": storage_dir,
            "writable": False,
        }


def run_diagnostics(redis_url: Optional[str] = None) -> Dict[str, Any]:
    """Run full diagnostic check on all subsystems and evaluate overall health."""
    provider = os.getenv("PROVIDER", "copilot").lower()

    redis_diag = check_redis(redis_url)
    limiter_diag = check_rate_limiter(redis_diag)
    copilot_diag = check_copilot()
    antigravity_diag = check_antigravity()
    storage_diag = check_storage()

    # Determine overall health
    # The active provider must be operational for the service to be healthy.
    active_diag = antigravity_diag if provider == "antigravity" else copilot_diag
    if active_diag.get("status") == "ok" and redis_diag.get("status") == "ok":
        overall = "healthy"
    elif active_diag.get("status") == "ok":
        overall = "degraded"  # Active provider works, but Redis or another subsystem is down
    else:
        overall = "error"

    return {
        "overall_status": overall,
        "active_provider": provider,
        "timestamp": int(time.time()),
        "components": {
            "redis": redis_diag,
            "rate_limiter": limiter_diag,
            "copilot": copilot_diag,
            "antigravity": antigravity_diag,
            "storage": storage_diag,
        },
    }


def format_doctor_report(diag: Dict[str, Any], use_colors: bool = True) -> str:
    """Format diagnostic results into a clean, human-readable terminal table."""
    # ANSI color helpers
    if use_colors:
        C_GREEN = "\033[92m"
        C_YELLOW = "\033[93m"
        C_RED = "\033[91m"
        C_CYAN = "\033[96m"
        C_BOLD = "\033[1m"
        C_RESET = "\033[0m"
    else:
        C_GREEN = C_YELLOW = C_RED = C_CYAN = C_BOLD = C_RESET = ""

    def status_badge(status: str) -> str:
        s = status.lower()
        if s == "ok" or s == "healthy":
            return f"{C_GREEN}✅ OK       {C_RESET}"
        elif s == "warning" or s == "degraded":
            return f"{C_YELLOW}⚠️  WARN     {C_RESET}"
        else:
            return f"{C_RED}❌ ERROR    {C_RESET}"

    provider = diag.get("active_provider", "copilot")
    comps = diag.get("components", {})

    lines = []
    lines.append("=" * 82)
    lines.append(f"{C_BOLD}{C_CYAN}  cli2api-llm-gate – System Diagnostics & Health Check (Doctor){C_RESET}")
    lines.append("=" * 82)
    lines.append(f"  Active Provider : {C_BOLD}{provider.upper()}{C_RESET}")
    lines.append("─" * 82)
    lines.append(f"  {'COMPONENT':<16} {'STATUS':<12} {'DETAILS'}")
    lines.append("─" * 82)

    # Display components in logical order
    order = [
        ("Copilot", comps.get("copilot", {})),
        ("Antigravity", comps.get("antigravity", {})),
        ("Redis", comps.get("redis", {})),
        ("Rate Limiter", comps.get("rate_limiter", {})),
        ("Storage", comps.get("storage", {})),
    ]

    for name, cinfo in order:
        badge = status_badge(cinfo.get("status", "error"))
        msg = cinfo.get("message", "")
        # Add marker if this is the active provider
        prefix = "★ " if name.lower() == provider else "  "
        lines.append(f"{prefix}{name:<14} {badge} {msg}")

    lines.append("─" * 82)
    overall = diag.get("overall_status", "error").upper()
    if overall == "HEALTHY":
        overall_str = f"{C_GREEN}✅ HEALTHY (Ready to serve OpenAI-compatible requests){C_RESET}"
    elif overall == "DEGRADED":
        overall_str = f"{C_YELLOW}⚠️  DEGRADED (Active provider ready, but background cache/redis is offline){C_RESET}"
    else:
        overall_str = f"{C_RED}❌ ATTENTION REQUIRED (Active provider '{provider}' is not ready){C_RESET}"

    lines.append(f"  Overall Health  : {overall_str}")
    lines.append("=" * 82)

    return "\n".join(lines)


def main() -> None:
    """CLI entrypoint for `python -m doctor`."""
    json_mode = "--json" in sys.argv
    diag = run_diagnostics()

    if json_mode:
        print(json.dumps(diag, indent=2))
    else:
        use_colors = sys.stdout.isatty()
        print(format_doctor_report(diag, use_colors=use_colors))

    # Exit code: 0 if healthy/degraded (provider active), 1 if active provider has an error
    if diag.get("overall_status") in ("healthy", "degraded"):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
