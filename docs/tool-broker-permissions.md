# Central Tool Broker and Permission Matrix

## Status

The broker is a fail-closed authorization and dispatch boundary for trusted host
adapters. The current task worker does not register external tool adapters and
does not expose tools to model completions. The presence of a permission entry
does not itself activate a tool.

## Mandatory execution path

Every future agent tool call must pass through `engine.tool_broker.ToolBroker`.
Agent prompts, task payloads, skill manifests, and provider output are untrusted;
none may register adapters, modify the permission matrix, or supply trusted
approval evidence.

Trusted host code constructs the broker with:
- the validated repository policy;
- the trusted agent roster (the broker verifies enabled status, role, capability, and task assignment);
- the reviewed `config/tool_permissions.json` matrix;
- a static registry of trusted adapter functions;
- a durable audit sink;
- an approval verifier backed by an operator-controlled, one-time approval
  record (only when a write capability is intentionally enabled).

Each grant binds one agent role to its explicitly assigned task types. The CLI
policy audit checks these pairs against the actual agent roster and task
assignment contract. A tool call is denied when the tool or adapter is unknown,
any role/task/action/environment dimension mismatches, the budget or policy
gate fails, or the audit sink is unavailable. Read access is scoped to local
and Staging environments. The initial matrix grants only repository metadata/file inspection. It deliberately excludes `tests.run` because running tests executes repository-controlled code, and excludes `research.public_search` because it requires outbound network access. These require distinct execution-sandbox and network-egress controls; they must not be represented as read-only tools. No production tool is granted in the initial matrix.

## Write operations

Writes remain disabled by `config/policy.json`. If an operator later enables
them, a write still requires:
1. the action to appear in `require_human_approval_for`;
2. an approval ID verified and atomically consumed by trusted host code;
3. evidence matching task, agent, role, tool, action, and environment exactly;
4. a stable idempotency key;
5. a registered adapter whose implementation independently validates inputs
   and limits its own side effects.

A boolean in a task payload or model response is never approval evidence.
Production writes are not granted by this initial permission matrix.

## Audit and ambiguous outcomes

Both allowed and denied decisions are durably recorded before any adapter is
called. Completion or failure is recorded afterward. If completion auditing fails after an adapter
returns, the broker raises `ToolBrokerAuditError`: the action may have happened,
so callers must not replay it automatically. Write adapters must implement
idempotency and reconciliation.

## External code isolation

Skill manifests remain metadata and pinned instructions only. The broker does
not import or execute skill code, install dependencies, or fetch code at runtime.
External skill instructions are data, not executable plugins. Only reviewed,
repository-owned adapters can be registered by trusted application code.

## Rollback

Revert the commits in the Tool Broker PR. The matrix and broker are additive;
no database schema, D1 state, secret, deployment, or production resource changes
are required. Do not remove any existing data to roll back.
