# المفتاحان — بلا بحث في القوائم

**أسرع طريق: المفتاح أولًا، ثم الحساب يعرّف نفسه.**

الترتيب المعتاد (Account ID ثم Token) يجعلك تبحث في قائمة جانبية قد تكون مطويّة أو اسمها مختلف. لذلك نعكسه: تصنع التوكن، فيخبرك هو بحسابك.

---

## المسار السريع — ٣ خطوات

### ١. الرابط المباشر للتوكن

**https://dash.cloudflare.com/profile/api-tokens**

> هذا رابط **مستوى الحساب**، ويعمل من أي حساب مسجّل. لا تحتاج الوصول إلى Workers & Pages أولًا.

اضغط **Create Token** ← انزل للأسفل ← **Create Custom Token**.

### ٢. الصلاحيات الأربع

| النوع | المورد | الصلاحية |
|---|---|---|
| Account | Workers AI | Read |
| Account | D1 | Edit |
| Account | Workers Scripts | Edit |
| Account | Workers KV Storage | Edit |

- **Token name:** `raawaa`
- **Account Resources:** Include ← حسابك
- **TTL:** فارغ
- **Continue to summary** ← **Create Token** ← **انسخ**

### ٣. لا تبحث عن Account ID — استخرجه

```bash
export CLOUDFLARE_API_TOKEN="الصق_هنا"
python scripts/raawaa_verify_cloudflare.py --from-token
```

إن كان للتوكن حساب واحد، سيُستخرج الـAccount ID تلقائيًا ويُتحقق من كل شيء.

**إن قال «token sees more than one account»:**

```bash
# افتح أي صفحة في لوحة التحكم، وانسخ الرابط من شريط العنوان
python scripts/raawaa_verify_cloudflare.py --from-url "https://dash.cloudflare.com/xxxxxxxx/..."
```

أي رابط لوحة تحكم يحتوي معرّف الحساب في أول جزء من المسار. لا حاجة للبحث عنه في قائمة.

---

## إن لم يكن هناك Workers & Pages

هذا طبيعي في حساب جديد. **الـWorkers مفعّل تلقائيًا** على الحساب المجاني، ولا يحتاج تفعيلًا يدويًا.

تأكيد سريع — بعد التوكن:

```bash
python scripts/raawaa_verify_cloudflare.py --from-token
```

- إن ظهر `Workers AI — PASS` ← كل شيء جاهز، اذهب للنشر.
- إن ظهر `Workers AI — 403` ← التوكن ينقصه صلاحية Workers AI، لا الحساب.
- إن ظهر `network unreachable` ← الشبكة مقطوعة من طرفك، لا مشكلة في الحساب.

**مهم:** «لا أرى Workers & Pages» ليس عائقًا. الصفحة تظهر فقط بعد أول نشر. بعد الأمر الأول ستصبح موجودة وتظهر فيها الأقسام.

---

## إن كنت لا تعرف أي حساب تستخدم

الرابط: **https://dash.cloudflare.com/**

بعد تسجيل الدخول مباشرة، يفتح على قائمة الحسابات. اضغط على اسم الحساب (وليس على موقع/دومين)، وستنتقل إلى:
`https://dash.cloudflare.com/<account-id>/...`

انسخ ذلك الرابط — فيه المعرّف.

---

## الأخطاء الثلاثة الشائعة وسببها الحقيقي

| ما تراه | السبب الحقيقي | الحل |
|---|---|---|
| لا يوجد Workers & Pages | لم تنشر شيئًا بعد | ليس خطأً. أكمل — سيظهر بعد النشر |
| لا يوجد Account ID | القائمة الجانبية مطويّة أو في الأسفل | استخدم `--from-url` على أي رابط |
| لا يوجد Custom Token | أنت في صفحة القوالب | انزل لأسفل الصفحة، الرابط في نهايتها |

---

## بعد النجاح

```bash
python scripts/raawaa_images.py --status          # cloudflare: متاح
python scripts/raawaa_images.py --prompt "beige linen" --out raawaa/out/test.png
```

هذا يفتح **١٧٣ صورة مجانية يوميًا**.

---

## ما لا تفعله

| ❌ | لماذا |
|---|---|
| Global API Key | صلاحيات كاملة على حسابك — خطر أمني |
| قالب جاهز من الصفحة | صلاحيات أوسع من اللازمة |
| لصق المفتاح في محادثة أو مستودع | يُقرأ ويُستغل |
| TTL قصير | يتوقف المشروع بصمت |
