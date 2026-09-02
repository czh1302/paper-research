-- Private multimodal attachments for the experiment workspace assistant.

create table public.experiment_chat_attachments (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  action_id uuid references public.experiment_actions(id) on delete cascade,
  storage_path text not null unique,
  file_name text not null check (char_length(file_name) between 1 and 180),
  declared_mime_type text not null check (declared_mime_type in (
    'image/jpeg', 'image/png', 'image/webp', 'image/gif'
  )),
  mime_type text check (mime_type is null or mime_type in (
    'image/jpeg', 'image/png', 'image/webp', 'image/gif'
  )),
  byte_size bigint not null check (byte_size between 1 and 10485760),
  sha256 text,
  width integer check (width is null or width between 1 and 32768),
  height integer check (height is null or height between 1 and 32768),
  status text not null default 'pending'
    check (status in ('pending', 'ready', 'bound', 'rejected')),
  rejection_reason text,
  expires_at timestamptz not null default now() + interval '24 hours',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index experiment_chat_attachments_experiment_idx
  on public.experiment_chat_attachments (experiment_id, created_at desc);
create index experiment_chat_attachments_action_idx
  on public.experiment_chat_attachments (action_id, created_at)
  where action_id is not null;
create index experiment_chat_attachments_expiry_idx
  on public.experiment_chat_attachments (expires_at)
  where action_id is null;

alter table public.experiment_chat_attachments enable row level security;
revoke all on public.experiment_chat_attachments from public, anon, authenticated;
grant all on public.experiment_chat_attachments to service_role;

insert into storage.buckets (id, name, public, file_size_limit)
values ('experiment-chat-attachments', 'experiment-chat-attachments', false, 10485760)
on conflict (id) do update
set public = false, file_size_limit = excluded.file_size_limit;

create or replace function public.queue_deleted_experiment_chat_attachment()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.storage_deletion_queue (bucket_id, storage_path)
  values ('experiment-chat-attachments', old.storage_path)
  on conflict (bucket_id, storage_path) do nothing;
  return old;
end;
$$;

create trigger queue_deleted_experiment_chat_attachment
before delete on public.experiment_chat_attachments
for each row execute function public.queue_deleted_experiment_chat_attachment();

revoke all on function public.queue_deleted_experiment_chat_attachment()
from public, anon, authenticated;

create or replace function public.claim_expired_experiment_chat_storage()
returns table(record_id uuid, storage_path text)
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.experiment_chat_attachments attachments
  where attachments.action_id is null
    and attachments.expires_at <= now();

  return query
  select queue.id, queue.storage_path
  from public.storage_deletion_queue queue
  where queue.bucket_id = 'experiment-chat-attachments'
    and queue.created_at < now() - interval '5 minutes';
end;
$$;

revoke all on function public.claim_expired_experiment_chat_storage()
from public, anon, authenticated;
grant execute on function public.claim_expired_experiment_chat_storage() to service_role;

-- Keep action admission and attachment binding in one database transaction so
-- the worker can never claim an assistant action before its new uploads are bound.
create or replace function public.enqueue_experiment_action_with_attachments(
  p_experiment_id uuid,
  p_user_id uuid,
  p_kind text,
  p_request jsonb default '{}'::jsonb,
  p_base_revision_id uuid default null,
  p_idempotency_key text default null,
  p_attachment_ids uuid[] default '{}'::uuid[],
  p_llm_reservation_cny numeric default 5,
  p_assistant_llm_max_cny numeric default 20,
  p_experiment_llm_max_cny numeric default 40,
  p_global_llm_max_cny numeric default 200,
  p_max_spend_usd numeric default 90
)
returns public.experiment_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_action public.experiment_actions;
  v_ids uuid[];
  v_request_ids uuid[];
  v_count integer;
begin
  select coalesce(array_agg(value order by value), '{}'::uuid[])
  into v_ids
  from (
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
      and (attachments.action_id is null or attachments.action_id = (
        select actions.id from public.experiment_actions actions
        where actions.experiment_id = p_experiment_id
          and actions.idempotency_key = nullif(left(p_idempotency_key, 160), '')
        limit 1
      ));
    if v_count <> cardinality(v_ids) then
      raise check_violation using message = 'experiment chat attachment is unavailable';
    end if;
  end if;

  v_action := public.enqueue_experiment_action(
    p_experiment_id => p_experiment_id,
    p_user_id => p_user_id,
    p_kind => p_kind,
    p_request => coalesce(p_request, '{}'::jsonb),
    p_base_revision_id => p_base_revision_id,
    p_idempotency_key => p_idempotency_key,
    p_llm_reservation_cny => p_llm_reservation_cny,
    p_assistant_llm_max_cny => p_assistant_llm_max_cny,
    p_experiment_llm_max_cny => p_experiment_llm_max_cny,
    p_global_llm_max_cny => p_global_llm_max_cny,
    p_max_spend_usd => p_max_spend_usd
  );

  select coalesce(array_agg(value::uuid order by value::uuid), '{}'::uuid[])
  into v_request_ids
  from (
    select distinct jsonb_array_elements_text(
      coalesce(v_action.request -> 'attachmentIds', '[]'::jsonb)
    ) as value
  ) request_ids;
  if v_request_ids is distinct from v_ids then
    raise serialization_failure using message = 'experiment chat attachment idempotency conflict';
  end if;

  if cardinality(v_ids) > 0 then
    update public.experiment_chat_attachments attachments
    set action_id = v_action.id,
        status = 'bound',
        expires_at = 'infinity'::timestamptz,
        updated_at = now()
    where attachments.id = any(v_ids)
      and attachments.experiment_id = p_experiment_id
      and attachments.user_id = p_user_id
      and (attachments.action_id is null or attachments.action_id = v_action.id);
    get diagnostics v_count = row_count;
    if v_count <> cardinality(v_ids) then
      raise serialization_failure using message = 'experiment chat attachment binding conflict';
    end if;
  end if;

  return v_action;
end;
$$;

revoke all on function public.enqueue_experiment_action_with_attachments(
  uuid, uuid, text, jsonb, uuid, text, uuid[], numeric, numeric, numeric, numeric, numeric
) from public, anon, authenticated;
grant execute on function public.enqueue_experiment_action_with_attachments(
  uuid, uuid, text, jsonb, uuid, text, uuid[], numeric, numeric, numeric, numeric, numeric
) to service_role;
