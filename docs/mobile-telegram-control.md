# Mobile Telegram control

## Scope

The Worker-native webhook provides a mobile-operated, cloud-executed read-only
command path. It does not execute shell commands, invoke arbitrary CLI operations,
start scans, alter strategy settings, access wallets, place orders, sign
transactions, swap assets, or submit transactions. The existing Python
long-polling controller is not replaced or started by this feature.

Commands in the source (deployment must be verified separately):

- `/start`: alias for help.
- `/help`: show the read-only controls.
- `/status`: counts existing task states in D1.
- `/report`: counts append-only paper research event types.

## Security contract

The route returns `503 webhook_disabled` until all three operator-owned settings
are structurally valid as Cloudflare Worker secrets/variables:

- `TELEGRAM_WEBHOOK_SECRET`: Telegram webhook secret (16–256 letters, digits,
  underscores or hyphens).
- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_ALLOWED_CHAT_IDS`: comma-separated numeric private-chat IDs.

The Worker validates the `X-Telegram-Bot-Api-Secret-Token` header, requires a
private chat in the allowlist, and requires `from.id == chat.id` with
`is_bot === false`. It caps webhook JSON at 8 KiB and limits each allowlisted
chat to ten accepted updates per rolling minute. Update IDs are unique in D1;
duplicate deliveries are acknowledged without a second reply. Delivery is
recorded as `sent`, `failed`, or `unknown`. A network timeout is ambiguous and
is never automatically replayed because Telegram may already have accepted the
message. Do not publish secret values or private chat IDs.

## Historical production evidence and recovery

Migration `0005_telegram_webhook_receipts.sql` was applied to production D1.
Production deployment [#41888263905](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41888263905)
succeeded on source SHA `148cb669d219c9cc4a346336d0c73f0cf4c848b8`. A later
registration run [#41891392653](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41891392653)
returned HTTP 200 for a signed synthetic ignored update. This verified that
probe's authentication/configuration path at that time, not the live Telegram
webhook destination, owner's allowlist membership, command menu or delivery of a
real reply. The deployed source and actual state must be rechecked before use.

The old registration workflow could set the Worker **origin**, not the required
`/v1/telegram/webhook` path, and its check accepted any non-empty URL. It could
also delete the webhook for discovery and drop pending updates. **Do not rerun
it.** Earlier documentation describing a pause/discovery flag as available on
main was ahead of the actual unmerged implementation.

See [the recovery guide](operations/owner-actions.md) for the replacement
workflow's read-only diagnostic mode and separately confirmed repair mode.
Review and merge it before dispatching it. The proposed repair preserves the
existing allowlist, refuses a known non-empty queue, never derives ownership from
an arbitrary sender, does not delete the webhook to discover a chat and does not
explicitly drop pending updates. Repair requires an operator attestation that all
existing allowed identities were independently verified; the workflow cannot
itself verify this. Do not repair an unverified list. Telegram updates can arrive
between checks and registration; unauthorized ones may still be delivered and
ignored. A synthetic ignored probe verifies only the handler contract, not owner
identity or delivery.

## Validation

Run `npm run test:worker`, the Python suite, and the full Engine CI. Tests stub
Telegram's HTTP API; they do not contact Telegram. No production credentials are
required. Verify a real owner `/status` reply separately before claiming
end-to-end operation.
