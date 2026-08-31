-- Fast report sections and pre-rendered evidence-page previews.

create table if not exists public.report_sections (
  report_id uuid not null references public.reports(id) on delete cascade,
  section text not null check (section in ('overview', 'problem', 'landscape', 'ideas')),
  content jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (report_id, section)
);

create table if not exists public.report_evidence_previews (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid not null references public.report_evidence_assets(id) on delete cascade,
  page integer not null check (page > 0),
  storage_path text not null unique,
  width integer not null check (width > 0),
  height integer not null check (height > 0),
  byte_size integer not null check (byte_size > 0),
  created_at timestamptz not null default now(),
  unique (asset_id, page)
);

alter table public.storage_deletion_queue
  add column if not exists bucket_id text not null default 'papers';
alter table public.storage_deletion_queue
  drop constraint if exists storage_deletion_queue_storage_path_key;
create unique index if not exists storage_deletion_queue_bucket_path_idx
  on public.storage_deletion_queue (bucket_id, storage_path);

create or replace function public.queue_deleted_storage_object()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.storage_deletion_queue (bucket_id, storage_path)
  values ('papers', old.storage_path)
  on conflict (bucket_id, storage_path) do nothing;
  return old;
end;
$$;

create index if not exists report_sections_report_idx
  on public.report_sections (report_id, section);
create index if not exists report_evidence_previews_asset_idx
  on public.report_evidence_previews (asset_id, page);

alter table public.report_sections enable row level security;
alter table public.report_evidence_previews enable row level security;

create policy "report_sections_select_own" on public.report_sections
for select to authenticated using (
  exists (
    select 1 from public.reports
    join public.jobs on jobs.id = reports.job_id
    where reports.id = report_sections.report_id and jobs.user_id = auth.uid()
  )
);

create policy "report_sections_select_admin" on public.report_sections
for select to authenticated using ((select public.is_admin()));

create policy "evidence_previews_select_own" on public.report_evidence_previews
for select to authenticated using (
  exists (
    select 1 from public.report_evidence_assets
    join public.jobs on jobs.id = report_evidence_assets.job_id
    where report_evidence_assets.id = report_evidence_previews.asset_id
      and jobs.user_id = auth.uid()
  )
);

create policy "evidence_previews_select_admin" on public.report_evidence_previews
for select to authenticated using ((select public.is_admin()));

grant select on public.report_sections, public.report_evidence_previews to authenticated;
grant all on public.report_sections, public.report_evidence_previews to service_role;

create or replace function public.queue_deleted_evidence_preview()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.storage_deletion_queue (bucket_id, storage_path)
  values ('evidence-previews', old.storage_path)
  on conflict (bucket_id, storage_path) do nothing;
  return old;
end;
$$;

drop trigger if exists queue_deleted_evidence_preview on public.report_evidence_previews;
create trigger queue_deleted_evidence_preview
before delete on public.report_evidence_previews
for each row execute function public.queue_deleted_evidence_preview();

create or replace function public.claim_expired_preview_storage()
returns table(record_id uuid, storage_path text)
language sql
security definer
set search_path = ''
as $$
  select storage_deletion_queue.id, storage_deletion_queue.storage_path
  from public.storage_deletion_queue
  where bucket_id = 'evidence-previews'
    and created_at < now() - interval '5 minutes';
$$;

revoke all on function public.claim_expired_preview_storage() from public, anon, authenticated;
grant execute on function public.claim_expired_preview_storage() to service_role;

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
  where bucket_id = 'papers'
    and created_at < now() - interval '5 minutes';

  return query
  delete from public.reports
  where delete_after < now()
  returning 'report'::text, reports.id, null::text;
end;
$$;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('evidence-previews', 'evidence-previews', false, 2097152, array['image/jpeg'])
on conflict (id) do update set public = false,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
