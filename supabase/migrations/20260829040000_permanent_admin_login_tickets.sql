do $$
declare
  constraint_name text;
begin
  for constraint_name in
    select conname
    from pg_constraint
    where conrelid = 'public.admin_login_tickets'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) like '%expires_at <=%'
  loop
    execute format(
      'alter table public.admin_login_tickets drop constraint %I',
      constraint_name
    );
  end loop;
end;
$$;

alter table public.admin_login_tickets
  add constraint admin_login_tickets_expiry_policy_check
  check (
    expires_at = 'infinity'::timestamptz
    or expires_at <= created_at + interval '31 days'
  );
