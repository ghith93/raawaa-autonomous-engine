-- Replace the initial dispatch validator with a portable jsonb key-set check.
-- PostgreSQL exposes jsonb existence operators but no jsonb_object_length().
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
  select * into v_task from engine_fallback.tasks where id = p_task_id for update;
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
      where dispatch_id = v_dispatch_id
        or (task_id = p_task_id and step_key = 'supervised_process:v1')
      order by created_at limit 1;
    if not found or v_dispatch.dispatch_id <> v_dispatch_id
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
      where dispatch_id = v_dispatch_id and task_id = p_task_id and status = 'prepared'
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
      'dispatch_id', v_dispatch_id, 'classification', v_outcome->>'classification'
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
