#!/bin/sh
# entrypoint.sh – prepare authentication for GitHub Copilot and Google Antigravity.
set -e

PROVIDER="${PROVIDER:-copilot}"

# ── GitHub Copilot Auth ───────────────────────────────────────────────────────
if [ -z "$GITHUB_TOKEN" ]; then
    if [ -d "/gh-config-ro" ]; then
        # gh attempts write migrations even on read, so copy the RO mount
        # to a writable temp directory before calling `gh auth token`.
        cp -r /gh-config-ro /tmp/gh-config
        GITHUB_TOKEN=$(GH_CONFIG_DIR=/tmp/gh-config gh auth token 2>/dev/null) || true
    fi

    if [ -n "$GITHUB_TOKEN" ]; then
        echo "[entrypoint] GITHUB_TOKEN set from host gh CLI auth."
        export GITHUB_TOKEN
    elif [ "$PROVIDER" = "copilot" ]; then
        echo "[entrypoint] WARNING: could not obtain GITHUB_TOKEN via 'gh auth token'."
        echo "[entrypoint] Make sure ~/.config/gh is mounted and you are logged in on the host (or set GITHUB_TOKEN in .env)."
    fi
fi

# ── Google Antigravity Auth / Binary Check ─────────────────────────────────────
if [ "$PROVIDER" = "antigravity" ] || [ -d "/root/.gemini" ]; then
    if command -v agy >/dev/null 2>&1 || [ -x "/root/.gemini/bin/agy" ]; then
        echo "[entrypoint] Antigravity 'agy' CLI binary detected."
    else
        if [ "$PROVIDER" = "antigravity" ]; then
            echo "[entrypoint] WARNING: PROVIDER='antigravity', but 'agy' binary was not found in PATH or /root/.gemini/bin."
            echo "[entrypoint] Make sure ~/.gemini is mounted to /root/.gemini in docker-compose.yml."
        fi
    fi
fi

exec "$@"
