# Task envelope protocol (ChatGPT ↔ Arena channel)

A task is a contract. This protocol makes the contract *checkable* so that a
relayed instruction cannot silently drift between the sender (ChatGPT, relayed
by the owner) and the executor (Arena), and so that "done" means a command that
exits `0` rather than a sentence claiming success.

The canonical thread is [#464](https://github.com/ghith93/raawaa-autonomous-engine/issues/464).

## What this is, and is not

| It is | It is not |
|---|---|
| A validator that rejects malformed or unfalsifiable tasks before work starts | Authentication — a fingerprint proves *text sameness*, not authorship |
| A procedure with legal state transitions, checked against a thread snapshot | An enforced state machine — nothing runs unless a human or agent runs it |
| A way to make every delivery carry a reproducible command and result | A wake mechanism — it cannot start a ChatGPT or Arena session |

Actor provenance stays **UNKNOWN**: every comment in the thread is authored by
the same GitHub account, so the tooling never infers which agent (or which
credential) produced a comment.

## Envelope

Post this block unfenced, or inside a ` ```task ` fence:

```
TASK v1
id: ARENA-COLLAB-LOW-LATENCY-001
from: chatgpt
to: arena
scope: research-only
base: e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e
forbidden: repo-write, pr-create, webhook-registration, permission-change, deployment
cost_cap_usd: 0
expires: 2026-11-18
acceptance: Post one report in #464 containing `python3 scripts/task_envelope.py ledger` output
evidence: issue-comment-6621054977
```

| Field | Rule | Why |
|---|---|---|
| `id` | `<ARENA\|CHATGPT>-<DOMAIN>-<NNN>`; never reused, even after failure | Duplicate detection and a stable handle |
| `from` / `to` | `chatgpt`, `arena`, `owner`; must differ | No self-assigned work |
| `scope` | `research-only`, `report-only`, `repo-change` | Ambiguity is rejected, not interpreted |
| `base` | 7–40 char hex commit SHA | Pins the contract to a tree |
| `forbidden` | Non-empty; `none` is rejected | A negative list is readable by a machine; intent is not |
| `cost_cap_usd` | Number ≥ 0, always explicit | No implicit spend |
| `expires` | `YYYY-MM-DD`, not in the past | Stale tasks cannot be executed |
| `acceptance` | Must name a command, artefact, exit code, count or thread a third party can check | This is the verification |
| `evidence` | `issue-comment-<id>`, `pr-<n>`, URL, or repository path; `;`-separated | A claim without an artefact does not count |

**Fences matter.** Unfenced text and ` ```task ` fences are live. Any other fence
is an example and is ignored by the parser. This exists because the first run
against the live thread parsed this very document's samples as real tasks.

## State markers

One comment per transition, starting with the marker:

```
[TASK ARENA-COLLAB-LOW-LATENCY-001] CLAIMED hash=66bce00b
```

```
POSTED ──claim──> CLAIMED ──start──> IN_PROGRESS ──> DELIVERED ──verify──> VERIFIED
   │                 │                    │              │
   └──expire──> EXPIRED                  └──abort──> REJECTED
```

* `CLAIMED` carries `hash=<8 hex>` — the receiver's rendering of the contract.
  A mismatch means the two sides are not working from the same task.
* `DELIVERED` must carry a backticked command **and** its result
  (`exit 0`, `562 tests`, …). Prose deliveries fail validation.
* `VERIFIED` is issued by the sender, never the executor.
* Terminal states: `VERIFIED`, `REJECTED`, `EXPIRED`.

## Usage

```bash
# Validate an envelope before posting it
python3 scripts/task_envelope.py lint task.txt --today 2026-11-04

# Fingerprint, to paste into CLAIMED
python3 scripts/task_envelope.py hash task.txt

# Validate the whole thread (read-only; needs no credentials beyond gh)
gh api "repos/ghith93/raawaa-autonomous-engine/issues/464/comments?per_page=100" \
  --jq '[.[] | {id, created_at, body}]' > /tmp/thread464.json
python3 scripts/task_envelope.py ledger /tmp/thread464.json

# Session preflight: watermark + what is waiting on whom
python3 scripts/task_envelope.py preflight /tmp/thread464.json
```

The channel protocol requires each agent to read every comment newer than the
last one it acknowledged. That rule is unenforceable while the watermark lives in
a model's memory, so `preflight` prints it as a number:

```
watermark: 6621136000  (comments in thread: 16)
  ARENA-COLLAB-LOW-LATENCY-001           DELIVERED    next=chatgpt
ACTION REQUIRED (arena): none
```

Every Arena reply to the channel quotes the watermark it processed. A later
session that sees a higher number than its own reply knows it is behind, without
trusting any recollection of the conversation.

Exit codes: `0` clean, `1` violations. `--json` emits machine-readable output for
CI or for the sender's verification step.

## Ingest handler: a comment can enqueue, but never think

`issue_comment_task` is completed by an explicit branch in `engine/worker.py`
that re-validates the envelope, stores the result and returns — **no provider
call, no contract, no agent**. The type is deliberately absent from
`SAFE_TASKS`, so admitting comments never widens the allowlist for
inference-capable work; a test pins both facts.

Why re-validate a second time: the queue row is durable and could be edited
between admission and claim, and an envelope that was valid yesterday is not
valid today if it has expired.

### What is re-checked at claim time, and why

The queue row is durable, so "it was true when enqueued" is not the same claim as
"it is true now". Before a task is completed the handler re-checks:

| Check | Rejected reason | Why it exists |
|---|---|---|
| `source == github_issue_comment` | `ingest_provenance_rejected` | a row from elsewhere is not this pipeline's |
| issue number matches the tracked issue | `ingest_provenance_rejected` | scope creep by editing one integer |
| author is on the allowlist | `ingest_provenance_rejected` | identity is the only gate the edge has |
| `comment_id` matches `idempotency_key` | `ingest_provenance_rejected` | replay under a different key |
| recomputed fingerprint equals the recorded one | `envelope_fingerprint_mismatch` | the envelope may have been edited after admission |
| `cost_cap_usd` is exactly `0` | `ingest_cost_cap_exceeded` | **a valid schema is not proof of authority** |

The last one is the sharpest: the general validator only rejects negative caps, so
an envelope may legally declare a budget. The ingest path refuses any budget at
all, because this edge exists to record work, not to fund it.

### Writing an envelope without wasting a run

A hand-written envelope is rejected for reasons that are invisible while you
write it: an acceptance criterion that is prose, evidence that names no openable
artefact, a date that has passed. The rejection only surfaces after a workflow
run. Generate it instead:

```
python scripts/owner_task.py \
    --to chatgpt --scope research-only \
    --acceptance "The \`task-runner\` report shows 1 task processed and 0 pending" \
    --evidence "pr-470; engine/worker.py" \
    --post
```

The generator validates against the same `validate()` the edge uses, refuses to
post when anything fails, and prints a hint naming what the two most common
rejections actually want. Pass `--id` to pin an identifier; otherwise the next
free `ARENA-OP-NNN` is chosen from the handoff issue.

### Reading a result without downloading logs

Job logs are not reachable from this project's tooling, so the bounded batch
states its own outcome: `engine/run_report.py` emits one `::notice` per task
(`id`, `status`, `reason`, `mode`) plus a summary line, and appends a table to
the step summary. Both are readable through the checks API and the run page.
A blocked task and a succeeded task are therefore distinguishable from outside,
which the queue counters alone could never show.

### What `succeeded` means

`succeeded` means **validated and recorded — nothing more**. The stored result
carries `inference: "none"`, `execution_authorized: false` and
`meaning: "validated_and_recorded_only"`. Turning a recorded task into execution
is a separate, explicit act; nothing in this path can do it, and a test asserts
that no contract or agent is ever constructed here.

Outcomes: `succeeded`, or `blocked` with one of the reasons above. A blocked row
is evidence that the gate held, not a failure of the pipeline.

## Deliberate design choices

* **No committed ledger file.** State lives in the GitHub thread; a JSON mirror
  in the repository would drift from it and become a second, wrong truth. The
  tool reads a snapshot and validates in memory.
* **No schema file.** Field rules live in one place (`scripts/task_envelope.py`)
  so a schema and a validator cannot disagree.
* **No credentials, no network, no spend.** The tool cannot deploy, dispatch,
  call a provider or write to D1. It reads text.
* **No new workflow trigger.** It runs inside the existing test suite, so it
  adds no permission scope and no new scheduler.

## Known limits

1. It validates *form*, not truth: a well-formed task can still ask for
   something unwise. The owner's approval remains the real authority.
2. It does not prevent a task from being executed twice; it only reports that a
   state transition was illegal or a claim's fingerprint did not match.
3. It cannot wake either agent. Latency between posts is bounded by whichever
   side next reads the thread — see `docs/operations/chatgpt-automation-inventory.md`
   for the automation state that governs this.
