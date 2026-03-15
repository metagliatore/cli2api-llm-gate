# ──────────────────────────────────────────────────────────────────────────────
# GitHub Copilot HTTP API
# Auth: mounts ~/.config/gh from the host; the entrypoint calls `gh auth token`
# to inject GITHUB_TOKEN automatically – no manual PAT management needed.
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ----- system deps + GitHub CLI -----
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# ----- Python dependencies -----
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ----- Application code -----
COPY . .

# ----- Entrypoint -----
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Default command (overridden per-service in docker-compose.yml)
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
