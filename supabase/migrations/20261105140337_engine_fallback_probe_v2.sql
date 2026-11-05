-- Independent, Free-plan Supabase ledger used only when the Cloudflare Staging
-- probe API is unavailable. This is not a general-purpose task queue.
create schema if not exists engine_fallback;
revoke all on schema engine_fallback from public, anon, authenticated;
grant usage on schema engine_fallback to service_role;

create table engine_fallback.tasks (
  id text primary key default gen_random_uuid()::text,
  idempotency_key text not null unique,
  task_type text not null check (task_type = 'supervised_probe'),
  title text not null check (title = 'Scheduled Staging read-only process probe'),
  payload jsonb not null check (payload = '{"operation":"python_platform_probe"}'::jsonb),
  status text not null check (status in ('pending','running','succeeded','failed','blocked')),
  attempts integer not null default 0 check (attempts between 0 and 1),
  max_attempts integer not null default 1 check (max_attempts = 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  result_json jsonb,
  error text,
  lease_token text,
  lease_expires_at timestamptz,
  execution_mode text not null default 'not_started'
    check (execution_mode in ('not_started','dry_run')),
  priority integer not null default 100 check (priority = 100),
  dependencies_json jsonb not null default '[]'::jsonb check (dependencies_json = '[]'::jsonb)
);

create index engine_fallback_tasks_status_created
  on engine_fallback.tasks(status, created_at, id);

create table engine_fallback.audit_log (
  id bigint generated always as identity primary key,
  task_id text references engine_fallback.tasks(id),
  event text not null,
  details_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table engine_fallback.task_process_dispatches (
  dispatch_id text primary key check (dispatch_id ~ '^[0-9a-f]{32}$'),
  task_id text not null references engine_fallback.tasks(id),
  step_key text not null check (step_key = 'supervised_process:v1'),
  operation text not null check (operation = 'python_platform_probe'),
  operation_version text not null check (operation_version = 'process-supervisor:v1'),
  operation_fingerprint text not null check (operation_fingerprint ~ '^[0-9a-f]{64}$'),
  lease_fingerprint text not null check (lease_fingerprint ~ '^[0-9a-f]{16}$'),
  status text not null check (status in ('prepared','in_flight','ambiguous','settled')),
  outcome_version integer,
  outcome jsonb,
  detail text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(task_id, step_key),
  check (
    (status = 'settled' and outcome_version = 1 and outcome is not null)
    or (status <> 'settled' and outcome_version is null and outcome is null)
  )
);

create table engine_fallback.heartbeat (
  singleton boolean primary key default true check (singleton),
  last_seen_at timestamptz not null
);

alter table engine_fallback.tasks enable row level security;
alter table engine_fallback.audit_log enable row level security;
alter table engine_fallback.task_process_dispatches enable row level security;
alter table engine_fallback.heartbeat enable row level security;
revoke all on all tables in schema engine_fallback from public, anon, authenticated;

create or replace function engine_fallback.task_json(
  p_task engine_fallback.tasks,
  p_include_lease boolean default false
) returns jsonb
language sql immutable security definer
set search_path = pg_catalog, engine_fallback
as $$
  select jsonb_build_object(
    'id', p_task.id,
    'idempotency_key', p_task.idempotency_key,
    'task_type', p_task.task_type,
    'title', p_task.title,
    'payload_json', p_task.payload::text,
    'status', p_task.status,
    'attempts', p_task.attempts,
    'max_attempts', p_task.max_attempts,
    'created_at', p_task.created_at,
    'updated_at', p_task.updated_at,
    'execution_mode', p_task.execution_mode,
    'priority', p_task.priority,
    'dependencies_json', p_task.dependencies_json::text,
    'depends_on', p_task.dependencies_json
  ) || case when p_include_lease then jsonb_build_object(
    'lease_token', p_task.lease_token,
    'lease_expires_at', p_task.lease_expires_at
  ) else '{}'::jsonb end
$$;

create or replace function engine_fallback.dispatch_json(
  p_dispatch engine_fallback.task_process_dispatches
) returns jsonb
language sql immutable security definer
set search_path = pg_catalog, engine_fallback
as $$
  select jsonb_build_object(
    'dispatch_id', p_dispatch.dispatch_id,
    'task_id', p_dispatch.task_id,
    'step_key', p_dispatch.step_key,
    'operation', p_dispatch.operation,
    'operation_version', p_dispatch.operation_version,
    'operation_fingerprint', p_dispatch.operation_fingerprint,
    'status', p_dispatch.status,
    'outcome_version', p_dispatch.outcome_version,
    'outcome', p_dispatch.outcome,
    'detail', p_dispatch.detail,
    'created_at', p_dispatch.created_at,
    'updated_at', p_dispatch.updated_at
  )
$$;

create or replace function public.engine_fallback_ping()
returns jsonb
language plpgsql security definer
set search_path = pg_catalog, engine_fallback
as $$
declare
  v_seen timestamptz := now();
begin
  insert into engine_fallback.heartbeat(singleton, last_seen_at)
  values (true, v_seen)
  on conflict (singleton) do update set last_seen_at = excluded.last_seen_at;
  return jsonb_build_object('ok', true, 'last_seen_at', v_seen);
end;
$$;

create or replace function public.engine_fallback_enqueue(
  p_task_type text,
  p_title text,
  p_payload jsonb,
  p_idempotency_key text,
  p_max_attempts integer,
  p_priority integer
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, engine_fallback
as $$
declare
  v_task engine_fallback.tasks%rowtype;
  v_created boolean := false;
  v_inserted integer := 0;
begin
  if p_task_type <> 'supervised_probe'
     or p_title <> 'Scheduled Staging read-only process probe'
     or p_payload <> '{"operation":"python_platform_probe"}'::jsonb
     or p_idempotency_key !~ '^staging-cron-supervised-probe:[0-9]{1,20}$'
     or p_max_attempts <> 1
     or p_priority <> 100 then
    return jsonb_build_object('http_status', 400, 'error', 'probe_envelope_rejected');
  end if;

  insert into engine_fallback.tasks(
    idempotency_key, task_type, title, payload, status, attempts,
    max_attempts, execution_mode, priority, dependencies_json
  ) values (
    p_idempotency_key, p_task_type, p_title, p_payload, 'pending', 0,
    1, 'not_started', 100, '[]'::jsonb
  ) on conflict (idempotency_key) do nothing;
  get diagnostics v_inserted = row_count;
  v_created := v_inserted = 1;
  select * into v_task from engine_fallback.tasks
    where idempotency_key = p_idempotency_key;
  if not found then
    return jsonb_build_object('http_status', 503, 'error', 'task_write_unverified');
  end if;
  if v_task.task_type <> p_task_type or v_task.title <> p_title
     or v_task.payload <> p_payload or v_task.max_attempts <> 1
     or v_task.priority <> 100 then
    return jsonb_build_object('http_status', 409, 'error', 'idempotency_conflict');
  end if;
  if v_created then
    insert into engine_fallback.audit_log(task_id, event, details_json)
    values (v_task.id, 'task_enqueued', jsonb_build_object(
      'task_type', v_task.task_type,
      'priority', v_task.priority,
      'backend', 'supabase_free_fallback'
    ));
  end if;
  return jsonb_build_object(
    'task', engine_fallback.task_json(v_task, false),
    'created', v_created
  );
end;
$$;

create or replace function public.engine_fallback_claim(p_task_id text)
returns jsonb
language plpgsql security definer
set search_path = pg_catalog, engine_fallback
as $$
declare
  v_task engine_fallback.tasks%rowtype;
begin
  if p_task_id !~ '^[A-Za-z0-9_-]{1,128}$' then
    return jsonb_build_object('http_status', 400, 'error', 'invalid_task_id');
  end if;
  update engine_fallback.tasks set
    status = 'running',
    attempts = attempts + 1,
    execution_mode = 'dry_run',
    updated_at = now(),
    lease_token = gen_random_uuid()::text,
    lease_expires_at = now() + interval '120 seconds'
  where id = p_task_id
    and status = 'pending'
    and task_type = 'supervised_probe'
    and payload = '{"operation":"python_platform_probe"}'::jsonb
    and attempts < max_attempts
    and dependencies_json = '[]'::jsonb
  returning * into v_task;
  if not found then
    return jsonb_build_object('task', null);
  end if;
  insert into engine_fallback.audit_log(task_id, event, details_json)
  values (v_task.id, 'task_claimed', jsonb_build_object(
    'attempt', v_task.attempts,
    'lease_expires_at', v_task.lease_expires_at,
    'execution_mode', 'dry_run',
    'claim_scope', 'supervised_probe_id'
  ));
  return jsonb_build_object(
    'task', engine_fallback.task_json(v_task, true),
    'lease_token', v_task.lease_token,
    'lease_expires_at', v_task.lease_expires_at
  );
end;
$$;

create or replace function public.engine_fallback_heartbeat(
  p_task_id text,
  p_lease_token text
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, engine_fallback
as $$
declare
  v_expires timestamptz := now() + interval '120 seconds';
begin
  update engine_fallback.tasks set
    updated_at = now(), lease_expires_at = v_expires
  where id = p_task_id and status = 'running'
    and lease_token = p_lease_token and lease_expires_at >= now()
  returning lease_expires_at into v_expires;
  if not found then
    return jsonb_build_object('http_status', 409, 'error', 'lease_lost');
  end if;
  insert into engine_fallback.audit_log(task_id, event, details_json)
  values (p_task_id, 'task_heartbeat', jsonb_build_object('lease_expires_at', v_expires));
  return jsonb_build_object('ok', true, 'lease_expires_at', v_expires);
end;
$$;

create or replace function public.engine_fallback_finish(
  p_task_id text,
  p_body jsonb
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, engine_fallback
as $$
declare
  v_task engine_fallback.tasks%rowtype;
  v_status text := p_body->>'status';
  v_result jsonb := p_body->'result';
  v_error text := p_body->>'error';
  v_execution_mode text := coalesce(p_body->>'execution_mode', 'dry_run');
begin
  if coalesce(v_status, '') not in ('succeeded','failed','blocked')
     or coalesce(p_body->>'lease_token','') = ''
     or v_execution_mode <> 'dry_run'
     or (v_error is not null and length(v_error) > 2000)
     or (v_result is not null and octet_length(v_result::text) > 16384) then
    return jsonb_build_object('http_status', 400, 'error', 'invalid_finish');
  end if;
  if v_status = 'succeeded' and (
       v_result is null
       or v_result->>'execution' <> 'supervised_process:v1'
       or v_result->>'operation' <> 'python_platform_probe'
       or v_result->>'classification' <> 'succeeded'
       or v_result->>'inference' <> 'none'
       or v_result->>'replayed' <> 'false'
       or v_result->>'dispatch_id' !~ '^[0-9a-f]{32}$'
       or coalesce(v_result->>'provider_calls', '') <> '0'
       or coalesce(v_result->>'external_writes', '') <> '0'
       or not exists (
         select 1 from engine_fallback.task_process_dispatches d
         where d.task_id = p_task_id
           and d.dispatch_id = v_result->>'dispatch_id'
           and d.status = 'settled'
           and d.outcome->>'classification' = 'succeeded'
           and d.outcome->>'exit_code' = '0'
       )
     ) then
    return jsonb_build_object('http_status', 400, 'error', 'unsafe_probe_result');
  end if;
  update engine_fallback.tasks set
    status = v_status,
    result_json = v_result,
    error = v_error,
    execution_mode = 'dry_run',
    updated_at = now(),
    lease_token = null,
    lease_expires_at = null
  where id = p_task_id and status = 'running'
    and lease_token = p_body->>'lease_token'
    and lease_expires_at >= now()
    and task_type = 'supervised_probe'
  returning * into v_task;
  if not found then
    return jsonb_build_object('http_status', 409, 'error', 'lease_lost');
  end if;
  insert into engine_fallback.audit_log(task_id, event, details_json)
  values (p_task_id, 'task_' || v_status, jsonb_build_object(
    'execution_mode', v_task.execution_mode,
    'error', v_error
  ));
  return jsonb_build_object('ok', true, 'status', v_status);
end;
$$;

create or replace function public.engine_fallback_process_dispatch(
  p_task_id text,
  p_body jsonb
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, engine_fallback
as $$
declare
  v_task engine_fallback.tasks%rowtype;
  v_dispatch engine_fallback.task_process_dispatches%rowtype;
  v_action text := p_body->>'action';
  v_dispatch_id text := p_body->>'dispatch_id';
  v_lease_token text := p_body->>'lease_token';
  v_outcome jsonb := p_body->'outcome';
  v_detail text := left(coalesce(p_body->>'detail',''), 400);
  v_inserted integer := 0;
begin
  select * into v_task from engine_fallback.tasks
    where id = p_task_id for update;
  if not found or v_task.status <> 'running'
     or v_task.lease_token is distinct from v_lease_token
     or v_task.lease_expires_at < now() then
    return jsonb_build_object('http_status', 409, 'error', 'lease_lost');
  end if;
  if v_task.task_type <> 'supervised_probe'
     or v_task.payload <> '{"operation":"python_platform_probe"}'::jsonb then
    return jsonb_build_object('http_status', 403, 'error', 'executor_mismatch');
  end if;

  if v_action = 'prepare' then
    if v_dispatch_id !~ '^[0-9a-f]{32}$'
       or p_body->>'step_key' <> 'supervised_process:v1'
       or p_body->>'operation' <> 'python_platform_probe'
       or p_body->>'operation_version' <> 'process-supervisor:v1'
       or p_body->>'operation_fingerprint' !~ '^[0-9a-f]{64}$'
       or p_body->>'lease_fingerprint' !~ '^[0-9a-f]{16}$' then
      return jsonb_build_object('http_status', 400, 'error', 'invalid_dispatch_identity');
    end if;
    insert into engine_fallback.task_process_dispatches(
      dispatch_id, task_id, step_key, operation, operation_version,
      operation_fingerprint, lease_fingerprint, status
    ) values (
      v_dispatch_id, p_task_id, 'supervised_process:v1',
      'python_platform_probe', 'process-supervisor:v1',
      p_body->>'operation_fingerprint', p_body->>'lease_fingerprint', 'prepared'
    ) on conflict do nothing;
    get diagnostics v_inserted = row_count;
    select * into v_dispatch from engine_fallback.task_process_dispatches
      where dispatch_id = v_dispatch_id or (task_id = p_task_id and step_key = 'supervised_process:v1')
      order by created_at limit 1;
    if not found
       or v_dispatch.dispatch_id <> v_dispatch_id
       or v_dispatch.task_id <> p_task_id
       or v_dispatch.operation_fingerprint <> p_body->>'operation_fingerprint'
       or v_dispatch.lease_fingerprint <> p_body->>'lease_fingerprint' then
      return jsonb_build_object('http_status', 409, 'error', 'dispatch_identity_conflict');
    end if;
    if v_inserted = 1 then
      insert into engine_fallback.audit_log(task_id, event, details_json)
      values (p_task_id, 'process_dispatch_prepared', jsonb_build_object('dispatch_id', v_dispatch_id));
    end if;
    return jsonb_build_object('dispatch', engine_fallback.dispatch_json(v_dispatch));
  elsif v_action = 'start' then
    update engine_fallback.task_process_dispatches set status = 'in_flight', updated_at = now()
      where dispatch_id = v_dispatch_id and task_id = p_task_id
        and status = 'prepared'
      returning * into v_dispatch;
    if found then
      insert into engine_fallback.audit_log(task_id, event, details_json)
      values (p_task_id, 'process_dispatch_started', jsonb_build_object('dispatch_id', v_dispatch_id));
      return jsonb_build_object('status', 'in_flight');
    end if;
    select * into v_dispatch from engine_fallback.task_process_dispatches
      where dispatch_id = v_dispatch_id and task_id = p_task_id;
    if found and v_dispatch.status = 'in_flight' then
      return jsonb_build_object('status', 'in_flight');
    end if;
    return jsonb_build_object('http_status', 409, 'error', 'dispatch_not_prepared');
  elsif v_action = 'settle' then
    if v_outcome is null or jsonb_typeof(v_outcome) <> 'object' then
      return jsonb_build_object('http_status', 400, 'error', 'invalid_dispatch_outcome');
    end if;
    if not (v_outcome ?& array[
         'classification','exit_code','signal_number','timed_out','cancelled',
         'output_truncated','stdout','stderr','duration_seconds','descendants_killed'
       ])
       or (v_outcome - array[
         'classification','exit_code','signal_number','timed_out','cancelled',
         'output_truncated','stdout','stderr','duration_seconds','descendants_killed'
       ]) <> '{}'::jsonb then
      return jsonb_build_object('http_status', 400, 'error', 'invalid_dispatch_outcome');
    end if;
    if v_outcome->>'classification' not in ('succeeded','failed','timed_out','cancelled','refused','ambiguous')
       or jsonb_typeof(v_outcome->'output_truncated') <> 'boolean'
       or jsonb_typeof(v_outcome->'timed_out') <> 'boolean'
       or jsonb_typeof(v_outcome->'cancelled') <> 'boolean'
       or jsonb_typeof(v_outcome->'stdout') <> 'string'
       or jsonb_typeof(v_outcome->'stderr') <> 'string'
       or jsonb_typeof(v_outcome->'duration_seconds') <> 'number'
       or jsonb_typeof(v_outcome->'descendants_killed') <> 'number'
       or jsonb_typeof(v_outcome->'exit_code') not in ('null','number')
       or jsonb_typeof(v_outcome->'signal_number') not in ('null','number') then
      return jsonb_build_object('http_status', 400, 'error', 'invalid_dispatch_outcome');
    end if;
    if octet_length(coalesce(v_outcome->>'stdout','')) > 3500
       or octet_length(coalesce(v_outcome->>'stderr','')) > 3500
       or octet_length(v_outcome::text) > 12288
       or (v_outcome->>'duration_seconds')::numeric < 0
       or (v_outcome->>'duration_seconds')::numeric > 900
       or (v_outcome->>'descendants_killed')::numeric < 0
       or (v_outcome->>'descendants_killed')::numeric > 10000
       or trunc((v_outcome->>'descendants_killed')::numeric) <> (v_outcome->>'descendants_killed')::numeric then
      return jsonb_build_object('http_status', 400, 'error', 'invalid_dispatch_outcome');
    end if;
    update engine_fallback.task_process_dispatches set
      status = 'settled', outcome_version = 1, outcome = v_outcome,
      detail = null, updated_at = now()
    where dispatch_id = v_dispatch_id and task_id = p_task_id and status = 'in_flight'
    returning * into v_dispatch;
    if not found then
      select * into v_dispatch from engine_fallback.task_process_dispatches
        where dispatch_id = v_dispatch_id and task_id = p_task_id;
      if found and v_dispatch.status = 'settled' and v_dispatch.outcome = v_outcome then
        return jsonb_build_object('status', 'settled');
      end if;
      return jsonb_build_object('http_status', 409, 'error', 'dispatch_not_in_flight');
    end if;
    insert into engine_fallback.audit_log(task_id, event, details_json)
    values (p_task_id, 'process_dispatch_settled', jsonb_build_object(
      'dispatch_id', v_dispatch_id,
      'classification', v_outcome->>'classification'
    ));
    return jsonb_build_object('status', 'settled');
  elsif v_action = 'ambiguous' then
    update engine_fallback.task_process_dispatches set
      status = 'ambiguous', detail = v_detail, updated_at = now()
    where dispatch_id = v_dispatch_id and task_id = p_task_id
      and status in ('prepared','in_flight')
    returning * into v_dispatch;
    if not found then
      select * into v_dispatch from engine_fallback.task_process_dispatches
        where dispatch_id = v_dispatch_id and task_id = p_task_id;
      if found and v_dispatch.status = 'ambiguous' then
        return jsonb_build_object('status', 'ambiguous');
      end if;
      return jsonb_build_object('http_status', 409, 'error', 'dispatch_not_ambiguous');
    end if;
    insert into engine_fallback.audit_log(task_id, event, details_json)
    values (p_task_id, 'process_dispatch_ambiguous', jsonb_build_object('dispatch_id', v_dispatch_id));
    return jsonb_build_object('status', 'ambiguous');
  end if;
  return jsonb_build_object('http_status', 400, 'error', 'unsupported_dispatch_action');
end;
$$;

-- PostgREST may execute these RPCs only with the private service role used by
-- the Edge Function. Anonymous and authenticated clients receive no table access.
revoke all on function public.engine_fallback_ping() from public, anon, authenticated;
revoke all on function public.engine_fallback_enqueue(text,text,jsonb,text,integer,integer) from public, anon, authenticated;
revoke all on function public.engine_fallback_claim(text) from public, anon, authenticated;
revoke all on function public.engine_fallback_heartbeat(text,text) from public, anon, authenticated;
revoke all on function public.engine_fallback_finish(text,jsonb) from public, anon, authenticated;
revoke all on function public.engine_fallback_process_dispatch(text,jsonb) from public, anon, authenticated;
grant execute on function public.engine_fallback_ping() to service_role;
grant execute on function public.engine_fallback_enqueue(text,text,jsonb,text,integer,integer) to service_role;
grant execute on function public.engine_fallback_claim(text) to service_role;
grant execute on function public.engine_fallback_heartbeat(text,text) to service_role;
grant execute on function public.engine_fallback_finish(text,jsonb) to service_role;
grant execute on function public.engine_fallback_process_dispatch(text,jsonb) to service_role;
