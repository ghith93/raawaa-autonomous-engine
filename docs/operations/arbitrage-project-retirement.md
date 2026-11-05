# Telegram Arbitrage Engine retirement — 2026-11-05

The owner cancelled the Telegram Arbitrage Engine subproject and explicitly
requested deletion of the project and its dedicated branch.

Removed: the entire `projects/telegram-arbitrage-engine` tree, five dedicated
market/venue/Solidity CI and scheduled workflows, the V3 smoke script, the
Python D1 paper-ledger compatibility facade that imported the deleted package,
its tests, and project-specific research documents. Workflow catalog entries
were removed too. No replacement trading project is planned.

The dedicated remote branch `feature/telegram-arbitrage-engine` is retired.
The active Arena session branch is not a project branch and is retained.

Unchanged: Raawaa core, its Telegram mobile control integration, existing
Worker paper-ledger routes and applied database migrations. No production
resource, shared secret, remote ledger or Git history is erased by this change.
Historical branch-audit entries remain as records, not active project references.
