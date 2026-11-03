# Durable task-step model invocation identity

## Contract

A logical model call is identified by `(task_id, step_key)`, not by provider or retry attempt. D1 persists a random `invocation_id` and `request_id` before any provider request. A retry of the same prepared step reads the same IDs. A distinct step in the same task gets distinct IDs.

The live gateway derives route-specific monetary reservation and idempotency UUIDs from the persisted request ID plus the selected route ID. This keeps route retries distinct while preserving the route-independent logical identity.

## Lifecycle

`prepared -> in_flight -> settled`

`prepared -> in_flight -> ambiguous`

`prepared -> in_flight -> prepared` is permitted only after an explicit route rejection (HTTP 429 or a recognized model-permission denial), recorded as a rejected route attempt. The same route cannot be attempted twice for one invocation.

Any timeout, transport error, malformed response, unknown adapter error, lost response after provider start, or process restart while in-flight leaves the invocation blocked as `ambiguous` or `in_flight`. It is not automatically replayed. Reconciliation must be explicit.

## Lease fencing

Every lifecycle request includes the current task lease token. D1 checks the lease and task state for each transition. Start, reject, and terminal transitions use D1 batches so the invocation and route-attempt records change together. A stale worker cannot advance an invocation after losing its lease.

## Security and operating limits

- The endpoint is authenticated by the existing Engine API bearer token and requires the task's active lease.
- Step keys and task kinds are bounded strings; prompt content is not stored in the invocation table.
- The API returns only invocation identifiers, step metadata, and state.
- The feature does not enable provider calls, paid fallbacks, parallel agent inference, or production migrations.
- The current runner remains single-inference-per-task unless the operator deliberately wires an invocation store and explicit step keys into a reviewed workflow.
- Migration `0007_task_model_invocations.sql` is additive and must be reviewed and applied through the existing migration workflow; this PR does not apply it to remote D1.
