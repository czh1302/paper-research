alter table public.profiles
  add column if not exists deletion_requested_at timestamptz;

alter table public.jobs
  add column if not exists admin_deletion_requested_at timestamptz;

create or replace function public.protect_profile_deletion_marker()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if auth.role() <> 'service_role'
    and new.deletion_requested_at is distinct from old.deletion_requested_at then
    raise insufficient_privilege using message = 'account deletion state is managed by the service';
  end if;
  return new;
end;
$$;

drop trigger if exists protect_profile_deletion_marker on public.profiles;
create trigger protect_profile_deletion_marker
before update of deletion_requested_at on public.profiles
for each row execute function public.protect_profile_deletion_marker();

create table if not exists public.admin_deletion_requests (
  id uuid primary key default gen_random_uuid(),
  target_kind text not null check (target_kind in ('job', 'user')),
  target_id uuid not null,
  requested_by uuid references public.admin_users(user_id) on delete set null,
  state text not null default 'pending' check (state in ('pending', 'processing', 'completed')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  next_attempt_at timestamptz not null default now(),
  lease_expires_at timestamptz,
  worker_id text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create unique index if not exists admin_deletion_requests_active_target_idx
on public.admin_deletion_requests (target_kind, target_id)
where state in ('pending', 'processing');

create index if not exists admin_deletion_requests_queue_idx
on public.admin_deletion_requests (next_attempt_at, created_at)
where state in ('pending', 'processing');

alter table public.admin_deletion_requests enable row level security;

drop policy if exists "admin_deletion_requests_select_admin" on public.admin_deletion_requests;
create policy "admin_deletion_requests_select_admin"
on public.admin_deletion_requests for select to authenticated
using ((select public.is_admin()));

grant select on public.admin_deletion_requests to authenticated;
grant all on public.admin_deletion_requests to service_role;

create or replace function public.admin_request_job_deletion(p_job_id uuid, p_requester_id uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job public.jobs;
begin
  if not exists (select 1 from public.admin_users where user_id = p_requester_id) then
    raise insufficient_privilege using message = 'administrator access required';
  end if;

  select * into v_job from public.jobs where id = p_job_id for update;
  if not found then
    if exists (
      select 1 from public.admin_deletion_requests
      where target_kind = 'job' and target_id = p_job_id
    ) then
      return 'deleted';
    end if;
    raise no_data_found using message = 'job not found';
  end if;

  insert into public.admin_deletion_requests (target_kind, target_id, requested_by)
  values ('job', p_job_id, p_requester_id)
  on conflict (target_kind, target_id) where state in ('pending', 'processing')
  do update set next_attempt_at = least(public.admin_deletion_requests.next_attempt_at, now()),
                updated_at = now();

  update public.jobs
  set admin_deletion_requested_at = coalesce(admin_deletion_requested_at, now()),
      cancellation_requested = true,
      updated_at = now()
  where id = p_job_id;

  perform public.request_job_cancellation(p_job_id, v_job.user_id);
  return 'pending';
end;
$$;

create or replace function public.admin_request_user_deletion(
  p_user_id uuid,
  p_confirmation_email text,
  p_requester_id uuid
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_email text;
  v_job record;
begin
  if not exists (select 1 from public.admin_users where user_id = p_requester_id) then
    raise insufficient_privilege using message = 'administrator access required';
  end if;
  if p_user_id = p_requester_id then
    raise check_violation using message = 'administrators cannot delete their own account';
  end if;
  if exists (select 1 from public.admin_users where user_id = p_user_id) then
    raise check_violation using message = 'administrator accounts are protected';
  end if;

  select email::text into v_email from auth.users where id = p_user_id;
  if not found then
    if exists (
      select 1 from public.admin_deletion_requests
      where target_kind = 'user' and target_id = p_user_id
    ) then
      return 'deleted';
    end if;
    raise no_data_found using message = 'user not found';
  end if;
  if lower(trim(coalesce(p_confirmation_email, ''))) <> lower(v_email) then
    raise check_violation using message = 'confirmation email does not match';
  end if;

  insert into public.admin_deletion_requests (target_kind, target_id, requested_by)
  values ('user', p_user_id, p_requester_id)
  on conflict (target_kind, target_id) where state in ('pending', 'processing')
  do update set next_attempt_at = least(public.admin_deletion_requests.next_attempt_at, now()),
                updated_at = now();

  update public.profiles
  set deletion_requested_at = coalesce(deletion_requested_at, now())
  where id = p_user_id;

  update public.jobs
  set admin_deletion_requested_at = coalesce(admin_deletion_requested_at, now()),
      cancellation_requested = true,
      updated_at = now()
  where user_id = p_user_id;

  for v_job in select id from public.jobs where user_id = p_user_id loop
    perform public.request_job_cancellation(v_job.id, p_user_id);
  end loop;
  return 'pending';
end;
$$;

create or replace function public.claim_admin_deletion_request(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.admin_deletion_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_request public.admin_deletion_requests;
begin
  select * into v_request
  from public.admin_deletion_requests
  where state in ('pending', 'processing')
    and next_attempt_at <= now()
    and (state = 'pending' or lease_expires_at is null or lease_expires_at <= now())
  order by created_at
  for update skip locked
  limit 1;

  if not found then
    return;
  end if;

  update public.admin_deletion_requests
  set state = 'processing',
      worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      attempt_count = attempt_count + 1,
      updated_at = now()
  where id = v_request.id
  returning * into v_request;
  return next v_request;
end;
$$;

create or replace function public.finish_admin_deletion_request(
  p_request_id uuid,
  p_worker_id text,
  p_success boolean,
  p_retry_seconds integer default 60,
  p_error text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_success then
    update public.admin_deletion_requests
    set state = 'completed', worker_id = null, lease_expires_at = null,
        last_error = null, completed_at = now(), updated_at = now()
    where id = p_request_id and worker_id = p_worker_id;
  else
    update public.admin_deletion_requests
    set state = 'pending', worker_id = null, lease_expires_at = null,
        next_attempt_at = now() + make_interval(secs => greatest(p_retry_seconds, 10)),
        last_error = left(coalesce(p_error, 'cleanup interrupted'), 2000), updated_at = now()
    where id = p_request_id and worker_id = p_worker_id;
  end if;
end;
$$;

create or replace function public.admin_deletion_target_ready(
  p_target_kind text,
  p_target_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_target_kind = 'job' then
    return not exists (
      select 1 from public.jobs
      where id = p_target_id and lease_expires_at > now()
    );
  elsif p_target_kind = 'user' then
    return not exists (
      select 1 from public.jobs
      where user_id = p_target_id and lease_expires_at > now()
    );
  end if;
  return false;
end;
$$;

create or replace function public.purge_job_records(p_job_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_upload_ids uuid[];
begin
  select coalesce(array_agg(upload_id), array[]::uuid[])
  into v_upload_ids
  from public.job_files
  where job_id = p_job_id;

  delete from public.jobs where id = p_job_id;

  delete from public.uploads
  where id = any(v_upload_ids)
    and not exists (
      select 1 from public.job_files where job_files.upload_id = uploads.id
    );
end;
$$;

drop function if exists public.admin_list_users(integer, integer);
create function public.admin_list_users(
  p_limit integer default 100,
  p_offset integer default 0
)
returns table (
  total_count bigint, user_id uuid, email text, created_at timestamptz,
  last_sign_in_at timestamptz, job_count bigint, active_job_count bigint,
  completed_job_count bigint, allocation integer, used integer, reserved integer,
  is_admin boolean
)
language plpgsql security definer set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise insufficient_privilege using message = 'administrator access required';
  end if;
  return query
  select count(*) over(), users.id, users.email::text, users.created_at, users.last_sign_in_at,
    (select count(*) from public.jobs where jobs.user_id = users.id and jobs.admin_deletion_requested_at is null),
    (select count(*) from public.jobs where jobs.user_id = users.id and jobs.admin_deletion_requested_at is null
      and jobs.status in ('queued','parsing','problem_ready','searching','analyzing','rendering','recovering','waiting_resources')),
    (select count(*) from public.jobs where jobs.user_id = users.id and jobs.admin_deletion_requested_at is null and jobs.status = 'completed'),
    coalesce((select quotas.allocation from public.user_quotas quotas where quotas.user_id = users.id and quotas.month_start = date_trunc('month', now())::date limit 1), 5)::integer,
    coalesce((select quotas.used from public.user_quotas quotas where quotas.user_id = users.id and quotas.month_start = date_trunc('month', now())::date limit 1), 0)::integer,
    coalesce((select quotas.reserved from public.user_quotas quotas where quotas.user_id = users.id and quotas.month_start = date_trunc('month', now())::date limit 1), 0)::integer,
    exists (select 1 from public.admin_users where admin_users.user_id = users.id)
  from auth.users users
  left join public.profiles profiles on profiles.id = users.id
  where profiles.deletion_requested_at is null
  order by users.created_at desc
  limit least(greatest(p_limit, 1), 500) offset greatest(p_offset, 0);
end;
$$;

create or replace function public.admin_list_jobs(
  p_limit integer default 100,
  p_offset integer default 0
)
returns table (
  total_count bigint, job_id uuid, user_id uuid, user_email text,
  mode public.analysis_mode, status public.job_status, stage text, progress smallint,
  max_rounds smallint, current_round smallint, reserved_units integer,
  charged_units integer, cancellation_requested boolean, error text,
  created_at timestamptz, started_at timestamptz, completed_at timestamptz,
  updated_at timestamptz, file_names text[], report_id uuid
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
    jobs.started_at, jobs.completed_at, jobs.updated_at,
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

revoke all on function public.admin_request_job_deletion(uuid, uuid) from public, anon, authenticated;
revoke all on function public.admin_request_user_deletion(uuid, text, uuid) from public, anon, authenticated;
revoke all on function public.claim_admin_deletion_request(text, integer) from public, anon, authenticated;
revoke all on function public.finish_admin_deletion_request(uuid, text, boolean, integer, text) from public, anon, authenticated;
revoke all on function public.admin_deletion_target_ready(text, uuid) from public, anon, authenticated;
revoke all on function public.purge_job_records(uuid) from public, anon, authenticated;
revoke all on function public.admin_list_users(integer, integer) from public, anon;
grant execute on function public.admin_request_job_deletion(uuid, uuid) to service_role;
grant execute on function public.admin_request_user_deletion(uuid, text, uuid) to service_role;
grant execute on function public.claim_admin_deletion_request(text, integer) to service_role;
grant execute on function public.finish_admin_deletion_request(uuid, text, boolean, integer, text) to service_role;
grant execute on function public.admin_deletion_target_ready(text, uuid) to service_role;
grant execute on function public.purge_job_records(uuid) to service_role;
grant execute on function public.admin_list_users(integer, integer) to authenticated, service_role;
