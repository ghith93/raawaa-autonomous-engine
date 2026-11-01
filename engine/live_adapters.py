"""Opt-in live inference adapters. Never enabled implicitly by the CLI or CI."""
from __future__ import annotations
import json, os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from engine.provider import GenerationRequest, GenerationResponse

class LiveAdapterError(RuntimeError):
    pass

def _post_json(url, headers, body, timeout=45):
    request = Request(url, data=json.dumps(body).encode("utf-8"),
                      headers={**headers, "Content-Type":"application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise LiveAdapterError(f"Provider returned HTTP {response.status}")
            payload=json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LiveAdapterError(f"Provider returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LiveAdapterError(f"Provider request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise LiveAdapterError("Provider returned an unexpected response shape")
    return payload

class OpenAICompatibleAdapter:
    def __init__(self, provider, base_url, api_key_env, timeout=45, environ=None):
        self.provider=provider; self.base_url=base_url.rstrip("/")
        self.api_key_env=api_key_env; self.timeout=timeout
        self.environ=os.environ if environ is None else environ

    def generate(self, model, request: GenerationRequest) -> GenerationResponse:
        key=self.environ.get(self.api_key_env, "").strip()
        if not key: raise LiveAdapterError(f"Missing environment variable: {self.api_key_env}")
        if not request.prompt.strip(): raise ValueError("prompt is required")
        if not 1 <= request.max_output_tokens <= 4096: raise ValueError("max_output_tokens must be between 1 and 4096")
        payload=_post_json(self.base_url+"/chat/completions", {"Authorization":f"Bearer {key}"},
            {"model":model,"messages":[{"role":"user","content":request.prompt}],
             "max_tokens":request.max_output_tokens,"stream":False}, self.timeout)
        try:
            text=payload["choices"][0]["message"]["content"]; usage=payload.get("usage",{})
            if not isinstance(text,str): raise TypeError
            input_tokens=int(usage.get("prompt_tokens",0)); output_tokens=int(usage.get("completion_tokens",0))
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise LiveAdapterError("Provider completion response is malformed") from exc
        return GenerationResponse(self.provider,model,text,input_tokens,output_tokens,0.0,mocked=False)

class GeminiAdapter:
    provider="gemini"
    def __init__(self, api_key_env="GEMINI_API_KEY", timeout=45, environ=None):
        self.api_key_env=api_key_env; self.timeout=timeout
        self.environ=os.environ if environ is None else environ

    def generate(self, model, request: GenerationRequest) -> GenerationResponse:
        key=self.environ.get(self.api_key_env,"").strip()
        if not key: raise LiveAdapterError(f"Missing environment variable: {self.api_key_env}")
        if not request.prompt.strip(): raise ValueError("prompt is required")
        if not 1 <= request.max_output_tokens <= 4096: raise ValueError("max_output_tokens must be between 1 and 4096")
        url=f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={key}"
        payload=_post_json(url,{},{"contents":[{"parts":[{"text":request.prompt}]}],
            "generationConfig":{"maxOutputTokens":request.max_output_tokens}},self.timeout)
        try:
            parts=payload["candidates"][0]["content"]["parts"]
            text="".join(p["text"] for p in parts if isinstance(p,dict) and isinstance(p.get("text"),str))
            usage=payload.get("usageMetadata",{})
            input_tokens=int(usage.get("promptTokenCount",0)); output_tokens=int(usage.get("candidatesTokenCount",0))
            if not text: raise TypeError
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise LiveAdapterError("Gemini completion response is malformed") from exc
        return GenerationResponse(self.provider,model,text,input_tokens,output_tokens,0.0,mocked=False)
