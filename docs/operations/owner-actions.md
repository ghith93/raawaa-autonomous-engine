# What the owner has to do — 2026-11-04

Everything that cannot be done without you, in the order that unblocks the most
work. Each item is deliberately small; the column on the right is what happens
automatically afterwards.

If you do nothing, the project keeps moving: the loop continues on code, tests,
docs and review, and simply never reaches the surfaces that need a human account.

## 1. Telegram bot — 2 minutes, unblocks #228 and the phone surface

Everything else in #228 is already written (`worker/telegram-webhook.js`, 15
Worker tests across `telegram_webhook.test.js` and `telegram-mobile-contract.test.js`,
idempotent receipts in D1, private-chat allowlist, `/help` `/status` `/report`).
The surface is simply not registered, because a bot token can only come from you.

1. Open Telegram → **@BotFather** → `/newbot` → give it a name and a username
   ending in `bot`. Copy the token it prints (`123456789:AA...`).
2. Open your new bot and send it any message — `/start` is enough. This is how the
   automation learns your chat id without you looking it up.
3. GitHub → this repository → **Settings → Secrets and variables → Actions →
   New repository secret** → name `TELEGRAM_BOT_TOKEN`, value = the token.

Then tell me it is done (one word). I will run **Register Telegram webhook**
(`.github/workflows/telegram-register.yml`), which:

- discovers your private chat id from the pending update,
- generates the webhook secret (32 random bytes, never printed),
- installs all three values as Worker secrets,
- points Telegram at `https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev/v1/telegram/webhook`,
- posts a signed update to prove the Worker accepts and gates it,
- and I close #228 with the evidence.

**Do not paste the token into this chat.** Nothing here needs to see it.

## 2. One decision on where task code executes — 1 minute, unblocks #440

#440 is blocked because the Codespaces bridge is not a security boundary: task
code runs as the same Unix user as the bridge. Three ways out, pick a letter:

- **A — sandbox in the Codespace** (`docker run --network none --read-only`,
  dropped capabilities, no credentials mounted). Real boundary, reuses what
  exists, needs Docker inside the Codespace.
- **B — GitHub Actions job as the boundary.** Each task is one ephemeral job.
  Cheapest to build, strongest credential isolation, slow (~40 s/task) and the
  job can still reach the network.
- **C — no repository-controlled code executes at all.** Only deterministic
  dry-run handlers run; anything needing real code stays a proposal. Zero risk,
  and the engine stops at "plan" rather than "execute".

My recommendation is **A**, with **C** as the fallback for anything that is not
clearly ours. Reply with one letter.

## 3. Free provider keys — 5 minutes, optional, unblocks live inference in #346

Only needed when you want the engine to actually call a model. All free tiers,
$0, no card:

- Groq → https://console.groq.com/keys → create key → add as repository secret
  `GROQ_API_KEY`.
- Gemini → https://aistudio.google.com/apikey → create key → add as
  `GEMINI_API_KEY`.

After that I verify the model catalog with the existing read-only checker
(`scripts/groq_connection_check.py`), enable exactly one route in
`config/provider_profiles.json`, and run a bounded smoke — never more than the
free quota, and the daily/monthly caps in the ledger still apply.

If you skip this, #346 stays "verified but disabled", which is a fine place to
be: nothing else depends on it.

## 4. One question for #357 — 30 seconds

Is the Cloudflare account on **Workers Free** or **Workers Paid**? Free means
Workers AI gives 10,000 Neurons/day and hard-fails after that — which is exactly
the behaviour the engine wants. Paid means overage is billed, and the engine must
enforce the cap itself.

Look at Cloudflare dashboard → Workers & Pages → Plans. One word back.

## 5. #405 — 5 seconds

The parked Workers AI Neuron ledger PR (#405, labelled `blocked`). Keep it parked
until there is a real workload, or close it? Reply **keep** or **close**. Doing
nothing keeps it parked, which is also fine.

## 6. Spend — no action required

The delegation says explicit approval is required only for actions that can incur
charges. Current state: **$0 spend**, every provider route `enabled: false`, no
paid plan, no inference. If that ever needs to change, you will get one sentence
stating the amount and what it buys. Until then I default to zero.

---

## What I never need from you

- Passwords, API tokens, OAuth codes or 2FA codes — never send these in chat, and
  I will never ask. Everything above is a value you paste into a settings field,
  not into a message.
- Permission changes, GitHub Apps, or ruleset edits.
- Approval for merges, commits, branches, PRs, migrations or deployments: that is
  delegated, and the engineering gates (tests, exact-head CI, migrations applied
  before code that needs them) are what stand in for approval.

## What is already moving without you

- #175: comparator increment merged; next is wiring adapter output into it, then
  triangular enumeration, then the evaluation harness.
- The development loop: ranking, freshness, stale-branch hygiene, and the
  queued-check-is-not-green fix.
- Documentation that disagrees with the code is being corrected as it is found —
  two such documents were corrected today.
