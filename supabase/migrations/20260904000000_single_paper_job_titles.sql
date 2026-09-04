-- Keep user-facing jobs single-paper while preserving legacy multi-paper rows,
-- and cache the extracted paper title for inexpensive task-list rendering.

alter table public.jobs
  add column if not exists paper_title text;

update public.jobs as jobs
set paper_title = titles.paper_title
from (
  select distinct on (statements.job_id)
    statements.job_id,
    nullif(btrim(statements.content->>'title'), '') as paper_title
  from public.problem_statements as statements
  join public.jobs as source_jobs on source_jobs.id = statements.job_id
  where source_jobs.mode = 'single'
    and statements.paper_id <> '__joint__'
    and nullif(btrim(statements.content->>'title'), '') is not null
  order by statements.job_id, statements.created_at, statements.id
) as titles
where jobs.id = titles.job_id
  and jobs.mode = 'single'
  and (jobs.paper_title is null or btrim(jobs.paper_title) = '');

create or replace function public.sync_single_job_paper_title()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_title text := nullif(btrim(new.content->>'title'), '');
begin
  if new.paper_id <> '__joint__' and v_title is not null then
    update public.jobs
    set paper_title = v_title
    where id = new.job_id and mode = 'single';
  end if;
  return new;
end;
$$;

drop trigger if exists sync_single_job_paper_title on public.problem_statements;
create trigger sync_single_job_paper_title
after insert or update of content on public.problem_statements
for each row execute function public.sync_single_job_paper_title();

revoke all on function public.sync_single_job_paper_title() from public, anon, authenticated;

drop function if exists public.list_my_jobs(integer, integer, boolean);
create function public.list_my_jobs(
  p_limit integer default 20,
  p_offset integer default 0,
  p_favorites_only boolean default false
)
returns table (
  total_count bigint, id uuid, mode public.analysis_mode, max_rounds smallint,
  current_round smallint, status public.job_status, stage text, progress smallint,
  created_at timestamptz, completed_at timestamptz, is_favorite boolean,
  paper_title text, file_names text[], report_id uuid, retry_count integer,
  next_retry_at timestamptz, last_recovery_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select count(*) over(), jobs.id, jobs.mode, jobs.max_rounds, jobs.current_round,
    jobs.status, jobs.stage, jobs.progress, jobs.created_at, jobs.completed_at,
    jobs.is_favorite, jobs.paper_title,
    coalesce((
      select array_agg(uploads.original_name order by job_files.position)
      from public.job_files join public.uploads on uploads.id = job_files.upload_id
      where job_files.job_id = jobs.id
    ), array[]::text[]),
    (select reports.id from public.reports where reports.job_id = jobs.id limit 1),
    jobs.retry_count, jobs.next_retry_at, jobs.last_recovery_at
  from public.jobs
  where jobs.user_id = auth.uid()
    and jobs.admin_deletion_requested_at is null
    and (not p_favorites_only or jobs.is_favorite)
  order by jobs.is_favorite desc, jobs.created_at desc
  limit least(greatest(p_limit, 1), 100) offset greatest(p_offset, 0);
$$;

revoke all on function public.list_my_jobs(integer, integer, boolean) from public, anon;
grant execute on function public.list_my_jobs(integer, integer, boolean) to authenticated;

drop function if exists public.admin_list_jobs(integer, integer);
create function public.admin_list_jobs(
  p_limit integer default 100,
  p_offset integer default 0
)
returns table (
  total_count bigint, job_id uuid, user_id uuid, user_email text,
  mode public.analysis_mode, status public.job_status, stage text, progress smallint,
  max_rounds smallint, current_round smallint, reserved_units integer,
  charged_units integer, cancellation_requested boolean, error text,
  created_at timestamptz, started_at timestamptz, completed_at timestamptz,
  updated_at timestamptz, paper_title text, file_names text[], report_id uuid
)
language plpgsql security definer set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise insufficient_privilege using message = 'administrator access required';
  end if;
  return query
  select count(*) over(), jobs.id, jobs.user_id, users.email::text, jobs.mode, jobs.status,
    jobs.stage, jobs.progress, jobs.max_rounds, jobs.current_round, jobs.reserved_units,
    jobs.charged_units, jobs.cancellation_requested, jobs.error, jobs.created_at,
    jobs.started_at, jobs.completed_at, jobs.updated_at, jobs.paper_title,
    coalesce((select array_agg(uploads.original_name order by job_files.position)
      from public.job_files join public.uploads on uploads.id = job_files.upload_id
      where job_files.job_id = jobs.id), array[]::text[]),
    (select reports.id from public.reports where reports.job_id = jobs.id limit 1)
  from public.jobs jobs join auth.users users on users.id = jobs.user_id
  where jobs.admin_deletion_requested_at is null
    and not exists (select 1 from public.profiles where profiles.id = jobs.user_id and profiles.deletion_requested_at is not null)
  order by jobs.created_at desc
  limit least(greatest(p_limit, 1), 500) offset greatest(p_offset, 0);
end;
$$;

revoke all on function public.admin_list_jobs(integer, integer) from public, anon;
grant execute on function public.admin_list_jobs(integer, integer) to authenticated, service_role;
