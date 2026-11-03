import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch
from engine.live_adapters import GeminiAdapter, LiveAdapterError, OpenAICompatibleAdapter, _post_json
from engine.provider import GenerationRequest

class FakeResponse:
    status=200
    def __init__(self,body): self.body=body
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return self.body

class LiveAdapterTests(unittest.TestCase):
    def test_openai_compatible(self):
        body=b'{"choices":[{"message":{"content":"hello"}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}'
        adapter=OpenAICompatibleAdapter("groq","https://api.example/v1","KEY",environ={"KEY":"secret"})
        with patch("engine.live_adapters.urlopen",return_value=FakeResponse(body)) as call:
            result=adapter.generate("model-x",GenerationRequest("test","hi",20))
        self.assertEqual(result.text,"hello"); self.assertEqual(result.input_tokens,3)
        self.assertFalse(result.mocked); self.assertIn("/chat/completions",call.call_args.args[0].full_url)

    def test_gemini(self):
        body=b'{"candidates":[{"content":{"parts":[{"text":"hello"}]}}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2}}'
        adapter=GeminiAdapter(environ={"GEMINI_API_KEY":"secret"})
        with patch("engine.live_adapters.urlopen",return_value=FakeResponse(body)) as call:
            result=adapter.generate("models/gemini-test",GenerationRequest("test","hi",20))
        self.assertEqual(result.text,"hello"); self.assertEqual(result.input_tokens,4)
        request = call.call_args.args[0]
        self.assertNotIn("secret", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "secret")

    def test_missing_key_fails_closed(self):
        adapter=OpenAICompatibleAdapter("groq","https://api.example/v1","KEY",environ={})
        with self.assertRaises(LiveAdapterError): adapter.generate("model",GenerationRequest("test","hi"))

    def test_malformed_response_fails_closed(self):
        adapter=OpenAICompatibleAdapter("groq","https://api.example/v1","KEY",environ={"KEY":"secret"})
        with patch("engine.live_adapters.urlopen",return_value=FakeResponse(b'{}')):
            with self.assertRaises(LiveAdapterError): adapter.generate("model",GenerationRequest("test","hi"))

    def test_http_error_captures_only_safe_machine_code(self):
        body = (
            b'{"error":{"message":"sensitive provider detail",'
            b'"type":"permissions_error","code":"model_permission_blocked_org"}}'
        )
        error = HTTPError("https://api.example/v1/chat/completions", 403, "Forbidden", {}, BytesIO(body))
        with patch("engine.live_adapters.urlopen", side_effect=error):
            with self.assertRaises(LiveAdapterError) as raised:
                _post_json("https://api.example/v1/chat/completions", {}, {"model": "model-x"})
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.error_code, "model_permission_blocked_org")
        self.assertNotIn("sensitive provider detail", str(raised.exception))

    def test_http_error_rejects_unbounded_machine_code(self):
        body = b'{"error":{"message":"secret","code":"bad code\\nwith newline"}}'
        error = HTTPError("https://api.example/v1/chat/completions", 403, "Forbidden", {}, BytesIO(body))
        with patch("engine.live_adapters.urlopen", side_effect=error):
            with self.assertRaises(LiveAdapterError) as raised:
                _post_json("https://api.example/v1/chat/completions", {}, {"model": "model-x"})
        self.assertIsNone(raised.exception.error_code)
        self.assertNotIn("secret", str(raised.exception))

if __name__=="__main__": unittest.main()
