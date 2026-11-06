# تكامل Arena Agent Mode وبروتوكول الاستمرارية الدائمة

- **تاريخ التحديث والتحقق:** 2026-11-06
- **المصدر الرسمي:** https://arena.ai/agent
- **القناة الدائمة المعتمدة للتنسيق والتسليم:** [GitHub Issue #464](https://github.com/ghith93/raawaa-autonomous-engine/issues/464)
- **ملف الحالة والـWatermark المرجعي في المستودع:** `config/session_continuity.json` + `scripts/session_continuity.py`
- **مستوى النضج الفعلي:** `active_manual_wake_operator` (مفعل تشغيليًا عبر جلسات يفتحها المالك مرتبطة بـGitHub، مع عدم وجود واجهة إيقاظ تلقائي رسمية)

## 1) الدور التشغيلي المعتمد والحدود التقنية الصريحة

بموجب سياسة التشغيل المعتمدة من المالك في مطلع وصف [Issue #464](https://github.com/ghith93/raawaa-autonomous-engine/issues/464) (تحديث 2026-11-05):

1. **Arena Agent Mode هو المنفّذ والمشغّل المساءل** داخل نطاق المستودع ضمن تفويض التنفيذ الكامل (فحص الكود، التعديل، الفروع، الاختبارات، PRs، التحقق من CI، الدمج الآمن، واستخدام الحصص المجانية المؤكدة)، بينما يعمل **ChatGPT كمستشار ومراجع تقني موازٍ** وليس بوابة موافقة تمنع التنفيذ الاعتيادي.
2. **القيد المالي الوحيد الصارم هو صفر إنفاق (`USD 0 spend`):** يُمنع أي شراء أو اشتراك أو رصيد مدفوع أو استدعاء API قد يرتّب رسومًا أو ترقية فوترة. يُسمح باستهلاك الحصص المجانية الحقيقية فقط عندما يستحيل تقنيًا أن تنشئ فاتورة ومع احترام تصنيف البيانات والخصوصية.
3. **الحد التقني الصريح لإيقاظ الجلسات (Manual-Wake Boundary):**
   - **لا توجد واجهة برمجية عامة أو Webhook رسمي مدعوم لإيقاظ جلسة Arena Agent Mode تلقائيًا** عند وصول تعليق جديد في GitHub Issue `#464` أو عند انتهاء GitHub Actions workflow.
   - يعمل `Issue #464` بوصفه **صندوق وارد دائمًا وسجل قرارات وتسليمات قابلًا للتدقيق (Durable Asynchronous Inbox)**، وليس ناقل تنبيه لحظي يوقظ Arena من تلقاء نفسه.
   - **محظور قطعًا** استخدام أي واجهات داخلية غير موثقة، أو الهندسة العكسية لنقاط نهاية خاصة، أو أتمتة واجهة المتصفح (UI scraping / browser automation) لمحاكاة إيقاظ الجلسة.
   - تبدأ كل جلسة جديدة أو مستأنفة برسالة قصيرة من المالك في Arena (مثل `.` أو طلب المهمة)، وتتكفل ملفات البداية الدائمة في المستودع باستعادة السياق الكامل تلقائيًا من GitHub دون أن ينسخ المالك أي تقارير يدويًا.

## 2) لماذا لا نعتمد على ذاكرة المحادثة أو SQLite المحلي للاستمرارية بين الجلسات؟

فحص الأصول القائمة في المشروع يثبت وجود آليتين مختلفتين يجب عدم الخلط بينهما:

- **`engine/memory.py` (`docs/memory-v1.md`):** مخزن ذاكرة مهيكل مبني على SQLite محلي (`RAAWAA_ENGINE_DB`) لعمليات المحرك. هذا المخزن **محلي ومؤقت** داخل بيئة التشغيل؛ لا ينتقل تلقائيًا بين صناديق Arena المعزولة (ephemeral sandboxes) ولا بين GitHub Actions runners، ولذلك لا يصلح ليكون مرجع الاستمرارية بين جلسات Arena.
- **الاستمرارية القابلة لإعادة البناء والتحقق (GitHub-Backed Continuity):** بدلاً من بناء قاعدة بيانات ذاكرة موازية بلا حاجة، يعتمد المشروع على ثلاث ركائز موجودة ومتكاملة:
  1. **نقطة الدخول الإلزامية (`AGENTS.md`)** المحمية بفحص البصمة `engine/operating_policy.py`.
  2. **بيان الاستمرارية والـWatermark (`config/session_continuity.json` + `scripts/session_continuity.py`)** الذي يتحقق آليًا من سلامة ملفات السياسة ويحسب قدرات المستودع الفعلية ويستخرج التعليقات غير المعالجة في `#464`.
  3. **سجل التسليمات الدائم في `GitHub Issue #464`** عبر تقارير تبدأ حرفيًا بـ`ARENA REPORT` وتحمل أعلى `Comment ID` تمت معالجته.

## 3) قائمة تحقق بداية الجلسة (Mandatory Session-Start Checklist)

في بداية **كل** جلسة Arena جديدة أو مستأنفة، وقبل كتابة أي خطة أو كود:

1. شغّل فحص الاستمرارية وقراءة الوارد الحي:
   ```bash
   python3 scripts/session_continuity.py status --fetch-live
   ```
2. افتح `GitHub Issue #464` مباشرة عبر `gh api`:
   - اقرأ `CURRENT OWNER-AUTHORIZED OPERATING POLICY` في أعلى وصف `#464`.
   - اقرأ **كامل النص** لكل تعليق جديد يحمل `id > effective_watermark` (حيث `effective_watermark` هو الأكبر بين `last_processed_comment_id` في `config/session_continuity.json` وآخر تعليق `ARENA REPORT` منشور في `#464`).
3. اقرأ الملفات المرجعية وسجل القدرات والتكاملات الفعلي (وليس من الذاكرة):
   - السياسات والمعمارية: `docs/master-autonomous-execution-directive.ar.md`، `docs/continuous-improvement-charter.md`، `docs/architecture.md`، `docs/operations.md`.
   - سجل القدرات والأدوات والتكاملات والـWorkflows: `config/policy.json`، `config/agents.json`، `config/provider_profiles.json`، `config/integrations.json`، `config/tool_permissions.json`، `docs/operations/asset-register.md`، `docs/operations/workflow-catalog.md`، وملفات `.github/workflows/*.yml`.
4. افحص الحالة الحية للمستودع:
   - `origin/main` SHA الفعلي، حالة الفرع النشط، جميع طلبات الدمج المفتوحة (`gh pr list`)، نتائج CI الدقيقة على SHA المطابق، وأي إخفاقات نشطة في Staging/CI.

## 4) خريطة القدرات الفعلية مقابل غير المتاح (قابلة للتحقق من المستودع)

| القدرة / السطح | أين تُعرَّف في المستودع | طريقة التحقق الآلي | الحالة والحدود الفعلية |
|---|---|---|---|
| سياسة التشغيل وبصمات الوثائق الخمس | `AGENTS.md`, `engine/operating_policy.py` | `python3 -m unittest tests.test_operating_policy` | **مفعّلة ومحمية (Fail-closed)** قبل كل دورة عامل Python |
| التدقيق الشامل للوكلاء والميزانية والصلاحيات | `config/policy.json`, `config/agents.json`, `config/tool_permissions.json` | `python3 -m engine.cli audit` | **مفعّل (`safe_dry_run`, سقف `$0.00`)**؛ 8 أدوار وكلاء تصريحية |
| دفتر المهام وعزل المسارات (Consumer Lanes) | `worker/index.js`, `engine/task_store.py`, `engine/remote_task_store.py` | `npm run test:worker` + `python3 -m unittest` | **مفعّل في SQLite وStaging D1**؛ Issue `#577` مفتوح لاستكمال الاستهداف الدقيق بالـID والتعافي |
| حواجز أمان GitHub Actions وتثبيت SHA | `scripts/check_workflow_action_pins.py`, `scripts/check_workflow_dispatch_guards.py` | تشغيل السكربتين مباشرةً في CI | **مفعّلة** لـ40-char SHA ولحراسة `workflow_dispatch` |
| البحث الاستباقي اليومي (Metadata Discovery) | `engine/research_discovery.py`, `.github/workflows/research-discovery.yml` | اختبارات `tests/test_research_discovery.py` | **مجدول للقراءة العامة** (GitHub/Crossref/arXiv)؛ ليس محرك FTS5 مكتمل بعد (`#573`) |
| واجهة متجر رواء النظيفة ولوحة المالك | `storefront/`, `commerce-worker/`, `scripts/publish_clean_storefront.py` | `https://raawaa.pages.dev/` و`/owner/` | **منشورة على Cloudflare Pages** بمسار معزول عن دفتر مهام المحرك |
| إيقاظ جلسة Arena تلقائيًا من GitHub | `config/integrations.json` (`arena_agent_mode`) | `python3 scripts/session_continuity.py status` | **غير مدعوم رسميًا**؛ يُحظر استخدام واجهات خاصة أو أتمتة متصفح |
| Google ARTEMIS / NVIDIA Nemotron / Codespaces Executor | `config/integrations.json`, `worker/index.js` | `python3 -m engine.cli audit` | **معطّلة افتراضيًا (Fail-closed)** لحين استيفاء بوابات الأمان والتكلفة الصفرية |

## 5) قائمة تحقق نهاية الجلسة وبروتوكول `ARENA REPORT`

في نهاية كل جلسة عمل:

1. إذا تضمنت الجلسة تعديلات برمجية أو توثيقية في المستودع:
   - حدّث `config/session_continuity.json` عبر:
     ```bash
     python3 scripts/session_continuity.py record --comment-id <HIGHEST_COMMENT_ID> --comment-created-at <ISO8601_UTC> --main-sha <MAIN_SHA> --summary "<ملخص موجز ومثبت>"
     ```
   - شغّل الاختبارات المحلية وحواجز الـWorkflows وتأكد من نجاح CI (`test`) على SHA رأس طلب الدمج قبل أي دمج.
2. انشر تقرير حالة في `GitHub Issue #464` يبدأ حرفيًا بالسطر:
   ```text
   ARENA REPORT
   ```
   ويشمل وجوبًا:
   - `Processed comment ID` (التعليق المعالج وأعلى `Comment ID` تمت قراءته في `#464`).
   - `Current main SHA / branch SHA` الفعليين وقت التقرير.
   - ما تم التحقق منه وتنفيذه فعليًا مفصولًا عما لم يثبت أو بقي معلقًا.
   - روابط الـPRs والـCommits وأرقام تشغيل CI / Workflows.
   - سجل الاستهلاك والتكلفة المثبتة (`USD 0`).
   - المخاطر المتبقية، المهمة المحدودة التالية، والمسؤول التالي (`Next actor`) مع التذكير الصريح بحد الاستئناف اليدوي لجلسة Arena عند الحاجة.
