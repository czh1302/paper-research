-- V4 report summaries, retained evidence assets, favorites, and paginated jobs.

alter table public.uploads alter column delete_after drop not null;
alter table public.jobs add column if not exists is_favorite boolean not null default false;
alter table public.jobs add column if not exists research_brief text not null default ''
  check (char_length(research_brief) <= 2000);
alter table public.reports add column if not exists summary jsonb;

create table if not exists public.report_evidence_assets (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  report_id uuid references public.reports(id) on delete cascade,
  upload_id uuid references public.uploads(id) on delete set null,
  paper_id text not null,
  source_kind text not null check (source_kind in ('input', 'external')),
  storage_path text not null unique,
  original_name text not null,
  sha256 text,
  source_url text,
  license text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, paper_id, source_kind)
);

create index if not exists report_evidence_assets_job_idx
  on public.report_evidence_assets (job_id, created_at);
create index if not exists report_evidence_assets_report_idx
  on public.report_evidence_assets (report_id) where report_id is not null;
create index if not exists jobs_user_favorite_idx
  on public.jobs (user_id, is_favorite desc, created_at desc);

create table if not exists public.storage_deletion_queue (
  id uuid primary key default gen_random_uuid(),
  storage_path text not null unique,
  created_at timestamptz not null default now()
);

create or replace function public.queue_deleted_storage_object()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.storage_deletion_queue (storage_path)
  values (old.storage_path)
  on conflict (storage_path) do nothing;
  return old;
end;
$$;

drop trigger if exists queue_deleted_evidence_asset on public.report_evidence_assets;
create trigger queue_deleted_evidence_asset
before delete on public.report_evidence_assets
for each row execute function public.queue_deleted_storage_object();

drop trigger if exists queue_deleted_upload on public.uploads;
create trigger queue_deleted_upload
before delete on public.uploads
for each row execute function public.queue_deleted_storage_object();

revoke all on public.storage_deletion_queue from public, anon, authenticated;
grant all on public.storage_deletion_queue to service_role;

alter table public.report_evidence_assets enable row level security;

create policy "evidence_assets_select_own" on public.report_evidence_assets
for select to authenticated using (
  exists (
    select 1 from public.jobs
    where jobs.id = report_evidence_assets.job_id and jobs.user_id = auth.uid()
  )
);

create policy "evidence_assets_select_admin" on public.report_evidence_assets
for select to authenticated using ((select public.is_admin()));

grant select on public.report_evidence_assets to authenticated;
grant all on public.report_evidence_assets to service_role;

-- This replaces the quota-free beta RPC and binds uploads to a retained task.
drop function if exists public.reserve_job(
  uuid, public.analysis_mode, uuid[], smallint, text[]
);

create or replace function public.reserve_job(
  p_user_id uuid,
  p_mode public.analysis_mode,
  p_file_ids uuid[],
  p_max_rounds smallint,
  p_languages text[] default array['zh','en']::text[],
  p_research_brief text default ''
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
  if char_length(coalesce(p_research_brief, '')) > 2000 then
    raise exception 'research_brief must not exceed 2000 characters';
  end if;
  if (p_mode = 'single' and v_count <> 1)
    or (p_mode = 'multi' and v_count not between 2 and 5) then
    raise exception 'invalid PDF count for analysis mode';
  end if;
  if exists (
    select 1 from public.jobs
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

  insert into public.jobs (
    user_id, mode, max_rounds, languages, reserved_units, research_brief
  )
  values (
    p_user_id, p_mode, p_max_rounds, p_languages, 0, coalesce(p_research_brief, '')
  )
  returning * into v_job;

  insert into public.job_files (job_id, upload_id, position)
  select v_job.id, item.upload_id, item.position::smallint
  from unnest(p_file_ids) with ordinality as item(upload_id, position);

  update public.uploads
  set delete_after = null
  where id = any(p_file_ids) and user_id = p_user_id;

  insert into public.job_events (job_id, kind, message)
  values (v_job.id, 'queued', 'Job queued');
  return v_job;
end;
$$;

revoke all on function public.reserve_job(
  uuid, public.analysis_mode, uuid[], smallint, text[], text
) from public, anon, authenticated;
grant execute on function public.reserve_job(
  uuid, public.analysis_mode, uuid[], smallint, text[], text
) to service_role;

create or replace function public.list_my_jobs(
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
  error text,
  created_at timestamptz,
  completed_at timestamptz,
  is_favorite boolean,
  file_names text[],
  report_id uuid
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
    jobs.error,
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
    ) as report_id
  from public.jobs
  where jobs.user_id = auth.uid()
    and (not p_favorites_only or jobs.is_favorite)
  order by jobs.is_favorite desc, jobs.created_at desc
  limit least(greatest(p_limit, 1), 100)
  offset greatest(p_offset, 0);
$$;

revoke all on function public.list_my_jobs(integer, integer, boolean) from public, anon;
grant execute on function public.list_my_jobs(integer, integer, boolean) to authenticated;

create or replace function public.set_job_favorite(
  p_job_id uuid,
  p_is_favorite boolean
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.jobs
  set is_favorite = p_is_favorite, updated_at = now()
  where id = p_job_id and user_id = auth.uid();
  if not found then
    raise exception 'job not found';
  end if;
  return p_is_favorite;
end;
$$;

revoke all on function public.set_job_favorite(uuid, boolean) from public, anon;
grant execute on function public.set_job_favorite(uuid, boolean) to authenticated;

create or replace function public.claim_expired_storage()
returns table(kind text, record_id uuid, storage_path text)
language plpgsql
security definer set search_path = public
as $$
begin
  return query
  select 'upload'::text, uploads.id, uploads.storage_path
  from public.uploads
  where delete_after is not null
    and delete_after < now()
    and status <> 'deleted';

  return query
  select 'orphan'::text, storage_deletion_queue.id, storage_deletion_queue.storage_path
  from public.storage_deletion_queue
  where created_at < now() - interval '5 minutes';

  return query
  delete from public.reports
  where delete_after < now()
  returning 'report'::text, reports.id, null::text;
end;
$$;

revoke all on function public.claim_expired_storage() from public, anon, authenticated;
grant execute on function public.claim_expired_storage() to service_role;
