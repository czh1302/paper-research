create table public.admin_login_tickets (
  token_hash text primary key check (token_hash ~ '^[0-9a-f]{64}$'),
  admin_user_id uuid not null references public.admin_users(user_id) on delete cascade,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  check (expires_at > created_at),
  check (expires_at <= created_at + interval '31 days')
);

create index admin_login_tickets_expiry_idx
on public.admin_login_tickets (expires_at)
where consumed_at is null and revoked_at is null;

alter table public.admin_login_tickets enable row level security;
grant all on public.admin_login_tickets to service_role;

create or replace function public.claim_admin_login_ticket(p_token_hash text)
returns table (user_id uuid, email text)
language plpgsql
security definer
set search_path = ''
as $$
begin
  return query
  with claimed as (
    update public.admin_login_tickets
    set consumed_at = now()
    where token_hash = p_token_hash
      and consumed_at is null
      and revoked_at is null
      and expires_at > now()
    returning admin_user_id
  )
  select claimed.admin_user_id, users.email::text
  from claimed
  join auth.users as users on users.id = claimed.admin_user_id
  join public.admin_users as admins on admins.user_id = claimed.admin_user_id;
end;
$$;

revoke all on function public.claim_admin_login_ticket(text) from public, anon, authenticated;
grant execute on function public.claim_admin_login_ticket(text) to service_role;
