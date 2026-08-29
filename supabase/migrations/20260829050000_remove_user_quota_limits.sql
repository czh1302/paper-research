create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

create or replace function public.reserve_job(
  p_user_id uuid,
  p_mode public.analysis_mode,
  p_file_ids uuid[],
  p_max_rounds smallint,
  p_languages text[] default array['zh','en']::text[]
)
returns public.jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer := coalesce(array_length(p_file_ids, 1), 0);
  v_job public.jobs;
  v_upload_count integer;
begin
  if p_max_rounds not between 1 and 5 then
    raise exception 'max_rounds must be between 1 and 5';
  end if;
  if (p_mode = 'single' and v_count <> 1)
    or (p_mode = 'multi' and v_count not between 2 and 5) then
    raise exception 'invalid PDF count for analysis mode';
  end if;
  if exists (
    select 1
    from public.jobs
    where user_id = p_user_id
      and status in ('queued','parsing','problem_ready','searching','analyzing','rendering')
  ) then
    raise exception 'only one active job is allowed per user';
  end if;

  select count(*) into v_upload_count
  from public.uploads
  where id = any(p_file_ids)
    and user_id = p_user_id
    and status in ('uploaded','validated');
  if v_upload_count <> v_count then
    raise exception 'one or more uploads are missing or not owned by the user';
  end if;

  insert into public.jobs (user_id, mode, max_rounds, languages, reserved_units)
  values (p_user_id, p_mode, p_max_rounds, p_languages, 0)
  returning * into v_job;

  insert into public.job_files (job_id, upload_id, position)
  select v_job.id, item.upload_id, item.position::smallint
  from unnest(p_file_ids) with ordinality as item(upload_id, position);

  insert into public.job_events (job_id, kind, message)
  values (v_job.id, 'queued', 'Job queued');
  return v_job;
end;
$$;

create or replace function public.finish_job(
  p_job_id uuid,
  p_status public.job_status,
  p_error text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job public.jobs;
begin
  if p_status not in ('completed','cancelled','failed','budget_blocked') then
    raise exception 'finish_job requires a terminal status';
  end if;
  select * into v_job
  from public.jobs
  where id = p_job_id
  for update;
  if not found or v_job.status in ('completed','cancelled','failed','budget_blocked') then
    return;
  end if;

  update public.jobs
  set status = p_status,
      stage = p_status::text,
      progress = case when p_status = 'completed' then 100 else progress end,
      reserved_units = 0,
      charged_units = 0,
      error = left(p_error, 2000),
      completed_at = now(),
      lease_expires_at = null,
      updated_at = now()
  where id = p_job_id;
end;
$$;

-- Historical rows are kept for migration compatibility, but they no longer gate any operation.
update public.user_quotas set reserved = 0;
