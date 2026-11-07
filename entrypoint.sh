#!/bin/sh
# entrypoint.sh – prepare authentication for GitHub Copilot and Google Antigravity.
set -e

PROVIDER="${PROVIDER:-copilot}"

# ── GitHub Copilot Auth ───────────────────────────────────────────────────────
if [ -z "$GITHUB_TOKEN" ]; then
    if [ -n "$COPILOT_GITHUB_TOKEN" ]; then
        GITHUB_TOKEN="$COPILOT_GITHUB_TOKEN"
    elif [ -n "$GH_TOKEN" ]; then
        GITHUB_TOKEN="$GH_TOKEN"
    fi
fi

if [ -z "$GITHUB_TOKEN" ]; then
    # Fallback 1: Check ~/.copilot/config.json from standalone GitHub Copilot CLI
    for copilot_file in "/copilot-config-ro/config.json" "/root/.copilot/config.json"; do
        if [ -f "$copilot_file" ]; then
            TOKEN_FOUND=$(grep -E '"(oauthToken|token|oauth_token|access_token)"' "$copilot_file" 2>/dev/null | head -n1 | sed -e 's/.*:[[:space:]]*"//' -e 's/".*//' | tr -d '\r\n') || true
            if [ -n "$TOKEN_FOUND" ]; then
                GITHUB_TOKEN="$TOKEN_FOUND"
                break
            fi
        fi
    done

    # Fallback 2: Check mounted GitHub CLI config (~/.config/gh)
    if [ -z "$GITHUB_TOKEN" ]; then
        if [ -d "/gh-config-ro" ] && [ "$(ls -A /gh-config-ro 2>/dev/null)" ]; then
            # gh attempts write migrations even on read, so copy the RO mount
            # to a writable temp directory before calling `gh auth token`.
            cp -r /gh-config-ro /tmp/gh-config
            GITHUB_TOKEN=$(GH_CONFIG_DIR=/tmp/gh-config gh auth token 2>/dev/null) || true
        fi
    fi

    # Fallback 3: Local gh configuration (e.g. standalone named volume or local container login)
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
        echo "[entrypoint] WARNING: could not obtain GITHUB_TOKEN."
        echo "[entrypoint] Options:"
        echo "[entrypoint]   1. Run 'gh auth login' on host (with ~/.config/gh mounted)."
        echo "[entrypoint]   2. Or run: docker compose exec -it api gh auth login"
        echo "[entrypoint]   3. Or set GITHUB_TOKEN (or COPILOT_GITHUB_TOKEN) in your .env file."
    fi
else
    echo "[entrypoint] GITHUB_TOKEN configured from environment."
    export GITHUB_TOKEN
fi

# ── Google Antigravity Auth / Binary Check ─────────────────────────────────────
# Ensure runtime symlink for /root/.gemini/bin/agy if /opt/antigravity/bin/agy exists
if [ -d "/root/.gemini" ] && [ ! -e "/root/.gemini/bin/agy" ] && [ -x "/opt/antigravity/bin/agy" ]; then
    mkdir -p /root/.gemini/bin 2>/dev/null || true
    ln -sf /opt/antigravity/bin/agy /root/.gemini/bin/agy 2>/dev/null || true
fi

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
