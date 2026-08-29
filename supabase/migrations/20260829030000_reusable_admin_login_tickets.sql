alter table public.admin_login_tickets
  add column use_count integer not null default 0 check (use_count >= 0),
  add column last_used_at timestamptz;

-- A ticket consumed under the former one-time policy must never be resurrected.
update public.admin_login_tickets
set revoked_at = coalesce(revoked_at, now())
where consumed_at is not null;

create or replace function public.redeem_admin_login_ticket(p_token_hash text)
returns table (user_id uuid, email text)
language plpgsql
security definer
set search_path = ''
as $$
begin
  return query
  with redeemed as (
    update public.admin_login_tickets
    set use_count = use_count + 1,
        last_used_at = now()
    where token_hash = p_token_hash
      and consumed_at is null
      and revoked_at is null
      and expires_at > now()
    returning admin_user_id
  )
  select redeemed.admin_user_id, users.email::text
  from redeemed
  join auth.users as users on users.id = redeemed.admin_user_id
  join public.admin_users as admins on admins.user_id = redeemed.admin_user_id;
end;
$$;

-- Keep the old RPC reusable during the rolling Edge Function deployment.
create or replace function public.claim_admin_login_ticket(p_token_hash text)
returns table (user_id uuid, email text)
language sql
security definer
set search_path = ''
as $$
  select * from public.redeem_admin_login_ticket(p_token_hash);
$$;

revoke all on function public.redeem_admin_login_ticket(text) from public, anon, authenticated;
revoke all on function public.claim_admin_login_ticket(text) from public, anon, authenticated;
grant execute on function public.redeem_admin_login_ticket(text) to service_role;
grant execute on function public.claim_admin_login_ticket(text) to service_role;
