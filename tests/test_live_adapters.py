import io
import unittest
from urllib.error import HTTPError
from unittest.mock import patch
from engine.live_adapters import GeminiAdapter, LiveAdapterError, OpenAICompatibleAdapter
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
        self.assertEqual(
            call.call_args.args[0].get_header("User-agent"),
            "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)",
        )

    def test_gemini(self):
        body=b'{"candidates":[{"content":{"parts":[{"text":"hello"}]}}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2}}'
        adapter=GeminiAdapter(environ={"GEMINI_API_KEY":"secret"})
        with patch("engine.live_adapters.urlopen",return_value=FakeResponse(body)) as call:
            result=adapter.generate("models/gemini-test",GenerationRequest("test","hi",20))
        self.assertEqual(result.text,"hello"); self.assertEqual(result.input_tokens,4)
        request = call.call_args.args[0]
        self.assertNotIn("secret", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "secret")

    def test_http_error_exposes_only_safe_machine_code(self):
        error_body = b'{"error":{"message":"do not retain this prompt echo","type":"permissions_error","code":"model_permission_blocked_org"}}'
        adapter = OpenAICompatibleAdapter("groq", "https://api.example/v1", "KEY", environ={"KEY":"secret"})
        failure = HTTPError("https://api.example/v1/chat/completions", 403, "Forbidden", {}, io.BytesIO(error_body))
        with patch("engine.live_adapters.urlopen", side_effect=failure):
            with self.assertRaises(LiveAdapterError) as caught:
                adapter.generate("model-x", GenerationRequest("test", "hi", 20))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.provider_error_code, "model_permission_blocked_org")
        self.assertNotIn("prompt echo", str(caught.exception))

    def test_missing_key_fails_closed(self):
        adapter=OpenAICompatibleAdapter("groq","https://api.example/v1","KEY",environ={})
        with self.assertRaises(LiveAdapterError): adapter.generate("model",GenerationRequest("test","hi"))

    def test_malformed_response_fails_closed(self):
        adapter=OpenAICompatibleAdapter("groq","https://api.example/v1","KEY",environ={"KEY":"secret"})
        with patch("engine.live_adapters.urlopen",return_value=FakeResponse(b'{}')):
            with self.assertRaises(LiveAdapterError): adapter.generate("model",GenerationRequest("test","hi"))

if __name__=="__main__": unittest.main()
