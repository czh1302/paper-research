create extension if not exists pgcrypto;

create type public.analysis_mode as enum ('single', 'multi');
create type public.job_status as enum (
  'queued', 'parsing', 'problem_ready', 'searching', 'analyzing', 'rendering',
  'completed', 'cancelled', 'failed', 'budget_blocked'
);

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.user_quotas (
  user_id uuid not null references auth.users(id) on delete cascade,
  month_start date not null default date_trunc('month', now())::date,
  allocation integer not null default 5 check (allocation >= 0),
  reserved integer not null default 0 check (reserved >= 0),
  used integer not null default 0 check (used >= 0),
  primary key (user_id, month_start)
);

create table public.uploads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  storage_path text not null unique,
  original_name text not null,
  size_bytes bigint not null check (size_bytes between 1 and 52428800),
  mime_type text not null default 'application/pdf' check (mime_type = 'application/pdf'),
  sha256 text,
  status text not null default 'pending' check (status in ('pending', 'uploaded', 'validated', 'deleted')),
  created_at timestamptz not null default now(),
  delete_after timestamptz not null default now() + interval '24 hours'
);

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  mode public.analysis_mode not null,
  max_rounds smallint not null default 1 check (max_rounds between 1 and 5),
  languages text[] not null default array['zh', 'en']::text[],
  status public.job_status not null default 'queued',
  stage text not null default 'queued',
  progress smallint not null default 0 check (progress between 0 and 100),
  current_round smallint not null default 0 check (current_round between 0 and 5),
  quota_month date not null default date_trunc('month', now())::date,
  reserved_units integer not null check (reserved_units >= 0),
  charged_units integer not null default 0,
  worker_id text,
  lease_expires_at timestamptz,
  checkpoint jsonb not null default '{}'::jsonb,
  cancellation_requested boolean not null default false,
  error text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now()
);

create table public.job_files (
  job_id uuid not null references public.jobs(id) on delete cascade,
  upload_id uuid not null references public.uploads(id) on delete restrict,
  position smallint not null,
  primary key (job_id, upload_id),
  unique (job_id, position)
);

create table public.job_events (
  id bigint generated always as identity primary key,
  job_id uuid not null references public.jobs(id) on delete cascade,
  kind text not null,
  message text not null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.problem_statements (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  paper_id text not null,
  content jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, paper_id)
);

create table public.search_runs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  round_number smallint not null check (round_number between 1 and 5),
  queries jsonb not null,
  analysis jsonb not null,
  created_at timestamptz not null default now(),
  unique (job_id, round_number)
);

create table public.candidate_papers (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  canonical_id text not null,
  content jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, canonical_id)
);

create table public.reports (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null unique references public.jobs(id) on delete cascade,
  content jsonb not null,
  markdown text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  delete_after timestamptz
);

create table public.share_tokens (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.reports(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create table public.provider_usage (
  id bigint generated always as identity primary key,
  job_id uuid references public.jobs(id) on delete set null,
  provider text not null,
  model text,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  requests integer not null default 1,
  estimated_cny numeric(12, 6) not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index jobs_queue_idx on public.jobs (created_at)
  where status in ('queued', 'parsing', 'problem_ready', 'searching', 'analyzing', 'rendering');
create index jobs_user_idx on public.jobs (user_id, created_at desc);
create index events_job_idx on public.job_events (job_id, created_at);
create index uploads_expiry_idx on public.uploads (delete_after) where status <> 'deleted';
create index reports_expiry_idx on public.reports (delete_after) where delete_after is not null;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email)
  on conflict (id) do nothing;
  insert into public.user_quotas (user_id, month_start)
  values (new.id, date_trunc('month', now())::date)
  on conflict (user_id, month_start) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute procedure public.handle_new_user();

create or replace function public.reserve_job(
  p_user_id uuid,
  p_mode public.analysis_mode,
  p_file_ids uuid[],
  p_max_rounds smallint,
  p_languages text[] default array['zh','en']::text[]
)
returns public.jobs
language plpgsql
security definer set search_path = public
as $$
declare
  v_count integer := coalesce(array_length(p_file_ids, 1), 0);
  v_units integer;
  v_quota public.user_quotas;
  v_job public.jobs;
  v_upload_count integer;
begin
  if p_max_rounds not between 1 and 5 then
    raise exception 'max_rounds must be between 1 and 5';
  end if;
  if (p_mode = 'single' and v_count <> 1) or (p_mode = 'multi' and v_count not between 2 and 5) then
    raise exception 'invalid PDF count for analysis mode';
  end if;
  if exists (
    select 1 from public.jobs where user_id = p_user_id
    and status in ('queued','parsing','problem_ready','searching','analyzing','rendering')
  ) then
    raise exception 'only one active job is allowed per user';
  end if;

  select count(*) into v_upload_count
  from public.uploads
  where id = any(p_file_ids) and user_id = p_user_id and status in ('uploaded','validated');
  if v_upload_count <> v_count then
    raise exception 'one or more uploads are missing or not owned by the user';
  end if;

  insert into public.user_quotas (user_id, month_start)
  values (p_user_id, date_trunc('month', now())::date)
  on conflict (user_id, month_start) do nothing;

  select * into v_quota from public.user_quotas
  where user_id = p_user_id and month_start = date_trunc('month', now())::date
  for update;
  v_units := v_count * p_max_rounds;
  if v_quota.allocation - v_quota.used - v_quota.reserved < v_units then
    raise exception 'insufficient analysis units';
  end if;

  update public.user_quotas set reserved = reserved + v_units
  where user_id = p_user_id and month_start = date_trunc('month', now())::date;

  insert into public.jobs (user_id, mode, max_rounds, languages, reserved_units)
  values (p_user_id, p_mode, p_max_rounds, p_languages, v_units)
  returning * into v_job;

  insert into public.job_files (job_id, upload_id, position)
  select v_job.id, item.upload_id, item.position::smallint
  from unnest(p_file_ids) with ordinality as item(upload_id, position);

  insert into public.job_events (job_id, kind, message)
  values (v_job.id, 'queued', 'Job queued');
  return v_job;
end;
$$;

create or replace function public.claim_next_job(p_worker_id text, p_lease_seconds integer default 300)
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
          status in ('parsing','problem_ready','searching','analyzing','rendering')
          and lease_expires_at < now()
        )
      )
    order by created_at
    for update skip locked
    limit 1
  )
  update public.jobs j
  set worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      started_at = coalesce(started_at, now()),
      status = case when status = 'queued' then 'parsing'::public.job_status else status end,
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
  if p_status not in ('completed','cancelled','failed','budget_blocked') then
    raise exception 'finish_job requires a terminal status';
  end if;
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.status in ('completed','cancelled','failed','budget_blocked') then
    return;
  end if;
  select count(*) into v_file_count from public.job_files where job_id = p_job_id;
  v_charged := least(v_job.reserved_units, v_file_count * v_job.current_round);

  update public.user_quotas
  set reserved = greatest(0, reserved - v_job.reserved_units), used = used + v_charged
  where user_id = v_job.user_id and month_start = v_job.quota_month;

  update public.jobs
  set status = p_status,
      stage = p_status::text,
      progress = case when p_status = 'completed' then 100 else progress end,
      charged_units = v_charged,
      error = left(p_error, 2000),
      completed_at = now(),
      lease_expires_at = null,
      updated_at = now()
  where id = p_job_id;
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
  if v_job.status in ('completed','cancelled','failed','budget_blocked') then
    return v_job;
  end if;

  update public.jobs
  set cancellation_requested = true, updated_at = now()
  where id = p_job_id
  returning * into v_job;

  if v_job.status = 'queued' then
    perform public.finish_job(p_job_id, 'cancelled'::public.job_status, null);
    select * into v_job from public.jobs where id = p_job_id;
  end if;
  return v_job;
end;
$$;

create or replace function public.claim_expired_storage()
returns table(kind text, record_id uuid, storage_path text)
language plpgsql
security definer set search_path = public
as $$
begin
  return query
  select 'upload'::text, uploads.id, uploads.storage_path
  from public.uploads
  where delete_after < now() and status <> 'deleted';

  return query
  delete from public.reports
  where delete_after < now()
  returning 'report'::text, reports.id, null::text;
end;
$$;

create or replace function public.current_month_provider_spend()
returns numeric
language sql
security definer set search_path = public
as $$
  select coalesce(sum(estimated_cny), 0)
  from public.provider_usage
  where created_at >= date_trunc('month', now());
$$;

revoke all on function public.reserve_job(uuid, public.analysis_mode, uuid[], smallint, text[]) from public, anon, authenticated;
revoke all on function public.claim_next_job(text, integer) from public, anon, authenticated;
revoke all on function public.finish_job(uuid, public.job_status, text) from public, anon, authenticated;
revoke all on function public.current_month_provider_spend() from public, anon, authenticated;
revoke all on function public.renew_job_lease(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.request_job_cancellation(uuid, uuid) from public, anon, authenticated;
revoke all on function public.claim_expired_storage() from public, anon, authenticated;
grant execute on function public.reserve_job(uuid, public.analysis_mode, uuid[], smallint, text[]) to service_role;
grant execute on function public.claim_next_job(text, integer) to service_role;
grant execute on function public.finish_job(uuid, public.job_status, text) to service_role;
grant execute on function public.current_month_provider_spend() to service_role;
grant execute on function public.renew_job_lease(uuid, text, integer) to service_role;
grant execute on function public.request_job_cancellation(uuid, uuid) to service_role;
grant execute on function public.claim_expired_storage() to service_role;

alter table public.profiles enable row level security;
alter table public.user_quotas enable row level security;
alter table public.uploads enable row level security;
alter table public.jobs enable row level security;
alter table public.job_files enable row level security;
alter table public.job_events enable row level security;
alter table public.problem_statements enable row level security;
alter table public.search_runs enable row level security;
alter table public.candidate_papers enable row level security;
alter table public.reports enable row level security;
alter table public.share_tokens enable row level security;
alter table public.provider_usage enable row level security;

create policy "profiles_select_own" on public.profiles for select using (id = auth.uid());
create policy "profiles_update_own" on public.profiles for update using (id = auth.uid());
create policy "quotas_select_own" on public.user_quotas for select using (user_id = auth.uid());
create policy "uploads_select_own" on public.uploads for select using (user_id = auth.uid());
create policy "jobs_select_own" on public.jobs for select using (user_id = auth.uid());
create policy "job_files_select_own" on public.job_files for select using (
  exists (select 1 from public.jobs where jobs.id = job_files.job_id and jobs.user_id = auth.uid())
);
create policy "events_select_own" on public.job_events for select using (
  exists (select 1 from public.jobs where jobs.id = job_events.job_id and jobs.user_id = auth.uid())
);
create policy "problems_select_own" on public.problem_statements for select using (
  exists (select 1 from public.jobs where jobs.id = problem_statements.job_id and jobs.user_id = auth.uid())
);
create policy "search_select_own" on public.search_runs for select using (
  exists (select 1 from public.jobs where jobs.id = search_runs.job_id and jobs.user_id = auth.uid())
);
create policy "candidates_select_own" on public.candidate_papers for select using (
  exists (select 1 from public.jobs where jobs.id = candidate_papers.job_id and jobs.user_id = auth.uid())
);
create policy "reports_select_own" on public.reports for select using (
  exists (select 1 from public.jobs where jobs.id = reports.job_id and jobs.user_id = auth.uid())
);
create policy "shares_select_own" on public.share_tokens for select using (user_id = auth.uid());
create policy "usage_select_own" on public.provider_usage for select using (
  exists (select 1 from public.jobs where jobs.id = provider_usage.job_id and jobs.user_id = auth.uid())
);

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('papers', 'papers', false, 52428800, array['application/pdf'])
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

create policy "paper_objects_select_own" on storage.objects for select to authenticated
using (bucket_id = 'papers' and (storage.foldername(name))[1] = auth.uid()::text);

alter publication supabase_realtime add table public.jobs;
alter publication supabase_realtime add table public.job_events;
alter publication supabase_realtime add table public.reports;
