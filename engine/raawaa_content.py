"""Raawaa content engine: deterministic, free, and honest about what AI may fake.

Why deterministic first
-----------------------
At zero followers the bottleneck is not model quality, it is *shipping*. A plan
that requires an API key, a queue and a watchdog before the first post exists is
a plan that produces no posts. So this module needs no network, no model and no
credentials: it emits a complete, reviewable week of content from the brand file.

Every item carries an `llm_prompt` so a later Workflow can rewrite the draft with
a model when one is enabled. The draft is the floor, not the ceiling.

The visual honesty rule
-----------------------
`real_asset_required` marks items that must use a real photo. AI may build
backdrops, textures and type layouts. It may NOT fabricate a before/after, a
customer's face, or a testimonial. Getting caught doing that on a beauty account
costs the account, not one post.

Run:
    python -m engine.raawaa_content --week 1 --start 2026-11-09
    python -m engine.raawaa_content --week 1 --format markdown
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from typing import Any

from engine.raawaa_economics import load_brand

DIALECT = "سورية بيضاء بسيطة"

# Pillars are the *reason* a post exists. A week that is all product shots is a
# catalog, not a feed. Proportions below keep the feed watchable.
PILLARS = {
    "result": {"ar": "النتيجة", "goal": "أرِ الملمس واللمعان"},
    "education": {"ar": "تعليم", "goal": "اشرح لماذا يحدث التطاير والتشابك"},
    "objection": {"ar": "اعتراض", "goal": "أجب عن سؤال يمنع الشراء"},
    "behind": {"ar": "خلف الكواليس", "goal": "أرِ أنها صناعة سورية حقيقية"},
    "offer": {"ar": "عرض", "goal": "حوّل الاهتمام إلى طلب"},
    "proof": {"ar": "دليل اجتماعي", "goal": "شهادة حقيقية فقط"},
}

HOOKS = {
    "result": [
        "شعرك مش ناعم؟ جرّبي هالحركة الصغيرة قبل التسريح",
        "الفرق بين شعر يلمع وشعر باهت… دقيقة وحدة",
        "ليش شعرك يطير أول ما يزيد الغبار؟",
        "هالقطرة الصغيرة على الأطراف… شوفي شو بتعمل",
        "ما تحتاجي روتين طويل، تحتاجي خطوة وحدة صح",
    ],
    "education": [
        "٩٠٪ من مشكلة التطاير سببها إنك تعملي هالشي غلط",
        "الشعر ما يحتاج غسيل أكتر، يحتاج ترطيب أخف",
        "التسريح على شعر مبلل شوي هو أكثر شي يكسّر الأطراف",
        "ليش اللمعان يروح بالصيف؟ السبب أبسط مما تتوقعي",
        "الرطوبة تقفل مسام الشعرة… وهون يصير التطاير",
    ],
    "objection": [
        "هل السيروم يترك أثر دهني؟ خليني أوضّح نقطة مهمة",
        "«جرّبت كل شي وما نفع» — هالجملة بتوقف عند سبب واحد",
        "السعر مقابل الحجم: خلينا نحسبها بالعقل",
        "شعري مصبوغ، يناسبني؟",
        "ما بشتري من متجر ما بعرفه — سؤال منطقي، وهالجواب",
    ],
    "behind": [
        "كل عبوة عندنا تُجهّز عند الطلب — وهذا سبب التأخير البسيط",
        "صنع في سوريا… من التطوير للتغليف",
        "ليش ما نبيع بكميات كبيرة جاهزة؟",
        "من وين تيجي فكرة العطر الخفيف بالمنتج",
    ],
    "offer": [
        "عبوتان بسعر أرخص من شراء واحدة تانية… والتوصيل علينا",
        "خصم ١٠٪ إذا دفعت شام كاش",
        "توصيل البيت مجاني على الحزم",
        "ما عجبك؟ ما تدفعي",
    ],
    "proof": [
        "هذا اللي قالته أول عميلة",
        "أول عشرة تجارب… والنتائج بالصور",
    ],
}

REEL_BEATS = [
    "0-2 ثانية: الهوك بالضبط كما هو مكتوب، بلا مقدمة",
    "2-6 ثانية: أظهر المشكلة (شعر متطاير / متشابك) بصورة حقيقية",
    "6-15 ثانية: أظهر طريقة الاستخدام — قطرتان على الأطراف",
    "15-25 ثانية: النتيجة تحت ضوء طبيعي، لا فلتر",
    "25-30 ثانية: دعوة واحدة واضحة فقط (اكتبي «سيروم» بالتعليق)",
]

VISUAL_BRIEF_AI = {
    "result": "ستايلشوت ماكرو لقطرة سيروم شفافة على خلفية ناعمة بلون كريمي، إضاءة جانبية دافئة، بلا أي شعر مولّد",
    "education": "أيقونات خطية بسيطة لشعرة متشققة مقابل شعرة ملساء، خلفية بيج فاتح، تصميم مسطّح",
    "objection": "لقطة ثابتة لثلاث عبوات بأحجامها على سطح رخام فاتح مع ظل خفيف، كتابة كبيرة بالعربي",
    "behind": "طاولة تغليف حقيقية بإضاءة نهارية، عبوات مرتبة، خلفية قماش كتان — لا وجوه",
    "offer": "بطاقة عرض بخط عربي كبير على خلفية بلون العلامة، بلا عناصر إضافية",
    "proof": "قالب كاروسيل بحدود بسيطة مكان لصورة الشهادة الحقيقية — لا تُملأ بصورة مولّدة",
}

CAPTION_TAILS = [
    "اكتبي «سيروم» بالتعليقات وبنبعتلك التفاصيل 🌿",
    "التوصيل لكل المحافظات — والبيت مجاني على الحزم",
    "متوفر بـ٣٠ / ٥٠ / ١٠٠ مل",
    "خصم ١٠٪ على شام كاش 💚",
]

# One post per weekday. Sunday is the start of the Syrian working week.
WEEK_TEMPLATE = [
    # (format, pillar, needs_real_photo)
    # A reel whose claim IS a visible result needs real hair. A reel that
    # explains or answers can carry text-over-motion and is safe to auto-draft.
    ("reel", "result", True),
    ("carousel", "education", False),
    ("reel", "objection", False),
    ("story", "offer", False),
    ("reel", "behind", True),
    ("carousel", "proof", True),
]


def _pick(seq: list[str], index: int) -> str:
    return seq[index % len(seq)]


def _offer_line(brand: dict[str, Any]) -> str:
    bundle = next(b for b in brand["bundles"] if b["id"] == "duo-50")
    return (
        f"{bundle['label_ar']} بسعر ${bundle['price_usd']} "
        f"وتوصيل البيت مجاني (بدل ${bundle['price_usd'] + brand['delivery']['home_usd']})."
    )


def build_item(
    brand: dict[str, Any],
    *,
    day: date,
    format: str,
    pillar: str,
    needs_real_photo: bool,
    hook_index: int,
    tail_index: int,
) -> dict[str, Any]:
    hook = _pick(HOOKS[pillar], hook_index)
    tail = _pick(CAPTION_TAILS, tail_index)
    product = brand["products"][1]  # 50 ml is the volume driver
    pains = brand["audience"]["top_pains_ar"]
    objection = brand["audience"]["objections_ar"][hook_index % len(brand["audience"]["objections_ar"])]

    caption = f"{hook}\n\n"
    if pillar == "offer":
        caption += _offer_line(brand) + "\n\n"
    elif pillar == "education":
        caption += f"أكثر سبب شائع: {pains[hook_index % len(pains)]}.\n\n"
    elif pillar == "objection":
        # Never write the regulated word, not even to deny it: a claim scanner
        # reads the token, and a customer screenshots the sentence.
        caption += (f"سؤالك: {objection}\n"
                    "الجواب: رواء مستحضر تصفيف للشعر — يعطي مظهرًا أنعم ولمعة أخف "
                    "من أول استخدام، ولا يغيّر طبيعة الشعر.\n\n")
    elif pillar == "behind":
        caption += f"{brand['brand']['origin_story_ar']}.\n\n"
    elif pillar == "proof":
        caption += "الشهادة أدناه حقيقية ومن عميلة استلمت المنتج. لا نستخدم شهادات مصنوعة.\n\n"
    else:
        caption += f"{brand['brand']['positioning_ar']}.\n\n"
    caption += f"{tail}\n{product['best_for_ar']} — ${product['price_usd']}"

    return {
        "id": f"raawaa-{day.isoformat()}-{format}-{pillar}",
        "date": day.isoformat(),
        "weekday_ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"][day.weekday()],
        "channel": "instagram",
        "format": format,
        "pillar": pillar,
        "pillar_ar": PILLARS[pillar]["ar"],
        "goal_ar": PILLARS[pillar]["goal"],
        "hook_ar": hook,
        "caption_ar": caption,
        "cta_ar": "اكتبي «سيروم» بالتعليقات",
        "visual_brief_ar": VISUAL_BRIEF_AI[pillar],
        "beats_ar": REEL_BEATS if format == "reel" else [],
        "real_asset_required": needs_real_photo,
        "ai_visual_allowed": not needs_real_photo,
        "publish_mode": "manual_review" if needs_real_photo else "auto_publish_candidate",
        "llm_prompt": build_llm_prompt(brand, pillar=pillar, format=format, hook=hook),
    }


def build_llm_prompt(brand: dict[str, Any], *, pillar: str, format: str, hook: str) -> str:
    """The prompt a Workflow sends to a free model to improve the draft."""
    claims = "، ".join(brand["claims"]["allowed_ar"])
    forbidden = "، ".join(brand["claims"]["forbidden_ar"])
    return (
        f"أنت كاتب محتوى لعلامة عطور وشعر سورية اسمها «{brand['brand']['name_ar']}».\n"
        f"اللهجة: {DIALECT}. الجمهور: {brand['audience']['persona_ar']}.\n"
        f"المدينة الأساسية: {brand['audience']['primary_city_ar']}.\n"
        f"نوع المنشور: {format} — المحور: {PILLARS[pillar]['ar']} ({PILLARS[pillar]['goal']}).\n"
        f"الهوك المعطى (لا تغيّره): «{hook}»\n"
        f"ادعاءات مسموحة فقط: {claims}.\n"
        f"ادعاءات ممنوعة تمامًا: {forbidden}.\n"
        "اكتب: سكربت ٣٠ ثانية بأربع نقاط، وكابشن أقل من ٦٠٠ حرف، ودعوة واحدة.\n"
        "لا تستخدم كلمات: علاج، شفاء، مضمون ١٠٠٪. لا تذكر أنك ذكاء اصطناعي."
    )


def week_plan(brand: dict[str, Any] | None = None, *, week: int = 1, start: date | None = None) -> dict[str, Any]:
    brand = brand or load_brand()
    if week < 1:
        raise ValueError("week must be >= 1")
    if start is None:
        today = date.today()
        # Snap to the coming Saturday, the first day of the content week.
        start = today + timedelta(days=(5 - today.weekday()) % 7)
    offset = (week - 1) * len(WEEK_TEMPLATE)
    items = []
    for i, (fmt, pillar, real) in enumerate(WEEK_TEMPLATE):
        # The template is relative to `start`, which is always a Saturday.
        day = start + timedelta(days=i)
        items.append(build_item(
            brand, day=day, format=fmt, pillar=pillar, needs_real_photo=real,
            hook_index=offset + i, tail_index=offset + i,
        ))
    return {
        "mode": "raawaa_content_plan_read_only",
        "note": "Deterministic draft. No model calls, no publishing, no task writes.",
        "brand": brand["brand"]["name_ar"],
        "week": week,
        "starts": start.isoformat(),
        "items": items,
        "counts": {
            "total": len(items),
            "reels": sum(1 for i in items if i["format"] == "reel"),
            "need_real_photo": sum(1 for i in items if i["real_asset_required"]),
            "auto_candidates": sum(1 for i in items if not i["real_asset_required"]),
        },
    }


def render_markdown(plan: dict[str, Any]) -> str:
    lines = [
        f"# خطة محتوى رواء — الأسبوع {plan['week']}",
        "",
        f"**يبدأ:** {plan['starts']} · **عدد المنشورات:** {plan['counts']['total']} · "
        f"**ريلز:** {plan['counts']['reels']} · "
        f"**يحتاج صورة حقيقية:** {plan['counts']['need_real_photo']}",
        "",
        "> المنشورات التي تحتاج صورة حقيقية لا تُنشر تلقائيًا. هذا مقصود.",
        "",
    ]
    for item in plan["items"]:
        lines += [
            f"## {item['date']} ({item['weekday_ar']}) — {item['format']} / {item['pillar_ar']}",
            "",
            f"**الهدف:** {item['goal_ar']}",
            "",
            f"**الهوك:** {item['hook_ar']}",
            "",
            "**الكابشن:**",
            "",
            "```",
            item["caption_ar"],
            "```",
            "",
            f"**الصورة:** {item['visual_brief_ar']}",
            "",
            f"**أصل حقيقي مطلوب:** {'نعم' if item['real_asset_required'] else 'لا'} · "
            f"**وضع النشر:** `{item['publish_mode']}`",
            "",
        ]
        if item["beats_ar"]:
            lines.append("**تقسيم الريل:**")
            lines += [f"- {beat}" for beat in item["beats_ar"]]
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic Raawaa content week")
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--start", help="ISO date, defaults to the coming Saturday")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start) if args.start else None
    plan = week_plan(week=args.week, start=start)
    if args.format == "markdown":
        print(render_markdown(plan))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
