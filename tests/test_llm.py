import io
import json
import os
import unittest
from unittest.mock import patch

from station.wiki import call_llm


class LLMResponseTests(unittest.TestCase):
    def invoke(self, content, finish_reason="stop"):
        response = io.BytesIO(json.dumps({"choices": [{
            "finish_reason": finish_reason,
            "message": {"content": content},
        }]}).encode())
        config = {"LLM_BASE_URL": "https://example.invalid", "LLM_MODEL": "test",
                  "LLM_API_KEY": "test-key", "LLM_JSON_MODE": "true"}
        with patch.dict(os.environ, config), patch("station.wiki.urllib.request.urlopen", return_value=response) as send:
            result = call_llm([{"role": "user", "content": "Return a JSON object."}])
            payload = json.loads(send.call_args.args[0].data)
        return result, payload

    def test_json_mode_request_and_response(self):
        result, payload = self.invoke('{"action":"create"}')
        self.assertEqual(result, {"action": "create"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIs(payload["stream"], False)

    def test_truncated_response_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Incomplete LLM response: length"):
            self.invoke('{"action":', "length")

    def test_non_object_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            self.invoke('[]')

    def test_extra_output_is_rejected(self):
        with self.assertRaises(json.JSONDecodeError):
            self.invoke('{"action":"create"}\n{"extra":true}')

    def test_empty_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no text content"):
            self.invoke(None)
