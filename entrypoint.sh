#!/bin/sh
# entrypoint.sh – prepare authentication for GitHub Copilot and Google Antigravity.
set -e

PROVIDER="${PROVIDER:-copilot}"

# ── GitHub Copilot Auth ───────────────────────────────────────────────────────
if [ -z "$GITHUB_TOKEN" ]; then
    if [ -d "/gh-config-ro" ] && [ "$(ls -A /gh-config-ro 2>/dev/null)" ]; then
        # gh attempts write migrations even on read, so copy the RO mount
        # to a writable temp directory before calling `gh auth token`.
        cp -r /gh-config-ro /tmp/gh-config
        GITHUB_TOKEN=$(GH_CONFIG_DIR=/tmp/gh-config gh auth token 2>/dev/null) || true
    fi

    # Fallback to local gh configuration (e.g. standalone named volume or local container login)
    if [ -z "$GITHUB_TOKEN" ]; then
        if [ -f "/root/.config/gh/hosts.yml" ] || [ -d "/root/.config/gh" ]; then
            GITHUB_TOKEN=$(gh auth token 2>/dev/null) || true
            if [ -z "$GITHUB_TOKEN" ] && [ -f "/root/.config/gh/hosts.yml" ]; then
                GITHUB_TOKEN=$(grep -E 'oauth_token:' /root/.config/gh/hosts.yml 2>/dev/null | head -n1 | sed -e 's/.*oauth_token:[[:space:]]*//' -e 's/["'"'"']//g' | tr -d '\r\n') || true
            fi
        fi
    fi

    if [ -n "$GITHUB_TOKEN" ]; then
        echo "[entrypoint] GITHUB_TOKEN configured successfully."
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

    if [ "$PROVIDER" = "antigravity" ]; then
        if [ ! -f "/root/.gemini/antigravity-cli/antigravity-oauth-token" ] && [ ! -f "$HOME/.gemini/antigravity-cli/antigravity-oauth-token" ]; then
            echo "[entrypoint] NOTICE: Antigravity session token not found."
            echo "[entrypoint] To authenticate, run: docker compose exec -it api agy (or 'docker compose run --rm api agy')"
        else
            echo "[entrypoint] Antigravity session token detected."
        fi
    fi
fi

exec "$@"
