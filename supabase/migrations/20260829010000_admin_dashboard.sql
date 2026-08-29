create table public.admin_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users(id) on delete set null
);

alter table public.admin_users enable row level security;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.admin_users
    where user_id = auth.uid()
  );
$$;

revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated, service_role;

create policy "admin_users_select_self"
on public.admin_users
for select
to authenticated
using (user_id = auth.uid());

grant select on public.admin_users to authenticated;
grant all on public.admin_users to service_role;

create policy "profiles_select_admin" on public.profiles
for select to authenticated using ((select public.is_admin()));
create policy "quotas_select_admin" on public.user_quotas
for select to authenticated using ((select public.is_admin()));
create policy "uploads_select_admin" on public.uploads
for select to authenticated using ((select public.is_admin()));
create policy "jobs_select_admin" on public.jobs
for select to authenticated using ((select public.is_admin()));
create policy "job_files_select_admin" on public.job_files
for select to authenticated using ((select public.is_admin()));
create policy "events_select_admin" on public.job_events
for select to authenticated using ((select public.is_admin()));
create policy "problems_select_admin" on public.problem_statements
for select to authenticated using ((select public.is_admin()));
create policy "search_select_admin" on public.search_runs
for select to authenticated using ((select public.is_admin()));
create policy "candidates_select_admin" on public.candidate_papers
for select to authenticated using ((select public.is_admin()));
create policy "reports_select_admin" on public.reports
for select to authenticated using ((select public.is_admin()));
create policy "shares_select_admin" on public.share_tokens
for select to authenticated using ((select public.is_admin()));
create policy "usage_select_admin" on public.provider_usage
for select to authenticated using ((select public.is_admin()));

create or replace function public.admin_list_users(
  p_limit integer default 100,
  p_offset integer default 0
)
returns table (
  total_count bigint,
  user_id uuid,
  email text,
  created_at timestamptz,
  last_sign_in_at timestamptz,
  job_count bigint,
  active_job_count bigint,
  completed_job_count bigint,
  allocation integer,
  used integer,
  reserved integer
)
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise insufficient_privilege using message = 'administrator access required';
  end if;

  return query
  select
    count(*) over() as total_count,
    users.id as user_id,
    users.email::text,
    users.created_at,
    users.last_sign_in_at,
    (
      select count(*)
      from public.jobs
      where jobs.user_id = users.id
    ) as job_count,
    (
      select count(*)
      from public.jobs
      where jobs.user_id = users.id
        and jobs.status in ('queued', 'parsing', 'problem_ready', 'searching', 'analyzing', 'rendering')
    ) as active_job_count,
    (
      select count(*)
      from public.jobs
      where jobs.user_id = users.id and jobs.status = 'completed'
    ) as completed_job_count,
    coalesce((
      select quotas.allocation
      from public.user_quotas as quotas
      where quotas.user_id = users.id
        and quotas.month_start = date_trunc('month', now())::date
      limit 1
    ), 5)::integer as allocation,
    coalesce((
      select quotas.used
      from public.user_quotas as quotas
      where quotas.user_id = users.id
        and quotas.month_start = date_trunc('month', now())::date
      limit 1
    ), 0)::integer as used,
    coalesce((
      select quotas.reserved
      from public.user_quotas as quotas
      where quotas.user_id = users.id
        and quotas.month_start = date_trunc('month', now())::date
      limit 1
    ), 0)::integer as reserved
  from auth.users as users
  order by users.created_at desc
  limit least(greatest(p_limit, 1), 500)
  offset greatest(p_offset, 0);
end;
$$;

create or replace function public.admin_list_jobs(
  p_limit integer default 100,
  p_offset integer default 0
)
returns table (
  total_count bigint,
  job_id uuid,
  user_id uuid,
  user_email text,
  mode public.analysis_mode,
  status public.job_status,
  stage text,
  progress smallint,
  max_rounds smallint,
  current_round smallint,
  reserved_units integer,
  charged_units integer,
  cancellation_requested boolean,
  error text,
  created_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz,
  file_names text[],
  report_id uuid
)
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise insufficient_privilege using message = 'administrator access required';
  end if;

  return query
  select
    count(*) over() as total_count,
    jobs.id as job_id,
    jobs.user_id,
    users.email::text as user_email,
    jobs.mode,
    jobs.status,
    jobs.stage,
    jobs.progress,
    jobs.max_rounds,
    jobs.current_round,
    jobs.reserved_units,
    jobs.charged_units,
    jobs.cancellation_requested,
    jobs.error,
    jobs.created_at,
    jobs.started_at,
    jobs.completed_at,
    jobs.updated_at,
    coalesce((
      select array_agg(uploads.original_name order by job_files.position)
      from public.job_files
      join public.uploads on uploads.id = job_files.upload_id
      where job_files.job_id = jobs.id
    ), array[]::text[]) as file_names,
    (
      select reports.id
      from public.reports
      where reports.job_id = jobs.id
      limit 1
    ) as report_id
  from public.jobs as jobs
  join auth.users as users on users.id = jobs.user_id
  order by jobs.created_at desc
  limit least(greatest(p_limit, 1), 500)
  offset greatest(p_offset, 0);
end;
$$;

revoke all on function public.admin_list_users(integer, integer) from public, anon;
revoke all on function public.admin_list_jobs(integer, integer) from public, anon;
grant execute on function public.admin_list_users(integer, integer) to authenticated, service_role;
grant execute on function public.admin_list_jobs(integer, integer) to authenticated, service_role;
