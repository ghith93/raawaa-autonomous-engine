# Mobile Telegram control (deployed; owner verification pending)

## Scope

The Worker-native webhook provides a mobile-operated, cloud-executed read-only command path. It deliberately does not execute shell commands, invoke arbitrary CLI operations, start scans, alter strategy settings, access wallets, place orders, sign transactions, swap assets, or submit transactions.

Supported commands:
- `/help`
- `/status`: counts existing task states in D1.
- `/report`: counts append-only paper research event types.

The existing Python long-polling controller is not replaced or started by this feature.

## Security contract

The Worker requires three operator-owned settings:
- `TELEGRAM_WEBHOOK_SECRET`: Telegram webhook secret token (16–256 characters; only letters, digits, underscore and hyphen).
- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_ALLOWED_CHAT_IDS`: comma-separated positive private-chat IDs.

The Worker validates the `X-Telegram-Bot-Api-Secret-Token` header, requires a private chat in the allowlist, and additionally requires `from.id == chat.id` with `is_bot === false`. It caps webhook JSON at 8 KiB and limits each allowlisted chat to ten accepted updates per rolling minute. Update IDs are unique in D1; duplicate deliveries are acknowledged without sending a second reply. Delivery is recorded as `sent`, `failed`, or `unknown`. A network timeout is ambiguous and is never automatically replayed because Telegram may already have accepted the message.

## Current production state (2026-11-04)

- Migration `0005_telegram_webhook_receipts.sql` is applied to production D1.
- Control Worker production version: `b38e46fc-2526-458b-81a8-879713ec1e52`.
- The reviewed registration workflow installed the Telegram Worker secrets and registered the webhook.
- The signed synthetic probe returned HTTP 200 with an ignored update. Because the probe deliberately uses a non-owner chat and omits a real sender, it confirms that the webhook secret/configuration path was accepted at that time; it does **not** prove that the configured allowlist contains the owner's actual chat ID or that a real owner command receives a reply.
- No secret values or chat IDs are recorded in this document. Do not paste bot tokens, webhook secrets, or allowlist values into chat or commits.

## Safe owner discovery and recovery

The registration script and workflow now fail more safely:

1. Supplying a verified positive `chat_id` bypasses `getUpdates`; it does not pause or delete an existing webhook.
2. Default discovery refuses to delete an existing webhook automatically.
3. If the owner ID is unknown, the owner can explicitly set `replace_webhook_for_discovery=true`. This pauses the webhook while preserving pending updates. If no update is queued yet, send `/start` to the correct bot and rerun the workflow with the same option.
4. Discovery accepts only one unique private human sender where `from.id == chat.id` and `is_bot === false`; it refuses multiple candidate owners.
5. Re-registration preserves pending updates (`drop_pending_updates=false)).
6. The production registration workflow is restricted to reviewed `main` and the repository owner account.

A successful webhook registration is not the same as verified owner access. Do not declare Telegram operational until a real `/status` command from the owner receives a reply.

## Validation

Run `npm run test:worker`, `python -m unittest tests.test_telegram_bootstrap`, and the repository's full Engine CI. Tests stub Telegram's HTTP API; they do not contact Telegram. The explicit pause/discovery workflow is a production operation and must only be run by the owner when needed.
