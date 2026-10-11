## v1.0.0 (2026-10-11)

### Feat

- add OpenAI Responses API (/v1/responses) compatibility for Home Assistant Assist
- add /v1/antigravity/usage and /usage statistics endpoint
- optimize streaming token usage, abort cleanup, and session reuse
- add full agent harness support with streaming, tool calling, and v1/models
- rename project to cli2api-ai
- transparently map non-antigravity model requests to default model
- add Google Antigravity support, multi-provider routing and project memory
- support multiple Bearers
- add Caddy rev proxy
- add endpoint ask-image
- first commit

### Fix

- report agy streaming errors instead of yielding empty content
- ensure object and created timestamp are always populated in copilot responses
- auto-detect copilot models and update default COPILOT_BASE_URL to api.githubcopilot.com
- import logging and initialize logger in tasks.py
- chat completions timeout handling with 504 and 180s poll
