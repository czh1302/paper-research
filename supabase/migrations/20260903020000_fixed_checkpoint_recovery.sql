-- Keep every resumable checkpoint on a fixed 30 second retry cadence.
--
-- These guards live in Postgres deliberately: already-running workers may
-- still send a legacy two-hour or six-hour delay until they are naturally
-- reloaded, but their writes are normalized before they become visible.
-- Explicit provider Retry-After delays, the ten-minute circuit breaker, and
-- the infinity sentinel used by a benchmark dependency gate are preserved.

create or replace function public.enforce_fixed_job_checkpoint_retry()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_category text;
  v_recent_failures integer := 0;
  v_delay_seconds integer := 30;
begin
  if new.status not in ('recovering', 'waiting_resources')
     or new.next_retry_at is null
     or new.next_retry_at = 'infinity'::timestamptz then
    return new;
  end if;

  select attempts.failure_category
    into v_category
  from public.job_attempts attempts
  where attempts.job_id = new.id
  order by attempts.created_at desc, attempts.id desc
  limit 1;

  -- A 429 Retry-After is authoritative. The worker records it as the next
  -- retry timestamp, so leave that timestamp alone for rate-limit failures.
  if v_category = 'rate_limit' then
    return new;
  end if;

  if v_category in ('network', 'model', 'mineru', 'database', 'rate_limit') then
    select count(*)
      into v_recent_failures
    from public.job_attempts attempts
    where attempts.failure_category = v_category
      and attempts.created_at >= now() - interval '5 minutes';
  end if;
  if v_recent_failures >= 5 then
    v_delay_seconds := 600;
  end if;

  new.next_retry_at := now() + make_interval(secs => v_delay_seconds);
  return new;
end;
$$;

drop trigger if exists jobs_fixed_checkpoint_retry on public.jobs;
create trigger jobs_fixed_checkpoint_retry
before insert or update of status, next_retry_at, retry_count on public.jobs
for each row execute function public.enforce_fixed_job_checkpoint_retry();

create or replace function public.enforce_fixed_experiment_checkpoint_retry()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_category text;
  v_recent_failures integer := 0;
  v_delay_seconds integer := 30;
begin
  if new.status not in ('recovering', 'waiting_resources')
     or new.next_retry_at is null
     or new.next_retry_at = 'infinity'::timestamptz then
    return new;
  end if;

  select attempts.failure_category
    into v_category
  from public.experiment_attempts attempts
  where attempts.experiment_id = new.id
    and attempts.action_id is null
  order by attempts.created_at desc, attempts.id desc
  limit 1;

  if v_category = 'rate_limit' then
    return new;
  end if;
  if v_category in ('network', 'model', 'mineru', 'database', 'rate_limit') then
    select count(*)
      into v_recent_failures
    from public.experiment_attempts attempts
    where attempts.action_id is null
      and attempts.failure_category = v_category
      and attempts.created_at >= now() - interval '5 minutes';
  end if;
  if v_recent_failures >= 5 then
    v_delay_seconds := 600;
  end if;

  new.next_retry_at := now() + make_interval(secs => v_delay_seconds);
  return new;
end;
$$;

drop trigger if exists idea_experiments_fixed_checkpoint_retry on public.idea_experiments;
create trigger idea_experiments_fixed_checkpoint_retry
before insert or update of status, next_retry_at, retry_count on public.idea_experiments
for each row execute function public.enforce_fixed_experiment_checkpoint_retry();

create or replace function public.enforce_fixed_action_checkpoint_retry()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.status = 'recovering' and new.next_retry_at is not null then
    new.next_retry_at := now() + interval '30 seconds';
  end if;
  return new;
end;
$$;

drop trigger if exists experiment_actions_fixed_checkpoint_retry on public.experiment_actions;
create trigger experiment_actions_fixed_checkpoint_retry
before insert or update of status, next_retry_at, retry_count on public.experiment_actions
for each row execute function public.enforce_fixed_action_checkpoint_retry();

create or replace function public.enforce_fixed_inference_checkpoint_retry()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.status = 'recovering' and new.next_retry_at is not null then
    new.next_retry_at := now() + interval '30 seconds';
  end if;
  return new;
end;
$$;

drop trigger if exists experiment_inference_fixed_checkpoint_retry
  on public.experiment_inference_requests;
create trigger experiment_inference_fixed_checkpoint_retry
before insert or update of status, next_retry_at, retry_count
on public.experiment_inference_requests
for each row execute function public.enforce_fixed_inference_checkpoint_retry();

create or replace function public.enforce_fixed_runtime_cleanup_retry()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.state = 'destroying' and new.destroy_after is not null then
    new.destroy_after := now() + interval '30 seconds';
  end if;
  return new;
end;
$$;

drop trigger if exists experiment_runtime_fixed_cleanup_retry
  on public.experiment_runtime;
create trigger experiment_runtime_fixed_cleanup_retry
before insert or update of state, destroy_after on public.experiment_runtime
for each row execute function public.enforce_fixed_runtime_cleanup_retry();

drop trigger if exists experiment_validation_runtime_fixed_cleanup_retry
  on public.experiment_validation_runtime;
create trigger experiment_validation_runtime_fixed_cleanup_retry
before insert or update of state, destroy_after on public.experiment_validation_runtime
for each row execute function public.enforce_fixed_runtime_cleanup_retry();

-- Wake every existing finite recovery record now. The triggers above convert
-- these writes to a due time no later than 30 seconds from migration time (or
-- ten minutes only when the circuit-breaker evidence is present).
update public.jobs
set next_retry_at = now(), updated_at = now()
where status in ('recovering', 'waiting_resources')
  and next_retry_at is distinct from 'infinity'::timestamptz;

update public.idea_experiments
set next_retry_at = now(), updated_at = now()
where status in ('recovering', 'waiting_resources')
  and next_retry_at is distinct from 'infinity'::timestamptz;

update public.experiment_actions
set next_retry_at = now(), updated_at = now()
where status = 'recovering';

update public.experiment_inference_requests
set next_retry_at = now(), updated_at = now()
where status = 'recovering';

update public.experiment_runtime
set destroy_after = now(), updated_at = now()
where state = 'destroying';

update public.experiment_validation_runtime
set destroy_after = now(), updated_at = now()
where state = 'destroying';

revoke all on function public.enforce_fixed_job_checkpoint_retry() from public, anon, authenticated;
revoke all on function public.enforce_fixed_experiment_checkpoint_retry() from public, anon, authenticated;
revoke all on function public.enforce_fixed_action_checkpoint_retry() from public, anon, authenticated;
revoke all on function public.enforce_fixed_inference_checkpoint_retry() from public, anon, authenticated;
revoke all on function public.enforce_fixed_runtime_cleanup_retry() from public, anon, authenticated;
