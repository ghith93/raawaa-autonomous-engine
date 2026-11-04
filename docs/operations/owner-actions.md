# Operator action and Telegram recovery guide

This is a recovery playbook, not proof of the live system's current state. Recheck
GitHub, CI and production before acting. Routine engineering, secrets management,
merges and deployment are delegated under the project's safety gates; only an
action that may incur spending needs separate owner approval. Do not paste tokens,
webhook secrets, chat IDs, or recovery codes into chat, issues or workflow logs.

## Telegram: diagnose before changing production (#228)

The production Worker has a read-only Telegram route at
`/v1/telegram/webhook`. It accepts only private messages from an allowlisted
human. The previous registration workflow could point Telegram at the Worker
origin instead of this path, discard pending updates, delete the webhook during
chat discovery and infer ownership from an unauthenticated sender. **Do not run
that old workflow.** The signed ignored-update probe in historical run
[#41891392653](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41891392653)
does not prove that Telegram was pointed at the route, that the owner is in the
allowlist, or that a real reply was delivered.

The replacement `telegram-register.yml` must first be reviewed, tested and merged
to `main`; only then can it be dispatched. Default **diagnose** mode makes no
Telegram or Worker changes: it checks the configured bot identity with `getMe`,
compares `getWebhookInfo.url` with the exact webhook path, reports a sanitized
pending-update count and last-error class, probes the Worker's unsigned
configuration gate, checks only whether the Worker allowlist secret exists, and
reads aggregate D1 receipt counts when available. The unsigned probe posts an
ineligible empty update without a secret; the expected response is HTTP 401.
It does not read the allowlist value, discover an owner, or send a message:

```sh
gh workflow run telegram-register.yml --ref main \
  -f confirm=REGISTER_TELEGRAM_WEBHOOK -f mode=diagnose
```

Review the resulting run and check-run annotations before deciding whether a
repair is appropriate. The existence of `TELEGRAM_ALLOWED_CHAT_IDS` does not
prove it is valid, contains the owner's ID, or belongs to the correct bot.
`getWebhookInfo` does not reveal the configured webhook secret. A nonmatching
URL is a confirmed configuration defect only when reported by this live check.

After exact-head tests, independent review, and an authorized Worker deployment
(including the `/start` alias), **repair** mode is a separate explicit action.
Before dispatch, independently verify the owner identity through a trusted channel
and configure the positive safe-integer ID as the GitHub Actions repository secret
`OWNER_TELEGRAM_CHAT_ID`. The workflow requires this secret for every mutating
mode and replaces the Worker allowlist with that single ID. An optional
`VERIFIED_EXISTING_ALLOWLIST` attestation is unverified and is only informative
in read-only diagnose mode; it cannot authorize repair, rotation, or recovery.

```sh
gh workflow run telegram-register.yml --ref main \
  -f confirm=REGISTER_TELEGRAM_WEBHOOK -f mode=repair
```

Repair checks bot identity and unsigned readiness, snapshots and verifies the
read-only command menu, prepares a secret in a private runner file and installs
it as the Worker staging secret. A signed, deliberately ineligible synthetic
update must receive the exact ignored response from the deployed Worker **before**
Telegram is switched. A second signed probe follows the switch before promotion
to primary and staging deletion. The summary reports staging cleanup as retired,
already absent, failed, or not attempted; investigate any residual staging key.
The probes cannot prove owner authorization or end-to-end delivery. Secret-write
output is suppressed and the prepared file is removed in an always-run step.
If command setup or readback fails, rollback of the menu is attempted and its
verification reported.

Repair refuses a **known non-empty pending queue**, both before mutations and
again immediately before `setWebhook`. `drop_pending_updates` is false; this is
not a backup or a guarantee of delivery. New updates can arrive between the last
check and registration, and the Worker may acknowledge and ignore unauthorized
messages. If staging succeeds but a later step fails, staging may remain active; do not claim rollback. Investigate the failure and queue,
then, when safe, rerun reviewed repair to install and register a fresh matching
secret. Cloudflare does not disclose the previous Worker secret value for
restoration.

A separate human message and observed delivery are needed to verify the complete
path. If the bot remains silent, inspect fresh sanitized diagnostics and receipt
counts. Never infer the owner from the first unsolicited message or replace an
allowlist using an unverified ID. An owner identity must be confirmed through an
independent trusted channel before a new private-chat ID is installed.

A previously exposed bot token must be revoked with BotFather `/revoke`. The
replacement belongs in the GitHub Actions secret `TELEGRAM_BOT_TOKEN`; never send
it through chat. The user reported changing the key, but that alone does not
prove the exposed token was revoked or that all configurations now use the new
one. A live `getMe` check verifies the configured token's bot identity, not
whether an old token was revoked.

## Other active safety boundaries

- Sandbox issue #440: Codespace execution remains release-disabled. A validator
  test or CI run is not a live sandbox escape proof. Do not run the adversarial
  live workflow until its exact changes and provenance have been independently
  reviewed.
- Model providers: remain disabled unless account eligibility, terms, hard spend
  limits and the existing inference gates are verified. No live inference, paid
  fallback or usage with uncertain cost is authorized by this playbook.
- Financial/market execution remains disabled. No wallet signing, orders, live
  market calls, swaps or other financial actions are implied by Telegram setup.
