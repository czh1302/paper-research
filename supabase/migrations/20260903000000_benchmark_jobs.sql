-- Service-role-only, idempotent cold benchmark submissions.
--
-- The public product keeps the one-active-job-per-user rule in reserve_job().
-- A benchmark run needs several queued jobs for the same owner so two isolated
-- workers can process the fixed corpus concurrently.  The nullable identity
-- below also makes supervisor restarts safe without changing ordinary jobs.

alter table public.jobs
  add column if not exists benchmark_run_id uuid,
  add column if not exists benchmark_paper_id text;

alter table public.jobs
  drop constraint if exists jobs_benchmark_identity_complete,
  add constraint jobs_benchmark_identity_complete check (
    (benchmark_run_id is null and benchmark_paper_id is null)
    or (
      benchmark_run_id is not null
      and benchmark_paper_id is not null
      and char_length(benchmark_paper_id) between 1 and 120
    )
  );

create unique index if not exists jobs_benchmark_run_paper_unique
  on public.jobs (benchmark_run_id, benchmark_paper_id)
  where benchmark_run_id is not null;

create or replace function public.reserve_benchmark_job(
  p_owner_job_id uuid,
  p_upload_id uuid,
  p_benchmark_run_id uuid,
  p_paper_id text,
  p_max_rounds smallint default 1,
  p_languages text[] default array['zh','en']::text[]
)
returns public.jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner_id uuid;
  v_upload public.uploads;
  v_job public.jobs;
begin
  if p_benchmark_run_id is null then
    raise exception 'benchmark run id is required';
  end if;
  if char_length(coalesce(p_paper_id, '')) not between 1 and 120 then
    raise exception 'invalid benchmark paper id';
  end if;
  if p_max_rounds not between 1 and 5 then
    raise exception 'max_rounds must be between 1 and 5';
  end if;

  select jobs.user_id into v_owner_id
  from public.jobs
  where jobs.id = p_owner_job_id;
  if v_owner_id is null then
    raise exception 'owner source job was not found';
  end if;

  select * into v_upload
  from public.uploads
  where uploads.id = p_upload_id
    and uploads.user_id = v_owner_id
    and uploads.status in ('uploaded', 'validated')
  for update;
  if not found then
    raise exception 'benchmark upload is missing or not owned by the source job user';
  end if;

  select * into v_job
  from public.jobs
  where jobs.benchmark_run_id = p_benchmark_run_id
    and jobs.benchmark_paper_id = p_paper_id;
  if found then
    if v_job.user_id <> v_owner_id then
      raise exception 'benchmark identity belongs to a different owner';
    end if;
    return v_job;
  end if;

  insert into public.jobs (
    user_id,
    mode,
    max_rounds,
    languages,
    reserved_units,
    research_brief,
    checkpoint,
    benchmark_run_id,
    benchmark_paper_id
  ) values (
    v_owner_id,
    'single'::public.analysis_mode,
    p_max_rounds,
    p_languages,
    0,
    '',
    jsonb_build_object(
      'benchmark', jsonb_build_object(
        'run_id', p_benchmark_run_id::text,
        'paper_id', p_paper_id,
        'cold', true
      )
    ),
    p_benchmark_run_id,
    p_paper_id
  )
  on conflict (benchmark_run_id, benchmark_paper_id)
    where benchmark_run_id is not null
  do nothing
  returning * into v_job;

  if v_job.id is null then
    select * into v_job
    from public.jobs
    where jobs.benchmark_run_id = p_benchmark_run_id
      and jobs.benchmark_paper_id = p_paper_id;
    return v_job;
  end if;

  insert into public.job_files (job_id, upload_id, position)
  values (v_job.id, p_upload_id, 1);

  update public.uploads
  set delete_after = null
  where uploads.id = p_upload_id;

  insert into public.job_events (job_id, kind, message, data)
  values (
    v_job.id,
    'queued',
    'Benchmark job queued',
    jsonb_build_object(
      'benchmark_run_id', p_benchmark_run_id::text,
      'paper_id', p_paper_id
    )
  );
  return v_job;
end;
$$;

revoke all on function public.reserve_benchmark_job(
  uuid, uuid, uuid, text, smallint, text[]
) from public, anon, authenticated;
grant execute on function public.reserve_benchmark_job(
  uuid, uuid, uuid, text, smallint, text[]
) to service_role;
