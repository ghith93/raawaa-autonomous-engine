# Table-rebuild migrations: risk, verification, recovery

SQLite — and therefore D1 — cannot alter a `CHECK` constraint in place. Widening
one requires the rebuild pattern: create the new table, copy every row, drop the
old table, rename the new one. `0011_research_source_expansion` is the first
migration in this repository to do it, so the risk and the recovery path are
written down here rather than discovered during an incident.

## What the pattern costs

The sequence is:

```sql
CREATE TABLE research_candidates_new (... widened CHECK ...);
INSERT INTO research_candidates_new (...) SELECT ... FROM research_candidates;
DROP TABLE research_candidates;
ALTER TABLE research_candidates_new RENAME TO research_candidates;
```

Two properties matter, and only the first is guaranteed:

1. **No data is destroyed before it is copied.** The `DROP` happens after the
   `INSERT ... SELECT`, so at every instant the rows exist in at least one table.
2. **The sequence is not atomic as a whole.** Wrangler applies the statements in
   order; an interruption between `DROP` and `RENAME` leaves the rows complete
   and correct inside `research_candidates_new`, but under the wrong name.

That second point is why the recovery procedure below exists. The failure is
recoverable and non-destructive; it is not invisible — the endpoint would start
failing with "no such table: research_candidates" and the deploy gate would
report the application as pending.

## Verification, in this order

1. **Local first.** `node --test tests/d1_local_worker_smoke.mjs` applies every
   migration to a real local D1, then exercises the endpoint end to end. It is
   the only place where the rebuilt constraint is proven by an actual insert.
2. **Row counts match.** Before applying to Staging, record
   `SELECT COUNT(*) FROM research_candidates` and compare after. A rebuild that
   loses rows loses review decisions, which are the only non-reproducible data
   in the table.
3. **Review decisions survive.** `review_status`, `review_note` and
   `reviewed_at` are copied verbatim — they are operator judgements, not
   derived state.
4. **Indexes exist.** `idx_research_review_last_seen` and
   `idx_research_source_last_seen` are recreated; without them the review
   queries scan.
5. **Staging before production.** `0011` has been applied to neither. The first
   application is a deliberate act, and the deploy gate refuses to ship Worker
   code whose migrations are unapplied — which is the correct order, not an
   obstacle.

## Recovery if the rebuild is interrupted

Symptom: `/v1/research/*` returns HTTP 500 with `no such table: research_candidates`,
and `research_candidates_new` exists.

```sql
-- only if research_candidates is absent and research_candidates_new is complete
ALTER TABLE research_candidates_new RENAME TO research_candidates;
CREATE INDEX IF NOT EXISTS idx_research_review_last_seen
  ON research_candidates(review_status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_source_last_seen
  ON research_candidates(source, last_seen_at DESC);
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0011_research_source_expansion');
```

Verify the row count before and after the rename. If `research_candidates` still
exists as well, stop: the copy may have been partial, and the safe move is to
compare the two tables row by row before dropping anything.

## Rolling back

There is no down-migration. Reverting the code does not revert the schema, and a
narrowing rebuild is another migration of the same shape — with the same window.
That asymmetry is the reason to apply this one to Staging and read it back before
production, rather than to discover the asymmetry afterwards.
