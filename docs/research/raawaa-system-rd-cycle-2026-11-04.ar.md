# دورة البحث والتطوير الشاملة — 2026-11-04

**الحالة:** تقرير بحث وتحليل موثق؛ لا يتضمن تفعيل استدلال أو مزود أو أداة تنفيذ جديدة.
**المرجع الحي:** main = `5c5605ddcc3e9cec623a66474aca8348e714bd04`
**الهدف:** مراجعة المحرك ومشاريعه الفرعية والأدوات والتكاملات والنماذج والذاكرة والأمان والاستدامة، ثم إيقاف تكرار البحث العام عندما تتناقص عوائده.

## 1. خلاصة القرار

1. احتفظ ببنية التحكم الحالية: Python + GitHub Actions + Cloudflare Worker/D1. لا تضف LangGraph أو CrewAI أو Agno أو Pydantic AI أو Temporal أو قاعدة متجهات كاعتماد إنتاجي جديد الآن.
2. لا تجعل تعدد الوكلاء هو الوضع الافتراضي. استخدم منسقاً حتمياً، ومنفذاً واحداً، ومراجعاً مستقلاً عند الحاجة؛ أضف عاملاً ثانياً فقط عندما تكون المهمة قابلة للتقسيم وتوجد فائدة قابلة للقياس.
3. أفضل فرصة جديدة للنماذج هي تقييم GLM-4.7-Flash عبر Cloudflare Workers AI، لا عبر Z.ai API المباشر. المسار المستضاف لدى Cloudflare يختلف تعاقدياً عن واجهة Z.ai؛ صفحة النموذج تعرض function calling وreasoning وzero data retention، وسياسة Cloudflare تنص على عدم الاحتفاظ بمحتوى الاستدلال القياسي وعدم استخدامه للتدريب. ترخيص النموذج المعروض في بطاقة Hugging Face هو MIT. هذا لا يغني عن مراجعة شروط Cloudflare وترخيص النموذج والتحقق من حساب المالك.
4. أبقِ NVIDIA Nemotron 3.5 Lightning عبر NVIDIA API مرشحاً معطلاً؛ لا توجد حصة رقمية مؤكدة ولا إثبات لحد إنفاق صفري. يوجد مسار منفصل يستحق الاختبار مستقبلاً: Nemotron 3 Super على Workers AI، مع تكلفة أعلى بكثير لكل رمز.
5. الذاكرة الحالية محلية في SQLite وبحثها نصي باستخدام LIKE؛ لا تتزامن مع عمال GitHub Actions المؤقتين. الحل الأول هو تخزين منظم دائم في D1 الحالي، وليس إدخال Qdrant أو Mem0 قبل قياس الحاجة.
6. Tool Broker وSkills Registry أساس أمان جيد، لكن لا توجد adapters مسجلة في عامل التشغيل ولا مهارات تنفيذية معتمدة. لا تفتح التنفيذ قبل إثبات عزل Sandbox.
7. يوجد انحراف توثيقي مهم: اعتماديات المهام أصبحت مطبقة في SQLite وD1 بعد PR #457، لكن docs/architecture.md ما زال يقول العكس. كما أن قواعد main لا تفرض موافقة مراجِع، وتحتوي على bypass لدور المستودع.
8. قسم R&D الآلي الحالي يكتشف metadata ويرتبها؛ لا يشغّل محللاً نموذجياً مستقلاً ولا ينشئ مهام تطوير تلقائياً. لا نصفه بأنه محرك بحث ذاتي مكتمل.

## 2. لقطة المستودع والحالة التشغيلية

- main SHA: `5c5605ddcc3e9cec623a66474aca8348e714bd04`.
- CI المطابق لهذا SHA: [run 41899750086](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41899750086) ناجح؛ شمل الاختبارات، ترحيلات Staging، نشر Worker المعزول إلى Staging، telemetry، وقراءة mobile status الموثقة. هذا ليس نشر Production.
- PR #457 مدمج بتاريخ 2026-11-04. أضاف أولوية 0–100، وروابط اعتماديات، ومنع claim قبل نجاح الاعتماديات في نمط التنفيذ نفسه، وحجب التابعين عند فشل/حجب الأصل. توثيق التنفيذ في [Executive Brain and Delegation Contract](../executive-brain-and-delegation.md).
- PRs المفتوحة ذات الصلة وقت الفحص: #405 (سجل استخدام Workers AI)، #517 (تقوية Sandbox)، و#520 (مسار Telegram الذي يعمل عليه Arena). لم أعدل أياً منها في هذه الدورة.
- قواعد main ruleset رقم 26464558 نشطة وتفرض PR ونجاح status check باسم test وتحديث الفرع وحل review threads. لكنها تضبط required approvals على صفر، ولا تفرض Code Owner review، وتحتوي bypass actor من نوع RepositoryRole بالمعرّف 5 وبوضع always؛ كما أن الحساب الحالي يستطيع التجاوز. لا تدّعِ أن هذه القواعد تفرض مراجعة مستقلة أو تمنع تجاوز دور المالك/الإدارة. لم أغير إعدادات الحماية.
- وثائق architecture ما زالت تصف أن queue لا يدعم dependency-aware claim/release، وهذا لم يعد صحيحاً. التغيير موثق في هذا PR.
- Task Runner وMaintenance وResearch Discovery تبقى وظائف GitHub Actions مجدولة ومحدودة، وليست عملية Python دائمة أو ضمان 24/7. Maintenance كل 6 ساعات قراءة وترتيب فقط؛ Research Discovery يومي يجمع مرشحين عامين ويصدر artifacts لمدة 30 يوماً، وحفظ المرشحين في D1 مشروط بمتغير persistence. لا يوجد استدلال نموذج داخل هذه الدورة الآلية.

## 3. خريطة القدرات الحالية والفجوات

| المجال | الموجود فعلياً | الحد الحالي | القرار |
|---|---|---|---|
| التحكم والمهام | SQLite محلي + D1 بعيد، leases وfencing وidempotency وpriority وdependencies | خطة DAG ما زالت preview؛ لا يوجد dispatcher متعدد الخطوات | احتفظ بالتحكم الحالي، ولا تضف طابوراً ثانياً |
| الوكلاء | roster للأدوار manager/researcher/engineer/reviewer/marketer/operator | agent_runtime حتمي ويعيد dry_run؛ لا يستدعي نموذجاً | الأدوار توصيفية حتى يثبت runtime |
| التنفيذ | Task Runner محدود على GitHub Actions | تشغيل دفعات مؤقتة، لا Python daemon دائم | تحسين الاستئناف والقياس بدلاً من خادم مدفوع |
| النماذج | Groq Free موثق بحصص حساب فعلية؛ NVIDIA candidate؛ provider catalog | جميع routes معطلة؛ لا inference | تجربة معزولة فقط بعد تفويض التفعيل |
| الأدوات | Tool Broker deny-by-default ومصفوفة صلاحيات | المصفوفة تحتوي أداتي قراءة للمستودع؛ لا adapters مسجلة في worker | لا تنفيذ أدوات حتى يكتمل Sandbox والربط |
| المهارات | Skills Registry يتحقق من manifest وrevision وhash والموافقة والإلغاء | مجلد skills لا يحتوي إلا README؛ لا تنفيذ لمحتوى المهارات | لا استيراد/تشغيل مهارات خارجية الآن |
| الذاكرة | MemoryStore محلي SQLite مع provenance/confidence/tags/TTL/archive | بحث LIKE فقط؛ لا D1 sync ولا embeddings ولا استخراج تلقائي | أولوية D1 memory قبل vector DB |
| البحث | GitHub/Crossref/arXiv وSearXNG اختياري؛ triage حتمي | ترتيب metadata لا يقيس جودة الأداة أو صلاحيتها | أضف تحقق claims/evidence، لا مزيداً من مصادر metadata فقط |
| التطوير المستمر | dev-cycle وbrain-cycle وmaintenance تقارير قراءة | لا يقرر أو ينفذ أو ينشئ المهام بنفسه | حافظ على فصل القراءة والتنفيذ |
| الأمان | سياسة deny-by-default، redaction، approvals، Sandbox validator | Codespaces executor غير مفعل؛ لم يثبت adversarial isolation | Sandbox gate قبل أي كود غير موثوق |
| المراجحة | CEX/DEX read-only scanners، math، Foundry/Anvil harness، paper ledger | لا تنفيذ تداول أو توقيع أو wallet؛ CEX CLI يدوي ولا يثبت الربحية | ابقَ simulation/paper فقط |

### ملاحظة عن الاعتماديات

F5 queue enforcement أصبح موجوداً بالفعل في SQLite وD1 بعد PR #457؛ لا تكرر تنفيذه. ما يزال workflow planner proposal-only، فلا يعني نجاح dependency-aware claims أن خطة DAG تُرسل تلقائياً أو أن المنفذ متعدد الخطوات مفعل.

## 4. الأدلة البحثية حول تعدد الوكلاء

دراسة Kim وآخرين في Nature Machine Intelligence (24 يوليو 2026)، بعنوان [Capable language models can outgrow the benefits of collaboration](https://www.nature.com/articles/s42256-026-01268-y)، قارنت 260 إعداداً عبر ستة benchmarks وخمسة أنماط تنسيق وثلاث عائلات نماذج. أبرز نتائجها:
- متوسط تحسن أنظمة MAS الإجمالي كان 0.0% مع تباين واسع؛ ليست هناك فائدة عامة من زيادة عدد الوكلاء.
- على SWE-bench Verified تراجعت كل البنى متعددة الوكلاء قليلاً عن single-agent في مجموعة الدراسة.
- ظهرت مكاسب كبيرة في Finance Agent حيث يمكن تقسيم التحليل إلى أجزاء مستقلة؛ وظهرت خسائر في مهام تسلسلية لا تتحمل كلفة التنسيق.
- الأنظمة الهجينة احتاجت 6.2 أضعاف عدد turns مقارنة بالوكيل المفرد في الدراسة.
- التحقق المركزي خفّض تضخيم الأخطاء مقارنة بالبنى المستقلة.

هذه نتائج الدراسة وليست قانوناً عاماً أو ضماناً لأداء محركنا. لكنها تدعم سياسة: **لا تستدعِ فريقاً متعدد الوكلاء إلا إذا كان تقسيم المهمة حقيقياً، والنتائج قابلة للتحقق، والميزانية محدودة مسبقاً.**

### شكل الفريق المقترح

- مهام بسيطة/متسلسلة: منفذ واحد، ثم تحقق آلي.
- بحث متعدد المصادر: باحث واحد، ومراجع واحد فقط عندما تكون الأدلة متعارضة أو عالية الأثر.
- تغييرات كود مؤثرة: منفذ + مراجع مستقل لا يكتب نفس التغيير.
- لا تستخدم peer debate أو 4–5 وكلاء افتراضياً؛ كلفة الرسائل والاتفاق وتضارب الحالة قد تتجاوز قيمة التنوع.

## 5. مقارنة الأطر والمنصات

| الخيار | ما يضيفه | الملاءمة الحالية | القرار |
|---|---|---|---|
| Cloudflare Workflows | خطوات durable، retries، waits، pause/resume | أعلى مرشح؛ يوجد prototype محلي منفصل، لكنه لا يتصل بـD1/الإنتاج | احتفظ بالـspike؛ لا نشر أو queue consumer ثانٍ |
| Microsoft Agent Framework | workflows/checkpoints | يوجد adapter وتجارب معزولة، لكن لا D1 durable store أو worker integration | استمر في اختبارات الاستعادة فقط |
| Pydantic AI | typed tools/MCP وStepPersistence؛ تكامل durable مع Temporal/DBOS/Prefect/Restate | جيد كطبقة agent صغيرة، لكن durability تحتاج engine إضافياً | لا تستبدل runtime الحالي |
| LangGraph | graph checkpoints، interrupts، pending writes وmemory stores | قوي، لكنه يكرر DAG/checkpoint orchestration الحالية | استعرِض مفاهيم الاختبار، لا dependency إنتاجية |
| Cloudflare Agents SDK | هويات Agent وDurable Objects وfibers | مناسب لوكيل Worker-native، لا يستضيف Python handlers الحالية مباشرة | مرشح مستقبلي فقط |
| Goose / OpenHands | coding agent وMCP وworkspace/sandbox | لا يحلان فجوة الاستمرارية أو D1؛ قد يضيفان بيئة تنفيذ أخرى | لا تثبيت حالياً |
| Temporal / DBOS / Restate | durability وإعادة تشغيل workflows | تتطلب خدمة/قاعدة/تشغيلاً إضافياً أو تغيير بنية التنفيذ | لا تضفها قبل قياس قصور Workflows الحالية |

Cloudflare Workflows متاحة على Free وPaid، لكن الحصة ليست غير محدودة: صفحة الأسعار الحالية تعرض 3,000 steps/day و1 GB-month state و100,000 requests/day على Free، كما بدأت فوترة الخطوات والتخزين في 2026-08-10. لذلك تبقى التجربة المحلية المنعزلة هي المسار الصحيح حتى قياس استهلاك كل مهمة ونجاح الاستعادة. لا تنشئ DO أو scheduler إضافياً؛ Worker Cron وD1 هما المصدر الحالي للحقيقة.

## 6. النماذج والمزودون: أفضل فرص التقييم

### المرشحون

| المسار | قدرات موثقة | كلفة Workers AI لكل مليون رمز | القرار |
|---|---|---:|---|
| Cloudflare GLM-4.7-Flash | 131K context، reasoning، function calling، zero data retention؛ MIT في بطاقة النموذج | 5,500 neurons input / 36,400 output | أفضل مرشح أول للتجربة منخفضة الكلفة |
| Cloudflare Gemma 4 26B A4B | 256K context، vision، reasoning، function calling | 9,091 input / 27,273 output | بديل قوي للمقارنة |
| Cloudflare Nemotron 3 Super 120B A12B | 256K context، reasoning، function calling، zero data retention؛ موجه للمهام agentic | 45,455 input / 136,364 output | مراجع مستقل انتقائي، لا تشغيل افتراضي |
| Groq GPT-OSS 20B | حساب Free موثق من المالك؛ 30 RPM، 8K TPM، 1,000 RPD، 200K TPD، و2M TPM شهرياً بحسب الملف | صفر ضمن الحصة المؤكدة | مرشح أساسي للبيانات العامة؛ route ما زال معطلاً |
| NVIDIA NIM Nemotron 3.5 Lightning | free prototype endpoint معلن، لكن لا quota رقمية موثقة للحساب | UNKNOWN | يبقى disabled |
| Z.ai API المباشر | توجد قيود تعاقدية غير محسومة حول decision-making | UNKNOWN | لا تستخدمه في التخطيط المستقل قبل توضيح مكتوب |

### لماذا يستحق Cloudflare GLM إعادة التقييم؟

- هذا endpoint هو `@cf/zai-org/glm-4.7-flash` المستضاف لدى Cloudflare، وليس `api.z.ai`. لا تنقل شروط Z.ai API الخاصة بالخدمة إلى Cloudflare تلقائياً؛ لكن افحص اتفاقية Cloudflare وترخيص النموذج قبل الإنتاج.
- صفحة Cloudflare تعرض tool/function calling وreasoning وzero data retention و131,072 context.
- Cloudflare تقول إن محتوى الاستدلال القياسي لا يحتفظ به بعد معالجة الطلب، ولا تستخدم Customer Content لتدريب النماذج. تخزين المحتوى اختيارياً في logs/cache/storage أو batch يغيّر ذلك.
- بطاقة Hugging Face الرسمية للنموذج تعرض MIT.
- Workers AI يمنح 10,000 Neurons يومياً على Free؛ تجاوز الحصة يفشل على Free، بينما خطة Paid تفرض 0.011 دولار لكل 1,000 Neurons فوق الحصة. بعض النماذج تتطلب billing method؛ GLM-4.7-Flash وGemma 4 وNemotron 3 Super ليست ضمن قائمة النماذج التي تذكر صفحة الأسعار أنها تتطلب paid billing method. أهلية حسابنا وخطته الفعلية غير مثبتة.

### تقدير استهلاك توضيحي

لطلب افتراضي حجمه 20,000 رمز إدخال و4,000 رمز إخراج، وباستخدام أسعار Neurons المنشورة:

- GLM-4.7-Flash: نحو 256 Neurons/طلب؛ قرابة 39 طلباً مماثلاً من حصة 10,000 يومياً.
- Gemma 4 26B A4B: نحو 291 Neurons/طلب؛ قرابة 34 طلباً.
- Nemotron 3 Super: نحو 1,455 Neurons/طلب؛ قرابة 6 طلبات.

هذه تقديرات حسابية وليست ضماناً؛ يجب اعتماد usage metadata الفعلية. لا تفعّل أي route قبل التأكد من Free plan، وتثبيت hard cap يومي أقل من 10,000، ورفض أي paid fallback، والتحقق من ledger. لا inference تم في هذه الدورة.

### سياسة توزيع مقترحة للتجربة المستقبلية

- GLM-4.7-Flash: مهام البحث/الفرز/صياغة الأدلة منخفضة المخاطر.
- Groq GPT-OSS 20B: مقارنة عامة على بيانات غير سرية فقط ضمن quota الحساب.
- Gemma 4: baseline بديل.
- Nemotron 3 Super: رأي مستقل محدود لمراجعة المعمارية أو مراجعة تغيير عالي الأثر، لا كوكيل دائم.
- لا تجعل النموذج يختار مزوده أو يغير الميزانية أو يفعّل نفسه. Router حتمي، والـledger هو صاحب قرار السماح.

## 7. الذاكرة: قرار دون قاعدة متجهات جديدة

الوضع الحالي:
- MemoryStore محلي SQLite.
- البحث يستخدم LIKE على المحتوى والتصنيف والوسوم.
- لا تزامن مع عمال GitHub Actions المؤقتين.
- لا extraction تلقائي، ولا embeddings، ولا vector DB.

التوصية:
1. أضف تخزيناً منظماً في D1 الحالي مع حقول category/content/source/confidence/tags/created_at/expires_at/archived_at.
2. أبقِ الذاكرة الشخصية الحساسة والبيانات السرية خارج هذه المنظومة.
3. اختبر البحث الحتمي الحالي مقابل استرجاع SQL/FTS المناسب لـD1 قبل إدخال embedding model.
4. أضف semantic retrieval فقط إذا فشل benchmark استرجاع حقيقي؛ لا تنشئ Qdrant أو Mem0 لمجرد توفرهما.
5. افصل ذاكرة المشروع/القرارات عن سجل المهام وعن research candidates؛ لا تخلط كل شيء في جدول واحد.

## 8. أدوات ChatGPT والتكاملات: الجرد الفعلي

جرد جلسة ChatGPT الحالية:
- 1,912 تعريف أداة موزعة على 62 namespace group.
- 259 skill موزعة على 37 حزمة.
- هذا جرد ما يستطيع ChatGPT استدعاءه في بيئة المحادثة؛ لا يعني أن Engine يستطيع استدعاء هذه الأدوات أو أن كل خدمة متصلة بحساب خارجي.

Activepieces:
- مشروع واحد مستخدم: Ghith's Project.
- اتصال واحد نشط: GitHub.
- Flow واحد منشور ومفعل: Raawaa GitHub Workflow Control.
- يتكون من Manual Trigger ثم GitHub workflow_dispatch لملف task-runner.yml على main، وينتظر اكتمال التشغيل حتى 30 دقيقة.
- سجل التشغيل أظهر تشغيلين TESTING ناجحين، ولا توجد production runs في الجرد الحالي.
- كتالوج Activepieces يعرض 20 نموذجاً، لكن السعر والحصة والفوترة وأهلية الحساب غير معلومة؛ لم يتم inference.
- هذا flow زر تحكم يدوي، وليس منسق وكلاء أو scheduler أو model runtime.

الأدوات البحثية التي استخدمت في هذه الدورة: GitHub API/connector لفحص المستودع، Exa للبحث التقني، Consensus للبحث الأكاديمي، وWeb للمصادر الرسمية. لا تنسخ أدوات ChatGPT إلى Engine؛ ابنِ adapters ضيقة ومراجعة فقط عند وجود حاجة، مع أقل صلاحية ممكنة.

## 9. مشروع المراجحة الفرعي

المشروع الحالي Simulation-first:
- CEX cross-venue scanner يقرأ order books من Bybit/Binance/Kraken/Coinbase/OKX ويحسب depth-swept VWAP.
- CLI يدوي، وليس strategy loop مجدولاً.
- لا يستنتج رسوم الحساب أو custody/inventory أو إعادة التوازن أو settlement.
- DEX/V2/V3 math وAnvil/Foundry harness موجودة للاختبار والمحاكاة.
- لا wallet أو signer أو approvals أو swaps أو exchange orders أو live trading.
- الدراسات السابقة عن MEV تؤكد أن gross spread ليس ربحاً صافياً؛ الغاز، inclusion، builder competition، price impact، inventory والـlatency عوامل أساسية.

القرار: لا تفعّل التداول. المرحلة ذات القيمة هي data quality، freshness/skew، fee evidence، quote-to-inclusion، وpaper/shadow results مع فصل estimates عن realized outcomes.

## 10. الأمان والحوكمة

- PR #517 وIssue #440 متعلقان بتقوية Sandbox. لا Codespaces executor ولا تشغيل مهارات أو كود خارجي قبل اجتياز validator/probes وadversarial tests.
- Telegram خارج نطاق عملي في هذه الدورة؛ Arena يتولى إصلاحه في PR #520.
- Tool Broker لا يثبت الأمان وحده؛ لا بد من adapters موثوقة مسجلة من كود host، وسجل تدقيق دائم، ومراجع موافقات موثوق.
- Skills Registry يثبت manifest وrevision وhash والموافقة والإلغاء، لكنه لا ينفذ المحتوى ولا يوفر sandbox.
- قواعد main الحالية تسمح بصفر موافقات وبـrepository-role bypass؛ سجل ذلك كخطر حوكمة، ولا تغيّر الصلاحيات دون تفويض مستقل.

## 11. ترتيب فرص التطوير

| الأولوية | الفرصة | سببها | الخطوة الآمنة التالية |
|---|---|---|---|
| P0 | تصحيح انحراف الوثائق | architecture يصف queue dependencies كأنها غير مطبقة؛ قواعد الحماية موثقة بصورة غير دقيقة | تصحيح docs في هذا PR فقط |
| P1 | إكمال Workers AI quota ledger | GLM/Gemma/Nemotron مرشحات قابلة للمقارنة ضمن 10k Neurons | مراجعة PR #405 وتوسيع model-specific rates بعد توثيق الحصة؛ دون inference |
| P1 | ذاكرة D1 دائمة | الذاكرة المحلية لا تعيش عبر runners المؤقتة | تصميم migration/API صغيرة تعيد استخدام D1 |
| P1 | Benchmark single-agent vs two-agent | الدليل التجريبي لا يبرر فرقاً متعددة على كل مهمة | مجموعة مهام ثابتة، compute budget متساوٍ، قياس النجاح/التكلفة/الزمن/الأخطاء |
| P1 | Tool Broker integration | البوابة موجودة لكن لا adapters | ابدأ بقراءة repository/research بعد إثبات Sandbox، لا كتابة |
| P2 | Verified research evidence | discovery الحالي metadata-first | سجل claims/sources/verifications في D1 أو artifact من نفس pipeline |
| P2 | تحسين paper arbitrage | لا يوجد دليل PnL قابل للتنفيذ | freshness + fees + latency + realized paper results، دون تداول حي |
| P3 | Vector DB أو إطار orchestration آخر | لا توجد فجوة مثبتة تبرر كلفته | لا تبنِ حتى يثبت benchmark الحاجة |

## 12. التشبع البحثي ونقطة الاستئناف

### أين وصلت العوائد إلى حد التناقص؟

- **أطر الوكلاء والتنسيق:** تشبع مرتفع. تمت مقارنة MAF وCloudflare Workflows/Agents وPydantic AI وLangGraph وGoose وOpenHands وTemporal/DBOS/Restate. لا توجد حجة حالية لاستبدال control plane.
- **تعدد الوكلاء:** تشبع مرتفع على مستوى المبدأ؛ الدليل الأكاديمي يرفض فرضية أن زيادة العدد تحسن كل مهمة. المطلوب benchmark داخلي لا مزيد من مقالات عامة.
- **الذاكرة:** تشبع متوسط؛ القرار واضح: D1 persistence أولاً، vector DB لاحقاً فقط عند إثبات الحاجة.
- **النماذج المجانية:** تشبع متوسط؛ ظهرت فرص Cloudflare جديدة، لكن الحسم الآن يتطلب التحقق من خطة الحساب والـquota والـledger، وليس بحثاً عاماً إضافياً.
- **الأدوات:** تشبع مرتفع؛ الكتالوج واسع جداً. الفجوة ليست نقص الأدوات، بل عدم ربط adapters الموثوقة بالمحرك.
- **المراجحة:** تشبع متوسط؛ توجد أبحاث ومكونات كثيرة لكن ينقصها قياس paper/shadow حي قابل للتحقق، لا المزيد من ادعاءات الربحية.

### متى نعيد البحث؟

أعد الدورة عند حدوث واحد من هذه المحفزات: دمج PR مؤثر، تغيير رسمي في الأسعار/الشروط/الحصص، إصدار نموذج أو framework يضيف قدرة غير موجودة، فشل اختبار أو حادث أمني، أو ظهور قياسات تشغيلية جديدة. لا تعاود بحث إطار سبق رفضه دون دليل جديد.

### حالة هذه الدورة

- بحث وتحليل: **منجز لهذه الجولة**.
- توثيق: **قيد هذا PR**.
- كود runtime أو inference أو deployment أو spend: **لم ينفذ**.
- نقاش Arena: **مؤجل حتى ينهي مسار Telegram**؛ هذا التقرير جاهز للمراجعة المشتركة لاحقاً.
- الاستئناف: ابدأ من ترتيب الفرص في القسم 11، ولا تعاود فتح مسارات التشبع في القسم 12 إلا بوجود محفز جديد.

## 13. المصادر الأساسية

- [Nature Machine Intelligence — multi-agent collaboration study (2026)](https://www.nature.com/articles/s42256-026-01268-y)
- [Cloudflare Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [Cloudflare Workers AI data usage](https://developers.cloudflare.com/workers-ai/platform/data-usage/)
- [Cloudflare GLM-4.7-Flash model](https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash/)
- [Cloudflare Nemotron 3 Super model](https://developers.cloudflare.com/workers-ai/models/nemotron-3-120b-a12b/)
- [Cloudflare Gemma 4 model](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/)
- [GLM-4.7-Flash model card and MIT license](https://huggingface.co/zai-org/GLM-4.7-Flash)
- [Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- [LangGraph persistence and checkpointing](https://docs.langchain.com/oss/python/langgraph/persistence)
- [PR #457 — dependency-aware queue](https://github.com/ghith93/raawaa-autonomous-engine/pull/457)
- [PR #405 — Workers AI ledger](https://github.com/ghith93/raawaa-autonomous-engine/pull/405)
- [PR #517 — Sandbox](https://github.com/ghith93/raawaa-autonomous-engine/pull/517)
- [PR #520 — Telegram (Arena)](https://github.com/ghith93/raawaa-autonomous-engine/pull/520)
