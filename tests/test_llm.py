import io
import json
import os
import unittest
from unittest.mock import patch

from station.wiki import call_llm, _read_llm_response


class LLMResponseTests(unittest.TestCase):
    def invoke(self, content, finish_reason="stop", **options):
        response = io.BytesIO(json.dumps({"choices": [{
            "finish_reason": finish_reason,
            "message": {"content": content},
        }]}).encode())
        config = {"LLM_BASE_URL": "https://example.invalid", "LLM_MODEL": "test",
                  "LLM_API_KEY": "test-key", "LLM_JSON_MODE": "true"}
        with patch.dict(os.environ, config), patch("station.wiki.urllib.request.urlopen", return_value=response) as send:
            result = call_llm([{"role": "user", "content": "Return a JSON object."}], **options)
            payload = json.loads(send.call_args.args[0].data)
        return result, payload

    def test_json_mode_request_and_response(self):
        result, payload = self.invoke('{"action":"create"}')
        self.assertEqual(result, {"action": "create"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIs(payload["stream"], False)
        self.assertNotIn('max_tokens', payload)

    def test_output_budget_override_is_sent_without_changing_model(self):
        result, payload = self.invoke('{"ready":true}', max_tokens=65536)
        self.assertTrue(result['ready'])
        self.assertEqual(payload['max_tokens'], 65536)
        self.assertEqual(payload['model'], 'test')

    def test_invalid_output_budget_is_rejected_before_network(self):
        with patch('station.wiki.urllib.request.urlopen') as send:
            for limit in (True, 0, -1, 65537, 1.5, '65536'):
                with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'output token budget'):
                    call_llm([], max_tokens=limit)
        send.assert_not_called()

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

    def test_keepalive_whitespace_cannot_extend_request_forever(self):
        response=io.BytesIO(b' '*100)
        with patch('time.monotonic',side_effect=[0,0,2]):
            with self.assertRaisesRegex(TimeoutError,'time limit'):
                _read_llm_response(response,1)

    def test_oversized_response_is_rejected(self):
        import time
        with self.assertRaisesRegex(ValueError,'size limit'):
            _read_llm_response(io.BytesIO(b' '*200),time.monotonic()+30,limit=100)

    def test_missing_message_has_a_diagnostic_error_and_closes_response(self):
        response=io.BytesIO(b'{"choices":[{"finish_reason":"stop"}]}')
        with patch.dict(os.environ,{'LLM_BASE_URL':'https://example.invalid','LLM_MODEL':'test'}),patch('station.wiki.urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(ValueError,'no completion message'):
                call_llm([])
        self.assertTrue(response.closed)

    def test_socket_keepalive_is_interrupted_at_deadline(self):
        import threading,time
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.end_headers()
                try:
                    for _ in range(60):
                        self.wfile.write(b' ');self.wfile.flush();time.sleep(.1)
                except (BrokenPipeError,ConnectionResetError): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        started=time.monotonic()
        try:
            config={'LLM_BASE_URL':f'http://127.0.0.1:{server.server_port}','LLM_MODEL':'test','LLM_TIMEOUT_SECONDS':'1'}
            with patch.dict(os.environ,config),self.assertRaises(TimeoutError): call_llm([])
            self.assertLess(time.monotonic()-started,3)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_deadline_ssl_shutdown_is_reported_as_timeout(self):
        import ssl
        class Response(io.BytesIO):
            def read1(self,size): raise ssl.SSLError('transport interrupted')
        with patch('time.monotonic',side_effect=[0,0,2]):
            with self.assertRaisesRegex(TimeoutError,'time limit'):
                _read_llm_response(Response(),1)
