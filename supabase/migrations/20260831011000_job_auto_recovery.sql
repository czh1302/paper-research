alter table public.jobs
  add column if not exists retry_count integer not null default 0 check (retry_count >= 0),
  add column if not exists next_retry_at timestamptz,
  add column if not exists last_recovery_at timestamptz;

create index if not exists jobs_recovery_due_idx
on public.jobs (next_retry_at, created_at)
where status in ('recovering', 'waiting_resources');

create table if not exists public.job_attempts (
  id bigint generated always as identity primary key,
  job_id uuid not null references public.jobs(id) on delete cascade,
  attempt_number integer not null check (attempt_number > 0),
  failure_category text not null,
  checkpoint_stage text not null,
  safe_error text,
  created_at timestamptz not null default now()
);

create index if not exists job_attempts_job_created_idx
on public.job_attempts (job_id, created_at desc);

alter table public.job_attempts enable row level security;

drop policy if exists "job_attempts_select_admin" on public.job_attempts;
create policy "job_attempts_select_admin"
on public.job_attempts for select to authenticated
using ((select public.is_admin()));

grant select on public.job_attempts to authenticated;
grant all on public.job_attempts to service_role;

drop policy if exists "events_select_own" on public.job_events;
create policy "events_select_own"
on public.job_events for select to authenticated
using (
  kind in (
    'queued', 'resumed', 'stage', 'paper_parsed', 'retrieval_batch',
    'retrieval_converged', 'external_profile', 'idea_attempt', 'round_complete',
    'evidence_previews', 'completed', 'auto_recovery', 'waiting_resources'
  )
  and exists (
    select 1 from public.jobs
    where jobs.id = job_events.job_id and jobs.user_id = auth.uid()
  )
);

create or replace function public.claim_next_job(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.jobs
language sql
security definer set search_path = public
as $$
  with next_job as (
    select id from public.jobs
    where cancellation_requested = false
      and (
        status = 'queued'
        or (
          status in ('recovering', 'waiting_resources')
          and coalesce(next_retry_at, now()) <= now()
        )
        or (
          status in ('parsing','problem_ready','searching','analyzing','rendering')
          and lease_expires_at < now()
        )
      )
    order by
      case when status in ('recovering', 'waiting_resources') then 0 else 1 end,
      coalesce(next_retry_at, created_at),
      created_at
    for update skip locked
    limit 1
  )
  update public.jobs j
  set worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      started_at = coalesce(started_at, now()),
      status = case
        when j.status not in ('queued', 'recovering', 'waiting_resources') then j.status
        when j.stage ~ '(render|report|export)' then 'rendering'::public.job_status
        when j.stage ~ '(idea|analyz)' then 'analyzing'::public.job_status
        when j.stage ~ '(search|retriev|full_text|landscape)' then 'searching'::public.job_status
        when j.stage ~ '(problem|brief)' then 'problem_ready'::public.job_status
        else 'parsing'::public.job_status
      end,
      next_retry_at = null,
      error = null,
      updated_at = now()
  from next_job
  where j.id = next_job.id
  returning j.*;
$$;

create or replace function public.finish_job(
  p_job_id uuid,
  p_status public.job_status,
  p_error text default null
)
returns void
language plpgsql
security definer set search_path = public
as $$
declare
  v_job public.jobs;
  v_file_count integer;
  v_charged integer;
begin
  if p_status not in ('completed','cancelled','failed','budget_blocked','needs_input') then
    raise exception 'finish_job requires a terminal status';
  end if;
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.status in ('completed','cancelled','needs_input') then
    return;
  end if;
  select count(*) into v_file_count from public.job_files where job_id = p_job_id;
  v_charged := greatest(
    v_job.charged_units,
    least(v_job.reserved_units, v_file_count * v_job.current_round)
  );

  update public.user_quotas
  set reserved = greatest(0, reserved - v_job.reserved_units),
      used = used + greatest(0, v_charged - v_job.charged_units)
  where user_id = v_job.user_id and month_start = v_job.quota_month;

  update public.jobs
  set status = p_status,
      stage = p_status::text,
      progress = case when p_status = 'completed' then 100 else progress end,
      charged_units = v_charged,
      error = case when p_status = 'failed' then left(p_error, 2000) else null end,
      completed_at = now(),
      worker_id = null,
      lease_expires_at = null,
      next_retry_at = null,
      updated_at = now()
  where id = p_job_id;
end;
$$;

create or replace function public.schedule_job_retry(
  p_job_id uuid,
  p_status public.job_status,
  p_retry_seconds integer,
  p_failure_category text,
  p_safe_error text default null
)
returns public.jobs
language plpgsql
security definer set search_path = public
as $$
declare
  v_job public.jobs;
  v_retry_count integer;
begin
  if p_status not in ('recovering', 'waiting_resources', 'needs_input') then
    raise exception 'invalid recovery status';
  end if;

  select * into v_job from public.jobs where id = p_job_id for update;
  if not found then
    raise exception 'job not found';
  end if;
  if v_job.status in ('completed', 'cancelled') or v_job.cancellation_requested then
    return v_job;
  end if;

  v_retry_count := v_job.retry_count + 1;
  insert into public.job_attempts (
    job_id, attempt_number, failure_category, checkpoint_stage, safe_error
  ) values (
    p_job_id,
    v_retry_count,
    left(coalesce(p_failure_category, 'unknown'), 120),
    left(coalesce(v_job.stage, 'unknown'), 120),
    left(p_safe_error, 2000)
  );

  if p_status = 'needs_input' then
    perform public.finish_job(p_job_id, 'needs_input'::public.job_status, null);
  else
    update public.jobs
    set status = p_status,
        retry_count = v_retry_count,
        next_retry_at = now() + make_interval(secs => greatest(1, p_retry_seconds)),
        last_recovery_at = now(),
        worker_id = null,
        lease_expires_at = null,
        error = null,
        completed_at = null,
        updated_at = now()
    where id = p_job_id;

    insert into public.job_events (job_id, kind, message, data)
    values (
      p_job_id,
      case when p_status = 'waiting_resources' then 'waiting_resources' else 'auto_recovery' end,
      case
        when p_status = 'waiting_resources' then 'Waiting for resources before continuing'
        else 'Automatically recovering from the latest checkpoint'
      end,
      jsonb_build_object('retry_count', v_retry_count)
    );
  end if;

  select * into v_job from public.jobs where id = p_job_id;
  return v_job;
end;
$$;

create or replace function public.renew_job_lease(
  p_job_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns boolean
language plpgsql
security definer set search_path = public
as $$
begin
  update public.jobs
  set lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      updated_at = now()
  where id = p_job_id and worker_id = p_worker_id
    and status in ('parsing','problem_ready','searching','analyzing','rendering');
  return found;
end;
$$;

create or replace function public.request_job_cancellation(
  p_job_id uuid,
  p_user_id uuid
)
returns public.jobs
language plpgsql
security definer set search_path = public
as $$
declare
  v_job public.jobs;
begin
  select * into v_job
  from public.jobs
  where id = p_job_id and user_id = p_user_id
  for update;
  if not found then
    raise exception 'job not found';
  end if;
  if v_job.status in ('completed','cancelled','needs_input') then
    return v_job;
  end if;

  update public.jobs
  set cancellation_requested = true, updated_at = now()
  where id = p_job_id
  returning * into v_job;

  if v_job.status in ('queued','recovering','waiting_resources','failed','budget_blocked') then
    perform public.finish_job(p_job_id, 'cancelled'::public.job_status, null);
    select * into v_job from public.jobs where id = p_job_id;
  end if;
  return v_job;
end;
$$;

drop function if exists public.list_my_jobs(integer, integer, boolean);
create function public.list_my_jobs(
  p_limit integer default 20,
  p_offset integer default 0,
  p_favorites_only boolean default false
)
returns table (
  total_count bigint,
  id uuid,
  mode public.analysis_mode,
  max_rounds smallint,
  current_round smallint,
  status public.job_status,
  stage text,
  progress smallint,
  created_at timestamptz,
  completed_at timestamptz,
  is_favorite boolean,
  file_names text[],
  report_id uuid,
  retry_count integer,
  next_retry_at timestamptz,
  last_recovery_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    count(*) over() as total_count,
    jobs.id,
    jobs.mode,
    jobs.max_rounds,
    jobs.current_round,
    jobs.status,
    jobs.stage,
    jobs.progress,
    jobs.created_at,
    jobs.completed_at,
    jobs.is_favorite,
    coalesce((
      select array_agg(uploads.original_name order by job_files.position)
      from public.job_files
      join public.uploads on uploads.id = job_files.upload_id
      where job_files.job_id = jobs.id
    ), array[]::text[]) as file_names,
    (
      select reports.id from public.reports
      where reports.job_id = jobs.id limit 1
    ) as report_id,
    jobs.retry_count,
    jobs.next_retry_at,
    jobs.last_recovery_at
  from public.jobs
  where jobs.user_id = auth.uid()
    and (not p_favorites_only or jobs.is_favorite)
  order by jobs.is_favorite desc, jobs.created_at desc
  limit least(greatest(p_limit, 1), 100)
  offset greatest(p_offset, 0);
$$;

revoke all on function public.schedule_job_retry(uuid, public.job_status, integer, text, text)
from public, anon, authenticated;
grant execute on function public.schedule_job_retry(uuid, public.job_status, integer, text, text)
to service_role;
revoke all on function public.claim_next_job(text, integer) from public, anon, authenticated;
revoke all on function public.finish_job(uuid, public.job_status, text) from public, anon, authenticated;
revoke all on function public.renew_job_lease(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.request_job_cancellation(uuid, uuid) from public, anon, authenticated;
revoke all on function public.list_my_jobs(integer, integer, boolean) from public, anon;
grant execute on function public.claim_next_job(text, integer) to service_role;
grant execute on function public.finish_job(uuid, public.job_status, text) to service_role;
grant execute on function public.renew_job_lease(uuid, text, integer) to service_role;
grant execute on function public.request_job_cancellation(uuid, uuid) to service_role;
grant execute on function public.list_my_jobs(integer, integer, boolean) to authenticated;

update public.jobs jobs
set status = case
      when exists (
        select 1
        from public.job_files
        join public.uploads on uploads.id = job_files.upload_id
        where job_files.job_id = jobs.id
          and uploads.status <> 'deleted'
      ) then 'recovering'::public.job_status
      else 'needs_input'::public.job_status
    end,
    next_retry_at = case
      when exists (
        select 1
        from public.job_files
        join public.uploads on uploads.id = job_files.upload_id
        where job_files.job_id = jobs.id
          and uploads.status <> 'deleted'
      ) then now()
      else null
    end,
    completed_at = null,
    error = null,
    updated_at = now()
where jobs.status = 'failed';

update public.jobs
set status = 'waiting_resources'::public.job_status,
    next_retry_at = now(),
    completed_at = null,
    error = null,
    updated_at = now()
where status = 'budget_blocked';
