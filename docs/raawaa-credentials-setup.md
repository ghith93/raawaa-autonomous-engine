# المفتاحان — خطوة بخطوة مع الروابط المباشرة

**الهدف:** مفتاحان فقط. لا ثالث. الوقت: ٤ دقائق.

بعد كل خطوة يوجد أمر تحقق. لا تنتقل للتالية قبل نجاحها.

---

## الخطوة ١ — Account ID (٣٠ ثانية)

**الرابط:** https://dash.cloudflare.com/

1. افتح الرابط وسجّل الدخول.
2. من القائمة اليسرى اختر **Workers & Pages**.
3. انظر إلى الشريط الجانبي الأيمن — ستجد **Account ID** أسفل قسم *Account details*.
4. اضغط **Copy**.

**شكله:** ٣٢ حرفًا سداسيًا، مثل `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6`.

> ⚠️ ليس `Zone ID`، وليس رقم الحساب المالي. هو **Account ID** فقط.

**تحقق:**

```bash
export CLOUDFLARE_ACCOUNT_ID="الصق_هنا"
python scripts/raawaa_verify_cloudflare.py
```

المتوقع: `[PASS] account id — format valid`

---

## الخطوة ٢ — API Token (دقيقتان)

**الرابط المباشر:** https://dash.cloudflare.com/profile/api-tokens

1. اضغط **Create Token**.
2. انزل للأسفل واختر **Create Custom Token** (لا تستخدم القوالب الجاهزة — صلاحياتها أوسع من اللازم).
3. **Token name:** `raawaa-image-and-deploy`
4. **Permissions** — أضف أربعة أسطر بالضبط:

| النوع | المورد | الصلاحية | لماذا |
|---|---|---|---|
| Account | Workers AI | **Read** | توليد الصور |
| Account | D1 | **Edit** | قاعدة البيانات |
| Account | Workers Scripts | **Edit** | النشر |
| Account | Workers KV Storage | **Edit** | تخزين الإعدادات |

5. **Account Resources:** Include → حسابك.
6. **Zone Resources:** لا تضف شيئًا (غير مطلوب).
7. **TTL:** اتركه فارغًا (بلا انتهاء) — حتى لا يتوقف المشروع فجأة بعد سنة.
8. اضغط **Continue to summary** ← **Create Token**.
9. **انسخ المفتاح الآن** — سيُعرض مرة واحدة فقط، ولن تستطيع رؤيته بعدها.

**شكله:** ٤٠ حرفًا تقريبًا، يبدأ عادة بـ`v1.0-` أو نص طويل عشوائي.

> ⚠️ **لا تستخدم** Global API Key. إن نسيت المفتاح، احذف التوكن وأنشئ غيره — لا تحاول استرجاعه.

**تحقق:**

```bash
export CLOUDFLARE_API_TOKEN="الصق_هنا"
python scripts/raawaa_verify_cloudflare.py
```

المتوقع: خمسة أسطر `PASS`:

```
[PASS] account id — format valid
[PASS] token active — status=active
[PASS] account access — account visible: اسم_حسابك
[PASS] D1 access — database found: raawaa-autonomous-e
[PASS] Workers AI — one 256px image generated (14.4 neurons)
```

---

## الخطوة ٣ — إن ظهر خطأ

| الرسالة | المعنى | الحل |
|---|---|---|
| `status=disabled` | التوكن معطّل | أنشئ واحدًا جديدًا |
| `account access` فشل | الحساب غير مضاف للتوكن | عدّل التوكن ← Account Resources ← Include حسابك |
| `D1 access` فشل | صلاحية D1 ناقصة | أضف `Account > D1 > Edit` |
| `raawaa-autonomous-e not among them` | القاعدة غير موجودة في هذا الحساب | أنشئها من لوحة D1 بنفس الاسم |
| `Workers AI` فشل | صلاحية Workers AI ناقصة | أضف `Account > Workers AI > Read` |
| `network unreachable` | الشبكة مقطوعة من هنا | أعد المحاولة، أو نفّذ الأمر من جهازك |

---

## الخطوة ٤ — احفظهما (مرة واحدة)

**محليًا — للتشغيل من جهازك:**

```bash
# في مجلد المشروع، ملف .env  (مستثنى من git تلقائيًا)
cat > .env <<'EOF'
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
EOF
chmod 600 .env
```

**للنشر — Cloudflare نفسها:**

```bash
npx wrangler login          # مرة واحدة
npx wrangler secret put ENGINE_API_TOKEN   # يُطلب منك لصق القيمة
```

> `ENGINE_API_TOKEN` يمكن أن يكون أي نص عشوائي طويل تصنعه أنت:
> `openssl rand -hex 32`

---

## الخطوة ٥ — عند النجاح

```bash
python scripts/raawaa_images.py --status     # يجب أن يظهر cloudflare: متاح
python scripts/raawaa_images.py --prompt "beige linen backdrop" --out raawaa/out/test.png
```

إن نجح الأمر، تكون قد فتحت **١٧٣ صورة مجانية يوميًا** — وهذا كل ما يحتاجه المشروع.

---

## ما لا تفعله

| ❌ | لماذا |
|---|---|
| Global API Key | صلاحيات كاملة على كل الحساب — خطر أمني |
| قالب "Edit Cloudflare Workers" الجاهز | صلاحيات زائدة عن الحاجة |
| لصق المفتاح في محادثة أو مستودع | يُقرأ ويُستغل |
| إضافة Zone permissions | غير مطلوبة لهذا المشروع |
| TTL قصير | سيتوقف المشروع بعد انتهائه بصمت |

---

## الترتيب الكامل

```
١. Account ID        → PASS
٢. API Token         → PASS ×٥
٣. images --status   → cloudflare متاح
٤. wrangler secret   → ENGINE_API_TOKEN
٥. نشر                → استقبال الطلبات
```
