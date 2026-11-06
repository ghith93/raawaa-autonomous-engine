"""Provider-neutral image generation with explicit failover.

Design constraints that come from the repository policy, not from taste:

* zero spend:  only routes with a verified $0 cost may be selected, and an
  unknown price is treated as unaffordable, never as free;
* no single point of failure:  providers are an ordered chain, and a failure is
  recorded with its reason and handed to the next one;
* claims need evidence:  a provider that is configured but lacks credentials is
  reported as unavailable, never as working.

Every provider implements the same three operations, so adding a fourth route
later requires no change to callers.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import struct
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class ImageProviderError(RuntimeError):
    """A provider was asked to generate and could not."""


class BudgetExhausted(ImageProviderError):
    """The daily free allowance for this route is spent."""


class ProviderUnavailable(ImageProviderError):
    """The route exists in theory but cannot run here (missing credentials or deps)."""


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    width: int = 1024
    height: int = 1024
    steps: int = 4
    negative_prompt: str = ""
    seed: int | None = None

    def validate(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width and height must be positive")
        if self.width % 8 or self.height % 8:
            raise ValueError("width and height must be multiples of 8")
        if not 1 <= self.steps <= 8:
            raise ValueError("steps must be between 1 and 8 for schnell-class models")


@dataclass
class ImageResult:
    provider: str
    model: str
    png_bytes: bytes
    width: int
    height: int
    cost_usd: float = 0.0
    neurons: float = 0.0
    notes: list[str] = field(default_factory=list)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.png_bytes)
        return target


class ImageProvider(Protocol):
    name: str
    model: str
    cost_usd_per_image: float

    def available(self) -> tuple[bool, str]: ...
    def generate(self, request: ImageRequest) -> ImageResult: ...


# --------------------------------------------------------------------------
# Pure-stdlib PNG encoding. No Pillow, no dependency, works everywhere.
# --------------------------------------------------------------------------

def encode_png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode 8-bit RGB scanlines (no filter) as a PNG."""
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} bytes of RGB data, got {len(pixels)}")

    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)  # filter type 0 (None)
        raw.extend(pixels[row * stride:(row + 1) * stride])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _gradient_pixels(width: int, height: int, top: tuple[int, int, int],
                     bottom: tuple[int, int, int]) -> bytes:
    pixels = bytearray()
    for y in range(height):
        t = y / max(1, height - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        pixels.extend(bytes((r, g, b)) * width)
    return bytes(pixels)


# --------------------------------------------------------------------------
# Provider 1: deterministic offline renderer.
# --------------------------------------------------------------------------

class OfflineGradient:
    """Always available. Renders a calm two-tone field, not a product photo.

    Its job is to keep the pipeline exercised end to end, so a credentials
    problem shows up against a real file rather than at upload time.
    """

    name = "offline"
    model = "offline-gradient"
    cost_usd_per_image = 0.0

    def __init__(self, top: tuple[int, int, int] = (242, 235, 227),
                 bottom: tuple[int, int, int] = (214, 201, 186)):
        self.top = top
        self.bottom = bottom

    def available(self) -> tuple[bool, str]:
        return True, "always available"

    def generate(self, request: ImageRequest) -> ImageResult:
        request.validate()
        pixels = _gradient_pixels(request.width, request.height, self.top, self.bottom)
        return ImageResult(
            provider=self.name,
            model=self.model,
            png_bytes=encode_png(request.width, request.height, pixels),
            width=request.width,
            height=request.height,
            notes=["offline renderer: layout and pipeline only, not usable as product imagery"],
        )


# --------------------------------------------------------------------------
# Provider 2: Cloudflare Workers AI (FLUX.1 schnell).
# --------------------------------------------------------------------------

class CloudflareFlux:
    """Free tier: 10,000 neurons/day, no credit card, commercial use allowed.

    Neuron cost per the published formula: 4.80 per 512x512 tile plus 9.60 per
    step. The daily free block does not roll over, and on the Workers Free plan
    an exhausted block fails the request rather than billing, which is why the
    failure is surfaced as BudgetExhausted instead of a generic error.
    """

    name = "cloudflare"
    model = "@cf/black-forest-labs/flux-1-schnell"
    cost_usd_per_image = 0.0
    NEURONS_PER_TILE = 4.80
    NEURONS_PER_STEP = 9.60
    FREE_DAILY_NEURONS = 10_000.0
    # The hosted endpoint accepts only prompt, steps, seed and negative_prompt. It
    # rejects width/height with 'Additional or unevaluated properties', and the
    # output is a fixed square, so dimensions are advisory rather than requested.
    SUPPORTED_MAX_SIDE = 1024

    def __init__(self, env: dict[str, str] | None = None, daily_neuron_cap: float | None = None):
        self.env = dict(os.environ if env is None else env)
        self.daily_neuron_cap = self.FREE_DAILY_NEURONS if daily_neuron_cap is None else daily_neuron_cap

    # -- cost model ---------------------------------------------------------
    @classmethod
    def neurons_for(cls, width: int, height: int, steps: int) -> float:
        tiles = -(-width // 512) * -(-height // 512)
        return tiles * cls.NEURONS_PER_TILE + steps * cls.NEURONS_PER_STEP

    def images_per_day(self, width: int, height: int, steps: int) -> int:
        per_image = self.neurons_for(width, height, steps)
        return 0 if per_image <= 0 else int(self.daily_neuron_cap // per_image)

    # -- availability -------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
                   if not self.env.get(k, "").strip()]
        if missing:
            return False, f"missing credentials: {', '.join(missing)}"
        return True, "credentials present"

    def _url(self) -> str:
        account = self.env["CLOUDFLARE_ACCOUNT_ID"].strip()
        return f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{self.model}"

    def generate(self, request: ImageRequest) -> ImageResult:
        request.validate()
        ok, reason = self.available()
        if not ok:
            raise ProviderUnavailable(f"{self.name}: {reason}")

        # Only the fields this model documents. Sending unsupported ones yields a
        # 400 that reads like an authorisation failure, which cost a debugging cycle.
        payload: dict[str, Any] = {"prompt": request.prompt, "steps": request.steps}
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            payload["seed"] = request.seed

        http = urllib.request.Request(
            self._url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.env['CLOUDFLARE_API_TOKEN'].strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http, timeout=90) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # pragma: no cover - best effort diagnostics
                pass
            if exc.code in (429, 402) or "neurons" in detail.lower():
                raise BudgetExhausted(f"{self.name}: daily allowance spent (HTTP {exc.code})") from exc
            raise ImageProviderError(f"{self.name}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ImageProviderError(f"{self.name}: transport failure {type(exc).__name__}") from exc

        png = _extract_png(body)
        actual_w, actual_h = _png_size(png)
        neurons = self.neurons_for(actual_w, actual_h, request.steps)
        notes = ["free tier; cost expressed in neurons, not dollars"]
        if (actual_w, actual_h) != (request.width, request.height):
            notes.append(
                f"model returned {actual_w}x{actual_h}; the hosted endpoint does not accept "
                "explicit dimensions, so compose the final frame locally")
        return ImageResult(
            provider=self.name,
            model=self.model,
            png_bytes=png,
            width=actual_w,
            height=actual_h,
            cost_usd=0.0,
            neurons=neurons,
            notes=notes,
        )


def _png_size(png: bytes) -> tuple[int, int]:
    """Read width and height from the IHDR chunk; fall back to (0, 0) if absent."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR":
        width, height = struct.unpack(">II", png[16:24])
        return int(width), int(height)
    return 0, 0


def _extract_png(body: bytes) -> bytes:
    """Cloudflare returns raw PNG or a JSON envelope depending on the model."""
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return body
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageProviderError("provider returned neither PNG nor JSON") from exc
    if not payload.get("success", True):
        raise ImageProviderError(f"provider reported failure: {payload.get('errors')}")
    image = payload.get("result", {}).get("image")
    if not image:
        raise ImageProviderError("provider response contained no image field")
    try:
        return base64.b64decode(image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageProviderError("provider image was not valid base64") from exc


# --------------------------------------------------------------------------
# Provider 3: local open weights (Qwen-Image-2512, Apache-2.0).
# --------------------------------------------------------------------------

class LocalQwenImage:
    """Self-hosted, unlimited, commercially licensed, and honestly optional.

    Weights are Apache-2.0 only for the 2512 generation. Qwen-Image-2.1 is under
    the non-commercial Qwen Research License, so it is deliberately not wired up
    here and the config rejects it.
    """

    name = "local_qwen"
    model = "Qwen/Qwen-Image-2512"
    cost_usd_per_image = 0.0

    def __init__(self, env: dict[str, str] | None = None):
        self.env = dict(os.environ if env is None else env)

    def _deps(self) -> tuple[Any, Any] | None:
        try:
            import torch  # type: ignore
            from diffusers import DiffusionPipeline  # type: ignore
        except Exception:
            return None
        return torch, DiffusionPipeline

    def available(self) -> tuple[bool, str]:
        if self._deps() is None:
            return False, "torch and diffusers are not installed"
        if self.env.get("RAAWAA_LOCAL_QWEN_ENABLED", "").strip() not in {"1", "true", "yes"}:
            return False, "set RAAWAA_LOCAL_QWEN_ENABLED=1 to opt in (large download)"
        return True, "dependencies present and opted in"

    def generate(self, request: ImageRequest) -> ImageResult:
        request.validate()
        ok, reason = self.available()
        if not ok:
            raise ProviderUnavailable(f"{self.name}: {reason}")
        torch, DiffusionPipeline = self._deps()  # type: ignore[misc]
        pipe = DiffusionPipeline.from_pretrained(self.model, torch_dtype=torch.bfloat16)
        pipe = pipe.to("cuda" if torch.cuda.is_available() else "cpu")
        image = pipe(prompt=request.prompt, width=request.width, height=request.height,
                     num_inference_steps=max(20, request.steps)).images[0]
        import io
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return ImageResult(provider=self.name, model=self.model, png_bytes=buffer.getvalue(),
                           width=request.width, height=request.height,
                           notes=["local open weights; no per-image cost"])


# --------------------------------------------------------------------------
# The chain.
# --------------------------------------------------------------------------

def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ImageChain:
    """Ordered providers with recorded failover and a daily free budget."""

    def __init__(self, providers: list[ImageProvider], *, ledger_path: str | Path | None = None,
                 daily_neuron_cap: float = 10_000.0):
        if not providers:
            raise ValueError("at least one provider is required")
        for provider in providers:
            if provider.cost_usd_per_image != 0:
                raise ValueError(
                    f"provider {provider.name} has a non-zero price; zero-spend policy forbids it"
                )
        self.providers = providers
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.daily_neuron_cap = daily_neuron_cap

    # -- ledger -------------------------------------------------------------
    def _read_ledger(self) -> list[dict[str, Any]]:
        if not self.ledger_path or not self.ledger_path.exists():
            return []
        entries = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def neurons_used_today(self) -> float:
        today = utc_day()
        return sum(
            float(entry.get("neurons", 0))
            for entry in self._read_ledger()
            if entry.get("day") == today and entry.get("ok")
        )

    def _append(self, entry: dict[str, Any]) -> None:
        if not self.ledger_path:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- status -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        rows = []
        for provider in self.providers:
            ok, reason = provider.available()
            rows.append({
                "provider": provider.name,
                "model": provider.model,
                "available": ok,
                "reason": reason,
                "cost_usd_per_image": provider.cost_usd_per_image,
            })
        used = self.neurons_used_today()
        return {
            "providers": rows,
            "usable": [r["provider"] for r in rows if r["available"]],
            "neurons_used_today": used,
            "neurons_remaining_today": max(0.0, self.daily_neuron_cap - used),
            "day_utc": utc_day(),
        }

    # -- generation ---------------------------------------------------------
    def generate(self, request: ImageRequest) -> tuple[ImageResult, dict[str, Any]]:
        request.validate()
        attempts: list[dict[str, str]] = []
        used = self.neurons_used_today()

        for provider in self.providers:
            ok, reason = provider.available()
            if not ok:
                attempts.append({"provider": provider.name, "outcome": "unavailable", "reason": reason})
                continue

            projected = 0.0
            if isinstance(provider, CloudflareFlux):
                projected = provider.neurons_for(request.width, request.height, request.steps)
                if used + projected > self.daily_neuron_cap:
                    attempts.append({
                        "provider": provider.name,
                        "outcome": "budget",
                        "reason": f"needs {projected} neurons, {max(0.0, self.daily_neuron_cap - used)} left",
                    })
                    continue

            try:
                result = provider.generate(request)
            except ImageProviderError as exc:
                attempts.append({"provider": provider.name, "outcome": "failed", "reason": str(exc)})
                continue

            used += result.neurons
            self._append({
                "day": utc_day(),
                "ts": datetime.now(timezone.utc).isoformat(),
                "provider": result.provider,
                "model": result.model,
                "ok": True,
                "neurons": result.neurons,
                "width": result.width,
                "height": result.height,
                "prompt": request.prompt[:160],
            })
            return result, {"attempts": attempts, "served_by": result.provider}

        raise ImageProviderError(
            "every provider in the chain failed: "
            + "; ".join(f"{a['provider']}={a['outcome']}({a['reason']})" for a in attempts)
        )


# --------------------------------------------------------------------------
# Factory from config.
# --------------------------------------------------------------------------

def chain_from_config(config: dict[str, Any], env: dict[str, str] | None = None,
                      ledger_path: str | Path | None = None) -> ImageChain:
    env = dict(os.environ if env is None else env)
    daily_cap = float(config.get("daily_neuron_cap", 10_000))
    providers: list[ImageProvider] = []
    for entry in config.get("providers", []):
        pid = entry.get("id")
        if pid == "cloudflare":
            providers.append(CloudflareFlux(env=env, daily_neuron_cap=daily_cap))
        elif pid == "local_qwen":
            providers.append(LocalQwenImage(env=env))
        elif pid == "offline":
            providers.append(OfflineGradient())
        else:
            raise ValueError(f"unknown image provider in config: {pid!r}")
    return ImageChain(providers, ledger_path=ledger_path, daily_neuron_cap=daily_cap)
