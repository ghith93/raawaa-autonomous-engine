-- Cover the engine_fallback.audit_log(task_id) foreign key so exact-task
-- audit filtering stays indexed as rows accumulate (Supabase performance
-- advisor finding, Issue #464 review 6667001597). Read-path only: no data,
-- grant, or RLS changes.
create index if not exists engine_fallback_audit_log_task_id
  on engine_fallback.audit_log (task_id);
