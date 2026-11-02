# Mobile Telegram control (staged, not activated)

## Scope

The Worker-native webhook adds a mobile-operated, cloud-executed read-only command path. It deliberately does not execute shell commands, invoke arbitrary CLI operations, start scans, alter strategy settings, access wallets, place orders, sign transactions, swap assets, or submit transactions.

Supported commands:
- `/help`
- `/status`: counts existing task states in D1.
- `/report`: counts append-only paper research event types.

The existing Python long-polling controller is not replaced or started by this feature.

## Disabled-by-default configuration

The route returns `503 webhook_disabled` until all three operator-owned settings are configured as Cloudflare Worker secrets/variables:
- `TELEGRAM_WEBHOOK_SECRET`: Telegram webhook secret token (16–256 characters; only letters, digits, underscore and hyphen).
- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_ALLOWED_CHAT_IDS`: comma-separated numeric private-chat IDs.

The Worker validates the `X-Telegram-Bot-Api-Secret-Token` header, requires a private chat in the allowlist, caps webhook JSON at 8 KiB, and limits each allowlisted chat to ten accepted updates per rolling minute. Update IDs are unique in D1; duplicate deliveries are acknowledged without sending a second reply. Delivery is recorded as `sent`, `failed`, or `unknown`. A network timeout is ambiguous and is never automatically replayed because Telegram may already have accepted the message.

## Migration and activation

Migration `0005_telegram_webhook_receipts.sql` is additive but has **not** been applied to production D1. The feature is not deployed by this change. Do not apply the migration, add production secrets, call Telegram `setWebhook`, or deploy the Worker until the operator has reviewed the PR and explicitly authorized those external/production actions.

## Validation

Run `npm run test:worker` and the repository's full Engine CI. Tests stub Telegram's HTTP API; they do not contact Telegram. No production credentials are required.
