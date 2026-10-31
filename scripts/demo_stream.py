#!/usr/bin/env python3
import sys
import time

lines = [
    'data: {"id":"chatcmpl-agy-01","model":"gemini-3.8-flash-high","choices":[{"delta":{"role":"assistant"}}]}',
    "",
    'data: {"choices":[{"delta":{"content":"⚡ Hello! "}}]}',
    "",
    'data: {"choices":[{"delta":{"content":"cli2api-llm-gate is live, "}}]}',
    "",
    'data: {"choices":[{"delta":{"content":"streaming directly from Google Antigravity "}}]}',
    "",
    'data: {"choices":[{"delta":{"content":"with zero external API token costs!"}}]}',
    "",
    'data: {"choices":[{"finish_reason":"stop"}]}',
    "",
    'data: [DONE]',
    ""
]

time.sleep(0.3)
for line in lines:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    if line.startswith("data:"):
        time.sleep(0.12)
    else:
        time.sleep(0.02)
