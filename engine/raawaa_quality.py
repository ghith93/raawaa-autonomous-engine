"""Visual quality gates for Raawaa assets.

The question this answers: *how do we guarantee the content is good when there
is no photographer, no studio and no budget?*

The honest answer is that taste cannot be automated, but defects can be
rejected. So this module is a rejection filter, not an art director:

* deterministic preflight over image metadata (size, ratio, brightness) using
  the standard library only — no Pillow, no model, no network;
* a human shot checklist derived from the brand's own visual rules;
* concrete camera settings for a phone, because "use good light" is not advice.

Run:
    python scripts/raawaa_asset_check.py shot.jpg
    python -m engine.raawaa_quality --checklist
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.raawaa_economics import load_brand

# Instagram accepts these; anything else fails at upload or gets recompressed.
MIN_SQUARE_PX = 1080
MAX_BYTES = 8 * 1024 * 1024
IDEAL_RATIOS = {
    (4, 5): "عمودي 4:5 — الأفضل للفيد",
    (3, 4): "شبكة الحساب 3:4 — معيار إنستغرام المحدّث",
    (1, 1): "مربع",
    (9, 16): "ستوري/ريل",
}
# Below this mean luminance a photo reads as grey and dirty on a phone screen.
MIN_MEAN_LUMA = 45
MAX_MEAN_LUMA = 235


@dataclass
class ImageInfo:
    path: str
    width: int
    height: int
    fmt: str
    bytes: int
    mean_luma: float | None = None
    problem: str | None = None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments to the SOF marker. Returns (width, height)."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + length
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """Parse RIFF/WEBP VP8, VP8L, or VP8X dimensions using stdlib only."""
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        w = int.from_bytes(data[26:28], "little") & 0x3FFF
        h = int.from_bytes(data[28:30], "little") & 0x3FFF
        return w, h
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return w, h
    return None


def inspect(path: str | Path) -> ImageInfo:
    p = Path(path)
    if not p.exists():
        return ImageInfo(str(p), 0, 0, "missing", 0, problem="file_not_found")
    data = p.read_bytes()
    if data[:4] == b"\x89PNG":
        size = _png_size(data)
        fmt = "png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        size = _webp_size(data)
        fmt = "webp"
    else:
        size = _jpeg_size(data)
        fmt = "jpeg" if size else "unknown"
    if size is None:
        return ImageInfo(str(p), 0, 0, fmt, len(data), problem="unreadable_dimensions")
    width, height = size
    return ImageInfo(str(p), width, height, fmt, len(data))


def assess(info: ImageInfo) -> list[str]:
    """Return a list of reject reasons. Empty list means it passes preflight."""
    problems: list[str] = []
    if info.problem:
        return [info.problem]
    if info.bytes > MAX_BYTES:
        problems.append(f"file_too_large:{info.bytes}")
    if min(info.width, info.height) < MIN_SQUARE_PX:
        problems.append(f"too_small:{info.width}x{info.height}")
    ratio = (info.width, info.height)
    if ratio not in IDEAL_RATIOS:
        short, long = sorted(ratio)
        simplified = (round(short / long, 3))
        allowed = {round(a / b, 3) for a, b in IDEAL_RATIOS}
        if simplified not in allowed:
            problems.append(f"unusual_ratio:{info.width}x{info.height}")
    if info.mean_luma is not None:
        if info.mean_luma < MIN_MEAN_LUMA:
            problems.append(f"too_dark:{info.mean_luma:.1f}")
        elif info.mean_luma > MAX_MEAN_LUMA:
            problems.append(f"too_bright:{info.mean_luma:.1f}")
    if info.fmt == "unknown":
        problems.append("unsupported_format")
    return problems


PHONE_SETTINGS = {
    "light_ar": "نافذة عند الظهر أو الجانب، لا شمس مباشرة، لا سقف مصباح فوق المنتج",
    "background_ar": "قماش كتان أو ورق أبيض أو حائط فاتح — لا سجاد، لا طاولة خشب غامق",
    "distance_ar": "٣٠–٤٠ سم، عدسة ١× وليس 0.5× (الواسعة تشوّه العبوة)",
    "stability_ar": "ثبّت الهاتف على كتب أو حامل، لا تمسكه باليد",
    "focus_ar": "المس الشاشة على الملصق وثبّت التعريض، ثم اخفض السطوع قليلًا",
    "shots_per_item_ar": "٥ لقطات على الأقل، اختر واحدة — لا تصوّر واحدة وتنتظر المعجزة",
    "no_filter_ar": "بلا فلتر وبلا تحسين تلقائي. الفلتر يجعل المنتج يبدو مصنوعًا بالذكاء الاصطناعي",
    "do_not_ar": [
        "لا تصوّر على خلفية خشب غامق",
        "لا تستخدم وميض الكاميرا",
        "لا تصوّر في الليل بإضاءة الصالة الصفراء",
        "لا تُظهر وجوهًا بلا موافقة",
        "لا تصوّر عبوة فارغة أو مفتوحة بشكل يوحي بأنها مستعملة"
    ]
}


def shot_checklist(brand: dict[str, Any] | None = None) -> dict[str, Any]:
    brand = brand or load_brand()
    return {
        "mode": "raawaa_visual_quality_read_only",
        "note": "Rejection filter + shot discipline. No model, no network.",
        "phone_settings": PHONE_SETTINGS,
        "preflight_limits": {
            "min_px": MIN_SQUARE_PX,
            "max_bytes": MAX_BYTES,
            "ratios": [f"{a}:{b} {label}" for (a, b), label in IDEAL_RATIOS.items()],
            "mean_luma_range": [MIN_MEAN_LUMA, MAX_MEAN_LUMA],
        },
        "real_asset_rules": brand["visual_rules"],
        "acceptance": [
            "الملصق مقروء عند تصغير الصورة إلى ٣٠٠ بكسل",
            "العبوة في منتصف الإطار ولها مساحة تنفّس حولها",
            "لا انعكاس على الزجاج يحجب الاسم",
            "اللون في الصورة يطابق العبوة بيدك (قارنها بجانب الشاشة)",
            "لا شيء في الخلفية يشتّت الانتباه: لا أسلاك، لا فوضى، لا أشخاص",
        ],
    }


def render_checklist(data: dict[str, Any]) -> str:
    lines = ["# ضبط جودة الصور — رواء", "", "## إعدادات الهاتف (انسخها مرة واحدة)", ""]
    for key, value in data["phone_settings"].items():
        if isinstance(value, list):
            lines += [f"**لا تفعل:**"] + [f"- {v}" for v in value]
        else:
            lines.append(f"- **{key}:** {value}")
    lines += ["", "## فحص تقني تلقائي", ""]
    limits = data["preflight_limits"]
    lines += [
        f"- الحد الأدنى: **{limits['min_px']}×{limits['min_px']} بكسل**",
        f"- الحجم الأقصى: **{limits['max_bytes'] // (1024*1024)} ميغابايت**",
        f"- النسب المقبولة: {' · '.join(limits['ratios'])}",
        "",
        "```bash",
        "python scripts/raawaa_asset_check.py path/to/shot.jpg",
        "```",
        "",
        "## معايير القبول (بالعين، قبل النشر)",
        "",
    ]
    lines += [f"- [ ] {item}" for item in data["acceptance"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if "--checklist" in args or not args:
        data = shot_checklist()
        print(json.dumps(data, ensure_ascii=False, indent=2) if "--json" in args
              else render_checklist(data))
        return 0
    failed = False
    for path in args:
        info = inspect(path)
        problems = assess(info)
        status = "PASS" if not problems else "REJECT " + ",".join(problems)
        print(f"{status:<40} {info.path} ({info.width}x{info.height}, {info.bytes//1024}KB)")
        failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
