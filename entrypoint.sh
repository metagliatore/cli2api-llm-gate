#!/bin/sh
# entrypoint.sh – inject GITHUB_TOKEN from the mounted gh CLI auth before starting the app.
set -e

if [ -z "$GITHUB_TOKEN" ]; then
    # gh tries to write-migrate config even on read, so copy the RO mount
    # to a writable temp directory before calling `gh auth token`.
    if [ -d "/gh-config-ro" ]; then
        cp -r /gh-config-ro /tmp/gh-config
        GITHUB_TOKEN=$(GH_CONFIG_DIR=/tmp/gh-config gh auth token 2>/dev/null) || true
    fi

    if [ -z "$GITHUB_TOKEN" ]; then
        echo "[entrypoint] WARNING: could not obtain GITHUB_TOKEN via 'gh auth token'."
        echo "[entrypoint] Make sure ~/.config/gh is mounted and you are logged in on the host."
    else
        echo "[entrypoint] GITHUB_TOKEN set from gh CLI auth."
    fi
    export GITHUB_TOKEN
fi

exec "$@"
