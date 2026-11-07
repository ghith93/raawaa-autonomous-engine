# Offline inventory of issue-comment tasks

When the billing preflight blocks issue-comment enqueue, the comment still exists
on GitHub; local validation does **not** establish that an engine task was
created or absent. `scripts/plan_deferred_issue_comments.py` accepts a locally
supplied JSON array of Issue #464 comment records, without contacting GitHub or
Cloudflare or writing any tasks:

```sh
python3 scripts/plan_deferred_issue_comments.py /path/to/comments.json
```

Supply a complete, authenticated GitHub API snapshot (all pages), only from the
intended issue. Do not commit the snapshot: it may contain private or sensitive
comment text. The tool never verifies snapshot completeness, GitHub provenance,
or current D1 state. It emits only comment IDs, fixed state/reason labels and,
for an eligible envelope, its deterministic `github-comment-<id>` key.

`candidate_enqueue_status_unknown` means only that a current comment whose timestamps show no edit
passes local envelope validation. Equal timestamps are not proof that the
current body matches the original event. It is **not** evidence that the original event
was deferred, that a task is missing, or that anything was enqueued. Edited
valid/invalid envelopes are flagged `edited_requires_review`; expired or invalid
ones cannot become automatic candidates. A replay must use verified original
event content and current policy and needs separate authenticated exact-key
reconciliation plus the financial safety gate. This tool intentionally offers no
replay, remote write or second queue. It cannot wake Arena Agent Mode.
