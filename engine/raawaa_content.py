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


def production_ready(brand: dict[str, Any]) -> bool:
    """True only when a manufactured batch actually exists."""
    return int(brand.get("production", {}).get("stock_units", 0)) > 0


def prelaunch_week(brand: dict[str, Any] | None = None, *, start: date | None = None) -> dict[str, Any]:
    """What to publish BEFORE anything is manufactured.

    Nothing here shows a product, because there is no product to show. This is
    the account's first impression and the only phase where the pharmacist's
    authority is worth more than the serum.
    """
    brand = brand or load_brand()
    if start is None:
        today = date.today()
        start = today + timedelta(days=(5 - today.weekday()) % 7)
    plan = [
        ("reel", "من أنا ولماذا أفتح حسابًا", "صيدلاني من حماة يشرح لماذا بدأ"),
        ("reel", "سيروم أم بلسم؟", "سؤال يُسأل كل يوم في الصيدلية"),
        ("carousel", "الزيوت لا تُنبت الشعر", "تصحيح مفهوم شائع — يبني سلطة مهنية"),
        ("reel", "٣ أخطاء في التسريح", "تعليم صرف بلا منتج"),
        ("story", "سؤال وجواب", "اسألني عن شعرك — بلا منتج"),
        ("carousel", "متى تراجع طبيبًا", "تساقط مفاجئ أو حكة = طبيب"),
    ]
    items = []
    for i, (fmt, hook, why) in enumerate(plan):
        day = start + timedelta(days=i)
        items.append({
            "id": f"raawaa-prelaunch-{day.isoformat()}-{i}",
            "date": day.isoformat(),
            "weekday_ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"][day.weekday()],
            "format": fmt,
            "pillar_ar": "تأسيس ثقة" if fmt != "story" else "تفاعل",
            "hook_ar": hook,
            "why_ar": why,
            "caption_ar": (
                f"{hook}\n\n"
                "أنا صيدلاني من حماة، وهشارك معلومات عن الشعر بصدق — "
                "بدون وعود، وبدون بيع شيء لسا ما جاهز.\n\n"
                "اسألني في التعليقات 🌿"
            ),
            "visual_brief_ar": "بطاقة نصية بخط عربي كبير على خلفية بلون العلامة — لا منتج، لا عبوة",
            "real_asset_required": False,
            "ai_visual_allowed": True,
            "publish_mode": "auto_publish_candidate",
            "shows_product": False,
        })
    return {
        "mode": "raawaa_prelaunch_plan_read_only",
        "note": "No product exists yet, so nothing here shows one.",
        "phase": "prelaunch",
        "week": 0,
        "starts": start.isoformat(),
        "items": items,
        "counts": {"total": len(items), "reels": sum(1 for i in items if i["format"] == "reel"),
                   "need_real_photo": 0, "auto_candidates": len(items)},
    }


def week_plan(brand: dict[str, Any] | None = None, *, week: int = 1, start: date | None = None) -> dict[str, Any]:
    brand = brand or load_brand()
    if week < 1:
        raise ValueError("week must be >= 1")
    if not production_ready(brand) and not brand.get("_test_fixture_stock"):
        raise ValueError(
            "no manufactured stock yet: use prelaunch_week() until the first batch arrives. "
            "Publishing product content for a product that does not exist is the one mistake "
            "this engine refuses to help with."
        )
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
        pillar_label = item.get("pillar_ar") or ""
        lines += [
            f"## {item['date']} ({item.get('weekday_ar', '')}) — {item['format']}"
            + (f" / {pillar_label}" if pillar_label else ""),
            "",
            f"**الهدف:** {item.get('goal_ar', item.get('why_ar', ''))}",
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
            f"**أصل حقيقي مطلوب:** {'نعم' if item.get('real_asset_required') else 'لا'} · "
            f"**وضع النشر:** `{item.get('publish_mode', 'manual_review')}`",
            "",
        ]
        if item.get("beats_ar"):
            lines.append("**تقسيم الريل:**")
            lines += [f"- {beat}" for beat in item["beats_ar"]]
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Asset kit: how to get every visual with no photographer, no model and no
# studio. The rule that unlocks all of it: a hand is not a face.
# ---------------------------------------------------------------------------

SHOT_KIT = [
    {
        "id": "drop-on-hand",
        "shot_ar": "قطرة على ظهر اليد ثم فركها ببطء",
        "time_sec": 20,
        "needs_ar": "هاتف + يدك + نافذة",
        "use_for_ar": "النتيجة والملمس — يبدو حقيقيًا لأنه حقيقي",
        "risk_ar": "لا شيء: بلا وجه، بلا هوية",
    },
    {
        "id": "bottle-open",
        "shot_ar": "لقطة قريبة لفتح الغطاء وخروج أول قطرة",
        "time_sec": 15,
        "needs_ar": "هاتف + العبوة",
        "use_for_ar": "افتتاحية كل ريل (٣ ثوان)",
        "risk_ar": "لا شيء",
    },
    {
        "id": "bottle-window",
        "shot_ar": "العبوة في اليد أمام ضوء النافذة، خلفية قماش أو حائط فاتح",
        "time_sec": 20,
        "needs_ar": "هاتف + كرسي + نافذة عند الظهر",
        "use_for_ar": "صورة رئيسية + بطاقة سعر",
        "risk_ar": "لا شيء",
    },
    {
        "id": "serum-texture",
        "shot_ar": "سيروم على زجاج أو مرآة، ماكرو، حركة بطيئة",
        "time_sec": 25,
        "needs_ar": "هاتف + زجاج + قطرتين",
        "use_for_ar": "خلفية تعليمية جميلة",
        "risk_ar": "استهلاك بسيط من عبوة واحدة فقط",
    },
    {
        "id": "hair-ends-only",
        "shot_ar": "أطراف الشعر فقط (من الخلف أو الجانب) قبل وبعد، بلا وجه",
        "time_sec": 40,
        "needs_ar": "شخص تعرفه + هاتف + ضوء نهار",
        "use_for_ar": "أقوى منشور ممكن: نتيجة حقيقية",
        "risk_ar": "يحتاج موافقة الشخص، ولا يُظهر وجهه",
    },
    {
        "id": "packing",
        "shot_ar": "تجهيز الطلب: علبة، مناديل، عبوة، إغلاق",
        "time_sec": 30,
        "needs_ar": "هاتف + طاولة + الطلب نفسه",
        "use_for_ar": "«صنع في سوريا» + يقلل الرفض",
        "risk_ar": "أخفِ أسماء وعناوين العملاء",
    },
    {
        "id": "pharmacist-explains",
        "shot_ar": "أنت تتحدث إلى الكاميرا ٢٥ ثانية عن المكوّن أو طريقة الاستخدام",
        "time_sec": 40,
        "needs_ar": "هاتف مثبّت + أنت",
        "use_for_ar": "أقوى إثبات ثقة في السوق السوري",
        "risk_ar": "لا ادعاء علاجي — شرح فقط",
    },
]

STOCK_SEARCH_TERMS = [
    "beige aesthetic background",
    "marble surface natural light",
    "silk fabric macro slow motion",
    "dropper macro water drop",
    "woman back view long hair soft light",
    "bathroom shelf minimal beige",
]

STOCK_RULES = {
    "allowed_ar": "مقاطع الجو والخلفيات فقط، وفوقها كلامك أو بطاقتك",
    "never_ar": (
        "لا تستخدمي مقطع شعر جاهز كأنه نتيجة منتجك، ولا مقطع شخص كأنه يشهد لك. "
        "هذا ممنوع في الترخيص نفسه (لا يجوز الإيحاء بتأييد شخص أو علامة)، "
        "وهو أسوأ شيء يمكن أن يحدث لحساب جديد."
    ),
    "license_check_ar": "Pexels وPixabay وMixkit: تجاري بلا إسناد — لكن Mixkit فيه مقاطع Restricted غير تجارية، فتحققي من وسم كل مقطع.",
}

BARTER_KIT = {
    "target_count": 10,
    "who_ar": ["صانعات محتوى صغيرات في حماة (١٠٠٠–٢٠٠٠٠ متابع)", "صالونات نسائية", "صيدليات ومحلات تجميل"],
    "give_ar": "عبوة ٥٠ مل مجانًا",
    "ask_ar": "فيديو عمودي ٢٠ ثانية، بلا فلتر، رأيها الصادق — ويحق لها أن تقول إنه لم يعجبها",
    "why_honest_allowed_ar": "الشهادة التي فيها احتمال «لم يعجبني» تُصدَّق. الشهادة المضمونة لا تُصدَّق.",
    "dm_template_ar": (
        "مرحبًا [الاسم]، أنا [اسمك]، صيدلاني من حماة. "
        "أطلقت سيروم شعر سوري اسمه رواء، وأبحث عن ١٠ أشخاص يجربونه بصدق. "
        "أرسل لك عبوة ٥٠ مل مجانًا، وإذا أعجبك تحكي عنه بـ٢٠ ثانية، وإذا ما أعجبك تحكي هذا كمان — "
        "مهم عندي الرأي الصادق أكثر من المدح. بتحبي أرسلها؟"
    ),
    "consent_ar": "قبل النشر اطلبي رسالة موافقة: «أوافق على استخدام الفيديو في حساب رواء».",
}

PHARMACIST_PILLARS = [
    ("مكوّن واحد في ٢٠ ثانية", "اشرحي وظيفة مكوّن تصفيف واحد فقط، بلا مبالغة"),
    ("سيروم أم بلسم؟", "الفرق في الوظيفة والتوقيت — هذا سؤال يُسأل في الصيدلية كل يوم"),
    ("أسطورة الزيوت", "الزيوت لا تُنبت الشعر؛ تُغلف وتمنح لمعة. قول هذا يبني ثقة هائلة"),
    ("طريقة الاستخدام الصحيحة", "قطرتان على الأطراف، لا على الفروة، ولا على شعر مبلل تمامًا"),
    ("متى تراجع طبيبًا", "تساقط مفاجئ أو حكة أو قشور = طبيب. هذه الجملة تبني سلطة مهنية"),
]


def asset_kit(brand: dict[str, Any] | None = None) -> dict[str, Any]:
    brand = brand or load_brand()
    return {
        "mode": "raawaa_asset_kit_read_only",
        "note": "No photographer, no model, no studio. Hands only.",
        "shots": SHOT_KIT,
        "total_minutes": 3 + sum(s["time_sec"] for s in SHOT_KIT) // 60,
        "stock": {"terms": STOCK_SEARCH_TERMS, "rules": STOCK_RULES},
        "barter": BARTER_KIT,
        "pharmacist_pillars": [
            {"hook_ar": h, "why_ar": w} for h, w in PHARMACIST_PILLARS
        ],
        "disclosure": brand["ai_disclosure"],
    }


def render_asset_markdown(kit: dict[str, Any]) -> str:
    lines = [
        "# أصول رواء بدون مصوّر ولا موديل",
        "",
        "> القاعدة التي تفتح كل شيء: **اليد ليست وجهًا.** لا تحتاج أحدًا يصوّرك، "
        "ولا تحتاج أن يظهر وجهك.",
        "",
        "## ١. لقطات تصوّرها بنفسك (هاتف + نافذة) — أهم مورد عندك",
        "",
        "| اللقطة | الطريقة | الوقت | تُستخدم لـ |",
        "|---|---|---|---|",
    ]
    for shot in kit["shots"]:
        lines.append(f"| **{shot['shot_ar']}** | {shot['needs_ar']} | {shot['time_sec']}ث | {shot['use_for_ar']} |")
    lines += [
        "",
        "## ٢. مقاطع مخزون مجانية — للجو فقط",
        "",
        "**مسموح:** " + kit["stock"]["rules"]["allowed_ar"],
        "",
        "**ممنوع تمامًا:** " + kit["stock"]["rules"]["never_ar"],
        "",
        "**تحقق من الترخيص:** " + kit["stock"]["rules"]["license_check_ar"],
        "",
        "**كلمات البحث (بالإنجليزية تعطي نتائج أفضل):**",
    ]
    lines += [f"- `{term}`" for term in kit["stock"]["terms"]]
    lines += [
        "",
        "## ٣. المقايضة: ١٠ أشخاص حقيقيون",
        "",
        f"**العدد:** {kit['barter']['target_count']} · **المقابل:** {kit['barter']['give_ar']} · "
        f"**المطلوب:** {kit['barter']['ask_ar']}",
        "",
        f"**لماذا نسمح لها بأن تقول «لم يعجبني»:** {kit['barter']['why_honest_allowed_ar']}",
        "",
        "**رسالة أولى جاهزة:**",
        "",
        "```",
        kit["barter"]["dm_template_ar"],
        "```",
        "",
        f"**الموافقة:** {kit['barter']['consent_ar']}",
        "",
        "## ٤. محتوى لا يستطيع أحد تقليده: أنت صيدلاني",
        "",
    ]
    for pillar in kit["pharmacist_pillars"]:
        lines += [f"- **{pillar['hook_ar']}** — {pillar['why_ar']}"]
    lines += [
        "",
        "## ٥. وسم الذكاء الاصطناعي (قواعد ميتا ٢٠٢٦)",
        "",
        f"{kit['disclosure']['rule_ar']}",
        "",
        "**يحتاج وسمًا:** " + "، ".join(kit["disclosure"]["requires_label"]),
        "",
        "**لا يحتاج:** " + "، ".join(kit["disclosure"]["no_label_needed"]),
        "",
        "**محظور دائمًا:** " + "، ".join(kit["disclosure"]["hard_bans"]),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic Raawaa content week")
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--start", help="ISO date, defaults to the coming Saturday")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--prelaunch", action="store_true",
                        help="Print the pre-manufacture content week (no product shown)")
    parser.add_argument("--assets", action="store_true",
                        help="Print the no-photographer asset kit instead of a content week")
    args = parser.parse_args(argv)

    if args.prelaunch:
        plan = prelaunch_week(start=date.fromisoformat(args.start) if args.start else None)
        print(render_markdown(plan) if args.format == "markdown"
              else json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    if args.assets:
        kit = asset_kit()
        print(render_asset_markdown(kit) if args.format == "markdown"
              else json.dumps(kit, ensure_ascii=False, indent=2))
        return 0

    start = date.fromisoformat(args.start) if args.start else None
    plan = week_plan(week=args.week, start=start)
    if args.format == "markdown":
        print(render_markdown(plan))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
