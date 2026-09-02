-- Isolate regenerated reports/experiments and provide a guarded V4 Idea-only resume.

alter table public.reports
  add column if not exists generation_id uuid;

update public.reports
set generation_id = coalesce(
  case when content->>'generation_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    then (content->>'generation_id')::uuid end,
  case when content #>> '{presentation,generation_id}' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    then (content #>> '{presentation,generation_id}')::uuid end,
  gen_random_uuid()
)
where generation_id is null;

alter table public.reports
  alter column generation_id set default gen_random_uuid(),
  alter column generation_id set not null;

alter table public.idea_experiments
  add column if not exists report_generation_id uuid;

update public.idea_experiments experiments
set report_generation_id = reports.generation_id
from public.reports reports
where reports.id = experiments.report_id
  and experiments.report_generation_id is null;

alter table public.idea_experiments
  alter column report_generation_id set not null;

alter table public.idea_experiments
  drop constraint if exists idea_experiments_report_id_idea_key_key;

alter table public.idea_experiments
  add constraint idea_experiments_report_generation_idea_unique
  unique (report_id, report_generation_id, idea_key);

create index if not exists idea_experiments_generation_idx
  on public.idea_experiments (report_id, report_generation_id, idea_rank);

create table if not exists public.report_generation_backups (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null,
  job_id uuid not null,
  generation_id uuid not null,
  report_content jsonb not null,
  report_markdown text not null,
  report_summary jsonb,
  job_checkpoint jsonb not null,
  created_at timestamptz not null default now()
);

alter table public.report_generation_backups enable row level security;
revoke all on public.report_generation_backups from public, anon, authenticated;
grant all on public.report_generation_backups to service_role;

create or replace function public.save_v4_report_generation(
  p_job_id uuid,
  p_generation_id uuid,
  p_content jsonb,
  p_markdown text,
  p_summary jsonb,
  p_checkpoint jsonb,
  p_sections jsonb default '{}'::jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_report_id uuid;
begin
  perform 1 from public.jobs where id = p_job_id for update;
  if not found then raise no_data_found using message = 'job not found'; end if;
  if p_generation_id is null
    or p_content->>'generation_id' <> p_generation_id::text
    or p_content #>> '{presentation,generation_id}' <> p_generation_id::text then
    raise check_violation using message = 'report generation mismatch';
  end if;

  insert into public.reports (job_id, generation_id, content, markdown, summary)
  values (p_job_id, p_generation_id, p_content, p_markdown, p_summary)
  on conflict (job_id) do update
  set generation_id = excluded.generation_id,
      content = excluded.content,
      markdown = excluded.markdown,
      summary = excluded.summary,
      updated_at = now()
  returning id into v_report_id;

  if jsonb_typeof(coalesce(p_sections, '{}'::jsonb)) <> 'object' then
    raise check_violation using message = 'report sections must be an object';
  end if;
  insert into public.report_sections (report_id, section, content)
  select v_report_id, entries.key, entries.value
  from jsonb_each(coalesce(p_sections, '{}'::jsonb)) entries
  on conflict (report_id, section) do update
  set content = excluded.content, updated_at = now();

  update public.jobs
  set checkpoint = p_checkpoint,
      updated_at = now()
  where id = p_job_id;
  return v_report_id;
end;
$$;

-- Admit read-only assistant questions before a repository or sandbox exists.
-- New image attachments are bound in this transaction, while prior bound
-- context images remain referenced by request.contextAttachmentIds.
create or replace function public.enqueue_experiment_answer_action_with_attachments(
  p_experiment_id uuid,
  p_user_id uuid,
  p_request jsonb default '{}'::jsonb,
  p_idempotency_key text default null,
  p_attachment_ids uuid[] default '{}'::uuid[],
  p_llm_reservation_cny numeric default 5,
  p_assistant_llm_max_cny numeric default 20,
  p_experiment_llm_max_cny numeric default 40,
  p_global_llm_max_cny numeric default 200
)
returns public.experiment_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_ids uuid[];
  v_request_ids uuid[];
  v_count integer;
  v_reservation numeric := least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5);
begin
  if coalesce(p_request->>'intent', '') not in ('answer', 'mutate') then
    raise check_violation using message = 'assistant intent required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found or v_experiment.user_id <> p_user_id then
    raise insufficient_privilege using message = 'experiment access denied';
  end if;
  if v_experiment.deletion_requested_at is not null
    or v_experiment.cancellation_requested
    or v_experiment.status = 'cancelled' then
    raise check_violation using message = 'experiment is being cancelled or deleted';
  end if;
  if coalesce(v_experiment.pilot_specification, '{}'::jsonb) = '{}'::jsonb then
    raise check_violation using message = 'experiment specification is not ready';
  end if;
  if p_idempotency_key is not null then
    select * into v_action from public.experiment_actions
    where experiment_id = p_experiment_id
      and idempotency_key = left(p_idempotency_key, 160);
    if found then return v_action; end if;
  end if;
  if exists (
    select 1 from public.experiment_actions actions
    where actions.experiment_id = p_experiment_id
      and actions.status in ('queued', 'running', 'recovering')
      and (
        p_request->>'intent' = 'mutate'
        or actions.request->>'intent' = 'answer'
      )
  ) then
    raise serialization_failure using message = 'experiment action already queued';
  end if;
  if v_experiment.llm_cost_cny
      + coalesce((
        select sum(actions.llm_cost_cny + case
          when actions.status in ('queued', 'running', 'recovering')
            then actions.llm_reserved_cny else 0 end)
        from public.experiment_actions actions
        where actions.experiment_id = p_experiment_id
          and actions.kind in ('assistant', 'chat')
      ), 0)
      + v_reservation
    > greatest(coalesce(p_assistant_llm_max_cny, 20), 0) then
    raise check_violation using message = 'experiment assistant budget reached';
  end if;
  if v_experiment.llm_cost_cny + v_reservation
    > greatest(coalesce(p_experiment_llm_max_cny, 40), 0) then
    raise check_violation using message = 'experiment inference budget reached';
  end if;
  if public.current_experiment_llm_commitment_cny() + v_reservation
    > greatest(coalesce(p_global_llm_max_cny, 200), 0) then
    raise check_violation using message = 'global experiment inference budget reached';
  end if;

  select coalesce(array_agg(value order by value), '{}'::uuid[])
  into v_ids from (
    select distinct unnest(coalesce(p_attachment_ids, '{}'::uuid[])) as value
  ) unique_ids;
  if cardinality(v_ids) > 4 then
    raise check_violation using message = 'too many experiment chat attachments';
  end if;
  if cardinality(v_ids) > 0 then
    select count(*) into v_count
    from public.experiment_chat_attachments attachments
    where attachments.id = any(v_ids)
      and attachments.experiment_id = p_experiment_id
      and attachments.user_id = p_user_id
      and attachments.status in ('ready', 'bound')
      and attachments.action_id is null;
    if v_count <> cardinality(v_ids) then
      raise check_violation using message = 'experiment chat attachment is unavailable';
    end if;
  end if;

  insert into public.experiment_actions (
    experiment_id, requested_by, kind, request, base_revision_id,
    idempotency_key, llm_reserved_cny, validation_slot_reserved
  ) values (
    p_experiment_id, p_user_id, 'assistant', coalesce(p_request, '{}'::jsonb),
    case when v_experiment.status = 'ready'
      then v_experiment.current_revision_id else null end,
    nullif(left(p_idempotency_key, 160), ''),
    v_reservation, false
  )
  on conflict (experiment_id, idempotency_key)
    where idempotency_key is not null do nothing
  returning * into v_action;
  if v_action.id is null then
    select * into v_action from public.experiment_actions
    where experiment_id = p_experiment_id
      and idempotency_key = left(p_idempotency_key, 160);
  end if;

  select coalesce(array_agg(value::uuid order by value::uuid), '{}'::uuid[])
  into v_request_ids from (
    select distinct jsonb_array_elements_text(
      coalesce(v_action.request->'attachmentIds', '[]'::jsonb)
    ) as value
  ) request_ids;
  if v_request_ids is distinct from v_ids then
    raise serialization_failure using message = 'experiment chat attachment idempotency conflict';
  end if;
  if cardinality(v_ids) > 0 then
    update public.experiment_chat_attachments
    set action_id = v_action.id, status = 'bound',
        expires_at = 'infinity'::timestamptz, updated_at = now()
    where id = any(v_ids) and experiment_id = p_experiment_id
      and user_id = p_user_id and action_id is null;
    get diagnostics v_count = row_count;
    if v_count <> cardinality(v_ids) then
      raise serialization_failure using message = 'experiment chat attachment binding conflict';
    end if;
  end if;
  return v_action;
end;
$$;

-- Read-only assistant answers need neither an E2B reservation nor a completed
-- automatic repository. They are claimed ahead of ordinary workspace actions.
create or replace function public.claim_next_experiment_answer_action(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.experiment_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_action public.experiment_actions;
begin
  select actions.* into v_action
  from public.experiment_actions actions
  join public.idea_experiments experiments on experiments.id = actions.experiment_id
  where actions.kind in ('assistant', 'chat')
    and actions.request->>'intent' = 'answer'
    and (
      (actions.status in ('queued', 'recovering')
        and coalesce(actions.next_retry_at, now()) <= now()
        and (actions.lease_expires_at is null or actions.lease_expires_at <= now()))
      or (actions.status = 'running' and actions.lease_expires_at <= now())
    )
    and experiments.status <> 'cancelled'
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null
  order by actions.created_at
  for update of actions skip locked
  limit 1;
  if not found then return; end if;
  update public.experiment_actions
  set status = 'running', worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      started_at = coalesce(started_at, now()), next_retry_at = null,
      updated_at = now()
  where id = v_action.id returning * into v_action;
  return next v_action;
end;
$$;

-- An executable assistant request may be submitted before automatic v1 is
-- archived. Bind it to that first immutable revision immediately before the
-- ordinary action claim performs its stale-revision fence.
create or replace function public.prepare_queued_experiment_mutations()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  update public.experiment_actions actions
  set base_revision_id = experiments.current_revision_id,
      updated_at = now()
  from public.idea_experiments experiments
  where experiments.id = actions.experiment_id
    and experiments.status = 'ready'
    and experiments.current_revision_id is not null
    and actions.kind in ('assistant', 'chat')
    and actions.request->>'intent' = 'mutate'
    and actions.status in ('queued', 'recovering')
    and actions.base_revision_id is null;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.enqueue_assistant_followup_validation(
  p_experiment_id uuid,
  p_user_id uuid,
  p_base_revision_id uuid,
  p_source_action_id uuid,
  p_llm_reservation_cny numeric default 5,
  p_experiment_llm_max_cny numeric default 40,
  p_global_llm_max_cny numeric default 200
)
returns public.experiment_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_key text := 'assistant-validation:' || p_source_action_id::text;
  v_reservation numeric := least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5);
begin
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found or v_experiment.user_id <> p_user_id then
    raise insufficient_privilege using message = 'experiment access denied';
  end if;
  select * into v_action from public.experiment_actions
  where experiment_id = p_experiment_id and idempotency_key = v_key;
  if found then return v_action; end if;
  if v_experiment.status <> 'ready'
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise check_violation using message = 'experiment is not ready for validation';
  end if;
  if p_base_revision_id is null
    or p_base_revision_id is distinct from v_experiment.current_revision_id then
    raise serialization_failure using message = 'experiment revision conflict';
  end if;
  if v_experiment.user_validation_count + (
    select count(*) from public.experiment_actions actions
    where actions.experiment_id = p_experiment_id
      and actions.kind = 'validation'
      and actions.validation_slot_reserved
      and actions.status in ('queued', 'running', 'recovering')
  ) >= v_experiment.max_user_validations then
    raise check_violation using message = 'manual validation limit reached';
  end if;
  if v_experiment.llm_cost_cny + v_reservation
    > greatest(coalesce(p_experiment_llm_max_cny, 40), 0)
    or public.current_experiment_llm_commitment_cny() + v_reservation
      > greatest(coalesce(p_global_llm_max_cny, 200), 0) then
    raise check_violation using message = 'experiment inference budget reached';
  end if;
  insert into public.experiment_actions (
    experiment_id, requested_by, kind, request, base_revision_id,
    idempotency_key, llm_reserved_cny, validation_slot_reserved
  ) values (
    p_experiment_id, p_user_id, 'validation',
    jsonb_build_object('sourceActionId', p_source_action_id),
    p_base_revision_id, v_key, v_reservation, true
  ) returning * into v_action;
  return v_action;
end;
$$;

-- Claim repository-only work before applying E2B concurrency/spend gates.
-- The existing runtime RPC remains the final authority when _sandbox starts.
create or replace function public.claim_next_experiment_repository_generation(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  select * into v_experiment from public.idea_experiments
  where cancellation_requested = false
    and deletion_requested_at is null
    and current_revision_id is null
    and not coalesce((checkpoint->>'repository_generation_complete')::boolean, false)
    and (
      status = 'queued'
      or (status in ('recovering', 'waiting_resources')
        and coalesce(next_retry_at, now()) <= now())
      or (status = 'running' and lease_expires_at <= now())
    )
  order by created_at
  for update skip locked
  limit 1;
  if not found then return; end if;
  update public.idea_experiments
  set status = 'running', worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      started_at = coalesce(started_at, now()), next_retry_at = null,
      updated_at = now()
  where id = v_experiment.id returning * into v_experiment;
  return next v_experiment;
end;
$$;

-- Heartbeats for read-only assistant answers are valid while the independent
-- automatic experiment is queued/running. Other actions retain the ready gate.
create or replace function public.renew_experiment_action_lease(
  p_action_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.experiment_actions actions
  set lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      updated_at = now()
  from public.idea_experiments experiments
  where actions.id = p_action_id
    and actions.worker_id = p_worker_id
    and actions.status = 'running'
    and experiments.id = actions.experiment_id
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null
    and (
      experiments.status = 'ready'
      or (
        actions.kind in ('assistant', 'chat')
        and actions.request->>'intent' = 'answer'
        and experiments.status <> 'cancelled'
      )
    );
  return found;
end;
$$;

-- Preserve the paid-call fence while allowing a read-only assistant answer
-- to use its own action lease before the automatic experiment reaches ready.
create or replace function public.authorize_experiment_llm_call(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid default null,
  p_usage_id uuid default null,
  p_max_call_cny numeric default null
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_invocation public.experiment_llm_invocations;
  v_max_call_cny numeric := round(greatest(coalesce(p_max_call_cny, 0), 0), 6);
  v_available_cny numeric := 0;
begin
  if p_usage_id is null then
    raise check_violation using message = 'experiment provider usage id is required';
  end if;
  if v_max_call_cny <= 0 then
    raise check_violation using message = 'positive experiment call reservation is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise check_violation using message = 'experiment is being cancelled or deleted';
  end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  else
    select * into v_action from public.experiment_actions
    where id = p_action_id and experiment_id = p_experiment_id for update;
    if not found or v_action.worker_id is distinct from p_worker_id
      or v_action.status <> 'running'
      or v_action.lease_expires_at is null
      or v_action.lease_expires_at <= now()
      or (
        v_experiment.status <> 'ready'
        and not (
          v_action.kind in ('assistant', 'chat')
          and v_action.request->>'intent' = 'answer'
          and v_experiment.status <> 'cancelled'
        )
      ) then
      raise serialization_failure using message = 'experiment action worker lease lost';
    end if;
  end if;

  select * into v_invocation from public.experiment_llm_invocations
  where usage_id = p_usage_id for update;
  if found then
    if v_invocation.experiment_id <> p_experiment_id
      or v_invocation.action_id is distinct from p_action_id
      or v_invocation.reserved_cny <> v_max_call_cny then
      raise check_violation using message = 'experiment invocation id belongs to another call';
    end if;
    if v_invocation.status <> 'authorized' then
      raise check_violation using message = 'experiment invocation is already settled';
    end if;
    return v_experiment;
  end if;
  if exists (
    select 1 from public.provider_usage usage
    where usage.metadata->>'experiment_usage_id' = p_usage_id::text
  ) then
    raise check_violation using message = 'experiment invocation is already settled';
  end if;
  if p_action_id is null then
    select greatest(
      v_experiment.llm_reserved_cny - coalesce(sum(invocations.reserved_cny), 0), 0
    ) into v_available_cny
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id is null and invocations.status = 'authorized';
  else
    select greatest(
      v_action.llm_reserved_cny - coalesce(sum(invocations.reserved_cny), 0), 0
    ) into v_available_cny
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id = p_action_id
      and invocations.status = 'authorized';
  end if;
  if v_available_cny < v_max_call_cny then
    raise check_violation using message = 'experiment inference budget reached';
  end if;
  insert into public.experiment_llm_invocations (
    usage_id, experiment_id, action_id, reserved_cny
  ) values (p_usage_id, p_experiment_id, p_action_id, v_max_call_cny);
  return v_experiment;
end;
$$;

create or replace function public.resume_job_from_v4_ideas(
  p_job_id uuid,
  p_expected_sha256 text,
  p_new_generation_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job public.jobs;
  v_report public.reports;
  v_checkpoint jsonb;
  v_v4 jsonb;
  v_hashes text[];
begin
  if p_new_generation_id is null then
    raise check_violation using message = 'new generation id is required';
  end if;
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found then raise no_data_found using message = 'job not found'; end if;
  if v_job.status <> 'completed' then
    raise check_violation using message = 'only a completed job can resume from v4_ideas';
  end if;

  select array_agg(lower(uploads.sha256) order by job_files.position)
  into v_hashes
  from public.job_files
  join public.uploads on uploads.id = job_files.upload_id
  where job_files.job_id = p_job_id;
  if coalesce(array_length(v_hashes, 1), 0) <> 1
    or v_hashes[1] <> lower(p_expected_sha256) then
    raise check_violation using message = 'input PDF hash mismatch';
  end if;

  select * into v_report from public.reports where job_id = p_job_id for update;
  if not found then raise no_data_found using message = 'report not found'; end if;
  if coalesce((v_job.checkpoint #>> '{v4,landscape,full_text_count}')::integer, 0) < 20
    or coalesce(jsonb_array_length(v_job.checkpoint #> '{v4,landscape,themes}'), 0) < 2 then
    raise check_violation using message = 'reusable V4 landscape checkpoint is incomplete';
  end if;

  insert into public.report_generation_backups (
    report_id, job_id, generation_id, report_content, report_markdown,
    report_summary, job_checkpoint
  ) values (
    v_report.id, v_job.id, v_report.generation_id, v_report.content,
    v_report.markdown, v_report.summary, v_job.checkpoint
  );

  v_checkpoint := v_job.checkpoint - 'experiment_auto_enqueue';
  v_v4 := coalesce(v_checkpoint->'v4', '{}'::jsonb)
    - 'complete'
    - 'presentation'
    - 'idea_attempts'
    - 'evolution_pool'
    - 'pilot_specifications';
  -- Freeze the already-delivered upstream landscape. Targeted evidence read
  -- by the new Idea loop can support Idea citations without changing the
  -- Overview/Input/Landscape sections the user already reviewed.
  if not (v_v4 ? 'delivery_landscape') and v_v4 ? 'landscape' then
    v_v4 := jsonb_set(v_v4, '{delivery_landscape}', v_v4->'landscape', true);
  end if;
  v_v4 := jsonb_set(v_v4, '{active_seconds}', '0'::jsonb, true);
  v_v4 := jsonb_set(v_v4, '{generation_id}', to_jsonb(p_new_generation_id::text), true);
  v_checkpoint := jsonb_set(v_checkpoint, '{v4}', v_v4, true);

  update public.jobs
  set status = 'queued',
      stage = 'v4_ideas',
      progress = 74,
      current_round = greatest(current_round, 1),
      checkpoint = v_checkpoint,
      worker_id = null,
      lease_expires_at = null,
      next_retry_at = null,
      retry_count = 0,
      last_recovery_at = null,
      cancellation_requested = false,
      error = null,
      completed_at = null,
      updated_at = now()
  where id = p_job_id;

  insert into public.job_events (job_id, kind, message, data)
  values (
    p_job_id,
    'auto_recovery',
    'Resuming from the V4 Idea checkpoint with a new report generation',
    jsonb_build_object('from', 'v4_ideas', 'generation_id', p_new_generation_id)
  );
  return jsonb_build_object(
    'job_id', p_job_id,
    'old_report_id', v_report.id,
    'old_generation_id', v_report.generation_id,
    'new_generation_id', p_new_generation_id,
    'input_sha256', v_hashes[1],
    'status', 'queued'
  );
end;
$$;

create or replace function public.enqueue_idea_experiment(
  p_report_id uuid,
  p_idea_key text,
  p_user_id uuid,
  p_automatic boolean default false,
  p_max_spend_usd numeric default 90,
  p_llm_reservation_cny numeric default 5,
  p_global_llm_max_cny numeric default 200
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_report public.reports;
  v_job public.jobs;
  v_ideas jsonb := '[]'::jsonb;
  v_idea jsonb;
  v_full_idea jsonb;
  v_spec jsonb;
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_report from public.reports where id = p_report_id;
  if not found then raise no_data_found using message = 'report not found'; end if;
  select * into v_job from public.jobs where id = v_report.job_id for share;
  if not found or v_job.user_id <> p_user_id then
    raise insufficient_privilege using message = 'report access denied';
  end if;
  if v_job.status <> 'completed' then
    raise check_violation using message = 'analysis must be completed before starting an experiment';
  end if;
  if v_job.admin_deletion_requested_at is not null
    or exists (
      select 1 from public.profiles
      where profiles.id = p_user_id and profiles.deletion_requested_at is not null
    ) then
    raise insufficient_privilege using message = 'account or task deletion is pending';
  end if;

  select * into v_experiment
  from public.idea_experiments
  where report_id = p_report_id
    and report_generation_id = v_report.generation_id
    and idea_key = p_idea_key;
  if found then return v_experiment; end if;

  -- Creating the experiment row and generating its repository do not consume
  -- E2B. Runtime reservation is enforced only when a sandbox is actually
  -- created/restored, so a temporarily exhausted E2B budget never hides code.
  if public.current_experiment_llm_commitment_cny()
      + least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
    > greatest(coalesce(p_global_llm_max_cny, 200), 0) then
    raise check_violation using message = 'global experiment inference budget reached';
  end if;

  select coalesce(content->'ideas', '[]'::jsonb) into v_ideas
  from public.report_sections
  where report_id = p_report_id and section = 'ideas';
  if coalesce(jsonb_typeof(v_ideas), 'null') <> 'array'
    or jsonb_array_length(v_ideas) = 0 then
    v_ideas := coalesce(v_report.content #> '{presentation,ideas}', '[]'::jsonb);
  end if;
  select value into v_idea from jsonb_array_elements(v_ideas)
  where value->>'key' = p_idea_key limit 1;
  if v_idea is null then raise no_data_found using message = 'idea not found in report'; end if;
  if coalesce(v_idea->>'verdict', '') not in ('recommended', 'alternative') then
    raise check_violation using message = 'only delivered report ideas can be validated';
  end if;

  select value into v_full_idea
  from jsonb_array_elements(
    case when jsonb_typeof(v_report.content #> '{presentation,ideas}') = 'array'
      then v_report.content #> '{presentation,ideas}' else '[]'::jsonb end
  )
  where value->>'key' = p_idea_key limit 1;
  v_spec := coalesce(v_full_idea->'pilot_specification', '{}'::jsonb);
  if jsonb_typeof(v_spec) <> 'object' then v_spec := '{}'::jsonb; end if;
  if v_spec = '{}'::jsonb then
    raise check_violation using message = 'current report idea has no valid pilot specification';
  end if;
  if p_automatic and coalesce((v_idea->>'rank')::integer, 1) <> 1 then
    raise check_violation using message = 'only the primary idea may start automatically';
  end if;

  insert into public.idea_experiments (
    report_id, report_generation_id, job_id, user_id, idea_key, idea_rank,
    idea_snapshot, pilot_specification, pilot_specification_hash,
    pilot_compilation_required, automatic_initial_run, llm_reserved_cny
  ) values (
    p_report_id, v_report.generation_id, v_report.job_id, p_user_id,
    p_idea_key, greatest(1, least(3, coalesce((v_idea->>'rank')::integer, 1))),
    v_idea, v_spec, null, false, p_automatic,
    least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
  )
  on conflict (report_id, report_generation_id, idea_key) do nothing
  returning * into v_experiment;
  if v_experiment.id is null then
    select * into v_experiment from public.idea_experiments
    where report_id = p_report_id
      and report_generation_id = v_report.generation_id
      and idea_key = p_idea_key;
  end if;
  return v_experiment;
end;
$$;

revoke all on function public.save_v4_report_generation(uuid, uuid, jsonb, text, jsonb, jsonb, jsonb)
  from public, anon, authenticated;
revoke all on function public.resume_job_from_v4_ideas(uuid, text, uuid)
  from public, anon, authenticated;
revoke all on function public.enqueue_experiment_answer_action_with_attachments(
  uuid, uuid, jsonb, text, uuid[], numeric, numeric, numeric, numeric
) from public, anon, authenticated;
revoke all on function public.claim_next_experiment_answer_action(text, integer)
  from public, anon, authenticated;
revoke all on function public.claim_next_experiment_repository_generation(text, integer)
  from public, anon, authenticated;
revoke all on function public.prepare_queued_experiment_mutations()
  from public, anon, authenticated;
revoke all on function public.enqueue_assistant_followup_validation(
  uuid, uuid, uuid, uuid, numeric, numeric, numeric
) from public, anon, authenticated;
grant execute on function public.save_v4_report_generation(uuid, uuid, jsonb, text, jsonb, jsonb, jsonb)
  to service_role;
grant execute on function public.resume_job_from_v4_ideas(uuid, text, uuid)
  to service_role;
grant execute on function public.enqueue_experiment_answer_action_with_attachments(
  uuid, uuid, jsonb, text, uuid[], numeric, numeric, numeric, numeric
) to service_role;
grant execute on function public.claim_next_experiment_answer_action(text, integer)
  to service_role;
grant execute on function public.claim_next_experiment_repository_generation(text, integer)
  to service_role;
grant execute on function public.prepare_queued_experiment_mutations()
  to service_role;
grant execute on function public.enqueue_assistant_followup_validation(
  uuid, uuid, uuid, uuid, numeric, numeric, numeric
) to service_role;
