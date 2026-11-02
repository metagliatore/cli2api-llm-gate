import os
import json
import unittest
from unittest.mock import patch, MagicMock

from tasks import (
    resolve_provider,
    is_antigravity_model,
    convert_responses_input_to_messages,
    adapt_responses_tools,
    format_messages_for_antigravity,
    dispatch_responses_completion,
    get_available_models,
    _find_agy_bin,
    _antigravity_token_check,
    ask_antigravity,
    chat_stream_antigravity,
)
from api import _get_valid_api_keys, _extract_api_key


class TestGatewayModelsAndRouting(unittest.TestCase):
    def test_resolve_provider_auto_detection(self):
        # Antigravity models auto-detected
        self.assertEqual(resolve_provider(model="gemini-3.8-flash-high"), "antigravity")
        self.assertEqual(resolve_provider(model="claude-sonnet-4-6"), "antigravity")
        self.assertEqual(resolve_provider(model="claude-opus-4-6-thinking"), "antigravity")
        self.assertEqual(resolve_provider(model="gpt-oss-120b-medium"), "antigravity")

        # Explicit provider override wins
        self.assertEqual(resolve_provider(model="gemini-3.8-flash-high", provider="copilot"), "copilot")
        self.assertEqual(resolve_provider(model="gpt-4o", provider="antigravity"), "antigravity")

    def test_is_antigravity_model(self):
        self.assertTrue(is_antigravity_model("gemini-3.8-flash-high"))
        self.assertTrue(is_antigravity_model("claude-sonnet-4-6"))
        self.assertFalse(is_antigravity_model("gpt-4o"))
        self.assertFalse(is_antigravity_model("meta-llama-3.1-8b-instruct"))

    def test_get_available_models_structure(self):
        models = get_available_models()
        self.assertIsInstance(models, list)
        self.assertGreater(len(models), 10)

        model_ids = [m["id"] for m in models]
        self.assertIn("gemini-3.8-flash-high", model_ids)
        self.assertIn("claude-sonnet-4-6", model_ids)
        self.assertIn("gpt-4o-mini", model_ids)

        for m in models:
            self.assertEqual(m.get("object"), "model")
            self.assertIn("owned_by", m)


class TestResponsesAPIConverters(unittest.TestCase):
    def test_convert_responses_input_string(self):
        msgs = convert_responses_input_to_messages(
            "Accendi la luce in cucina",
            instructions="Sei un assistente per la casa.",
        )
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "Sei un assistente per la casa.")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[1]["content"], "Accendi la luce in cucina")

    def test_convert_responses_input_complex_items(self):
        ha_input = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Che temperatura c'è in salotto?"}],
            },
            {
                "type": "function_call",
                "id": "call_temp_01",
                "call_id": "call_temp_01",
                "name": "get_temperature",
                "arguments": json.dumps({"room": "salotto"}),
            },
            {
                "type": "function_call_output",
                "call_id": "call_temp_01",
                "output": json.dumps({"temperature": 21.5, "unit": "C"}),
            },
        ]

        msgs = convert_responses_input_to_messages(ha_input)
        self.assertEqual(len(msgs), 3)

        # 1. User message
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "Che temperatura c'è in salotto?")

        # 2. Assistant tool call
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertIsNone(msgs[1]["content"])
        self.assertEqual(len(msgs[1]["tool_calls"]), 1)
        self.assertEqual(msgs[1]["tool_calls"][0]["id"], "call_temp_01")
        self.assertEqual(msgs[1]["tool_calls"][0]["function"]["name"], "get_temperature")
        self.assertEqual(json.loads(msgs[1]["tool_calls"][0]["function"]["arguments"]), {"room": "salotto"})

        # 3. Tool result
        self.assertEqual(msgs[2]["role"], "tool")
        self.assertEqual(msgs[2]["tool_call_id"], "call_temp_01")
        self.assertEqual(json.loads(msgs[2]["content"]), {"temperature": 21.5, "unit": "C"})

    def test_convert_responses_developer_role(self):
        msgs = convert_responses_input_to_messages([
            {"type": "message", "role": "developer", "content": "You are a helpful robot."},
            {"type": "message", "role": "user", "content": "Hi!"},
        ])
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "You are a helpful robot.")
        self.assertEqual(msgs[1]["role"], "user")

    def test_adapt_responses_tools(self):
        # Home Assistant flat function tool format
        flat_tools = [
            {
                "type": "function",
                "name": "turn_on",
                "description": "Turn on a device",
                "parameters": {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string"}},
                    "required": ["entity_id"],
                },
            }
        ]
        adapted = adapt_responses_tools(flat_tools)
        self.assertEqual(len(adapted), 1)
        self.assertIn("function", adapted[0])
        self.assertEqual(adapted[0]["function"]["name"], "turn_on")
        self.assertEqual(adapted[0]["function"]["parameters"]["required"], ["entity_id"])

        # Standard OpenAI format is preserved
        standard_tools = [
            {
                "type": "function",
                "function": {
                    "name": "turn_off",
                    "description": "Turn off a device",
                    "parameters": {},
                },
            }
        ]
        self.assertEqual(adapt_responses_tools(standard_tools), standard_tools)


class TestResponsesAPIDispatcher(unittest.TestCase):
    @patch("tasks.dispatch_chat_completion")
    def test_dispatch_responses_completion_formatting(self, mock_chat_completion):
        mock_chat_completion.return_value = {
            "id": "chatcmpl-mock123",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "La temperatura è 21.5 gradi.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
        }

        res = dispatch_responses_completion(
            messages=[{"role": "user", "content": "Che temperatura c'è?"}],
            model="gpt-4o-mini",
        )

        self.assertEqual(res["object"], "response")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(len(res["output"]), 1)
        self.assertEqual(res["output"][0]["type"], "message")
        self.assertEqual(res["output"][0]["role"], "assistant")
        self.assertEqual(res["output"][0]["content"][0]["text"], "La temperatura è 21.5 gradi.")
        self.assertEqual(res["usage"]["total_tokens"], 30)
        self.assertEqual(res["usage"]["input_tokens"], 20)
        self.assertEqual(res["usage"]["output_tokens"], 10)


class TestAuthHelpers(unittest.TestCase):
    def test_extract_api_key(self):
        # Bearer header
        self.assertEqual(_extract_api_key("Bearer secret_tok", None), "secret_tok")
        self.assertEqual(_extract_api_key("bearer secret_tok2", None), "secret_tok2")

        # X-API-Key header
        self.assertEqual(_extract_api_key(None, "custom_x_key"), "custom_x_key")

        # Query param fallback
        self.assertEqual(_extract_api_key(None, None, "query_key"), "query_key")

    def test_get_valid_api_keys(self):
        with patch("api.API_KEYS", "tok1, tok2 , tok3"), patch("api.API_KEY", "legacy_tok"):
            valid = _get_valid_api_keys()
            self.assertIn("tok1", valid)
            self.assertIn("tok2", valid)
            self.assertIn("tok3", valid)
            self.assertIn("legacy_tok", valid)
            self.assertEqual(len(valid), 4)


class TestBinaryResolution(unittest.TestCase):
    def test_find_agy_bin_explicit_env(self):
        with patch.dict(os.environ, {"AGY_PATH": "/custom/path/agy"}):
            with patch("tasks.os.path.exists", return_value=True):
                self.assertEqual(_find_agy_bin(), "/custom/path/agy")

    def test_find_agy_bin_path_lookup(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("tasks.shutil.which", return_value="/opt/antigravity/bin/agy"):
                self.assertEqual(_find_agy_bin(), "/opt/antigravity/bin/agy")

    def test_find_agy_bin_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("tasks.shutil.which", return_value=None):
                with patch("tasks.os.path.exists", side_effect=lambda p: p == "/usr/local/bin/agy"):
                    with patch("tasks.os.access", return_value=True):
                        self.assertEqual(_find_agy_bin(), "/usr/local/bin/agy")


class TestAntigravityAuthCheck(unittest.TestCase):
    def test_auth_check_token_missing(self):
        with patch("tasks.os.path.isfile", return_value=False):
            err = _antigravity_token_check()
            self.assertIsNotNone(err)
            self.assertIn("Antigravity session token not found", err["error"])

    def test_auth_check_token_present(self):
        with patch("tasks.os.path.isfile", return_value=True), patch("tasks.os.path.getsize", return_value=128):
            err = _antigravity_token_check()
            self.assertIsNone(err)

    def test_auth_check_skip_env(self):
        with patch.dict(os.environ, {"ANTIGRAVITY_SKIP_AUTH_CHECK": "true"}):
            with patch("tasks.os.path.isfile", return_value=False):
                err = _antigravity_token_check()
                self.assertIsNone(err)


class TestAntigravityStdinExecution(unittest.TestCase):
    def test_ask_antigravity_uses_stdin(self):
        with patch("tasks._antigravity_token_check", return_value=None), \
             patch("tasks._find_agy_bin", return_value="/mock/bin/agy"), \
             patch("tasks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"response": "ok"}', stderr="")
            res = ask_antigravity(prompt="hello world prompt")
            self.assertEqual(res.get("content"), "ok")
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            call_cmd = mock_run.call_args[0][0]
            self.assertNotIn("-p", call_cmd)
            self.assertIn("--input-format", call_cmd)
            self.assertIn("text", call_cmd)
            self.assertEqual(call_kwargs.get("input"), "hello world prompt")

    def test_stream_antigravity_uses_stdin(self):
        with patch("tasks._antigravity_token_check", return_value=None), \
             patch("tasks._find_agy_bin", return_value="/mock/bin/agy"), \
             patch("tasks.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.stdout = ['{"event": "result", "result": {"response": "done"}}']
            mock_proc.stderr = MagicMock()
            mock_proc.returncode = 0
            mock_proc.poll.return_value = 0
            mock_popen.return_value = mock_proc

            gen = chat_stream_antigravity(messages=[{"role": "user", "content": "hello streaming"}])
            chunks = list(gen)
            self.assertTrue(len(chunks) > 0)
            mock_popen.assert_called_once()
            call_cmd = mock_popen.call_args[0][0]
            self.assertNotIn("-p", call_cmd)
            self.assertIn("--input-format", call_cmd)
            self.assertIn("text", call_cmd)


if __name__ == "__main__":
    unittest.main()
