-- E2B-backed Idea experiments, immutable revisions, resumable actions and private artifacts.

create table public.idea_experiments (
  id uuid primary key default gen_random_uuid(),
  -- Opaque token used only to persist aggregate spend after an experiment is
  -- deleted. It is intentionally not a foreign key and carries no user/job
  -- identity in the durable ledger.
  cost_ledger_token uuid not null default gen_random_uuid() unique,
  report_id uuid not null references public.reports(id) on delete cascade,
  job_id uuid not null references public.jobs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  idea_key text not null check (char_length(idea_key) between 1 and 120),
  idea_rank smallint not null default 1 check (idea_rank between 1 and 3),
  idea_snapshot jsonb not null,
  pilot_specification jsonb not null default '{}'::jsonb,
  pilot_specification_hash text,
  pilot_compilation_required boolean not null default true,
  automatic_initial_run boolean not null default false,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'recovering', 'waiting_resources', 'ready', 'cancelled')),
  stage text not null default 'spec_freeze'
    check (stage in (
      'spec_freeze', 'repo_generation', 'environment_setup', 'baseline',
      'intervention', 'evaluation', 'repair', 'archive', 'interactive'
    )),
  progress smallint not null default 0 check (progress between 0 and 100),
  outcome text not null default 'pending'
    check (outcome in (
      'pending', 'initial_support', 'not_support', 'inconclusive',
      'environment_blocked', 'resource_limited', 'budget_blocked', 'cancelled'
    )),
  public_summary jsonb not null default '{}'::jsonb,
  checkpoint jsonb not null default '{}'::jsonb,
  baseline_revision_id uuid,
  current_revision_id uuid,
  latest_run_id uuid,
  user_validation_count smallint not null default 0 check (user_validation_count between 0 and 3),
  max_user_validations smallint not null default 3 check (max_user_validations between 1 and 3),
  repair_count smallint not null default 0 check (repair_count between 0 and 2),
  e2b_seconds bigint not null default 0 check (e2b_seconds >= 0),
  e2b_cost_usd numeric(12, 6) not null default 0 check (e2b_cost_usd >= 0),
  llm_cost_cny numeric(12, 6) not null default 0 check (llm_cost_cny >= 0),
  llm_reserved_cny numeric(12, 6) not null default 0 check (llm_reserved_cny >= 0),
  retry_count integer not null default 0 check (retry_count >= 0),
  next_retry_at timestamptz,
  last_recovery_at timestamptz,
  worker_id text,
  lease_expires_at timestamptz,
  cancellation_requested boolean not null default false,
  deletion_requested_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  last_activity_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (report_id, idea_key)
);

create unique index reports_id_job_id_unique_for_experiments on public.reports (id, job_id);
create unique index jobs_id_user_id_unique_for_experiments on public.jobs (id, user_id);
alter table public.idea_experiments
  add constraint idea_experiments_report_job_fk
    foreign key (report_id, job_id) references public.reports(id, job_id) on delete cascade,
  add constraint idea_experiments_job_user_fk
    foreign key (job_id, user_id) references public.jobs(id, user_id) on delete cascade;

create table public.experiment_revisions (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  parent_revision_id uuid references public.experiment_revisions(id) on delete set null,
  revision_number integer not null check (revision_number > 0),
  actor text not null check (actor in ('automatic', 'user', 'assistant', 'terminal', 'system')),
  git_commit text,
  tree_hash text,
  bundle_storage_path text,
  summary jsonb not null default '{}'::jsonb,
  immutable boolean not null default false,
  created_at timestamptz not null default now(),
  unique (experiment_id, revision_number)
);

create table public.experiment_runs (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  action_id uuid,
  revision_id uuid references public.experiment_revisions(id) on delete set null,
  run_number integer not null check (run_number > 0),
  trigger_kind text not null check (trigger_kind in ('automatic', 'user', 'repair')),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'recovering', 'completed', 'cancelled')),
  outcome text not null default 'pending'
    check (outcome in (
      'pending', 'initial_support', 'not_support', 'inconclusive',
      'environment_blocked', 'resource_limited', 'budget_blocked', 'cancelled'
    )),
  commands jsonb not null default '{}'::jsonb,
  metrics jsonb not null default '{}'::jsonb,
  evaluation jsonb not null default '{}'::jsonb,
  safe_error text,
  e2b_seconds bigint not null default 0 check (e2b_seconds >= 0),
  e2b_cost_usd numeric(12, 6) not null default 0 check (e2b_cost_usd >= 0),
  llm_cost_cny numeric(12, 6) not null default 0 check (llm_cost_cny >= 0),
  started_at timestamptz,
  -- A scientific validation may never receive a fresh wall-clock budget on
  -- Worker recovery. The conservative v1 fence counts recovery waits too;
  -- a later active-time ledger can relax this without weakening the cap.
  hard_deadline_at timestamptz not null default now() + interval '60 minutes',
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint experiment_runs_hard_deadline_check check (
    hard_deadline_at <= created_at + interval '60 minutes'
  ),
  unique (experiment_id, run_number)
);

create table public.experiment_actions (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  requested_by uuid references auth.users(id) on delete set null,
  kind text not null check (kind in (
    'assistant', 'chat', 'save_file', 'move_file', 'delete_file', 'read_file',
    'command', 'rollback', 'validation', 'restore', 'system'
  )),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'recovering', 'completed', 'cancelled')),
  request jsonb not null default '{}'::jsonb,
  response jsonb not null default '{}'::jsonb,
  base_revision_id uuid references public.experiment_revisions(id) on delete set null,
  result_revision_id uuid references public.experiment_revisions(id) on delete set null,
  idempotency_key text,
  llm_cost_cny numeric(12, 6) not null default 0 check (llm_cost_cny >= 0),
  llm_reserved_cny numeric(12, 6) not null default 0 check (llm_reserved_cny >= 0),
  validation_slot_reserved boolean not null default false,
  validation_slot_consumed boolean not null default false,
  retry_count integer not null default 0 check (retry_count >= 0),
  next_retry_at timestamptz,
  worker_id text,
  lease_expires_at timestamptz,
  safe_error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Each paid Claude invocation receives an idempotent child reservation from
-- the experiment/action envelope before the provider process starts. These
-- rows are deliberately not added to global commitment because their amount
-- is already contained in llm_reserved_cny on the parent or action.
create table public.experiment_llm_invocations (
  usage_id uuid primary key,
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  action_id uuid references public.experiment_actions(id) on delete cascade,
  reserved_cny numeric(12, 6) not null check (reserved_cny > 0),
  status text not null default 'authorized'
    check (status in ('authorized', 'settled')),
  settled_cny numeric(12, 6) not null default 0 check (settled_cny >= 0),
  settlement_kind text,
  created_at timestamptz not null default now(),
  settled_at timestamptz,
  updated_at timestamptz not null default now()
);

alter table public.experiment_runs
  add constraint experiment_runs_action_fk
  foreign key (action_id) references public.experiment_actions(id) on delete set null;

create table public.experiment_artifacts (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  run_id uuid references public.experiment_runs(id) on delete cascade,
  revision_id uuid references public.experiment_revisions(id) on delete set null,
  kind text not null check (kind in (
    'repository_zip', 'git_bundle', 'source_file', 'log', 'metrics',
    'plot', 'result_report', 'diff', 'other'
  )),
  storage_path text not null unique,
  file_name text not null,
  mime_type text not null default 'application/octet-stream',
  byte_size bigint check (byte_size is null or byte_size >= 0),
  sha256 text,
  public_safe boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.experiment_runtime (
  experiment_id uuid primary key references public.idea_experiments(id) on delete cascade,
  cost_ledger_token uuid not null default gen_random_uuid() unique,
  sandbox_id text unique,
  state text not null default 'absent'
    check (state in ('absent', 'creating', 'running', 'paused', 'destroying', 'destroyed')),
  pty_session_id text,
  controller_token_hash text,
  terminal_ticket_hash text,
  terminal_ticket_mode text check (terminal_ticket_mode is null or terminal_ticket_mode in ('read', 'write')),
  terminal_ticket_expires_at timestamptz,
  terminal_session_epoch bigint not null default 0 check (terminal_session_epoch >= 0),
  paused_at timestamptz,
  destroy_after timestamptz,
  last_heartbeat_at timestamptz,
  lifecycle_claim_token uuid,
  lifecycle_lease_expires_at timestamptz,
  active_started_at timestamptz,
  reserved_until timestamptz,
  metered_seconds bigint not null default 0 check (metered_seconds >= 0),
  metered_cost_usd numeric(12, 6) not null default 0 check (metered_cost_usd >= 0),
  estimated_cost_per_second_usd numeric(12, 9) not null default 0.000092
    check (estimated_cost_per_second_usd > 0),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Formal validation runs in a fresh, non-user-writable sandbox. It is a
-- distinct billable resource from the interactive runtime and therefore has
-- its own durable identity, meter and lifecycle lease.
create table public.experiment_validation_runtime (
  action_id uuid primary key references public.experiment_actions(id) on delete cascade,
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  cost_ledger_token uuid not null default gen_random_uuid() unique,
  run_id uuid references public.experiment_runs(id) on delete set null,
  sandbox_id text unique,
  state text not null default 'creating'
    check (state in ('creating', 'running', 'destroying', 'destroyed')),
  active_started_at timestamptz,
  reserved_until timestamptz,
  destroy_after timestamptz,
  metered_seconds bigint not null default 0 check (metered_seconds >= 0),
  metered_cost_usd numeric(12, 6) not null default 0 check (metered_cost_usd >= 0),
  estimated_cost_per_second_usd numeric(12, 9) not null default 0.000092
    check (estimated_cost_per_second_usd > 0),
  lifecycle_claim_token uuid,
  lifecycle_lease_expires_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (experiment_id, action_id)
);

-- Global spend must survive job/user/experiment deletion. Rows contain only a
-- random source token and aggregate currency values; no user, job, report,
-- experiment, sandbox, or action identifier is retained.
create table public.experiment_global_cost_ledger (
  source_token uuid primary key,
  source_kind text not null check (source_kind in ('runtime', 'validation_runtime', 'llm')),
  e2b_cost_usd numeric(14, 6) not null default 0 check (e2b_cost_usd >= 0),
  llm_cost_cny numeric(14, 6) not null default 0 check (llm_cost_cny >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.experiment_attempts (
  id bigint generated always as identity primary key,
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  action_id uuid references public.experiment_actions(id) on delete cascade,
  attempt_number integer not null check (attempt_number > 0),
  failure_category text not null,
  checkpoint_stage text not null,
  safe_error text,
  created_at timestamptz not null default now()
);

alter table public.idea_experiments
  add constraint idea_experiments_baseline_revision_fk
  foreign key (baseline_revision_id) references public.experiment_revisions(id) on delete set null,
  add constraint idea_experiments_current_revision_fk
  foreign key (current_revision_id) references public.experiment_revisions(id) on delete set null,
  add constraint idea_experiments_latest_run_fk
  foreign key (latest_run_id) references public.experiment_runs(id) on delete set null;

create index idea_experiments_user_created_idx on public.idea_experiments (user_id, created_at desc);
create index idea_experiments_report_idx on public.idea_experiments (report_id, idea_rank);
create index idea_experiments_queue_idx on public.idea_experiments (next_retry_at, created_at)
  where status in ('queued', 'recovering', 'waiting_resources');
create index experiment_revisions_experiment_idx on public.experiment_revisions (experiment_id, revision_number desc);
create index experiment_runs_experiment_idx on public.experiment_runs (experiment_id, run_number desc);
create unique index experiment_runs_action_idx on public.experiment_runs (action_id)
  where action_id is not null;
create index experiment_actions_queue_idx on public.experiment_actions (next_retry_at, created_at)
  where status in ('queued', 'recovering');
create index experiment_llm_invocations_active_idx
  on public.experiment_llm_invocations (experiment_id, action_id, created_at)
  where status = 'authorized';
create unique index experiment_actions_idempotency_idx
  on public.experiment_actions (experiment_id, idempotency_key)
  where idempotency_key is not null;
create index experiment_artifacts_experiment_idx on public.experiment_artifacts (experiment_id, created_at desc);
create index experiment_attempts_experiment_idx on public.experiment_attempts (experiment_id, created_at desc);
create index experiment_validation_runtime_cleanup_idx
  on public.experiment_validation_runtime (destroy_after, updated_at)
  where state in ('creating', 'running', 'destroying');
create unique index provider_usage_experiment_usage_id_idx
  on public.provider_usage ((metadata->>'experiment_usage_id'))
  where nullif(metadata->>'experiment_usage_id', '') is not null;
create index jobs_pending_primary_experiment_idx on public.jobs (updated_at, id)
  where status = 'completed'
    and (checkpoint #>> '{experiment_auto_enqueue,state}') = 'pending';

alter table public.idea_experiments enable row level security;
alter table public.experiment_revisions enable row level security;
alter table public.experiment_runs enable row level security;
alter table public.experiment_actions enable row level security;
alter table public.experiment_llm_invocations enable row level security;
alter table public.experiment_artifacts enable row level security;
alter table public.experiment_runtime enable row level security;
alter table public.experiment_validation_runtime enable row level security;
alter table public.experiment_attempts enable row level security;
alter table public.experiment_global_cost_ledger enable row level security;

create policy "idea_experiments_select_own" on public.idea_experiments
for select to authenticated using (user_id = auth.uid());
create policy "idea_experiments_select_admin" on public.idea_experiments
for select to authenticated using ((select public.is_admin()));

create policy "experiment_revisions_select_own" on public.experiment_revisions
for select to authenticated using (exists (
  select 1 from public.idea_experiments
  where idea_experiments.id = experiment_revisions.experiment_id
    and idea_experiments.user_id = auth.uid()
));
create policy "experiment_revisions_select_admin" on public.experiment_revisions
for select to authenticated using ((select public.is_admin()));

create policy "experiment_runs_select_own" on public.experiment_runs
for select to authenticated using (exists (
  select 1 from public.idea_experiments
  where idea_experiments.id = experiment_runs.experiment_id
    and idea_experiments.user_id = auth.uid()
));
create policy "experiment_runs_select_admin" on public.experiment_runs
for select to authenticated using ((select public.is_admin()));

create policy "experiment_actions_select_own" on public.experiment_actions
for select to authenticated using (exists (
  select 1 from public.idea_experiments
  where idea_experiments.id = experiment_actions.experiment_id
    and idea_experiments.user_id = auth.uid()
));
create policy "experiment_artifacts_select_own" on public.experiment_artifacts
for select to authenticated using (exists (
  select 1 from public.idea_experiments
  where idea_experiments.id = experiment_artifacts.experiment_id
    and idea_experiments.user_id = auth.uid()
));
create policy "experiment_artifacts_select_admin" on public.experiment_artifacts
for select to authenticated using ((select public.is_admin()));

create policy "experiment_attempts_select_admin" on public.experiment_attempts
for select to authenticated using ((select public.is_admin()));

-- Authenticated clients receive only the columns needed for RLS/Realtime
-- notifications. Specifications, checkpoints, storage paths, worker leases,
-- and internal errors remain service-role data and are exposed only through
-- the sanitizing Edge Functions below.
revoke all on public.idea_experiments, public.experiment_revisions,
  public.experiment_runs, public.experiment_actions, public.experiment_artifacts
from authenticated;
revoke all on public.experiment_llm_invocations from public, anon, authenticated;
grant select (
  id, report_id, job_id, user_id, idea_key, idea_rank, automatic_initial_run,
  status, stage, progress, outcome, public_summary, baseline_revision_id,
  current_revision_id, latest_run_id, user_validation_count,
  max_user_validations, repair_count, e2b_seconds, e2b_cost_usd, llm_cost_cny,
  retry_count, next_retry_at, last_recovery_at, cancellation_requested,
  deletion_requested_at, started_at, completed_at, last_activity_at,
  created_at, updated_at
) on public.idea_experiments to authenticated;
grant select (
  id, experiment_id, parent_revision_id, revision_number, actor, git_commit,
  tree_hash, summary, immutable, created_at
) on public.experiment_revisions to authenticated;
grant select (
  id, experiment_id, revision_id, run_number, trigger_kind, status, outcome,
  commands, metrics, evaluation, e2b_seconds, e2b_cost_usd, llm_cost_cny,
  started_at, hard_deadline_at, completed_at, created_at
) on public.experiment_runs to authenticated;
grant select (
  id, experiment_id, requested_by, kind, status, request, response,
  base_revision_id, result_revision_id, llm_cost_cny, retry_count,
  started_at, completed_at, created_at, updated_at
) on public.experiment_actions to authenticated;
grant select (
  id, experiment_id, run_id, revision_id, kind, file_name, mime_type,
  byte_size, sha256, public_safe, metadata, created_at
) on public.experiment_artifacts to authenticated;
grant select on public.experiment_attempts to authenticated;
grant all on public.idea_experiments, public.experiment_revisions,
  public.experiment_runs, public.experiment_actions,
  public.experiment_llm_invocations, public.experiment_artifacts,
  public.experiment_runtime, public.experiment_validation_runtime,
  public.experiment_attempts, public.experiment_global_cost_ledger
  to service_role;

revoke all on public.experiment_runtime from public, anon, authenticated;
revoke all on public.experiment_validation_runtime from public, anon, authenticated;
revoke all on public.experiment_global_cost_ledger from public, anon, authenticated;
revoke insert, update, delete on public.idea_experiments, public.experiment_revisions,
  public.experiment_runs, public.experiment_actions, public.experiment_artifacts,
  public.experiment_attempts
from public, anon, authenticated;

insert into storage.buckets (id, name, public, file_size_limit)
values ('experiment-artifacts', 'experiment-artifacts', false, 1073741824)
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit;

create or replace function public.queue_deleted_experiment_artifact()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.storage_deletion_queue (bucket_id, storage_path)
  values ('experiment-artifacts', old.storage_path)
  on conflict (bucket_id, storage_path) do nothing;
  return old;
end;
$$;

create trigger queue_deleted_experiment_artifact
before delete on public.experiment_artifacts
for each row execute function public.queue_deleted_experiment_artifact();

revoke all on function public.queue_deleted_experiment_artifact() from public, anon, authenticated;

create or replace function public.claim_expired_experiment_storage()
returns table(record_id uuid, storage_path text)
language sql
security definer
set search_path = ''
as $$
  select storage_deletion_queue.id, storage_deletion_queue.storage_path
  from public.storage_deletion_queue
  where bucket_id = 'experiment-artifacts'
    and created_at < now() - interval '5 minutes';
$$;

revoke all on function public.claim_expired_experiment_storage() from public, anon, authenticated;
grant execute on function public.claim_expired_experiment_storage() to service_role;

-- One database-side budget/slot view is shared by automatic runs, interactive
-- actions and terminal sessions. Settled spend comes from the anonymous,
-- non-cascading ledger; live rows contribute only their unledgered delta and a
-- conservative reservation. experiment_runs are deliberately not summed here
-- because they describe the same sandbox interval and would double-charge it.
create or replace function public.current_experiment_e2b_commitment(
  p_estimated_cost_per_second_usd numeric default 0.000092,
  p_reserve_seconds integer default 3600
)
returns numeric
language sql
stable
security definer
set search_path = ''
as $$
  with active_runtime as (
    select runtime.experiment_id
    from public.experiment_runtime runtime
    where runtime.state in ('creating', 'running', 'destroying')
  ), active_validation_runtime as (
    select runtime.action_id, runtime.experiment_id
    from public.experiment_validation_runtime runtime
    where runtime.state in ('creating', 'running', 'destroying')
  ), active_without_runtime as (
    select experiments.id as experiment_id
    from public.idea_experiments experiments
    where experiments.status = 'running'
      and experiments.lease_expires_at > now()
      and not exists (
        select 1 from active_runtime where active_runtime.experiment_id = experiments.id
      )
    union
    select actions.experiment_id
    from public.experiment_actions actions
    where actions.status = 'running'
      and actions.lease_expires_at > now()
      and not exists (
        select 1 from active_runtime where active_runtime.experiment_id = actions.experiment_id
      )
      and not exists (
        select 1 from active_validation_runtime
        where active_validation_runtime.action_id = actions.id
      )
  )
  select
    coalesce((select sum(ledger.e2b_cost_usd)
      from public.experiment_global_cost_ledger ledger), 0)
    + coalesce((
      select sum(
        greatest(runtime.metered_cost_usd - coalesce(ledger.e2b_cost_usd, 0), 0)
        + case when runtime.state in ('creating', 'running', 'destroying') then
            greatest(
              extract(epoch from (
                greatest(
                  now(),
                  coalesce(
                    runtime.reserved_until,
                    now() + make_interval(secs => greatest(coalesce(p_reserve_seconds, 3600), 60))
                  )
                ) - coalesce(runtime.active_started_at, runtime.updated_at, runtime.created_at)
              )),
              0
            ) * coalesce(
              runtime.estimated_cost_per_second_usd,
              greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001)
            )
          else 0 end
      )
      from public.experiment_runtime runtime
      left join public.experiment_global_cost_ledger ledger
        on ledger.source_token = runtime.cost_ledger_token
    ), 0)
    + coalesce((
      select sum(
        greatest(runtime.metered_cost_usd - coalesce(ledger.e2b_cost_usd, 0), 0)
        + case when runtime.state in ('creating', 'running', 'destroying') then
            greatest(
              extract(epoch from (
                greatest(now(), coalesce(runtime.reserved_until, now() + make_interval(
                  secs => greatest(coalesce(p_reserve_seconds, 3600), 60)
                ))) - coalesce(runtime.active_started_at, runtime.updated_at, runtime.created_at)
              )), 0
            ) * coalesce(runtime.estimated_cost_per_second_usd,
              greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001))
          else 0 end
      )
      from public.experiment_validation_runtime runtime
      left join public.experiment_global_cost_ledger ledger
        on ledger.source_token = runtime.cost_ledger_token
    ), 0)
    + coalesce((select count(*) from active_without_runtime), 0)
      * greatest(coalesce(p_reserve_seconds, 3600), 60)
      * greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001);
$$;

create or replace function public.current_experiment_llm_commitment_cny()
returns numeric
language sql
stable
security definer
set search_path = ''
as $$
  select
    coalesce((select sum(ledger.llm_cost_cny)
      from public.experiment_global_cost_ledger ledger), 0)
    + coalesce((
      select sum(
        greatest(experiments.llm_cost_cny - coalesce(ledger.llm_cost_cny, 0), 0)
        + experiments.llm_reserved_cny
      )
      from public.idea_experiments experiments
      left join public.experiment_global_cost_ledger ledger
        on ledger.source_token = experiments.cost_ledger_token
    ), 0)
    + coalesce((select sum(actions.llm_reserved_cny)
      from public.experiment_actions actions
      where actions.status in ('queued', 'running', 'recovering')), 0);
$$;

create or replace function public.active_experiment_slot_count(
  p_exclude_experiment_id uuid default null,
  p_exclude_validation_action_id uuid default null
)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  with primary_runtime as (
    select runtime.experiment_id
    from public.experiment_runtime runtime
    where runtime.state in ('creating', 'running', 'destroying')
      and (p_exclude_experiment_id is null or runtime.experiment_id <> p_exclude_experiment_id)
  ), validation_runtime as (
    select runtime.action_id
    from public.experiment_validation_runtime runtime
    where runtime.state in ('creating', 'running', 'destroying')
      and (p_exclude_validation_action_id is null or runtime.action_id <> p_exclude_validation_action_id)
  ), fallback as (
    select experiments.id as experiment_id
    from public.idea_experiments experiments
    where experiments.status = 'running' and experiments.lease_expires_at > now()
      and (p_exclude_experiment_id is null or experiments.id <> p_exclude_experiment_id)
      and not exists (
        select 1 from public.experiment_runtime runtime
        where runtime.experiment_id = experiments.id
          and runtime.state in ('creating', 'running', 'destroying')
      )
    union
    select actions.experiment_id
    from public.experiment_actions actions
    where actions.status = 'running' and actions.lease_expires_at > now()
      and (p_exclude_experiment_id is null or actions.experiment_id <> p_exclude_experiment_id)
      and (p_exclude_validation_action_id is null or actions.id <> p_exclude_validation_action_id)
      and not exists (
        select 1 from public.experiment_runtime runtime
        where runtime.experiment_id = actions.experiment_id
          and runtime.state in ('creating', 'running', 'destroying')
      )
      and not exists (
        select 1 from public.experiment_validation_runtime runtime
        where runtime.action_id = actions.id
          and runtime.state in ('creating', 'running', 'destroying')
      )
  )
  select (
    (select count(*) from primary_runtime)
    + (select count(*) from validation_runtime)
    + (select count(*) from fallback)
  )::integer;
$$;

revoke all on function public.current_experiment_e2b_commitment(numeric, integer) from public, anon, authenticated;
revoke all on function public.current_experiment_llm_commitment_cny() from public, anon, authenticated;
revoke all on function public.active_experiment_slot_count(uuid, uuid) from public, anon, authenticated;
grant execute on function public.current_experiment_e2b_commitment(numeric, integer) to service_role;
grant execute on function public.current_experiment_llm_commitment_cny() to service_role;
grant execute on function public.active_experiment_slot_count(uuid, uuid) to service_role;

-- Keep the user-visible workspace cost derived from physical runtime meters.
-- experiment_runs are audit/result records for those same intervals and are
-- intentionally excluded to avoid double counting.
create or replace function public.refresh_experiment_runtime_totals(
  p_experiment_id uuid
)
returns void
language sql
security definer
set search_path = ''
as $$
  with runtime_totals as (
    select coalesce(sum(seconds), 0)::bigint as seconds,
      coalesce(sum(cost), 0)::numeric as cost
    from (
      select runtime.metered_seconds + case
          when runtime.state in ('creating', 'running', 'destroying')
            and runtime.active_started_at is not null
          then greatest(floor(extract(epoch from (now() - runtime.active_started_at)))::bigint, 0)
          else 0 end as seconds,
        runtime.metered_cost_usd + case
          when runtime.state in ('creating', 'running', 'destroying')
            and runtime.active_started_at is not null
          then greatest(extract(epoch from (now() - runtime.active_started_at)), 0)
            * runtime.estimated_cost_per_second_usd
          else 0 end as cost
      from public.experiment_runtime runtime
      where runtime.experiment_id = p_experiment_id
      union all
      select runtime.metered_seconds + case
          when runtime.state in ('creating', 'running', 'destroying')
            and runtime.active_started_at is not null
          then greatest(floor(extract(epoch from (now() - runtime.active_started_at)))::bigint, 0)
          else 0 end,
        runtime.metered_cost_usd + case
          when runtime.state in ('creating', 'running', 'destroying')
            and runtime.active_started_at is not null
          then greatest(extract(epoch from (now() - runtime.active_started_at)), 0)
            * runtime.estimated_cost_per_second_usd
          else 0 end
      from public.experiment_validation_runtime runtime
      where runtime.experiment_id = p_experiment_id
    ) physical_runtime
  )
  update public.idea_experiments experiments
  set e2b_seconds = runtime_totals.seconds,
      e2b_cost_usd = runtime_totals.cost,
      updated_at = now()
  from runtime_totals
  where experiments.id = p_experiment_id;
$$;

revoke all on function public.refresh_experiment_runtime_totals(uuid) from public, anon, authenticated;
grant execute on function public.refresh_experiment_runtime_totals(uuid) to service_role;

create or replace function public.enqueue_idea_experiment(
  p_report_id uuid,
  p_idea_key text,
  p_user_id uuid,
  p_automatic boolean default false,
  p_max_spend_usd numeric default 90,
  p_llm_reservation_cny numeric default 5,
  p_global_llm_max_cny numeric default 200
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_report public.reports;
  v_job public.jobs;
  v_ideas jsonb := '[]'::jsonb;
  v_idea jsonb;
  v_full_idea jsonb;
  v_spec jsonb;
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_report from public.reports where id = p_report_id;
  if not found then
    raise no_data_found using message = 'report not found';
  end if;

  select * into v_job from public.jobs where id = v_report.job_id for share;
  if not found or v_job.user_id <> p_user_id then
    raise insufficient_privilege using message = 'report access denied';
  end if;
  if v_job.status <> 'completed' then
    raise check_violation using message = 'analysis must be completed before starting an experiment';
  end if;
  if v_job.admin_deletion_requested_at is not null
    or exists (
      select 1 from public.profiles
      where profiles.id = p_user_id and profiles.deletion_requested_at is not null
    ) then
    raise insufficient_privilege using message = 'account or task deletion is pending';
  end if;

  -- Idempotent retries must still be able to retrieve an experiment that was
  -- created before the global budget was exhausted.
  select * into v_experiment
  from public.idea_experiments
  where report_id = p_report_id and idea_key = p_idea_key;
  if found then return v_experiment; end if;

  if public.current_experiment_e2b_commitment() >= least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    raise check_violation using message = 'experiment spend limit reached';
  end if;
  if public.current_experiment_llm_commitment_cny()
      + least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
    > greatest(coalesce(p_global_llm_max_cny, 200), 0) then
    raise check_violation using message = 'global experiment inference budget reached';
  end if;

  select coalesce(content->'ideas', '[]'::jsonb) into v_ideas
  from public.report_sections
  where report_id = p_report_id and section = 'ideas';
  if coalesce(jsonb_typeof(v_ideas), 'null') <> 'array' then
    v_ideas := coalesce(v_report.content #> '{presentation,ideas}', '[]'::jsonb);
  elsif jsonb_array_length(v_ideas) = 0 then
    v_ideas := coalesce(v_report.content #> '{presentation,ideas}', '[]'::jsonb);
  end if;
  if coalesce(jsonb_typeof(v_ideas), 'null') <> 'array' then
    v_ideas := coalesce(v_report.summary #> '{presentation,ideas}', v_report.summary->'ideas', '[]'::jsonb);
  elsif jsonb_array_length(v_ideas) = 0 then
    v_ideas := coalesce(v_report.summary #> '{presentation,ideas}', v_report.summary->'ideas', '[]'::jsonb);
  end if;

  select value into v_idea
  from jsonb_array_elements(v_ideas)
  where value->>'key' = p_idea_key
  limit 1;
  if v_idea is null then
    raise no_data_found using message = 'idea not found in report';
  end if;
  if coalesce(v_idea->>'verdict', '') not in ('recommended', 'alternative') then
    raise check_violation using message = 'only formally reviewed ideas can be validated';
  end if;

  -- The summary/section payload intentionally omits evaluator source code so
  -- opening a report stays fast. Freeze the executable contract from the
  -- private full report while keeping the smaller section Idea as the UI
  -- snapshot. The section value remains a legacy fallback only.
  select value into v_full_idea
  from jsonb_array_elements(
    case
      when jsonb_typeof(v_report.content #> '{presentation,ideas}') = 'array'
        then v_report.content #> '{presentation,ideas}'
      else '[]'::jsonb
    end
  )
  where value->>'key' = p_idea_key
  limit 1;
  v_spec := coalesce(
    v_full_idea->'pilot_specification',
    v_idea->'pilot_specification',
    '{}'::jsonb
  );
  if jsonb_typeof(v_spec) <> 'object' then v_spec := '{}'::jsonb; end if;
  if p_automatic and coalesce((v_idea->>'rank')::integer, 1) <> 1 then
    raise check_violation using message = 'only the primary idea may start automatically';
  end if;
  if p_automatic and v_spec = '{}'::jsonb then
    raise check_violation using message = 'automatic experiment requires a pilot specification';
  end if;

  insert into public.idea_experiments (
    report_id, job_id, user_id, idea_key, idea_rank, idea_snapshot,
    pilot_specification, pilot_specification_hash, pilot_compilation_required,
    automatic_initial_run, llm_reserved_cny
  ) values (
    p_report_id,
    v_report.job_id,
    p_user_id,
    p_idea_key,
    greatest(1, least(3, coalesce((v_idea->>'rank')::integer, 1))),
    v_idea,
    v_spec,
    -- Python freezes the specification with sorted, compact JSON and writes
    -- the canonical hash at the first worker checkpoint. PostgreSQL jsonb
    -- text formatting is not byte-compatible with that representation.
    null,
    v_spec = '{}'::jsonb,
    p_automatic,
    least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
  )
  on conflict (report_id, idea_key) do nothing
  returning * into v_experiment;

  if v_experiment.id is null then
    select * into v_experiment
    from public.idea_experiments
    where report_id = p_report_id and idea_key = p_idea_key;
  end if;
  return v_experiment;
end;
$$;

create or replace function public.list_pending_primary_experiments(
  p_limit integer default 25
)
returns table(job_id uuid, user_id uuid, idea_key text)
language sql
stable
security definer
set search_path = ''
as $$
  select jobs.id, jobs.user_id,
    jobs.checkpoint #>> '{experiment_auto_enqueue,idea_key}'
  from public.jobs
  where jobs.status = 'completed'
    and jobs.admin_deletion_requested_at is null
    and jobs.checkpoint #>> '{experiment_auto_enqueue,state}' = 'pending'
    and nullif(trim(jobs.checkpoint #>> '{experiment_auto_enqueue,idea_key}'), '') is not null
  order by jobs.updated_at, jobs.id
  limit least(greatest(coalesce(p_limit, 25), 1), 100);
$$;

-- Before cancellation or deletion can clear experiment/action rows,
-- conservatively turn any reservation left by an expired in-flight Claude
-- invocation into anonymous durable spend. This is idempotent because the
-- same transaction clears each reservation after charging it exactly once.
create or replace function public.settle_experiment_terminal_reservations(
  p_experiment_id uuid,
  p_reason text default 'terminal_cleanup_with_unsettled_reservation',
  p_include_running boolean default true
)
returns numeric
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action_amount numeric := 0;
  v_guard_amount numeric := 0;
  v_total numeric := 0;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then return 0; end if;
  select coalesce(sum(actions.llm_reserved_cny), 0) into v_action_amount
  from public.experiment_actions actions
  where actions.experiment_id = p_experiment_id
    and (
      actions.status = 'recovering'
      or (actions.status = 'cancelled' and actions.llm_reserved_cny > 0)
      or (coalesce(p_include_running, true) and actions.status = 'running')
    );
  v_total := case
      when v_experiment.status = 'recovering'
        or (coalesce(p_include_running, true) and v_experiment.status = 'running')
      then greatest(coalesce(v_experiment.llm_reserved_cny, 0), 0)
      else 0
    end
    + greatest(coalesce(v_action_amount, 0), 0);
  select coalesce(sum(invocations.reserved_cny), 0) into v_guard_amount
  from public.experiment_llm_invocations invocations
  where invocations.experiment_id = p_experiment_id
    and invocations.status = 'authorized'
    and (
      (
        invocations.action_id is null
        and (
          v_experiment.status = 'recovering'
          or (coalesce(p_include_running, true) and v_experiment.status = 'running')
        )
      )
      or exists (
        select 1 from public.experiment_actions actions
        where actions.id = invocations.action_id
          and actions.experiment_id = p_experiment_id
          and (
            actions.status = 'recovering'
            or (actions.status = 'cancelled' and actions.llm_reserved_cny > 0)
            or (coalesce(p_include_running, true) and actions.status = 'running')
          )
      )
    );
  -- Normally every guard is a subset of its parent's remaining envelope. If
  -- a provider reports an unavoidable overage for another call, the envelope
  -- can reach zero first; terminal cleanup must still conservatively account
  -- for every invocation that may already have started.
  v_total := greatest(v_total, greatest(coalesce(v_guard_amount, 0), 0));
  update public.experiment_llm_invocations invocations
  set status = 'settled',
      settled_cny = invocations.reserved_cny,
      settlement_kind = left(
        coalesce(p_reason, 'terminal_cleanup_with_unsettled_reservation'), 120
      ),
      settled_at = now(),
      updated_at = now()
  where invocations.experiment_id = p_experiment_id
    and invocations.status = 'authorized'
    and (
      (
        invocations.action_id is null
        and (
          v_experiment.status = 'recovering'
          or (coalesce(p_include_running, true) and v_experiment.status = 'running')
        )
      )
      or exists (
        select 1 from public.experiment_actions actions
        where actions.id = invocations.action_id
          and actions.experiment_id = p_experiment_id
          and (
            actions.status = 'recovering'
            or (actions.status = 'cancelled' and actions.llm_reserved_cny > 0)
            or (coalesce(p_include_running, true) and actions.status = 'running')
          )
      )
    );
  if v_total <= 0 then return 0; end if;

  update public.experiment_actions
  set llm_cost_cny = llm_cost_cny + llm_reserved_cny,
      llm_reserved_cny = 0,
      updated_at = now()
  where experiment_id = p_experiment_id and llm_reserved_cny > 0
    and (
      status = 'recovering'
      or (status = 'cancelled' and llm_reserved_cny > 0)
      or (coalesce(p_include_running, true) and status = 'running')
    );
  update public.idea_experiments
  set llm_cost_cny = llm_cost_cny + v_total,
      llm_reserved_cny = case
        when status = 'recovering'
          or (coalesce(p_include_running, true) and status = 'running')
        then 0 else llm_reserved_cny end,
      updated_at = now()
  where id = p_experiment_id returning * into v_experiment;
  insert into public.provider_usage (
    job_id, provider, model, requests, estimated_cny, metadata
  ) values (
    v_experiment.job_id, 'deepseek', null, 1, v_total,
    jsonb_build_object(
      'transport', 'claude_code', 'accounting_estimate', true,
      'reason', left(
        coalesce(p_reason, 'terminal_cleanup_with_unsettled_reservation'), 200
      )
    )
  );
  insert into public.experiment_global_cost_ledger (
    source_token, source_kind, llm_cost_cny, updated_at
  ) values (
    v_experiment.cost_ledger_token, 'llm', v_experiment.llm_cost_cny, now()
  ) on conflict (source_token) do update
  set llm_cost_cny = greatest(
        public.experiment_global_cost_ledger.llm_cost_cny,
        excluded.llm_cost_cny
      ),
      updated_at = now();
  return v_total;
end;
$$;

create or replace function public.claim_next_experiment(
  p_worker_id text,
  p_lease_seconds integer default 300,
  p_max_concurrency integer default 1,
  p_max_spend_usd numeric default 90,
  p_estimated_cost_per_second_usd numeric default 0.000092,
  p_reserve_seconds integer default 3600
)
returns setof public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise check_violation using message = 'worker id is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  -- Deletion cleanup is always claimable, even after the spend cap is reached.
  select * into v_experiment
  from public.idea_experiments
  where deletion_requested_at is not null
    and (worker_id is null or lease_expires_at is null or lease_expires_at <= now())
    and not exists (
      select 1 from public.experiment_actions actions
      where actions.experiment_id = idea_experiments.id
        and (
          actions.status in ('queued', 'recovering')
          or (actions.status = 'running' and actions.lease_expires_at > now())
        )
    )
    and not exists (
      select 1 from public.experiment_validation_runtime runtime
      where runtime.experiment_id = idea_experiments.id
        and runtime.state <> 'destroyed'
    )
  order by deletion_requested_at, created_at
  for update skip locked
  limit 1;
  if found then
    -- A worker may disappear after deletion was requested. Once its action
    -- lease expires and every clean validation runtime is confirmed gone, the
    -- deletion owner may close that orphan without letting it block forever.
    perform public.settle_experiment_terminal_reservations(
      v_experiment.id, 'deletion_with_unsettled_reservation'
    );
    update public.experiment_actions
    set status = 'cancelled', llm_reserved_cny = 0,
        validation_slot_reserved = false, validation_slot_consumed = false,
        worker_id = null, lease_expires_at = null, next_retry_at = null,
        completed_at = now(), updated_at = now()
    where experiment_id = v_experiment.id
      and status = 'running' and lease_expires_at <= now();
    update public.experiment_runs runs
    set status = 'cancelled', outcome = 'cancelled', completed_at = now(),
        safe_error = coalesce(safe_error, 'validation cancelled for deletion')
    where runs.action_id in (
      select actions.id from public.experiment_actions actions
      where actions.experiment_id = v_experiment.id and actions.status = 'cancelled'
    ) and runs.status in ('queued', 'running', 'recovering');
    update public.idea_experiments
    set status = 'running', stage = 'archive', worker_id = p_worker_id,
        lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
        updated_at = now()
    where id = v_experiment.id returning * into v_experiment;
    return next v_experiment;
    return;
  end if;
  -- Cancellation cleanup, like deletion, must remain claimable after a spend
  -- cap or feature shutdown. The original worker deliberately stops renewing
  -- once cancellation is requested; a new fenced owner must then settle any
  -- in-flight reservation and destroy the runtime instead of leaving the row
  -- permanently in `running`.
  select * into v_experiment
  from public.idea_experiments
  where cancellation_requested = true
    and deletion_requested_at is null
    and (worker_id is null or lease_expires_at is null or lease_expires_at <= now())
    and not exists (
      select 1 from public.experiment_actions actions
      where actions.experiment_id = idea_experiments.id
        and actions.status = 'running' and actions.lease_expires_at > now()
    )
    and not exists (
      select 1 from public.experiment_validation_runtime runtime
      where runtime.experiment_id = idea_experiments.id
        and runtime.state <> 'destroyed'
    )
  order by updated_at, created_at
  for update skip locked
  limit 1;
  if found then
    perform public.settle_experiment_terminal_reservations(
      v_experiment.id, 'cancellation_with_unsettled_reservation'
    );
    update public.experiment_actions
    set status = 'cancelled', llm_reserved_cny = 0,
        validation_slot_reserved = false, validation_slot_consumed = false,
        worker_id = null, lease_expires_at = null, next_retry_at = null,
        completed_at = now(), updated_at = now()
    where experiment_id = v_experiment.id
      and status = 'running' and lease_expires_at <= now();
    update public.experiment_runs runs
    set status = 'cancelled', outcome = 'cancelled', completed_at = now(),
        safe_error = coalesce(safe_error, 'validation cancelled by user')
    where runs.action_id in (
      select actions.id from public.experiment_actions actions
      where actions.experiment_id = v_experiment.id and actions.status = 'cancelled'
    ) and runs.status in ('queued', 'running', 'recovering');
    update public.idea_experiments
    set status = 'running', worker_id = p_worker_id,
        lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
        updated_at = now()
    where id = v_experiment.id returning * into v_experiment;
    return next v_experiment;
    return;
  end if;
  if public.current_experiment_e2b_commitment()
    >= least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    -- Reclaim an expired in-flight owner before parking new work. This does
    -- not authorize another runtime reservation: an existing runtime keeps
    -- its already-accounted reservation, while a missing runtime is fenced
    -- by save_claimed_experiment_runtime's incremental cap check and can only
    -- be closed out by the recovered worker.
    select * into v_experiment
    from public.idea_experiments
    where status = 'running'
      and lease_expires_at <= now()
      and cancellation_requested = false
      and deletion_requested_at is null
      and not exists (
        select 1 from public.experiment_runtime runtime
        where runtime.experiment_id = idea_experiments.id
          and runtime.state = 'destroying'
      )
      and public.active_experiment_slot_count(idea_experiments.id)
        < greatest(coalesce(p_max_concurrency, 1), 1)
    order by updated_at, created_at
    for update skip locked
    limit 1;
    if found then
      update public.idea_experiments
      set worker_id = p_worker_id,
          lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
          next_retry_at = null,
          updated_at = now()
      where id = v_experiment.id
      returning * into v_experiment;
      return next v_experiment;
      return;
    end if;
    update public.idea_experiments
    set status = 'waiting_resources',
        next_retry_at = now() + interval '6 hours',
        last_recovery_at = now(),
        worker_id = null,
        lease_expires_at = null,
        updated_at = now()
    where deletion_requested_at is null
      and cancellation_requested = false
      and (
        status = 'queued'
        or (status in ('recovering', 'waiting_resources')
          and coalesce(next_retry_at, now()) <= now())
      );
    return;
  end if;
  select * into v_experiment
  from public.idea_experiments
  where cancellation_requested = false
    and deletion_requested_at is null
    and not exists (
      select 1 from public.experiment_runtime runtime
      where runtime.experiment_id = idea_experiments.id
        and runtime.state = 'destroying'
    )
    and (
      status = 'queued'
      or (status in ('recovering', 'waiting_resources') and coalesce(next_retry_at, now()) <= now())
      or (status = 'running' and lease_expires_at <= now())
    )
  order by
    case when status in ('recovering', 'waiting_resources', 'running') then 0 else 1 end,
    coalesce(next_retry_at, created_at), created_at
  for update skip locked
  limit 1;
  if not found then
    return;
  end if;
  -- A recovered worker may reclaim its own still-live runtime, but a runtime,
  -- action or analysis belonging to any other experiment occupies the single
  -- global E2B slot.
  if public.active_experiment_slot_count(v_experiment.id)
    >= greatest(coalesce(p_max_concurrency, 1), 1) then
    return;
  end if;
  if not exists (
    select 1 from public.experiment_runtime runtime
    where runtime.experiment_id = v_experiment.id
      and runtime.state in ('creating', 'running', 'destroying')
  ) and public.current_experiment_e2b_commitment(
      p_estimated_cost_per_second_usd, p_reserve_seconds
    ) + greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001)
      * greatest(coalesce(p_reserve_seconds, 3600), 60)
    > least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    update public.idea_experiments
    set status = 'waiting_resources', next_retry_at = now() + interval '6 hours',
        last_recovery_at = now(), worker_id = null, lease_expires_at = null,
        updated_at = now()
    where id = v_experiment.id;
    return;
  end if;

  update public.idea_experiments
  set status = 'running',
      worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      started_at = coalesce(started_at, now()),
      next_retry_at = null,
      updated_at = now()
  where id = v_experiment.id
  returning * into v_experiment;
  return next v_experiment;
end;
$$;

create or replace function public.renew_experiment_lease(
  p_experiment_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.idea_experiments
  set lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      updated_at = now()
  where id = p_experiment_id and worker_id = p_worker_id
    and status = 'running' and cancellation_requested = false;
  return found;
end;
$$;

-- Feature-off workers must continue irreversible lifecycle/deletion cleanup
-- without being able to claim ordinary experiments or create new sandboxes.
create or replace function public.claim_next_experiment_cleanup(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise check_violation using message = 'worker id is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment
  from public.idea_experiments
  where (deletion_requested_at is not null or cancellation_requested = true)
    and (worker_id is null or lease_expires_at is null or lease_expires_at <= now())
    and not exists (
      select 1 from public.experiment_actions actions
      where actions.experiment_id = idea_experiments.id
        and (
          actions.status in ('queued', 'recovering')
          or (actions.status = 'running' and actions.lease_expires_at > now())
        )
    )
    and not exists (
      select 1 from public.experiment_validation_runtime runtime
      where runtime.experiment_id = idea_experiments.id
        and runtime.state <> 'destroyed'
    )
  order by deletion_requested_at nulls last, updated_at, created_at
  for update skip locked
  limit 1;
  if not found then return; end if;

  perform public.settle_experiment_terminal_reservations(
    v_experiment.id,
    case when v_experiment.deletion_requested_at is not null
      then 'deletion_with_unsettled_reservation'
      else 'cancellation_with_unsettled_reservation'
    end
  );
  update public.experiment_actions
  set status = 'cancelled', llm_reserved_cny = 0,
      validation_slot_reserved = false,
      worker_id = null, lease_expires_at = null, next_retry_at = null,
      completed_at = now(), updated_at = now()
  where experiment_id = v_experiment.id
    and status = 'running' and lease_expires_at <= now();
  update public.experiment_runs runs
  set status = 'cancelled', outcome = 'cancelled', completed_at = now(),
      safe_error = coalesce(
        safe_error,
        case when v_experiment.deletion_requested_at is not null
          then 'validation cancelled for deletion'
          else 'validation cancelled by user'
        end
      )
  where runs.action_id in (
    select actions.id from public.experiment_actions actions
    where actions.experiment_id = v_experiment.id and actions.status = 'cancelled'
  ) and runs.status in ('queued', 'running', 'recovering');
  update public.idea_experiments
  set status = 'running', stage = 'archive', worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      updated_at = now()
  where id = v_experiment.id returning * into v_experiment;
  return next v_experiment;
end;
$$;

create or replace function public.save_experiment_checkpoint(
  p_experiment_id uuid,
  p_worker_id text,
  p_stage text,
  p_progress integer,
  p_checkpoint jsonb,
  p_action_id uuid default null
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_stage not in (
    'spec_freeze', 'repo_generation', 'environment_setup', 'baseline',
    'intervention', 'evaluation', 'repair', 'archive', 'interactive'
  ) then
    raise check_violation using message = 'invalid experiment stage';
  end if;
  if jsonb_typeof(coalesce(p_checkpoint, '{}'::jsonb)) <> 'object' then
    raise check_violation using message = 'experiment checkpoint must be an object';
  end if;
  update public.idea_experiments
  set checkpoint = coalesce(p_checkpoint, '{}'::jsonb),
      stage = p_stage,
      progress = greatest(progress, least(greatest(p_progress, 0), 100)),
      last_activity_at = now(),
      updated_at = now()
  where id = p_experiment_id
    and (
      (
        p_action_id is null
        and idea_experiments.worker_id = p_worker_id
        and idea_experiments.status = 'running'
        and idea_experiments.lease_expires_at > now()
      )
      or (
        p_action_id is not null
        and exists (
          select 1 from public.experiment_actions
          where experiment_actions.id = p_action_id
            and experiment_actions.experiment_id = p_experiment_id
            and experiment_actions.worker_id = p_worker_id
            and experiment_actions.status = 'running'
            and experiment_actions.lease_expires_at > now()
        )
      )
    )
    and cancellation_requested = false
    and deletion_requested_at is null;
  return found;
end;
$$;

-- Whitelisted state updates performed by a live experiment or interactive
-- action worker. Keeping this as an RPC prevents service-role REST PATCHes
-- from bypassing lease fencing after a worker has been replaced.
create or replace function public.update_claimed_experiment(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid default null,
  p_pilot_specification jsonb default null,
  p_pilot_specification_hash text default null,
  p_pilot_compilation_required boolean default null,
  p_baseline_revision_id uuid default null,
  p_current_revision_id uuid default null,
  p_repair_count smallint default null,
  p_latest_run_id uuid default null,
  p_outcome text default null,
  p_public_summary jsonb default null
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now()
      or v_experiment.cancellation_requested
      or v_experiment.deletion_requested_at is not null then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1 from public.experiment_actions actions
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
  ) or v_experiment.status <> 'ready'
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  if p_baseline_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_baseline_revision_id and experiment_id = p_experiment_id
  ) then
    raise check_violation using message = 'baseline revision does not belong to experiment';
  end if;
  if p_current_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_current_revision_id and experiment_id = p_experiment_id
  ) then
    raise check_violation using message = 'current revision does not belong to experiment';
  end if;
  if p_latest_run_id is not null and not exists (
    select 1 from public.experiment_runs
    where id = p_latest_run_id and experiment_id = p_experiment_id
  ) then
    raise check_violation using message = 'latest run does not belong to experiment';
  end if;
  if p_repair_count is not null and (p_repair_count < 0 or p_repair_count > 2) then
    raise check_violation using message = 'invalid repair count';
  end if;
  if p_outcome is not null and p_outcome not in (
    'pending', 'initial_support', 'not_support', 'inconclusive',
    'environment_blocked', 'resource_limited', 'budget_blocked', 'cancelled'
  ) then
    raise check_violation using message = 'invalid experiment outcome';
  end if;
  update public.idea_experiments
  set pilot_specification = coalesce(p_pilot_specification, pilot_specification),
      pilot_specification_hash = coalesce(p_pilot_specification_hash, pilot_specification_hash),
      pilot_compilation_required = coalesce(
        p_pilot_compilation_required, pilot_compilation_required
      ),
      baseline_revision_id = coalesce(p_baseline_revision_id, baseline_revision_id),
      current_revision_id = coalesce(p_current_revision_id, current_revision_id),
      repair_count = coalesce(p_repair_count, repair_count),
      latest_run_id = coalesce(p_latest_run_id, latest_run_id),
      outcome = coalesce(p_outcome, outcome),
      public_summary = coalesce(p_public_summary, public_summary),
      last_activity_at = now(),
      updated_at = now()
  where id = p_experiment_id
  returning * into v_experiment;
  return v_experiment;
end;
$$;

create or replace function public.save_claimed_experiment_runtime(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid,
  p_state text,
  p_sandbox_id text default null,
  p_paused_at timestamptz default null,
  p_clear_paused_at boolean default false,
  p_destroy_after timestamptz default null,
  p_last_heartbeat_at timestamptz default null,
  p_metadata jsonb default null,
  p_estimated_cost_per_second_usd numeric default 0.000092,
  p_reserve_seconds integer default 3600,
  p_max_spend_usd numeric default 90,
  p_max_concurrency integer default 1
)
returns public.experiment_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_runtime public.experiment_runtime;
  v_existing public.experiment_runtime;
  v_elapsed_seconds bigint := 0;
  v_rate numeric := greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001);
  v_becomes_active boolean;
  v_runtime_exists boolean := false;
  v_reserve_until timestamptz;
  v_current_commitment numeric := 0;
  v_existing_reservation numeric := 0;
  v_target_reservation numeric := 0;
  v_incremental_reservation numeric := 0;
  v_active_started_at timestamptz;
begin
  if p_state not in ('creating', 'running', 'paused', 'destroyed') then
    raise check_violation using message = 'invalid claimed runtime state';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now()
      or ((v_experiment.cancellation_requested or v_experiment.deletion_requested_at is not null)
        and p_state <> 'destroyed') then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1 from public.experiment_actions actions
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
  ) or v_experiment.status <> 'ready'
    or ((v_experiment.cancellation_requested or v_experiment.deletion_requested_at is not null)
      and p_state <> 'destroyed') then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  select * into v_existing from public.experiment_runtime
  where experiment_id = p_experiment_id for update;
  v_runtime_exists := found;
  if v_runtime_exists and v_existing.state = 'destroying'
    and v_existing.lifecycle_lease_expires_at > now() then
    raise serialization_failure using message = 'experiment runtime lifecycle lease is active';
  end if;
  v_becomes_active := p_state in ('creating', 'running');
  v_reserve_until := now() + make_interval(
    secs => greatest(coalesce(p_reserve_seconds, 3600), 60)
  );
  if v_becomes_active then
    if public.active_experiment_slot_count(p_experiment_id)
      >= greatest(coalesce(p_max_concurrency, 1), 1) then
      raise check_violation using message = 'global experiment concurrency limit reached';
    end if;
    v_current_commitment := public.current_experiment_e2b_commitment(
      v_rate, p_reserve_seconds
    );
    if v_runtime_exists
      and v_existing.state in ('creating', 'running', 'destroying') then
      v_active_started_at := coalesce(
        v_existing.active_started_at, v_existing.updated_at, v_existing.created_at
      );
      v_existing_reservation := greatest(
        extract(epoch from (
          greatest(now(), coalesce(v_existing.reserved_until, v_reserve_until))
          - v_active_started_at
        )),
        0
      ) * coalesce(v_existing.estimated_cost_per_second_usd, v_rate);
      v_target_reservation := greatest(
        extract(epoch from (
          greatest(
            now(),
            greatest(coalesce(v_existing.reserved_until, now()), v_reserve_until)
          ) - v_active_started_at
        )),
        0
      ) * v_rate;
    else
      v_target_reservation := greatest(
        extract(epoch from (v_reserve_until - now())), 0
      ) * v_rate;
      -- The live primary lease always has a fallback. An action lease has the
      -- same fallback unless its own separately metered validation runtime is
      -- already active.
      if p_action_id is null or not exists (
        select 1 from public.experiment_validation_runtime validation_runtime
        where validation_runtime.action_id = p_action_id
          and validation_runtime.state in ('creating', 'running', 'destroying')
      ) then
        v_existing_reservation := v_target_reservation;
      end if;
    end if;
    v_incremental_reservation := greatest(
      v_target_reservation - v_existing_reservation, 0
    );
    -- A claimed parent/action without a live runtime already contributes the
    -- same fallback reservation. Only an extension or rate increase on an
    -- existing active runtime is incremental; an equivalent fallback-to-row
    -- conversion remains legal when commitment is exactly at the hard cap.
    if v_current_commitment + v_incremental_reservation
      > least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
      raise check_violation using message = 'experiment spend limit reached';
    end if;
  end if;

  if v_runtime_exists then
    if v_existing.state in ('creating', 'running', 'destroying')
      and not v_becomes_active then
      v_elapsed_seconds := greatest(
        floor(extract(epoch from (now() - coalesce(
          v_existing.active_started_at, v_existing.updated_at, v_existing.created_at
        ))))::bigint,
        0
      );
    end if;
    update public.experiment_runtime
    set sandbox_id = coalesce(p_sandbox_id, sandbox_id),
        state = p_state,
        paused_at = case when p_clear_paused_at then null
          else coalesce(p_paused_at, paused_at) end,
        destroy_after = coalesce(p_destroy_after, destroy_after),
        last_heartbeat_at = coalesce(p_last_heartbeat_at, last_heartbeat_at),
        metadata = coalesce(p_metadata, metadata),
        active_started_at = case
          when v_becomes_active and v_existing.state in ('creating', 'running', 'destroying')
            then coalesce(v_existing.active_started_at, v_existing.updated_at, v_existing.created_at)
          when v_becomes_active then now()
          else null
        end,
        reserved_until = case when v_becomes_active then greatest(
          coalesce(v_existing.reserved_until, now()),
          v_reserve_until
        ) else null end,
        metered_seconds = metered_seconds + v_elapsed_seconds,
        metered_cost_usd = metered_cost_usd + v_elapsed_seconds
          * coalesce(v_existing.estimated_cost_per_second_usd, v_rate),
        estimated_cost_per_second_usd = v_rate,
        terminal_ticket_hash = case when p_state in ('paused', 'destroyed') then null else terminal_ticket_hash end,
        terminal_ticket_mode = case when p_state in ('paused', 'destroyed') then null else terminal_ticket_mode end,
        terminal_ticket_expires_at = case when p_state in ('paused', 'destroyed') then null else terminal_ticket_expires_at end,
        terminal_session_epoch = case when p_state in ('paused', 'destroyed')
          then terminal_session_epoch + 1 else terminal_session_epoch end,
        lifecycle_claim_token = null,
        lifecycle_lease_expires_at = null,
        updated_at = now()
    where experiment_id = p_experiment_id
    returning * into v_runtime;
  else
    insert into public.experiment_runtime (
      experiment_id, sandbox_id, state, paused_at, destroy_after,
      last_heartbeat_at, metadata, active_started_at, reserved_until,
      estimated_cost_per_second_usd, updated_at
    ) values (
      p_experiment_id, p_sandbox_id, p_state,
      case when p_clear_paused_at then null else p_paused_at end,
      p_destroy_after, p_last_heartbeat_at, coalesce(p_metadata, '{}'::jsonb),
      case when v_becomes_active then now() else null end,
      case when v_becomes_active then v_reserve_until else null end,
      v_rate, now()
    ) returning * into v_runtime;
  end if;
  if p_state in ('paused', 'destroyed') then
    insert into public.experiment_global_cost_ledger (
      source_token, source_kind, e2b_cost_usd, updated_at
    ) values (
      v_runtime.cost_ledger_token, 'runtime', v_runtime.metered_cost_usd, now()
    ) on conflict (source_token) do update
    set e2b_cost_usd = greatest(
          public.experiment_global_cost_ledger.e2b_cost_usd,
          excluded.e2b_cost_usd
        ),
        updated_at = now();
  end if;
  perform public.refresh_experiment_runtime_totals(p_experiment_id);
  return v_runtime;
end;
$$;

create or replace function public.register_claimed_experiment_artifact(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid,
  p_run_id uuid,
  p_revision_id uuid,
  p_kind text,
  p_storage_path text,
  p_file_name text,
  p_mime_type text,
  p_byte_size bigint,
  p_sha256 text,
  p_public_safe boolean,
  p_metadata jsonb default '{}'::jsonb
)
returns public.experiment_artifacts
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_artifact public.experiment_artifacts;
begin
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now()
      or v_experiment.cancellation_requested
      or v_experiment.deletion_requested_at is not null then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1 from public.experiment_actions actions
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
  ) or v_experiment.status <> 'ready'
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  if p_kind not in (
    'repository_zip', 'git_bundle', 'source_file', 'log', 'metrics',
    'plot', 'result_report', 'diff', 'other'
  ) then raise check_violation using message = 'invalid experiment artifact kind'; end if;
  if p_run_id is not null and not exists (
    select 1 from public.experiment_runs
    where id = p_run_id and experiment_id = p_experiment_id
  ) then raise check_violation using message = 'artifact run does not belong to experiment'; end if;
  if p_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_revision_id and experiment_id = p_experiment_id
  ) then raise check_violation using message = 'artifact revision does not belong to experiment'; end if;
  select * into v_artifact from public.experiment_artifacts
  where storage_path = p_storage_path;
  if found then
    if v_artifact.experiment_id <> p_experiment_id then
      raise unique_violation using message = 'artifact path belongs to another experiment';
    end if;
    return v_artifact;
  end if;
  insert into public.experiment_artifacts (
    experiment_id, run_id, revision_id, kind, storage_path, file_name,
    mime_type, byte_size, sha256, public_safe, metadata
  ) values (
    p_experiment_id, p_run_id, p_revision_id, p_kind, p_storage_path,
    p_file_name, coalesce(nullif(p_mime_type, ''), 'application/octet-stream'),
    p_byte_size, p_sha256, coalesce(p_public_safe, false),
    coalesce(p_metadata, '{}'::jsonb)
  ) returning * into v_artifact;
  return v_artifact;
end;
$$;

create or replace function public.create_experiment_revision(
  p_experiment_id uuid,
  p_parent_revision_id uuid,
  p_actor text,
  p_git_commit text,
  p_tree_hash text,
  p_bundle_storage_path text,
  p_summary jsonb default '{}'::jsonb,
  p_immutable boolean default false,
  p_worker_id text default null,
  p_action_id uuid default null
)
returns public.experiment_revisions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_revision public.experiment_revisions;
  v_revision_number integer;
begin
  perform 1 from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if not exists (
      select 1 from public.idea_experiments
      where id = p_experiment_id
        and worker_id = p_worker_id
        and status = 'running'
        and lease_expires_at > now()
        and cancellation_requested = false
        and deletion_requested_at is null
    ) then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1
    from public.experiment_actions actions
    join public.idea_experiments experiments on experiments.id = actions.experiment_id
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
      and experiments.status = 'ready'
      and experiments.cancellation_requested = false
      and experiments.deletion_requested_at is null
  ) then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  if p_actor not in ('automatic', 'user', 'assistant', 'terminal', 'system') then
    raise check_violation using message = 'invalid experiment revision actor';
  end if;
  if p_git_commit is null or length(trim(p_git_commit)) = 0 then
    raise check_violation using message = 'git commit is required';
  end if;
  select * into v_revision from public.experiment_revisions
  where experiment_id = p_experiment_id and git_commit = p_git_commit
  order by revision_number desc limit 1;
  if found then return v_revision; end if;
  if p_parent_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_parent_revision_id and experiment_id = p_experiment_id
  ) then
    raise check_violation using message = 'parent revision does not belong to experiment';
  end if;
  select coalesce(max(revision_number), 0) + 1 into v_revision_number
  from public.experiment_revisions where experiment_id = p_experiment_id;
  insert into public.experiment_revisions (
    experiment_id, parent_revision_id, revision_number, actor, git_commit,
    tree_hash, bundle_storage_path, summary, immutable
  ) values (
    p_experiment_id, p_parent_revision_id, v_revision_number, p_actor,
    p_git_commit, p_tree_hash, p_bundle_storage_path,
    coalesce(p_summary, '{}'::jsonb), p_immutable
  ) returning * into v_revision;
  return v_revision;
end;
$$;

drop function if exists public.create_experiment_run(uuid, uuid, text, boolean, text, uuid);
create or replace function public.create_experiment_run(
  p_experiment_id uuid,
  p_revision_id uuid,
  p_trigger_kind text,
  p_reuse_running boolean default false,
  p_worker_id text default null,
  p_action_id uuid default null,
  p_max_active_seconds integer default 3600
)
returns public.experiment_runs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_run public.experiment_runs;
  v_run_number integer;
  v_action public.experiment_actions;
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if not exists (
      select 1 from public.idea_experiments
      where id = p_experiment_id
        and worker_id = p_worker_id
        and status = 'running'
        and lease_expires_at > now()
        and cancellation_requested = false
        and deletion_requested_at is null
    ) then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1
    from public.experiment_actions actions
    join public.idea_experiments experiments on experiments.id = actions.experiment_id
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
      and experiments.status = 'ready'
      and experiments.cancellation_requested = false
      and experiments.deletion_requested_at is null
  ) then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  if p_trigger_kind not in ('automatic', 'user', 'repair') then
    raise check_violation using message = 'invalid experiment run trigger';
  end if;
  if p_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_revision_id and experiment_id = p_experiment_id
  ) then
    raise check_violation using message = 'run revision does not belong to experiment';
  end if;
  if p_trigger_kind = 'user' then
    if p_action_id is null then
      raise check_violation using message = 'manual validation action is required';
    end if;
    select * into v_action from public.experiment_actions
    where id = p_action_id and experiment_id = p_experiment_id for update;
    if not found or v_action.kind <> 'validation' then
      raise check_violation using message = 'manual validation action is required';
    end if;
    if v_action.validation_slot_reserved then
      if v_experiment.user_validation_count >= v_experiment.max_user_validations then
        raise check_violation using message = 'manual validation limit reached';
      end if;
      update public.idea_experiments
      set user_validation_count = user_validation_count + 1, updated_at = now()
      where id = p_experiment_id
      returning * into v_experiment;
      update public.experiment_actions
      set validation_slot_reserved = false,
          validation_slot_consumed = true,
          updated_at = now()
      where id = p_action_id;
    elsif not v_action.validation_slot_consumed then
      raise check_violation using message = 'manual validation slot is not reserved';
    end if;
  end if;
  if p_reuse_running then
    select * into v_run from public.experiment_runs
    where experiment_id = p_experiment_id
      and trigger_kind = p_trigger_kind
      and status = 'running'
      and (
        (p_action_id is not null and action_id = p_action_id)
        or (
          p_action_id is null and action_id is null
          and revision_id is not distinct from p_revision_id
        )
      )
    order by run_number desc limit 1;
    if found then return v_run; end if;
  end if;
  select coalesce(max(run_number), 0) + 1 into v_run_number
  from public.experiment_runs where experiment_id = p_experiment_id;
  insert into public.experiment_runs (
    experiment_id, action_id, revision_id, run_number, trigger_kind, status,
    started_at, hard_deadline_at
  ) values (
    p_experiment_id, p_action_id, p_revision_id, v_run_number, p_trigger_kind,
    'running', now(), now() + make_interval(
      secs => least(greatest(coalesce(p_max_active_seconds, 3600), 60), 3600)
    )
  ) returning * into v_run;
  update public.idea_experiments
  set latest_run_id = v_run.id, last_activity_at = now(), updated_at = now()
  where id = p_experiment_id;
  return v_run;
end;
$$;

-- Called immediately before every potentially long validation command. It
-- verifies the same fenced Worker/action lease as the run mutations and
-- returns a timeout that can only shrink across retries and process restarts.
create or replace function public.assert_experiment_run_within_deadline(
  p_run_id uuid,
  p_worker_id text,
  p_action_id uuid default null
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_run public.experiment_runs;
  v_experiment public.idea_experiments;
  v_remaining integer;
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = (
    select runs.experiment_id from public.experiment_runs runs
    where runs.id = p_run_id
  ) for update;
  if not found then raise no_data_found using message = 'experiment run not found'; end if;
  select * into v_run from public.experiment_runs
  where id = p_run_id and experiment_id = v_experiment.id for update;
  if not found or v_run.status <> 'running' then
    raise serialization_failure using message = 'experiment run is not active';
  end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now()
      or v_experiment.cancellation_requested
      or v_experiment.deletion_requested_at is not null then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1 from public.experiment_actions actions
    where actions.id = p_action_id
      and actions.experiment_id = v_experiment.id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
  ) or v_experiment.status <> 'ready'
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null
    or v_run.action_id is distinct from p_action_id then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  v_remaining := floor(extract(epoch from (v_run.hard_deadline_at - now())))::integer;
  if v_remaining <= 0 then
    raise check_violation using message = 'experiment run deadline reached';
  end if;
  return least(v_remaining, 3600);
end;
$$;

create or replace function public.increment_experiment_costs(
  p_experiment_id uuid,
  p_llm_cost_cny numeric default 0,
  p_e2b_seconds bigint default 0,
  p_e2b_cost_usd numeric default 0,
  p_worker_id text default null,
  p_action_id uuid default null,
  p_action_llm_max_cny numeric default 5,
  p_assistant_llm_max_cny numeric default 20,
  p_experiment_llm_max_cny numeric default 40,
  p_global_llm_max_cny numeric default 200,
  p_job_id uuid default null,
  p_provider text default null,
  p_model text default null,
  p_input_tokens bigint default 0,
  p_output_tokens bigint default 0,
  p_requests integer default 0,
  p_usage_metadata jsonb default '{}'::jsonb,
  p_usage_id uuid default null
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_invocation public.experiment_llm_invocations;
  v_llm_delta numeric := greatest(coalesce(p_llm_cost_cny, 0), 0);
  v_remaining_guard_cny numeric := 0;
begin
  if coalesce(p_llm_cost_cny, 0) < 0
    or coalesce(p_e2b_seconds, 0) < 0
    or coalesce(p_e2b_cost_usd, 0) < 0 then
    raise check_violation using message = 'experiment cost increments cannot be negative';
  end if;
  if coalesce(p_e2b_seconds, 0) <> 0 or coalesce(p_e2b_cost_usd, 0) <> 0 then
    raise check_violation using message = 'E2B usage must be settled by a fenced runtime meter';
  end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  else
    select * into v_action from public.experiment_actions
    where id = p_action_id and experiment_id = p_experiment_id for update;
    if not found or v_action.worker_id is distinct from p_worker_id
      or v_action.status <> 'running'
      or v_action.lease_expires_at is null
      or v_action.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment action worker lease lost';
    end if;
  end if;

  -- This function settles provider usage that has already been incurred. The
  -- invocation guard gates the call before it starts; rejecting an overage
  -- here would erase real spend and let retries bypass the cumulative cap.
  -- Future calls are blocked after this durable settlement.
  if v_llm_delta > 0 and p_usage_id is null then
    raise check_violation using message = 'experiment provider usage id is required';
  end if;
  if p_usage_id is not null then
    select * into v_invocation
    from public.experiment_llm_invocations
    where usage_id = p_usage_id
    for update;
    if not found then
      if exists (
        select 1 from public.provider_usage usage
        where usage.metadata->>'experiment_usage_id' = p_usage_id::text
      ) then
        return v_experiment;
      end if;
      raise check_violation using message = 'experiment invocation was not authorized';
    end if;
    if v_invocation.experiment_id <> p_experiment_id
      or v_invocation.action_id is distinct from p_action_id then
      raise check_violation using message = 'experiment invocation id belongs to another call';
    end if;
    if v_invocation.status = 'settled' then
      return v_experiment;
    end if;
    if exists (
      select 1 from public.provider_usage usage
      where usage.metadata->>'experiment_usage_id' = p_usage_id::text
    ) then
      update public.experiment_llm_invocations
      set status = 'settled', settlement_kind = 'exact_usage',
          settled_at = now(), updated_at = now()
      where usage_id = p_usage_id;
      return v_experiment;
    end if;
    select coalesce(sum(invocations.reserved_cny), 0)
    into v_remaining_guard_cny
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id is not distinct from p_action_id
      and invocations.status = 'authorized'
      and invocations.usage_id <> p_usage_id;
  end if;
  if p_job_id is not null and p_job_id <> v_experiment.job_id then
    raise check_violation using message = 'provider usage job does not match experiment';
  end if;
  if p_usage_id is not null then
    insert into public.provider_usage (
      job_id, provider, model, input_tokens, output_tokens, requests,
      estimated_cny, metadata
    ) values (
      v_experiment.job_id, coalesce(nullif(trim(p_provider), ''), 'deepseek'),
      p_model, greatest(coalesce(p_input_tokens, 0), 0),
      greatest(coalesce(p_output_tokens, 0), 0),
      greatest(coalesce(p_requests, 0), 0), v_llm_delta,
      coalesce(p_usage_metadata, '{}'::jsonb)
        || jsonb_build_object('experiment_usage_id', p_usage_id::text)
    );
  end if;
  update public.idea_experiments
  set llm_cost_cny = llm_cost_cny + v_llm_delta,
      llm_reserved_cny = case when p_action_id is null then
        greatest(
          llm_reserved_cny - v_llm_delta,
          v_remaining_guard_cny,
          0
        ) else llm_reserved_cny end,
      e2b_seconds = e2b_seconds + coalesce(p_e2b_seconds, 0),
      e2b_cost_usd = e2b_cost_usd + coalesce(p_e2b_cost_usd, 0),
      updated_at = now()
  where id = p_experiment_id
  returning * into v_experiment;
  if p_action_id is not null and v_llm_delta > 0 then
    update public.experiment_actions
    set llm_cost_cny = llm_cost_cny + v_llm_delta,
        -- A provider can exceptionally settle above its reservation. Keep
        -- every other authorized invocation covered by the parent envelope.
        llm_reserved_cny = greatest(
          llm_reserved_cny - v_llm_delta,
          v_remaining_guard_cny,
          0
        ),
        updated_at = now()
    where id = p_action_id;
  end if;
  if v_llm_delta > 0 then
    insert into public.experiment_global_cost_ledger (
      source_token, source_kind, llm_cost_cny, updated_at
    ) values (
      v_experiment.cost_ledger_token, 'llm', v_experiment.llm_cost_cny, now()
    ) on conflict (source_token) do update
    set llm_cost_cny = greatest(
          public.experiment_global_cost_ledger.llm_cost_cny,
          excluded.llm_cost_cny
        ),
        updated_at = now();
  end if;
  if p_usage_id is not null then
    update public.experiment_llm_invocations
    set status = 'settled', settled_cny = v_llm_delta,
        settlement_kind = 'exact_usage', settled_at = now(), updated_at = now()
    where usage_id = p_usage_id and status = 'authorized';
  end if;
  return v_experiment;
end;
$$;

-- Fence every paid Claude invocation against the durable reservation that was
-- created with the automatic run or interactive action. A Worker restart may
-- not reset this envelope: once the reservation is exhausted, no later claim
-- can start another provider call merely because its in-memory counter reset.
drop function if exists public.authorize_experiment_llm_call(uuid, text, uuid);
create or replace function public.authorize_experiment_llm_call(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid default null,
  p_usage_id uuid default null,
  p_max_call_cny numeric default null
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_invocation public.experiment_llm_invocations;
  v_max_call_cny numeric := round(greatest(coalesce(p_max_call_cny, 0), 0), 6);
  v_available_cny numeric := 0;
begin
  if p_usage_id is null then
    raise check_violation using message = 'experiment provider usage id is required';
  end if;
  if v_max_call_cny <= 0 then
    raise check_violation using message = 'positive experiment call reservation is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise check_violation using message = 'experiment is being cancelled or deleted';
  end if;

  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  else
    select * into v_action from public.experiment_actions
    where id = p_action_id and experiment_id = p_experiment_id for update;
    if not found or v_action.worker_id is distinct from p_worker_id
      or v_action.status <> 'running'
      or v_action.lease_expires_at is null
      or v_action.lease_expires_at <= now()
      or v_experiment.status <> 'ready' then
      raise serialization_failure using message = 'experiment action worker lease lost';
    end if;
  end if;

  select * into v_invocation
  from public.experiment_llm_invocations
  where usage_id = p_usage_id
  for update;
  if found then
    if v_invocation.experiment_id <> p_experiment_id
      or v_invocation.action_id is distinct from p_action_id
      or v_invocation.reserved_cny <> v_max_call_cny then
      raise check_violation using message = 'experiment invocation id belongs to another call';
    end if;
    if v_invocation.status <> 'authorized' then
      raise check_violation using message = 'experiment invocation is already settled';
    end if;
    -- The authorization response may be lost after commit. Reusing the same
    -- immutable usage id is idempotent and must not reserve the call twice.
    return v_experiment;
  end if;
  if exists (
    select 1 from public.provider_usage usage
    where usage.metadata->>'experiment_usage_id' = p_usage_id::text
  ) then
    raise check_violation using message = 'experiment invocation is already settled';
  end if;

  if p_action_id is null then
    select greatest(
      v_experiment.llm_reserved_cny - coalesce(sum(invocations.reserved_cny), 0),
      0
    ) into v_available_cny
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id is null
      and invocations.status = 'authorized';
  else
    select greatest(
      v_action.llm_reserved_cny - coalesce(sum(invocations.reserved_cny), 0),
      0
    ) into v_available_cny
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id = p_action_id
      and invocations.status = 'authorized';
  end if;
  if v_available_cny < v_max_call_cny then
    raise check_violation using message = 'experiment inference budget reached';
  end if;

  insert into public.experiment_llm_invocations (
    usage_id, experiment_id, action_id, reserved_cny
  ) values (
    p_usage_id, p_experiment_id, p_action_id, v_max_call_cny
  );
  return v_experiment;
end;
$$;

create or replace function public.sync_experiment_run_costs(
  p_experiment_id uuid
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  perform public.refresh_experiment_runtime_totals(p_experiment_id);
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  return v_experiment;
end;
$$;

drop function if exists public.settle_experiment_llm_reservation(uuid, text, uuid, text);
create or replace function public.settle_experiment_llm_reservation(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid default null,
  p_reason text default 'provider_usage_unavailable',
  p_usage_id uuid default null
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_invocation public.experiment_llm_invocations;
  v_amount numeric := 0;
  v_remaining_guard_cny numeric := 0;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  else
    select * into v_action from public.experiment_actions
    where id = p_action_id and experiment_id = p_experiment_id for update;
    if not found or v_action.worker_id is distinct from p_worker_id
      or v_action.status <> 'running'
      or v_action.lease_expires_at is null
      or v_action.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment action worker lease lost';
    end if;
  end if;

  if p_usage_id is not null then
    select * into v_invocation
    from public.experiment_llm_invocations
    where usage_id = p_usage_id
    for update;
    if not found then
      if exists (
        select 1 from public.provider_usage usage
        where usage.metadata->>'experiment_usage_id' = p_usage_id::text
      ) then
        return v_experiment;
      end if;
      raise check_violation using message = 'experiment invocation was not authorized';
    end if;
    if v_invocation.experiment_id <> p_experiment_id
      or v_invocation.action_id is distinct from p_action_id then
      raise check_violation using message = 'experiment invocation id belongs to another call';
    end if;
    if v_invocation.status = 'settled' then
      return v_experiment;
    end if;
    if exists (
      select 1 from public.provider_usage usage
      where usage.metadata->>'experiment_usage_id' = p_usage_id::text
    ) then
      update public.experiment_llm_invocations
      set status = 'settled', settlement_kind = 'exact_usage',
          settled_at = now(), updated_at = now()
      where usage_id = p_usage_id;
      return v_experiment;
    end if;
    v_amount := v_invocation.reserved_cny;
    select coalesce(sum(invocations.reserved_cny), 0)
    into v_remaining_guard_cny
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id is not distinct from p_action_id
      and invocations.status = 'authorized'
      and invocations.usage_id <> p_usage_id;
  elsif p_action_id is null then
    -- Compatibility fallback for an old worker during a rolling deployment.
    -- New workers always settle one durable invocation by usage id.
    v_amount := v_experiment.llm_reserved_cny;
  else
    v_amount := v_action.llm_reserved_cny;
  end if;
  if v_amount > 0 then
    update public.idea_experiments
    set llm_cost_cny = llm_cost_cny + v_amount,
        llm_reserved_cny = case when p_action_id is null then
          case when p_usage_id is null then 0 else greatest(
            llm_reserved_cny - v_amount,
            v_remaining_guard_cny,
            0
          ) end
        else llm_reserved_cny end,
        updated_at = now()
    where id = p_experiment_id returning * into v_experiment;
    if p_action_id is not null then
      update public.experiment_actions
      set llm_cost_cny = llm_cost_cny + v_amount,
          llm_reserved_cny = case when p_usage_id is null then 0 else greatest(
            llm_reserved_cny - v_amount,
            v_remaining_guard_cny,
            0
          ) end,
          updated_at = now()
      where id = p_action_id;
    end if;
    insert into public.provider_usage (
      job_id, provider, model, requests, estimated_cny, metadata
    ) values (
      v_experiment.job_id, 'deepseek', null, 1, v_amount,
      jsonb_build_object(
        'transport', 'claude_code', 'accounting_estimate', true,
        'reason', left(coalesce(p_reason, 'provider_usage_unavailable'), 200)
      ) || case when p_usage_id is null then '{}'::jsonb else
        jsonb_build_object('experiment_usage_id', p_usage_id::text) end
    );
    insert into public.experiment_global_cost_ledger (
      source_token, source_kind, llm_cost_cny, updated_at
    ) values (
      v_experiment.cost_ledger_token, 'llm', v_experiment.llm_cost_cny, now()
    ) on conflict (source_token) do update
    set llm_cost_cny = greatest(
          public.experiment_global_cost_ledger.llm_cost_cny,
          excluded.llm_cost_cny
        ),
        updated_at = now();
  end if;
  if p_usage_id is not null then
    update public.experiment_llm_invocations
    set status = 'settled', settled_cny = v_amount,
        settlement_kind = left(
          coalesce(p_reason, 'provider_usage_unavailable'), 120
        ),
        settled_at = now(), updated_at = now()
    where usage_id = p_usage_id and status = 'authorized';
  else
    update public.experiment_llm_invocations
    set status = 'settled', settled_cny = reserved_cny,
        settlement_kind = left(
          coalesce(p_reason, 'provider_usage_unavailable'), 120
        ),
        settled_at = now(), updated_at = now()
    where experiment_id = p_experiment_id
      and action_id is not distinct from p_action_id
      and status = 'authorized';
  end if;
  return v_experiment;
end;
$$;

create or replace function public.finalize_experiment_run(
  p_run_id uuid,
  p_status text,
  p_outcome text,
  p_commands jsonb default '{}'::jsonb,
  p_metrics jsonb default '{}'::jsonb,
  p_evaluation jsonb default '{}'::jsonb,
  p_safe_error text default null,
  p_e2b_seconds bigint default 0,
  p_e2b_cost_usd numeric default 0,
  p_llm_cost_cny numeric default 0,
  p_worker_id text default null,
  p_action_id uuid default null
)
returns public.experiment_runs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_run public.experiment_runs;
  v_experiment_id uuid;
begin
  if p_status not in ('completed', 'cancelled') then
    raise check_violation using message = 'invalid finalized experiment run status';
  end if;
  if p_outcome not in (
    'initial_support', 'not_support', 'inconclusive', 'environment_blocked',
    'resource_limited', 'budget_blocked', 'cancelled'
  ) then
    raise check_violation using message = 'invalid finalized experiment run outcome';
  end if;
  if (p_status = 'cancelled') <> (p_outcome = 'cancelled') then
    raise check_violation using message = 'cancelled run status and outcome must agree';
  end if;
  if coalesce(p_e2b_seconds, 0) < 0
    or coalesce(p_e2b_cost_usd, 0) < 0
    or coalesce(p_llm_cost_cny, 0) < 0 then
    raise check_violation using message = 'experiment run costs cannot be negative';
  end if;
  select experiment_id into v_experiment_id from public.experiment_runs
  where id = p_run_id;
  if not found then raise no_data_found using message = 'experiment run not found'; end if;
  -- Match the lock order used by claims and settlement: global budget fence,
  -- parent experiment, then the child run.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  perform 1 from public.idea_experiments
  where id = v_experiment_id for update;
  select * into v_run from public.experiment_runs
  where id = p_run_id and experiment_id = v_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment run not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if not exists (
      select 1 from public.idea_experiments
      where id = v_run.experiment_id
        and worker_id = p_worker_id
        and status = 'running'
        and lease_expires_at > now()
        and cancellation_requested = false
        and deletion_requested_at is null
    ) then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1
    from public.experiment_actions actions
    join public.idea_experiments experiments on experiments.id = actions.experiment_id
    where actions.id = p_action_id
      and actions.experiment_id = v_run.experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
      and experiments.status = 'ready'
      and experiments.cancellation_requested = false
      and experiments.deletion_requested_at is null
  ) then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  update public.experiment_runs
  set status = p_status,
      outcome = p_outcome,
      commands = coalesce(p_commands, '{}'::jsonb),
      metrics = coalesce(p_metrics, '{}'::jsonb),
      evaluation = coalesce(p_evaluation, '{}'::jsonb),
      safe_error = left(p_safe_error, 2000),
      e2b_seconds = coalesce(p_e2b_seconds, 0),
      e2b_cost_usd = coalesce(p_e2b_cost_usd, 0),
      llm_cost_cny = coalesce(p_llm_cost_cny, 0),
      completed_at = coalesce(completed_at, now())
  where id = p_run_id
  returning * into v_run;
  update public.idea_experiments
  set latest_run_id = v_run.id,
      -- LLM usage is posted incrementally by the Claude Code usage callback.
      -- The per-run value remains an audit snapshot, but must not be added a
      -- second time while finalizing or resuming the same run.
      last_activity_at = now(),
      updated_at = now()
  where id = v_run.experiment_id;
  perform public.refresh_experiment_runtime_totals(v_run.experiment_id);
  return v_run;
end;
$$;

create or replace function public.schedule_experiment_retry(
  p_experiment_id uuid,
  p_worker_id text,
  p_status text,
  p_retry_seconds integer,
  p_failure_category text,
  p_safe_error text default null
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_retry integer;
begin
  if p_status not in ('recovering', 'waiting_resources') then
    raise check_violation using message = 'invalid experiment recovery status';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if v_experiment.worker_id is distinct from p_worker_id
    or v_experiment.status <> 'running'
    or v_experiment.lease_expires_at is null
    or v_experiment.lease_expires_at <= now() then
    raise serialization_failure using message = 'experiment worker lease lost';
  end if;
  if v_experiment.cancellation_requested then
    perform public.settle_experiment_terminal_reservations(
      v_experiment.id, 'cancellation_during_recovery_with_unsettled_reservation'
    );
    update public.idea_experiments
    set status = 'cancelled', outcome = 'cancelled', stage = 'archive',
        llm_reserved_cny = 0,
        worker_id = null, lease_expires_at = null, next_retry_at = null,
        completed_at = now(), updated_at = now()
    where id = p_experiment_id returning * into v_experiment;
    return v_experiment;
  end if;

  v_retry := v_experiment.retry_count + 1;
  insert into public.experiment_attempts (
    experiment_id, attempt_number, failure_category, checkpoint_stage, safe_error
  ) values (
    p_experiment_id, v_retry, left(coalesce(p_failure_category, 'unknown'), 120),
    left(coalesce(v_experiment.stage, 'unknown'), 120), left(p_safe_error, 2000)
  );
  update public.idea_experiments
  set status = p_status,
      retry_count = v_retry,
      next_retry_at = now() + make_interval(secs => greatest(p_retry_seconds, 1)),
      last_recovery_at = now(), worker_id = null, lease_expires_at = null,
      updated_at = now()
  where id = p_experiment_id returning * into v_experiment;
  return v_experiment;
end;
$$;

create or replace function public.finish_experiment(
  p_experiment_id uuid,
  p_worker_id text,
  p_status text,
  p_outcome text,
  p_public_summary jsonb default '{}'::jsonb
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  if p_status not in ('ready', 'cancelled') then
    raise check_violation using message = 'finish_experiment requires a terminal status';
  end if;
  if p_outcome not in (
    'initial_support', 'not_support', 'inconclusive', 'environment_blocked',
    'resource_limited', 'budget_blocked', 'cancelled'
  ) then
    raise check_violation using message = 'invalid experiment outcome';
  end if;
  if (p_status = 'cancelled') <> (p_outcome = 'cancelled') then
    raise check_violation using message = 'cancelled status and outcome must agree';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if v_experiment.worker_id is distinct from p_worker_id
    or v_experiment.status <> 'running'
    or v_experiment.lease_expires_at is null
    or v_experiment.lease_expires_at <= now() then
    raise serialization_failure using message = 'experiment worker lease lost';
  end if;
  -- A cancellation/deletion request that races the final ready transition wins.
  -- The fenced worker may finish cleanup, but it must not resurrect a ready
  -- workspace after the owner requested termination.
  if v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    p_status := 'cancelled';
    p_outcome := 'cancelled';
    p_public_summary := jsonb_build_object('outcome', 'cancelled');
  end if;
  if p_status = 'cancelled' then
    perform public.settle_experiment_terminal_reservations(
      v_experiment.id, 'cancellation_finish_with_unsettled_reservation'
    );
  end if;
  if p_status = 'ready' and exists (
    select 1 from public.experiment_llm_invocations invocations
    where invocations.experiment_id = p_experiment_id
      and invocations.action_id is null
      and invocations.status = 'authorized'
  ) then
    raise serialization_failure using message = 'experiment invocation settlement pending';
  end if;
  update public.idea_experiments
  set status = p_status, outcome = p_outcome,
      stage = case when p_status = 'ready' then 'interactive' else 'archive' end,
      progress = case when p_status = 'ready' then 100 else progress end,
      public_summary = coalesce(p_public_summary, '{}'::jsonb),
      llm_reserved_cny = 0,
      worker_id = null, lease_expires_at = null, next_retry_at = null,
      completed_at = now(), updated_at = now()
  where id = p_experiment_id returning * into v_experiment;
  return v_experiment;
end;
$$;

create or replace function public.enqueue_experiment_action(
  p_experiment_id uuid,
  p_user_id uuid,
  p_kind text,
  p_request jsonb default '{}'::jsonb,
  p_base_revision_id uuid default null,
  p_idempotency_key text default null,
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
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
begin
  if p_kind = 'chat' then p_kind := 'assistant'; end if;
  if p_kind not in (
    'assistant', 'chat', 'save_file', 'move_file', 'delete_file', 'read_file',
    'command', 'rollback', 'validation', 'restore'
  ) then
    raise check_violation using message = 'invalid experiment action';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found or v_experiment.user_id <> p_user_id then
    raise insufficient_privilege using message = 'experiment access denied';
  end if;
  if v_experiment.deletion_requested_at is not null or v_experiment.cancellation_requested then
    raise check_violation using message = 'experiment is being cancelled or deleted';
  end if;
  if p_kind <> 'read_file' and v_experiment.status <> 'ready' then
    raise check_violation using message = 'experiment is read-only until the automatic run is ready';
  end if;
  if p_kind = 'read_file' and v_experiment.status not in ('running', 'recovering', 'waiting_resources', 'ready') then
    raise check_violation using message = 'experiment files are not available';
  end if;
  -- An idempotent transport retry must return the action that was already
  -- accepted even if that action is now the active mutation or has advanced
  -- the current revision. The experiment owner check above still applies.
  if p_idempotency_key is not null then
    select * into v_action from public.experiment_actions
    where experiment_id = p_experiment_id
      and idempotency_key = left(p_idempotency_key, 160);
    if found then return v_action; end if;
  end if;
  if p_base_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_base_revision_id and experiment_id = p_experiment_id
  ) then
    raise check_violation using message = 'base revision does not belong to experiment';
  end if;
  if p_kind in (
      'save_file', 'move_file', 'delete_file', 'assistant', 'chat',
      'command', 'rollback', 'validation', 'restore'
    )
    and p_base_revision_id is distinct from v_experiment.current_revision_id then
    raise serialization_failure using message = 'experiment revision conflict';
  end if;
  -- The locked parent row serializes admission. At most one queued, running,
  -- or recovering revision-sensitive operation may exist, so two requests
  -- cannot both be admitted against the same base revision and later apply
  -- out of order. Read-only file reads remain independent.
  if p_kind <> 'read_file' and exists (
    select 1 from public.experiment_actions actions
    where actions.experiment_id = p_experiment_id
      and actions.kind <> 'read_file'
      and actions.status in ('queued', 'running', 'recovering')
  ) then
    raise serialization_failure using message = 'experiment revision conflict';
  end if;
  if p_kind <> 'read_file'
    and public.current_experiment_e2b_commitment()
      >= least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    raise check_violation using message = 'experiment spend limit reached';
  end if;
  if p_kind in ('assistant', 'chat', 'validation') then
    if v_experiment.llm_cost_cny
      + coalesce((
        select sum(actions.llm_reserved_cny)
        from public.experiment_actions actions
        where actions.experiment_id = p_experiment_id
          and actions.status in ('queued', 'running', 'recovering')
      ), 0)
      + least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
      > greatest(coalesce(p_experiment_llm_max_cny, 40), 0) then
      raise check_violation using message = 'experiment inference budget reached';
    end if;
    if p_kind in ('assistant', 'chat') and (
      coalesce((
        select sum(actions.llm_cost_cny + case
          when actions.status in ('queued', 'running', 'recovering')
            then actions.llm_reserved_cny else 0 end)
        from public.experiment_actions actions
        where actions.experiment_id = p_experiment_id
          and actions.kind in ('assistant', 'chat')
      ), 0)
      + least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
      > greatest(coalesce(p_assistant_llm_max_cny, 20), 0)
    ) then
      raise check_violation using message = 'experiment assistant budget reached';
    end if;
    if public.current_experiment_llm_commitment_cny()
      + least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
      > greatest(coalesce(p_global_llm_max_cny, 200), 0) then
      raise check_violation using message = 'global experiment inference budget reached';
    end if;
  end if;
  if p_kind = 'validation' then
    if v_experiment.user_validation_count + (
      select count(*) from public.experiment_actions actions
      where actions.experiment_id = p_experiment_id
        and actions.kind = 'validation'
        and actions.validation_slot_reserved
        and actions.status in ('queued', 'running', 'recovering')
    ) >= v_experiment.max_user_validations then
      raise check_violation using message = 'manual validation limit reached';
    end if;
  end if;

  insert into public.experiment_actions (
    experiment_id, requested_by, kind, request, base_revision_id, idempotency_key,
    llm_reserved_cny, validation_slot_reserved
  ) values (
    p_experiment_id, p_user_id, p_kind, coalesce(p_request, '{}'::jsonb),
    p_base_revision_id, nullif(left(p_idempotency_key, 160), ''),
    case when p_kind in ('assistant', 'chat', 'validation')
      then least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5) else 0 end,
    p_kind = 'validation'
  )
  on conflict (experiment_id, idempotency_key)
    where idempotency_key is not null do nothing
  returning * into v_action;
  if v_action.id is null and p_idempotency_key is not null then
    select * into v_action from public.experiment_actions
    where experiment_id = p_experiment_id and idempotency_key = left(p_idempotency_key, 160);
  end if;
  return v_action;
end;
$$;

create or replace function public.claim_next_experiment_action(
  p_worker_id text,
  p_lease_seconds integer default 300,
  p_max_spend_usd numeric default 90,
  p_max_concurrency integer default 1,
  p_estimated_cost_per_second_usd numeric default 0.000092,
  p_reserve_seconds integer default 3600
)
returns setof public.experiment_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_action public.experiment_actions;
  v_stale_action public.experiment_actions;
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise check_violation using message = 'worker id is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  -- A queued/recovering action can become stale if another fenced writer
  -- advances the repository revision before this worker claims it. Never run
  -- such an action against a different tree. Recovering reservations are
  -- conservatively settled before cancellation; queued reservations have not
  -- started a provider call and are simply released.
  select actions.* into v_stale_action
  from public.experiment_actions actions
  join public.idea_experiments experiments on experiments.id = actions.experiment_id
  where actions.status in ('queued', 'recovering')
    and actions.kind in (
      'save_file', 'move_file', 'delete_file', 'assistant', 'chat',
      'command', 'rollback', 'validation', 'restore'
    )
    and actions.base_revision_id is distinct from experiments.current_revision_id
    and experiments.status = 'ready'
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null
  order by actions.created_at
  for update of actions, experiments skip locked
  limit 1;
  if found then
    perform public.settle_experiment_terminal_reservations(
      v_stale_action.experiment_id,
      'stale_action_revision_conflict',
      false
    );
    update public.experiment_actions
    set status = 'cancelled', llm_reserved_cny = 0,
        validation_slot_reserved = false, next_retry_at = null,
        worker_id = null, lease_expires_at = null,
        safe_error = 'experiment revision conflict',
        completed_at = now(), updated_at = now()
    where id = v_stale_action.id;
    update public.experiment_runs
    set status = 'cancelled', outcome = 'cancelled',
        safe_error = 'experiment revision conflict',
        completed_at = now()
    where action_id = v_stale_action.id
      and status in ('queued', 'running', 'recovering');
    return;
  end if;
  if public.current_experiment_e2b_commitment()
    >= least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    -- At the hard cap, only reclaim a lease-expired action that already owns
    -- the in-flight work. New/recovering actions remain parked and runtime
    -- creation is still fenced by the incremental reservation check.
    select actions.* into v_action
    from public.experiment_actions actions
    join public.idea_experiments experiments on experiments.id = actions.experiment_id
    where actions.status = 'running'
      and actions.lease_expires_at <= now()
      and experiments.status = 'ready'
      and experiments.cancellation_requested = false
      and experiments.deletion_requested_at is null
      and not exists (
        select 1 from public.experiment_runtime runtime
        where runtime.experiment_id = actions.experiment_id
          and runtime.state = 'destroying'
      )
      and not exists (
        select 1 from public.experiment_actions active_action
        where active_action.experiment_id = actions.experiment_id
          and active_action.id <> actions.id
          and active_action.status = 'running'
          and active_action.lease_expires_at > now()
      )
      and public.active_experiment_slot_count(actions.experiment_id, actions.id)
        < greatest(coalesce(p_max_concurrency, 1), 1)
    order by actions.updated_at, actions.created_at
    for update of actions, experiments skip locked
    limit 1;
    if found then
      update public.experiment_actions
      set worker_id = p_worker_id,
          lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
          next_retry_at = null,
          updated_at = now()
      where id = v_action.id
      returning * into v_action;
      if v_action.kind <> 'read_file' then
        update public.experiment_runtime
        set terminal_ticket_hash = null,
            terminal_ticket_mode = null,
            terminal_ticket_expires_at = null,
            terminal_session_epoch = terminal_session_epoch + 1,
            pty_session_id = null,
            updated_at = now()
        where experiment_id = v_action.experiment_id;
      end if;
      return next v_action;
      return;
    end if;
    update public.experiment_actions actions
    set status = 'recovering',
        next_retry_at = now() + interval '6 hours',
        safe_error = null,
        worker_id = null,
        lease_expires_at = null,
        updated_at = now()
    from public.idea_experiments experiments
    where experiments.id = actions.experiment_id
      and experiments.status = 'ready'
      and experiments.cancellation_requested = false
      and experiments.deletion_requested_at is null
      and actions.status in ('queued', 'recovering')
      and coalesce(actions.next_retry_at, now()) <= now();
    return;
  end if;
  select actions.* into v_action
  from public.experiment_actions actions
  join public.idea_experiments experiments on experiments.id = actions.experiment_id
  where (
      (actions.status in ('queued', 'recovering')
        and coalesce(actions.next_retry_at, now()) <= now()
        and (actions.lease_expires_at is null or actions.lease_expires_at <= now()))
      or (actions.status = 'running' and actions.lease_expires_at <= now())
    )
    -- Interactive work may only start after the immutable automatic baseline
    -- was archived. This prevents action and analysis workers from mutating
    -- the same checkpoint/repository concurrently.
    and experiments.status = 'ready'
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null
    and not exists (
      select 1 from public.experiment_runtime runtime
      where runtime.experiment_id = actions.experiment_id
        and runtime.state = 'destroying'
    )
    and not exists (
      select 1 from public.experiment_actions active_action
      where active_action.experiment_id = actions.experiment_id
        and active_action.id <> actions.id
        and active_action.status = 'running'
        and active_action.lease_expires_at > now()
    )
  order by actions.created_at
  for update of actions, experiments skip locked
  limit 1;
  if not found then return; end if;
  if public.active_experiment_slot_count(v_action.experiment_id, v_action.id)
    >= greatest(coalesce(p_max_concurrency, 1), 1) then
    return;
  end if;
  if not exists (
    select 1 from public.experiment_runtime runtime
    where runtime.experiment_id = v_action.experiment_id
      and runtime.state in ('creating', 'running', 'destroying')
  ) and public.current_experiment_e2b_commitment(
      p_estimated_cost_per_second_usd, p_reserve_seconds
    ) + greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001)
      * greatest(coalesce(p_reserve_seconds, 3600), 60)
    > least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    update public.experiment_actions
    set status = 'recovering', next_retry_at = now() + interval '6 hours',
        safe_error = null, worker_id = null, lease_expires_at = null,
        updated_at = now()
    where id = v_action.id;
    return;
  end if;
  update public.experiment_actions
  set status = 'running', worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      started_at = coalesce(started_at, now()), next_retry_at = null, updated_at = now()
  where id = v_action.id returning * into v_action;
  if v_action.kind <> 'read_file' then
    -- A mutable action owns the repository exclusively. Revoke every terminal
    -- attachment in the same transaction before the Worker receives it; the
    -- Worker also kills the fixed tmux session before applying any mutation.
    update public.experiment_runtime
    set terminal_ticket_hash = null,
        terminal_ticket_mode = null,
        terminal_ticket_expires_at = null,
        terminal_session_epoch = terminal_session_epoch + 1,
        pty_session_id = null,
        updated_at = now()
    where experiment_id = v_action.experiment_id;
  end if;
  return next v_action;
end;
$$;

create or replace function public.renew_experiment_action_lease(
  p_action_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.experiment_actions actions
  set lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
      updated_at = now()
  from public.idea_experiments experiments
  where actions.id = p_action_id
    and actions.worker_id = p_worker_id
    and actions.status = 'running'
    and experiments.id = actions.experiment_id
    and experiments.status = 'ready'
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null;
  return found;
end;
$$;

create or replace function public.save_experiment_action_progress(
  p_action_id uuid,
  p_worker_id text,
  p_response jsonb
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.experiment_actions
  set response = coalesce(p_response, '{}'::jsonb), updated_at = now()
  where id = p_action_id
    and worker_id = p_worker_id
    and status = 'running'
    and lease_expires_at > now();
  return found;
end;
$$;

create or replace function public.finish_experiment_action(
  p_action_id uuid,
  p_worker_id text,
  p_success boolean,
  p_response jsonb default '{}'::jsonb,
  p_result_revision_id uuid default null,
  p_retry_seconds integer default 30,
  p_safe_error text default null
)
returns public.experiment_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_action public.experiment_actions;
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = (
    select actions.experiment_id from public.experiment_actions actions
    where actions.id = p_action_id
  ) for update;
  if not found then raise no_data_found using message = 'experiment action not found'; end if;
  select * into v_action from public.experiment_actions
  where id = p_action_id and experiment_id = v_experiment.id for update;
  if not found then raise no_data_found using message = 'experiment action not found'; end if;
  if v_action.worker_id is distinct from p_worker_id
    or v_action.status <> 'running'
    or v_action.lease_expires_at is null
    or v_action.lease_expires_at <= now() then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  -- Cancellation/deletion wins every race with an action result. The valid
  -- fenced owner may still settle already-incurred usage, but it may not put
  -- the action back into recovery and thereby block deletion indefinitely.
  if v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    perform public.settle_experiment_terminal_reservations(
      v_experiment.id,
      case when v_experiment.deletion_requested_at is not null
        then 'deletion_action_race_with_unsettled_reservation'
        else 'cancellation_action_race_with_unsettled_reservation'
      end
    );
    p_success := false;
    p_retry_seconds := 0;
    p_result_revision_id := null;
    p_safe_error := coalesce(p_safe_error, 'experiment action cancelled');
  end if;
  if p_result_revision_id is not null and not exists (
    select 1 from public.experiment_revisions
    where id = p_result_revision_id and experiment_id = v_action.experiment_id
  ) then
    raise check_violation using message = 'result revision does not belong to experiment';
  end if;
  if (p_success or p_retry_seconds <= 0) and exists (
    select 1 from public.experiment_llm_invocations invocations
    where invocations.experiment_id = v_action.experiment_id
      and invocations.action_id = p_action_id
      and invocations.status = 'authorized'
  ) then
    raise serialization_failure using message = 'experiment invocation settlement pending';
  end if;

  if p_success then
    update public.experiment_actions
    set status = 'completed', response = coalesce(p_response, '{}'::jsonb),
        result_revision_id = p_result_revision_id, safe_error = null,
        llm_reserved_cny = 0, validation_slot_reserved = false,
        worker_id = null, lease_expires_at = null, completed_at = now(), updated_at = now()
    where id = p_action_id returning * into v_action;
    if p_result_revision_id is not null then
      update public.idea_experiments
      set current_revision_id = p_result_revision_id, last_activity_at = now(), updated_at = now()
      where id = v_action.experiment_id;
    end if;
  else
    insert into public.experiment_attempts (
      experiment_id, action_id, attempt_number, failure_category, checkpoint_stage, safe_error
    ) values (
      v_action.experiment_id, v_action.id, v_action.retry_count + 1,
      'action', v_action.kind, left(p_safe_error, 2000)
    );
    update public.experiment_actions
    set status = case when p_retry_seconds <= 0 then 'cancelled' else 'recovering' end,
        retry_count = retry_count + 1,
        next_retry_at = case when p_retry_seconds <= 0 then null
          else now() + make_interval(secs => greatest(p_retry_seconds, 1)) end,
        safe_error = left(p_safe_error, 2000), worker_id = null,
        llm_reserved_cny = case when p_retry_seconds <= 0 then 0 else llm_reserved_cny end,
        validation_slot_reserved = case when p_retry_seconds <= 0
          then false else validation_slot_reserved end,
        -- Once create_experiment_run consumes a user validation slot, the
        -- attempt permanently counts even if it is cancelled or fails.
        validation_slot_consumed = validation_slot_consumed,
        lease_expires_at = null,
        completed_at = case when p_retry_seconds <= 0 then now() else completed_at end,
        updated_at = now()
    where id = p_action_id returning * into v_action;
    if p_retry_seconds <= 0 then
      update public.experiment_runs
      set status = 'cancelled', outcome = 'cancelled',
          safe_error = left(p_safe_error, 2000), completed_at = now()
      where action_id = p_action_id
        and status in ('queued', 'running', 'recovering');
    end if;
  end if;
  return v_action;
end;
$$;

create or replace function public.request_experiment_cancellation(
  p_experiment_id uuid,
  p_user_id uuid
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id and user_id = p_user_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if v_experiment.status in ('ready', 'cancelled') then return v_experiment; end if;
  perform public.settle_experiment_terminal_reservations(
    v_experiment.id, 'cancellation_of_recovering_invocation', false
  );
  update public.idea_experiments
  set cancellation_requested = true,
      status = case when status in ('queued', 'recovering', 'waiting_resources') then 'cancelled' else status end,
      outcome = case when status in ('queued', 'recovering', 'waiting_resources') then 'cancelled' else outcome end,
      stage = case when status in ('queued', 'recovering', 'waiting_resources') then 'archive' else stage end,
      llm_reserved_cny = case when status in ('queued', 'recovering', 'waiting_resources')
        then 0 else llm_reserved_cny end,
      completed_at = case when status in ('queued', 'recovering', 'waiting_resources') then now() else completed_at end,
      updated_at = now()
  where id = p_experiment_id returning * into v_experiment;
  return v_experiment;
end;
$$;

create or replace function public.request_experiment_deletion(
  p_experiment_id uuid,
  p_user_id uuid
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id and user_id = p_user_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  perform public.settle_experiment_terminal_reservations(
    v_experiment.id, 'deletion_of_recovering_invocation', false
  );
  update public.idea_experiments
  set deletion_requested_at = coalesce(deletion_requested_at, now()),
      cancellation_requested = true,
      -- A live Claude invocation owns this reservation until its usage callback
      -- settles. Deletion cleanup converts any amount still present after the
      -- lease expires into the anonymous ledger before cascading this row.
      llm_reserved_cny = case
        when status = 'running'
          then llm_reserved_cny
        else 0 end,
      status = case when status in ('queued', 'recovering', 'waiting_resources') then 'cancelled' else status end,
      outcome = case when status in ('queued', 'recovering', 'waiting_resources') then 'cancelled' else outcome end,
      stage = case when status in ('queued', 'recovering', 'waiting_resources') then 'archive' else stage end,
      updated_at = now()
  where id = p_experiment_id returning * into v_experiment;
  update public.experiment_actions
  set status = 'cancelled',
      llm_reserved_cny = case when status = 'queued' then 0 else llm_reserved_cny end,
      validation_slot_reserved = false, next_retry_at = null,
      worker_id = null, lease_expires_at = null,
      completed_at = now(), updated_at = now()
  where experiment_id = p_experiment_id
    and status in ('queued', 'recovering');
  return v_experiment;
end;
$$;

create or replace function public.propagate_job_deletion_to_experiments()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.admin_deletion_requested_at is not null
    and old.admin_deletion_requested_at is distinct from new.admin_deletion_requested_at then
    update public.idea_experiments
    set deletion_requested_at = coalesce(deletion_requested_at, now()),
        cancellation_requested = true,
        updated_at = now()
    where job_id = new.id;
    update public.experiment_actions actions
    set status = 'cancelled',
        llm_reserved_cny = case when actions.status = 'queued'
          then 0 else actions.llm_reserved_cny end,
        validation_slot_reserved = false, next_retry_at = null,
        worker_id = null, lease_expires_at = null,
        completed_at = now(), updated_at = now()
    from public.idea_experiments experiments
    where experiments.id = actions.experiment_id
      and experiments.job_id = new.id
      and actions.status in ('queued', 'recovering');
  end if;
  return new;
end;
$$;

create trigger propagate_job_deletion_to_experiments
after update of admin_deletion_requested_at on public.jobs
for each row execute function public.propagate_job_deletion_to_experiments();

revoke all on function public.propagate_job_deletion_to_experiments() from public, anon, authenticated;

-- Use a lock compatible with the provider_usage -> jobs foreign-key KEY SHARE
-- check. The previous FOR UPDATE order could deadlock with usage settlement
-- (budget advisory -> experiment -> provider_usage/jobs) while this trigger
-- propagated job deletion in the opposite jobs -> experiment direction.
create or replace function public.admin_request_job_deletion(
  p_job_id uuid,
  p_requester_id uuid
)
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

  select * into v_job from public.jobs where id = p_job_id for no key update;
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

create or replace function public.request_user_job_deletion(p_job_id uuid, p_user_id uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job public.jobs;
begin
  select * into v_job from public.jobs
  where id = p_job_id and user_id = p_user_id for no key update;
  if not found then raise no_data_found using message = 'job not found'; end if;
  if v_job.status not in ('completed', 'cancelled', 'failed', 'budget_blocked', 'needs_input') then
    raise check_violation using message = 'active analysis must be cancelled before deletion';
  end if;
  insert into public.admin_deletion_requests (target_kind, target_id, requested_by)
  values ('job', p_job_id, null)
  on conflict (target_kind, target_id) where state in ('pending', 'processing')
  do update set next_attempt_at = least(public.admin_deletion_requests.next_attempt_at, now()),
                updated_at = now();
  update public.jobs
  set admin_deletion_requested_at = coalesce(admin_deletion_requested_at, now()),
      cancellation_requested = true, updated_at = now()
  where id = p_job_id;
  return 'pending';
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
      select 1 from public.jobs where id = p_target_id and lease_expires_at > now()
    ) and not exists (
      select 1 from public.idea_experiments where job_id = p_target_id
    );
  elsif p_target_kind = 'user' then
    return not exists (
      select 1 from public.jobs where user_id = p_target_id and lease_expires_at > now()
    ) and not exists (
      select 1 from public.idea_experiments where user_id = p_target_id
    );
  end if;
  return false;
end;
$$;

drop function if exists public.list_my_jobs(integer, integer, boolean);
create or replace function public.list_my_jobs(
  p_limit integer default 20,
  p_offset integer default 0,
  p_favorites_only boolean default false
)
returns table (
  total_count bigint, id uuid, mode public.analysis_mode, max_rounds smallint,
  current_round smallint, status public.job_status, stage text, progress smallint,
  created_at timestamptz, completed_at timestamptz, is_favorite boolean,
  file_names text[], report_id uuid, retry_count integer,
  next_retry_at timestamptz, last_recovery_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select count(*) over(), jobs.id, jobs.mode, jobs.max_rounds, jobs.current_round,
    jobs.status, jobs.stage, jobs.progress, jobs.created_at, jobs.completed_at,
    jobs.is_favorite,
    coalesce((
      select array_agg(uploads.original_name order by job_files.position)
      from public.job_files join public.uploads on uploads.id = job_files.upload_id
      where job_files.job_id = jobs.id
    ), array[]::text[]),
    (select reports.id from public.reports where reports.job_id = jobs.id limit 1),
    jobs.retry_count, jobs.next_retry_at, jobs.last_recovery_at
  from public.jobs
  where jobs.user_id = auth.uid()
    and jobs.admin_deletion_requested_at is null
    and (not p_favorites_only or jobs.is_favorite)
  order by jobs.is_favorite desc, jobs.created_at desc
  limit least(greatest(p_limit, 1), 100) offset greatest(p_offset, 0);
$$;

create or replace function public.issue_experiment_terminal_ticket(
  p_experiment_id uuid,
  p_user_id uuid,
  p_token_hash text,
  p_ticket_mode text,
  p_expires_at timestamptz,
  p_max_spend_usd numeric default 90,
  p_max_concurrency integer default 1,
  p_estimated_cost_per_second_usd numeric default 0.000092,
  p_reserve_seconds integer default 3600
)
returns public.experiment_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_runtime public.experiment_runtime;
  v_rate numeric := greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001);
  v_reserve_until timestamptz := now() + make_interval(
    secs => greatest(coalesce(p_reserve_seconds, 3600), 60)
  );
  v_target_reserve_until timestamptz;
  v_existing_reservation numeric := 0;
  v_target_reservation numeric := 0;
  v_incremental_reservation numeric := 0;
begin
  if p_ticket_mode not in ('read', 'write') then
    raise check_violation using message = 'invalid terminal ticket mode';
  end if;
  if p_token_hash is null or length(p_token_hash) < 32
    or p_expires_at is null or p_expires_at <= now()
    or p_expires_at > now() + interval '2 minutes' then
    raise check_violation using message = 'invalid terminal ticket';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id and user_id = p_user_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if v_experiment.status <> 'ready' or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise check_violation using message = 'experiment terminal is not available';
  end if;
  if exists (
    select 1 from public.experiment_validation_runtime validation_runtime
    where validation_runtime.experiment_id = p_experiment_id
      and validation_runtime.state in ('creating', 'running', 'destroying')
  ) or exists (
    select 1 from public.experiment_actions actions
    where actions.experiment_id = p_experiment_id
      and actions.kind = 'validation'
      and actions.status in ('queued', 'running', 'recovering')
  ) then
    raise check_violation using message = 'experiment terminal is not available during validation';
  end if;
  if p_ticket_mode = 'write' and exists (
    select 1 from public.experiment_actions actions
    where actions.experiment_id = p_experiment_id
      and actions.status in ('queued', 'running', 'recovering')
  ) then
    raise check_violation using message = 'experiment terminal write is not available during an action';
  end if;

  select * into v_runtime from public.experiment_runtime
  where experiment_id = p_experiment_id for update;
  if not found or v_runtime.sandbox_id is null
    or v_runtime.state not in ('running', 'paused') then
    raise check_violation using message = 'experiment terminal is not available';
  end if;
  if public.active_experiment_slot_count(p_experiment_id)
    >= greatest(coalesce(p_max_concurrency, 1), 1) then
    raise check_violation using message = 'global experiment concurrency limit reached';
  end if;
  v_target_reserve_until := greatest(
    coalesce(v_runtime.reserved_until, now()), v_reserve_until
  );
  if v_runtime.state = 'paused' then
    v_target_reserve_until := v_reserve_until;
    v_target_reservation := greatest(
      extract(epoch from (v_target_reserve_until - now())), 0
    ) * v_rate;
  else
    -- Changing the rate rewrites the live runtime row, so compare the full
    -- commitment represented by the old row with the full target row. Merely
    -- pricing the extension would under-reserve when the new rate is higher.
    v_existing_reservation := greatest(extract(epoch from (
      greatest(now(), coalesce(v_runtime.reserved_until, now()))
        - coalesce(v_runtime.active_started_at, v_runtime.updated_at, v_runtime.created_at)
    )), 0) * v_runtime.estimated_cost_per_second_usd;
    v_target_reservation := greatest(extract(epoch from (
      v_target_reserve_until
        - coalesce(v_runtime.active_started_at, v_runtime.updated_at, v_runtime.created_at)
    )), 0) * v_rate;
  end if;
  v_incremental_reservation := greatest(
    v_target_reservation - v_existing_reservation, 0
  );
  if public.current_experiment_e2b_commitment(v_rate, p_reserve_seconds)
      + v_incremental_reservation
    > least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    raise check_violation using message = 'experiment spend limit reached';
  end if;

  update public.experiment_runtime
  set state = 'running',
      active_started_at = case when v_runtime.state = 'paused'
        then now() else coalesce(active_started_at, now()) end,
      reserved_until = v_target_reserve_until,
      estimated_cost_per_second_usd = v_rate,
      paused_at = null,
      terminal_ticket_hash = p_token_hash,
      terminal_ticket_mode = p_ticket_mode,
      terminal_ticket_expires_at = p_expires_at,
      -- Issuing a replacement ticket revokes every previously attached
      -- browser session. The relay fences every operation by this epoch.
      terminal_session_epoch = terminal_session_epoch + 1,
      last_heartbeat_at = now(),
      updated_at = now()
  where experiment_id = p_experiment_id
  returning * into v_runtime;
  return v_runtime;
end;
$$;

create or replace function public.consume_experiment_terminal_ticket(p_token_hash text)
returns table(
  experiment_id uuid, sandbox_id text, pty_session_id text,
  ticket_mode text, terminal_session_epoch bigint
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_runtime public.experiment_runtime;
begin
  select * into v_runtime from public.experiment_runtime
  where terminal_ticket_hash = p_token_hash
    and terminal_ticket_expires_at > now()
    and state = 'running'
  for update;
  if not found then return; end if;
  update public.experiment_runtime
  set terminal_ticket_hash = null, terminal_ticket_mode = null,
      terminal_ticket_expires_at = null, updated_at = now()
  where public.experiment_runtime.experiment_id = v_runtime.experiment_id;
  return query select v_runtime.experiment_id, v_runtime.sandbox_id,
    v_runtime.pty_session_id, v_runtime.terminal_ticket_mode,
    v_runtime.terminal_session_epoch;
end;
$$;

create or replace function public.reserve_claimed_validation_runtime(
  p_experiment_id uuid,
  p_action_id uuid,
  p_worker_id text,
  p_run_id uuid,
  p_max_spend_usd numeric default 90,
  p_max_concurrency integer default 1,
  p_estimated_cost_per_second_usd numeric default 0.000092,
  p_reserve_seconds integer default 3600
)
returns public.experiment_validation_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_runtime public.experiment_validation_runtime;
  v_rate numeric := greatest(coalesce(p_estimated_cost_per_second_usd, 0.000092), 0.000000001);
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found or v_experiment.status <> 'ready'
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise serialization_failure using message = 'validation experiment is unavailable';
  end if;
  select * into v_action from public.experiment_actions
  where id = p_action_id and experiment_id = p_experiment_id for update;
  if not found or v_action.kind <> 'validation' or v_action.status <> 'running'
    or v_action.worker_id is distinct from p_worker_id
    or v_action.lease_expires_at is null or v_action.lease_expires_at <= now() then
    raise serialization_failure using message = 'validation action worker lease lost';
  end if;
  if not exists (
    select 1 from public.experiment_runs runs
    where runs.id = p_run_id and runs.experiment_id = p_experiment_id
      and runs.action_id = p_action_id
  ) then raise check_violation using message = 'validation run does not belong to experiment'; end if;
  select * into v_runtime from public.experiment_validation_runtime
  where action_id = p_action_id for update;
  if found and v_runtime.state in ('creating', 'running') then
    return v_runtime;
  elsif found and v_runtime.state = 'destroying' then
    raise serialization_failure using message = 'validation runtime cleanup is pending';
  end if;
  if public.active_experiment_slot_count(null, p_action_id)
    >= greatest(coalesce(p_max_concurrency, 1), 1) then
    raise check_violation using message = 'global experiment concurrency limit reached';
  end if;
  -- The claimed validation action already contributes the same reservation as
  -- a fallback lease. Replacing it with a durable runtime must not reserve the
  -- hour twice.
  if public.current_experiment_e2b_commitment(v_rate, p_reserve_seconds)
    > least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then
    raise check_violation using message = 'experiment spend limit reached';
  end if;
  insert into public.experiment_validation_runtime as current_runtime (
    action_id, experiment_id, run_id, state, active_started_at, reserved_until,
    estimated_cost_per_second_usd, destroy_after, metadata, updated_at
  ) values (
    p_action_id, p_experiment_id, p_run_id, 'creating', now(),
    now() + make_interval(secs => greatest(coalesce(p_reserve_seconds, 3600), 60)),
    v_rate, now() + interval '1 hour',
    jsonb_build_object('runtime_purpose', 'formal_validation', 'reserved_at', now()), now()
  ) on conflict (action_id) do update
  set experiment_id = excluded.experiment_id, run_id = excluded.run_id,
      sandbox_id = null, state = 'creating', active_started_at = now(),
      reserved_until = excluded.reserved_until, destroy_after = excluded.destroy_after,
      estimated_cost_per_second_usd = excluded.estimated_cost_per_second_usd,
      lifecycle_claim_token = null, lifecycle_lease_expires_at = null,
      metadata = excluded.metadata, updated_at = now()
  returning * into v_runtime;
  return v_runtime;
end;
$$;

create or replace function public.attach_claimed_validation_runtime(
  p_experiment_id uuid,
  p_action_id uuid,
  p_worker_id text,
  p_sandbox_id text,
  p_destroy_after timestamptz default null,
  p_metadata jsonb default '{}'::jsonb
)
returns public.experiment_validation_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_runtime public.experiment_validation_runtime;
begin
  if p_sandbox_id is null or length(trim(p_sandbox_id)) = 0 then
    raise check_violation using message = 'validation sandbox id is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  select * into v_action from public.experiment_actions
  where id = p_action_id and experiment_id = p_experiment_id for update;
  if not found or v_action.status <> 'running'
    or v_action.worker_id is distinct from p_worker_id
    or v_action.lease_expires_at is null or v_action.lease_expires_at <= now() then
    raise serialization_failure using message = 'validation action worker lease lost';
  end if;
  select * into v_runtime from public.experiment_validation_runtime
  where action_id = p_action_id and experiment_id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'validation runtime reservation not found'; end if;
  if v_runtime.sandbox_id is not null and v_runtime.sandbox_id <> p_sandbox_id then
    raise serialization_failure using message = 'validation runtime already has another sandbox';
  end if;
  update public.experiment_validation_runtime
  set sandbox_id = p_sandbox_id,
      state = case when v_experiment.cancellation_requested
          or v_experiment.deletion_requested_at is not null
        then 'destroying' else 'running' end,
      destroy_after = case when v_experiment.cancellation_requested
          or v_experiment.deletion_requested_at is not null
        then now() else coalesce(p_destroy_after, destroy_after) end,
      metadata = coalesce(metadata, '{}'::jsonb) || coalesce(p_metadata, '{}'::jsonb),
      updated_at = now()
  where action_id = p_action_id returning * into v_runtime;
  return v_runtime;
end;
$$;

create or replace function public.finish_claimed_validation_runtime(
  p_experiment_id uuid,
  p_action_id uuid,
  p_worker_id text,
  p_sandbox_id text,
  p_destroyed boolean,
  p_retry_seconds integer default 300,
  p_safe_error text default null
)
returns public.experiment_validation_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_runtime public.experiment_validation_runtime;
  v_elapsed bigint := 0;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  select * into v_action from public.experiment_actions
  where id = p_action_id and experiment_id = p_experiment_id for update;
  if not found or v_action.status <> 'running'
    or v_action.worker_id is distinct from p_worker_id
    or v_action.lease_expires_at is null or v_action.lease_expires_at <= now() then
    raise serialization_failure using message = 'validation action worker lease lost';
  end if;
  select * into v_runtime from public.experiment_validation_runtime
  where action_id = p_action_id and experiment_id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'validation runtime not found'; end if;
  if v_runtime.sandbox_id is distinct from p_sandbox_id then
    raise serialization_failure using message = 'validation runtime sandbox changed';
  end if;
  if p_destroyed then
    v_elapsed := greatest(floor(extract(epoch from (now() - coalesce(
      v_runtime.active_started_at, v_runtime.updated_at, v_runtime.created_at
    ))))::bigint, 0);
  end if;
  update public.experiment_validation_runtime
  set state = case when p_destroyed then 'destroyed' else 'destroying' end,
      active_started_at = case when p_destroyed then null else coalesce(active_started_at, now()) end,
      reserved_until = case when p_destroyed then null else greatest(
        coalesce(reserved_until, now()),
        now() + make_interval(secs => greatest(coalesce(p_retry_seconds, 300), 60))
      ) end,
      destroy_after = case when p_destroyed then now()
        else now() + make_interval(secs => greatest(coalesce(p_retry_seconds, 300), 1)) end,
      metered_seconds = metered_seconds + v_elapsed,
      metered_cost_usd = metered_cost_usd + v_elapsed * estimated_cost_per_second_usd,
      lifecycle_claim_token = null,
      lifecycle_lease_expires_at = case when p_destroyed then null else now() end,
      metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
        'cleanup_error', case when p_destroyed then null else left(p_safe_error, 2000) end,
        'cleanup_updated_at', now()
      ), updated_at = now()
  where action_id = p_action_id returning * into v_runtime;
  if p_destroyed then
    insert into public.experiment_global_cost_ledger (
      source_token, source_kind, e2b_cost_usd, updated_at
    ) values (
      v_runtime.cost_ledger_token, 'validation_runtime', v_runtime.metered_cost_usd, now()
    ) on conflict (source_token) do update
    set e2b_cost_usd = greatest(
          public.experiment_global_cost_ledger.e2b_cost_usd,
          excluded.e2b_cost_usd
        ),
        updated_at = now();
  end if;
  perform public.refresh_experiment_runtime_totals(p_experiment_id);
  return v_runtime;
end;
$$;

create or replace function public.claim_expired_validation_runtime(p_limit integer default 10)
returns setof public.experiment_validation_runtime
language sql
security definer
set search_path = ''
as $$
  with candidates as (
    select runtime.action_id from public.experiment_validation_runtime runtime
    where runtime.state in ('creating', 'running', 'destroying')
      and runtime.destroy_after <= now()
      and (runtime.lifecycle_lease_expires_at is null or runtime.lifecycle_lease_expires_at <= now())
    order by runtime.destroy_after, runtime.action_id
    for update of runtime skip locked
    limit least(greatest(coalesce(p_limit, 10), 1), 100)
  )
  update public.experiment_validation_runtime runtime
  set state = 'destroying', lifecycle_claim_token = gen_random_uuid(),
      lifecycle_lease_expires_at = now() + interval '2 minutes', updated_at = now()
  from candidates where runtime.action_id = candidates.action_id
  returning runtime.*;
$$;

create or replace function public.finish_validation_runtime_lifecycle(
  p_action_id uuid,
  p_claim_token uuid,
  p_destroyed boolean,
  p_retry_seconds integer default 300,
  p_safe_error text default null
)
returns public.experiment_validation_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_runtime public.experiment_validation_runtime;
  v_elapsed bigint := 0;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = (
    select runtime.experiment_id from public.experiment_validation_runtime runtime
    where runtime.action_id = p_action_id
  ) for update;
  if not found then raise no_data_found using message = 'validation experiment not found'; end if;
  select * into v_runtime from public.experiment_validation_runtime
  where action_id = p_action_id for update;
  if not found or v_runtime.state <> 'destroying'
    or v_runtime.lifecycle_claim_token is distinct from p_claim_token
    or v_runtime.lifecycle_lease_expires_at <= now() then
    raise serialization_failure using message = 'validation runtime lifecycle lease lost';
  end if;
  if p_destroyed then
    v_elapsed := greatest(floor(extract(epoch from (now() - coalesce(
      v_runtime.active_started_at, v_runtime.updated_at, v_runtime.created_at
    ))))::bigint, 0);
  end if;
  update public.experiment_validation_runtime
  set state = case when p_destroyed then 'destroyed' else 'destroying' end,
      active_started_at = case when p_destroyed then null else coalesce(active_started_at, now()) end,
      reserved_until = case when p_destroyed then null else greatest(
        coalesce(reserved_until, now()), now() + make_interval(
          secs => greatest(coalesce(p_retry_seconds, 300), 60)
        )) end,
      destroy_after = case when p_destroyed then now() else now() + make_interval(
        secs => greatest(coalesce(p_retry_seconds, 300), 1)
      ) end,
      metered_seconds = metered_seconds + v_elapsed,
      metered_cost_usd = metered_cost_usd + v_elapsed * estimated_cost_per_second_usd,
      lifecycle_claim_token = null, lifecycle_lease_expires_at = null,
      metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
        'cleanup_error', case when p_destroyed then null else left(p_safe_error, 2000) end,
        'cleanup_updated_at', now()
      ), updated_at = now()
  where action_id = p_action_id returning * into v_runtime;
  if p_destroyed then
    insert into public.experiment_global_cost_ledger (
      source_token, source_kind, e2b_cost_usd, updated_at
    ) values (
      v_runtime.cost_ledger_token, 'validation_runtime', v_runtime.metered_cost_usd, now()
    ) on conflict (source_token) do update
    set e2b_cost_usd = greatest(
          public.experiment_global_cost_ledger.e2b_cost_usd,
          excluded.e2b_cost_usd
        ),
        updated_at = now();
  end if;
  perform public.refresh_experiment_runtime_totals(v_runtime.experiment_id);
  return v_runtime;
end;
$$;

-- A failed E2B kill is never treated as success. Preserve the external
-- sandbox identifier and move the runtime into a fenced lifecycle retry so a
-- later reconciler can destroy it before the parent experiment is deleted.
create or replace function public.schedule_claimed_runtime_cleanup(
  p_experiment_id uuid,
  p_worker_id text,
  p_action_id uuid default null,
  p_sandbox_id text default null,
  p_retry_seconds integer default 300,
  p_safe_error text default null
)
returns public.experiment_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_runtime public.experiment_runtime;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise serialization_failure using message = 'active experiment worker lease required';
  end if;
  if p_action_id is null then
    if v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif not exists (
    select 1 from public.experiment_actions actions
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
  ) then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;

  select * into v_runtime from public.experiment_runtime
  where experiment_id = p_experiment_id for update;
  if found then
    if p_sandbox_id is not null and v_runtime.sandbox_id is not null
      and v_runtime.sandbox_id <> p_sandbox_id then
      raise serialization_failure using message = 'experiment runtime changed before cleanup';
    end if;
    update public.experiment_runtime
    set sandbox_id = coalesce(p_sandbox_id, sandbox_id),
        state = 'destroying',
        destroy_after = now() + make_interval(secs => greatest(coalesce(p_retry_seconds, 300), 1)),
        reserved_until = greatest(
          coalesce(reserved_until, now()),
          now() + make_interval(secs => greatest(coalesce(p_retry_seconds, 300), 60))
        ),
        active_started_at = coalesce(active_started_at, now()),
        lifecycle_claim_token = null,
        lifecycle_lease_expires_at = now(),
        terminal_ticket_hash = null,
        terminal_ticket_mode = null,
        terminal_ticket_expires_at = null,
        terminal_session_epoch = terminal_session_epoch + 1,
        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
          'lifecycle_action', 'destroy',
          'cleanup_scheduled_at', now(),
          'cleanup_error', left(coalesce(p_safe_error, 'sandbox cleanup pending'), 2000)
        ),
        updated_at = now()
    where experiment_id = p_experiment_id
    returning * into v_runtime;
  else
    if p_sandbox_id is null or length(trim(p_sandbox_id)) = 0 then
      raise no_data_found using message = 'experiment runtime not found';
    end if;
    insert into public.experiment_runtime (
      experiment_id, sandbox_id, state, destroy_after, active_started_at,
      reserved_until, lifecycle_lease_expires_at, metadata, updated_at
    ) values (
      p_experiment_id, p_sandbox_id, 'destroying',
      now() + make_interval(secs => greatest(coalesce(p_retry_seconds, 300), 1)),
      now(), now() + make_interval(secs => greatest(coalesce(p_retry_seconds, 300), 60)),
      now(), jsonb_build_object(
        'lifecycle_action', 'destroy',
        'cleanup_scheduled_at', now(),
        'cleanup_error', left(coalesce(p_safe_error, 'sandbox cleanup pending'), 2000)
      ), now()
    ) returning * into v_runtime;
  end if;
  perform public.refresh_experiment_runtime_totals(p_experiment_id);
  return v_runtime;
end;
$$;

-- A command transport failure or Worker lease loss makes the external
-- process state unknowable. This service-role-only fence does not require the
-- stale Worker lease: it can only move the exact persisted sandbox into the
-- stricter destroying state. The lifecycle reconciler remains responsible for
-- independently confirming provider deletion before releasing spend/slots.
create or replace function public.mark_experiment_runtime_tainted(
  p_experiment_id uuid,
  p_sandbox_id text,
  p_action_id uuid default null,
  p_safe_error text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_validation public.experiment_validation_runtime;
  v_runtime public.experiment_runtime;
begin
  if p_sandbox_id is null or length(trim(p_sandbox_id)) = 0 then
    raise check_violation using message = 'sandbox id is required';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );

  if p_action_id is not null then
    select * into v_validation
    from public.experiment_validation_runtime
    where action_id = p_action_id
      and experiment_id = p_experiment_id
      and sandbox_id = p_sandbox_id
    for update;
    if found then
      update public.experiment_validation_runtime
      set state = 'destroying',
          active_started_at = coalesce(active_started_at, now()),
          reserved_until = greatest(
            coalesce(reserved_until, now()), now() + interval '5 minutes'
          ),
          destroy_after = now(),
          lifecycle_claim_token = null,
          lifecycle_lease_expires_at = now(),
          metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
            'cleanup_error', left(coalesce(p_safe_error, 'runtime tainted'), 2000),
            'tainted_at', now()
          ),
          updated_at = now()
      where action_id = p_action_id
      returning * into v_validation;
      perform public.refresh_experiment_runtime_totals(p_experiment_id);
      return jsonb_build_object(
        'marked', true,
        'runtime_kind', 'validation',
        'sandbox_id', p_sandbox_id
      );
    end if;
  end if;

  select * into v_runtime
  from public.experiment_runtime
  where experiment_id = p_experiment_id
    and sandbox_id = p_sandbox_id
  for update;
  if not found then
    return jsonb_build_object(
      'marked', false,
      'runtime_kind', null,
      'sandbox_id', p_sandbox_id
    );
  end if;
  update public.experiment_runtime
  set state = 'destroying',
      active_started_at = coalesce(active_started_at, now()),
      reserved_until = greatest(
        coalesce(reserved_until, now()), now() + interval '5 minutes'
      ),
      destroy_after = now(),
      lifecycle_claim_token = null,
      lifecycle_lease_expires_at = now(),
      terminal_ticket_hash = null,
      terminal_ticket_mode = null,
      terminal_ticket_expires_at = null,
      terminal_session_epoch = terminal_session_epoch + 1,
      metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
        'lifecycle_action', 'destroy',
        'cleanup_error', left(coalesce(p_safe_error, 'runtime tainted'), 2000),
        'tainted_at', now()
      ),
      updated_at = now()
  where experiment_id = p_experiment_id
    and sandbox_id = p_sandbox_id
  returning * into v_runtime;
  perform public.refresh_experiment_runtime_totals(p_experiment_id);
  return jsonb_build_object(
    'marked', true,
    'runtime_kind', 'interactive',
    'sandbox_id', p_sandbox_id
  );
end;
$$;

create or replace function public.claim_expired_experiment_runtime(
  p_limit integer default 10
)
returns setof public.experiment_runtime
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Serialize lifecycle admission with action admission. Otherwise an idle
  -- runtime and a queued action can both pass predicates from different
  -- READ COMMITTED snapshots, leaving the action attached to a runtime that
  -- has already entered provider destruction.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  return query
  with candidates as (
    select runtime.experiment_id
    from public.experiment_runtime runtime
    where (
        runtime.state in ('running', 'paused')
        or (
          runtime.state = 'destroying'
          and runtime.metadata->>'lifecycle_action' = 'destroy'
          and runtime.lifecycle_lease_expires_at <= now()
        )
      )
      and runtime.destroy_after is not null
      and runtime.destroy_after <= now()
    order by runtime.destroy_after, runtime.experiment_id
    for update of runtime skip locked
    limit least(greatest(p_limit, 1), 100)
  )
  update public.experiment_runtime runtime
  set state = 'destroying',
      lifecycle_claim_token = gen_random_uuid(),
      lifecycle_lease_expires_at = now() + interval '2 minutes',
      metadata = coalesce(runtime.metadata, '{}'::jsonb) || jsonb_build_object(
        'lifecycle_action', 'destroy', 'lifecycle_claimed_at', now()
      ),
      updated_at = now()
  from candidates
  where runtime.experiment_id = candidates.experiment_id
  returning runtime.*;
end;
$$;

create or replace function public.claim_idle_experiment_runtime(
  p_idle_seconds integer default 600,
  p_limit integer default 10
)
returns setof public.experiment_runtime
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- The global fence makes the no-pending-action predicate and lifecycle
  -- transition atomic with enqueue/claim, whose lock order starts with the
  -- same advisory lock.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  return query
  with candidates as (
    select runtime.experiment_id
    from public.experiment_runtime runtime
    join public.idea_experiments experiments
      on experiments.id = runtime.experiment_id
    where (
        runtime.state = 'running'
        or (
          runtime.state = 'destroying'
          and runtime.metadata->>'lifecycle_action' = 'pause'
          and runtime.lifecycle_lease_expires_at <= now()
        )
      )
      and experiments.status = 'ready'
      and experiments.deletion_requested_at is null
      and coalesce(runtime.last_heartbeat_at, runtime.updated_at, runtime.created_at)
        <= now() - make_interval(secs => greatest(coalesce(p_idle_seconds, 600), 60))
      and not exists (
        select 1 from public.experiment_actions actions
        where actions.experiment_id = runtime.experiment_id
          and actions.status in ('queued', 'running', 'recovering')
      )
    order by coalesce(runtime.last_heartbeat_at, runtime.updated_at, runtime.created_at),
      runtime.experiment_id
    for update of runtime skip locked
    limit least(greatest(coalesce(p_limit, 10), 1), 100)
  )
  update public.experiment_runtime runtime
  set state = 'destroying',
      lifecycle_claim_token = gen_random_uuid(),
      lifecycle_lease_expires_at = now() + interval '2 minutes',
      metadata = coalesce(runtime.metadata, '{}'::jsonb) || jsonb_build_object(
        'lifecycle_action', 'pause', 'lifecycle_claimed_at', now()
      ),
      updated_at = now()
  from candidates
  where runtime.experiment_id = candidates.experiment_id
  returning runtime.*;
end;
$$;

create or replace function public.finish_experiment_runtime_lifecycle(
  p_experiment_id uuid,
  p_claim_token uuid,
  p_lifecycle_action text,
  p_state text,
  p_sandbox_id text default null,
  p_paused_at timestamptz default null,
  p_destroy_after timestamptz default null,
  p_last_heartbeat_at timestamptz default null,
  p_metadata jsonb default null
)
returns public.experiment_runtime
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_runtime public.experiment_runtime;
  v_elapsed_seconds bigint := 0;
begin
  if p_lifecycle_action not in ('pause', 'destroy') then
    raise check_violation using message = 'invalid runtime lifecycle action';
  end if;
  if p_state not in ('running', 'paused', 'destroying', 'destroyed') then
    raise check_violation using message = 'invalid lifecycle target state';
  end if;
  if (p_lifecycle_action = 'destroy' and p_state not in ('destroying', 'destroyed'))
    or (p_lifecycle_action = 'pause' and p_state not in ('running', 'paused', 'destroyed')) then
    raise check_violation using message = 'invalid lifecycle action transition';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('research_atlas_experiment_budget'));
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment not found'; end if;
  select * into v_runtime from public.experiment_runtime
  where experiment_id = p_experiment_id for update;
  if not found then raise no_data_found using message = 'experiment runtime not found'; end if;
  if v_runtime.state <> 'destroying'
    or v_runtime.lifecycle_claim_token is distinct from p_claim_token
    or v_runtime.lifecycle_lease_expires_at is null
    or v_runtime.lifecycle_lease_expires_at <= now()
    or v_runtime.metadata->>'lifecycle_action' is distinct from p_lifecycle_action then
    raise serialization_failure using message = 'runtime lifecycle lease lost';
  end if;
  if p_state in ('paused', 'destroyed') then
    v_elapsed_seconds := greatest(
      floor(extract(epoch from (now() - coalesce(
        v_runtime.active_started_at, v_runtime.updated_at, v_runtime.created_at
      ))))::bigint,
      0
    );
  end if;
  update public.experiment_runtime
  set sandbox_id = coalesce(p_sandbox_id, sandbox_id),
      state = p_state,
      paused_at = case when p_state = 'running' then null
        else coalesce(p_paused_at, paused_at) end,
      destroy_after = coalesce(p_destroy_after, destroy_after),
      last_heartbeat_at = coalesce(p_last_heartbeat_at, last_heartbeat_at),
      metered_seconds = metered_seconds + v_elapsed_seconds,
      metered_cost_usd = metered_cost_usd + v_elapsed_seconds
        * estimated_cost_per_second_usd,
      active_started_at = case when p_state in ('running', 'destroying')
        then coalesce(active_started_at, updated_at, created_at, now()) else null end,
      reserved_until = case
        when p_state = 'running' then greatest(
          coalesce(reserved_until, now()), now() + interval '1 hour'
        )
        when p_state = 'destroying' then greatest(
          coalesce(reserved_until, now()), coalesce(p_destroy_after, now())
        )
        else null
      end,
      metadata = case when p_state = 'destroying' then
          (coalesce(metadata, '{}'::jsonb) - 'lifecycle_claimed_at')
            || coalesce(p_metadata, '{}'::jsonb)
            || jsonb_build_object(
              'lifecycle_action', 'destroy', 'cleanup_updated_at', now()
            )
        else (coalesce(metadata, '{}'::jsonb)
          - 'lifecycle_action' - 'lifecycle_claimed_at')
          || coalesce(p_metadata, '{}'::jsonb)
        end,
      lifecycle_claim_token = null,
      lifecycle_lease_expires_at = case when p_state = 'destroying' then now() else null end,
      terminal_ticket_hash = case when p_state in ('paused', 'destroying', 'destroyed')
        then null else terminal_ticket_hash end,
      terminal_ticket_mode = case when p_state in ('paused', 'destroying', 'destroyed')
        then null else terminal_ticket_mode end,
      terminal_ticket_expires_at = case when p_state in ('paused', 'destroying', 'destroyed')
        then null else terminal_ticket_expires_at end,
      terminal_session_epoch = case when p_state in ('paused', 'destroying', 'destroyed')
        then terminal_session_epoch + 1 else terminal_session_epoch end,
      updated_at = now()
  where experiment_id = p_experiment_id
  returning * into v_runtime;
  if p_state in ('paused', 'destroyed') then
    insert into public.experiment_global_cost_ledger (
      source_token, source_kind, e2b_cost_usd, updated_at
    ) values (
      v_runtime.cost_ledger_token, 'runtime', v_runtime.metered_cost_usd, now()
    ) on conflict (source_token) do update
    set e2b_cost_usd = greatest(
          public.experiment_global_cost_ledger.e2b_cost_usd,
          excluded.e2b_cost_usd
        ),
        updated_at = now();
  end if;
  perform public.refresh_experiment_runtime_totals(p_experiment_id);
  return v_runtime;
end;
$$;

revoke all on function public.enqueue_idea_experiment(uuid, text, uuid, boolean, numeric, numeric, numeric) from public, anon, authenticated;
revoke all on function public.list_pending_primary_experiments(integer) from public, anon, authenticated;
revoke all on function public.settle_experiment_terminal_reservations(uuid, text, boolean) from public, anon, authenticated;
revoke all on function public.claim_next_experiment(text, integer, integer, numeric, numeric, integer) from public, anon, authenticated;
revoke all on function public.renew_experiment_lease(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.claim_next_experiment_cleanup(text, integer) from public, anon, authenticated;
revoke all on function public.save_experiment_checkpoint(uuid, text, text, integer, jsonb, uuid) from public, anon, authenticated;
revoke all on function public.update_claimed_experiment(uuid, text, uuid, jsonb, text, boolean, uuid, uuid, smallint, uuid, text, jsonb) from public, anon, authenticated;
revoke all on function public.save_claimed_experiment_runtime(uuid, text, uuid, text, text, timestamptz, boolean, timestamptz, timestamptz, jsonb, numeric, integer, numeric, integer) from public, anon, authenticated;
revoke all on function public.register_claimed_experiment_artifact(uuid, text, uuid, uuid, uuid, text, text, text, text, bigint, text, boolean, jsonb) from public, anon, authenticated;
revoke all on function public.create_experiment_revision(uuid, uuid, text, text, text, text, jsonb, boolean, text, uuid) from public, anon, authenticated;
revoke all on function public.create_experiment_run(uuid, uuid, text, boolean, text, uuid, integer) from public, anon, authenticated;
revoke all on function public.assert_experiment_run_within_deadline(uuid, text, uuid) from public, anon, authenticated;
revoke all on function public.increment_experiment_costs(uuid, numeric, bigint, numeric, text, uuid, numeric, numeric, numeric, numeric, uuid, text, text, bigint, bigint, integer, jsonb, uuid) from public, anon, authenticated;
revoke all on function public.authorize_experiment_llm_call(uuid, text, uuid, uuid, numeric) from public, anon, authenticated;
revoke all on function public.sync_experiment_run_costs(uuid) from public, anon, authenticated;
revoke all on function public.settle_experiment_llm_reservation(uuid, text, uuid, text, uuid) from public, anon, authenticated;
revoke all on function public.finalize_experiment_run(uuid, text, text, jsonb, jsonb, jsonb, text, bigint, numeric, numeric, text, uuid) from public, anon, authenticated;
revoke all on function public.schedule_experiment_retry(uuid, text, text, integer, text, text) from public, anon, authenticated;
revoke all on function public.finish_experiment(uuid, text, text, text, jsonb) from public, anon, authenticated;
revoke all on function public.enqueue_experiment_action(uuid, uuid, text, jsonb, uuid, text, numeric, numeric, numeric, numeric, numeric) from public, anon, authenticated;
revoke all on function public.claim_next_experiment_action(text, integer, numeric, integer, numeric, integer) from public, anon, authenticated;
revoke all on function public.renew_experiment_action_lease(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.save_experiment_action_progress(uuid, text, jsonb) from public, anon, authenticated;
revoke all on function public.finish_experiment_action(uuid, text, boolean, jsonb, uuid, integer, text) from public, anon, authenticated;
revoke all on function public.request_experiment_cancellation(uuid, uuid) from public, anon, authenticated;
revoke all on function public.request_experiment_deletion(uuid, uuid) from public, anon, authenticated;
revoke all on function public.admin_request_job_deletion(uuid, uuid) from public, anon, authenticated;
revoke all on function public.request_user_job_deletion(uuid, uuid) from public, anon, authenticated;
revoke all on function public.admin_deletion_target_ready(text, uuid) from public, anon, authenticated;
revoke all on function public.list_my_jobs(integer, integer, boolean) from public, anon;
revoke all on function public.issue_experiment_terminal_ticket(uuid, uuid, text, text, timestamptz, numeric, integer, numeric, integer) from public, anon, authenticated;
revoke all on function public.consume_experiment_terminal_ticket(text) from public, anon, authenticated;
revoke all on function public.reserve_claimed_validation_runtime(uuid, uuid, text, uuid, numeric, integer, numeric, integer) from public, anon, authenticated;
revoke all on function public.attach_claimed_validation_runtime(uuid, uuid, text, text, timestamptz, jsonb) from public, anon, authenticated;
revoke all on function public.finish_claimed_validation_runtime(uuid, uuid, text, text, boolean, integer, text) from public, anon, authenticated;
revoke all on function public.claim_expired_validation_runtime(integer) from public, anon, authenticated;
revoke all on function public.finish_validation_runtime_lifecycle(uuid, uuid, boolean, integer, text) from public, anon, authenticated;
revoke all on function public.schedule_claimed_runtime_cleanup(uuid, text, uuid, text, integer, text) from public, anon, authenticated;
revoke all on function public.claim_expired_experiment_runtime(integer) from public, anon, authenticated;
revoke all on function public.claim_idle_experiment_runtime(integer, integer) from public, anon, authenticated;
revoke all on function public.finish_experiment_runtime_lifecycle(uuid, uuid, text, text, text, timestamptz, timestamptz, timestamptz, jsonb) from public, anon, authenticated;
revoke all on function public.mark_experiment_runtime_tainted(uuid, text, uuid, text) from public, anon, authenticated;

grant execute on function public.enqueue_idea_experiment(uuid, text, uuid, boolean, numeric, numeric, numeric) to service_role;
grant execute on function public.list_pending_primary_experiments(integer) to service_role;
grant execute on function public.settle_experiment_terminal_reservations(uuid, text, boolean) to service_role;
grant execute on function public.claim_next_experiment(text, integer, integer, numeric, numeric, integer) to service_role;
grant execute on function public.renew_experiment_lease(uuid, text, integer) to service_role;
grant execute on function public.claim_next_experiment_cleanup(text, integer) to service_role;
grant execute on function public.save_experiment_checkpoint(uuid, text, text, integer, jsonb, uuid) to service_role;
grant execute on function public.update_claimed_experiment(uuid, text, uuid, jsonb, text, boolean, uuid, uuid, smallint, uuid, text, jsonb) to service_role;
grant execute on function public.save_claimed_experiment_runtime(uuid, text, uuid, text, text, timestamptz, boolean, timestamptz, timestamptz, jsonb, numeric, integer, numeric, integer) to service_role;
grant execute on function public.register_claimed_experiment_artifact(uuid, text, uuid, uuid, uuid, text, text, text, text, bigint, text, boolean, jsonb) to service_role;
grant execute on function public.create_experiment_revision(uuid, uuid, text, text, text, text, jsonb, boolean, text, uuid) to service_role;
grant execute on function public.create_experiment_run(uuid, uuid, text, boolean, text, uuid, integer) to service_role;
grant execute on function public.assert_experiment_run_within_deadline(uuid, text, uuid) to service_role;
grant execute on function public.increment_experiment_costs(uuid, numeric, bigint, numeric, text, uuid, numeric, numeric, numeric, numeric, uuid, text, text, bigint, bigint, integer, jsonb, uuid) to service_role;
grant execute on function public.authorize_experiment_llm_call(uuid, text, uuid, uuid, numeric) to service_role;
grant execute on function public.sync_experiment_run_costs(uuid) to service_role;
grant execute on function public.settle_experiment_llm_reservation(uuid, text, uuid, text, uuid) to service_role;
grant execute on function public.finalize_experiment_run(uuid, text, text, jsonb, jsonb, jsonb, text, bigint, numeric, numeric, text, uuid) to service_role;
grant execute on function public.schedule_experiment_retry(uuid, text, text, integer, text, text) to service_role;
grant execute on function public.finish_experiment(uuid, text, text, text, jsonb) to service_role;
grant execute on function public.enqueue_experiment_action(uuid, uuid, text, jsonb, uuid, text, numeric, numeric, numeric, numeric, numeric) to service_role;
grant execute on function public.claim_next_experiment_action(text, integer, numeric, integer, numeric, integer) to service_role;
grant execute on function public.renew_experiment_action_lease(uuid, text, integer) to service_role;
grant execute on function public.save_experiment_action_progress(uuid, text, jsonb) to service_role;
grant execute on function public.finish_experiment_action(uuid, text, boolean, jsonb, uuid, integer, text) to service_role;
grant execute on function public.request_experiment_cancellation(uuid, uuid) to service_role;
grant execute on function public.request_experiment_deletion(uuid, uuid) to service_role;
grant execute on function public.admin_request_job_deletion(uuid, uuid) to service_role;
grant execute on function public.request_user_job_deletion(uuid, uuid) to service_role;
grant execute on function public.admin_deletion_target_ready(text, uuid) to service_role;
grant execute on function public.list_my_jobs(integer, integer, boolean) to authenticated;
grant execute on function public.issue_experiment_terminal_ticket(uuid, uuid, text, text, timestamptz, numeric, integer, numeric, integer) to service_role;
grant execute on function public.consume_experiment_terminal_ticket(text) to service_role;
grant execute on function public.reserve_claimed_validation_runtime(uuid, uuid, text, uuid, numeric, integer, numeric, integer) to service_role;
grant execute on function public.attach_claimed_validation_runtime(uuid, uuid, text, text, timestamptz, jsonb) to service_role;
grant execute on function public.finish_claimed_validation_runtime(uuid, uuid, text, text, boolean, integer, text) to service_role;
grant execute on function public.claim_expired_validation_runtime(integer) to service_role;
grant execute on function public.finish_validation_runtime_lifecycle(uuid, uuid, boolean, integer, text) to service_role;
grant execute on function public.schedule_claimed_runtime_cleanup(uuid, text, uuid, text, integer, text) to service_role;
grant execute on function public.claim_expired_experiment_runtime(integer) to service_role;
grant execute on function public.claim_idle_experiment_runtime(integer, integer) to service_role;
grant execute on function public.finish_experiment_runtime_lifecycle(uuid, uuid, text, text, text, timestamptz, timestamptz, timestamptz, jsonb) to service_role;
grant execute on function public.mark_experiment_runtime_tainted(uuid, text, uuid, text) to service_role;

do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'idea_experiments'
    ) then alter publication supabase_realtime add table public.idea_experiments; end if;
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'experiment_actions'
    ) then alter publication supabase_realtime add table public.experiment_actions; end if;
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'experiment_runs'
    ) then alter publication supabase_realtime add table public.experiment_runs; end if;
  end if;
end $$;

-- Short-lived, one-shot credentials used only by scientific subject code.
-- Raw bearer values never enter PostgreSQL: the experiment Worker writes them
-- into the subject sandbox's /tmp directory after persisting only SHA-256.
create table public.experiment_inference_tokens (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  run_id uuid not null references public.experiment_runs(id) on delete cascade,
  specification_hash text not null check (specification_hash ~ '^[0-9a-f]{64}$'),
  contract_key text not null check (contract_key ~ '^[a-z][a-z0-9_]{1,47}$'),
  slot smallint not null check (slot between 1 and 8),
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  request_id uuid,
  created_at timestamptz not null default now(),
  unique (run_id, contract_key, slot)
);

create table public.experiment_inference_requests (
  id uuid primary key default gen_random_uuid(),
  token_id uuid not null unique references public.experiment_inference_tokens(id) on delete cascade,
  experiment_id uuid not null references public.idea_experiments(id) on delete cascade,
  run_id uuid not null references public.experiment_runs(id) on delete cascade,
  action_id uuid references public.experiment_actions(id) on delete cascade,
  specification_hash text not null check (specification_hash ~ '^[0-9a-f]{64}$'),
  contract_key text not null check (contract_key ~ '^[a-z][a-z0-9_]{1,47}$'),
  contract jsonb not null,
  request jsonb not null,
  request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  response jsonb,
  response_sha256 text check (response_sha256 is null or response_sha256 ~ '^[0-9a-f]{64}$'),
  poll_token_hash text not null unique check (poll_token_hash ~ '^[0-9a-f]{64}$'),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'recovering', 'completed', 'blocked', 'cancelled')),
  invocation_id uuid unique,
  reserved_cny numeric(12, 6) not null default 0 check (reserved_cny >= 0),
  cost_cny numeric(12, 6) not null default 0 check (cost_cny >= 0),
  input_tokens bigint not null default 0 check (input_tokens >= 0),
  output_tokens bigint not null default 0 check (output_tokens >= 0),
  provider_started_at timestamptz,
  retry_count integer not null default 0 check (retry_count >= 0),
  next_retry_at timestamptz,
  worker_id text,
  lease_expires_at timestamptz,
  public_error_code text,
  expires_at timestamptz not null,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.experiment_inference_tokens
  add constraint experiment_inference_tokens_request_fk
  foreign key (request_id) references public.experiment_inference_requests(id)
  on delete set null;

create index experiment_inference_requests_queue_idx
  on public.experiment_inference_requests (next_retry_at, created_at)
  where status in ('queued', 'recovering', 'running');
create index experiment_inference_requests_run_idx
  on public.experiment_inference_requests (run_id, contract_key, created_at);

alter table public.experiment_inference_tokens enable row level security;
alter table public.experiment_inference_requests enable row level security;
revoke all on public.experiment_inference_tokens,
  public.experiment_inference_requests from public, anon, authenticated;
grant all on public.experiment_inference_tokens,
  public.experiment_inference_requests to service_role;

-- Replace every still-unused token for a run. This makes Worker recovery safe:
-- a rebuilt sandbox receives fresh plaintext credentials while any credentials
-- possibly left in the old sandbox become invalid immediately.
create or replace function public.replace_sandbox_inference_tokens(
  p_experiment_id uuid,
  p_run_id uuid,
  p_worker_id text,
  p_action_id uuid default null,
  p_specification_hash text default null,
  p_tokens jsonb default '[]'::jsonb,
  p_expires_at timestamptz default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_run public.experiment_runs;
  v_token jsonb;
  v_contract jsonb;
  v_hash text;
  v_key text;
  v_slot integer;
  v_accepted jsonb := '[]'::jsonb;
  v_expires timestamptz;
begin
  if p_specification_hash is null
    or p_specification_hash !~ '^[0-9a-f]{64}$'
    or jsonb_typeof(p_tokens) <> 'array'
    or jsonb_array_length(p_tokens) > 32 then
    raise check_violation using message = 'invalid sandbox inference token batch';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_experiment from public.idea_experiments
  where id = p_experiment_id for update;
  select * into v_run from public.experiment_runs
  where id = p_run_id and experiment_id = p_experiment_id for update;
  if not found or v_run.status <> 'running' or v_run.hard_deadline_at <= now() then
    raise check_violation using message = 'experiment inference run is unavailable';
  end if;
  if v_experiment.pilot_specification_hash is distinct from p_specification_hash
    or coalesce((v_experiment.pilot_specification->>'requires_live_inference')::boolean, false) is not true
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise check_violation using message = 'experiment inference contract is unavailable';
  end if;
  if p_action_id is null then
    if v_run.action_id is not null
      or v_experiment.worker_id is distinct from p_worker_id
      or v_experiment.status <> 'running'
      or v_experiment.lease_expires_at is null
      or v_experiment.lease_expires_at <= now() then
      raise serialization_failure using message = 'experiment worker lease lost';
    end if;
  elsif v_run.action_id is distinct from p_action_id or not exists (
    select 1 from public.experiment_actions actions
    where actions.id = p_action_id
      and actions.experiment_id = p_experiment_id
      and actions.worker_id = p_worker_id
      and actions.status = 'running'
      and actions.lease_expires_at > now()
  ) then
    raise serialization_failure using message = 'experiment action worker lease lost';
  end if;
  v_expires := least(
    coalesce(p_expires_at, v_run.hard_deadline_at),
    v_run.hard_deadline_at,
    now() + interval '60 minutes'
  );
  if v_expires <= now() then
    raise check_violation using message = 'experiment inference token expiry is invalid';
  end if;

  delete from public.experiment_inference_tokens tokens
  where tokens.run_id = p_run_id and tokens.consumed_at is null;

  for v_token in select value from jsonb_array_elements(p_tokens) loop
    v_hash := lower(coalesce(v_token->>'token_hash', ''));
    v_key := coalesce(v_token->>'contract_key', '');
    begin
      v_slot := (v_token->>'slot')::integer;
    exception when others then
      raise check_violation using message = 'invalid sandbox inference token slot';
    end;
    if v_hash !~ '^[0-9a-f]{64}$' or v_key !~ '^[a-z][a-z0-9_]{1,47}$' then
      raise check_violation using message = 'invalid sandbox inference token';
    end if;
    select contract into v_contract
    from jsonb_array_elements(v_experiment.pilot_specification->'inference_contracts') contract
    where contract->>'key' = v_key limit 1;
    if v_contract is null
      or v_slot < 1
      or v_slot > least(coalesce((v_contract->>'max_calls')::integer, 0), 8) then
      raise check_violation using message = 'sandbox inference token exceeds frozen contract';
    end if;
    insert into public.experiment_inference_tokens (
      experiment_id, run_id, specification_hash, contract_key, slot,
      token_hash, expires_at
    ) values (
      p_experiment_id, p_run_id, p_specification_hash, v_key, v_slot,
      v_hash, v_expires
    ) on conflict (run_id, contract_key, slot) do nothing;
    if exists (
      select 1 from public.experiment_inference_tokens tokens
      where tokens.run_id = p_run_id and tokens.contract_key = v_key
        and tokens.slot = v_slot and tokens.token_hash = v_hash
        and tokens.consumed_at is null
    ) then
      v_accepted := v_accepted || jsonb_build_array(v_hash);
    end if;
  end loop;
  return v_accepted;
end;
$$;

-- Edge uses this service-only read to validate the frozen schema before it
-- attempts the atomic consume below. A racing replay can pass this read, but
-- only one transaction can consume the row.
create or replace function public.inspect_sandbox_inference_token(
  p_token_hash text
)
returns jsonb
language sql
security definer
set search_path = ''
stable
as $$
  select jsonb_build_object(
    'contract', contract.contract,
    'experiment_id', tokens.experiment_id,
    'run_id', tokens.run_id,
    'specification_hash', tokens.specification_hash,
    'expires_at', tokens.expires_at
  )
  from public.experiment_inference_tokens tokens
  join public.experiment_runs runs on runs.id = tokens.run_id
  join public.idea_experiments experiments on experiments.id = tokens.experiment_id
  cross join lateral (
    select value as contract
    from jsonb_array_elements(experiments.pilot_specification->'inference_contracts')
    where value->>'key' = tokens.contract_key limit 1
  ) contract
  where tokens.token_hash = lower(p_token_hash)
    and tokens.consumed_at is null
    and tokens.expires_at > now()
    and runs.status = 'running'
    and runs.hard_deadline_at > now()
    and experiments.pilot_specification_hash = tokens.specification_hash
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null
    and (
      (runs.action_id is null and experiments.status = 'running'
        and experiments.lease_expires_at > now())
      or exists (
        select 1 from public.experiment_actions actions
        where actions.id = runs.action_id and actions.status = 'running'
          and actions.lease_expires_at > now()
      )
    );
$$;

create or replace function public.consume_sandbox_inference_token(
  p_token_hash text,
  p_request_id uuid,
  p_request jsonb,
  p_request_sha256 text,
  p_poll_token_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_token public.experiment_inference_tokens;
  v_run public.experiment_runs;
  v_experiment public.idea_experiments;
  v_contract jsonb;
  v_request public.experiment_inference_requests;
  v_max_request integer;
begin
  if p_request_id is null
    or lower(coalesce(p_token_hash, '')) !~ '^[0-9a-f]{64}$'
    or lower(coalesce(p_request_sha256, '')) !~ '^[0-9a-f]{64}$'
    or lower(coalesce(p_poll_token_hash, '')) !~ '^[0-9a-f]{64}$'
    or jsonb_typeof(p_request) <> 'object' then
    raise insufficient_privilege using message = 'invalid or expired inference token';
  end if;
  select * into v_token from public.experiment_inference_tokens
  where token_hash = lower(p_token_hash) for update;
  if not found or v_token.consumed_at is not null or v_token.expires_at <= now() then
    raise insufficient_privilege using message = 'invalid or expired inference token';
  end if;
  select * into v_run from public.experiment_runs
  where id = v_token.run_id and experiment_id = v_token.experiment_id for update;
  select * into v_experiment from public.idea_experiments
  where id = v_token.experiment_id for update;
  if v_run.status <> 'running' or v_run.hard_deadline_at <= now()
    or v_experiment.pilot_specification_hash is distinct from v_token.specification_hash
    or v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null
    or not (
      (v_run.action_id is null and v_experiment.status = 'running'
        and v_experiment.lease_expires_at > now())
      or exists (
        select 1 from public.experiment_actions actions
        where actions.id = v_run.action_id and actions.status = 'running'
          and actions.lease_expires_at > now()
      )
    ) then
    raise insufficient_privilege using message = 'invalid or expired inference token';
  end if;
  select contract into v_contract
  from jsonb_array_elements(v_experiment.pilot_specification->'inference_contracts') contract
  where contract->>'key' = v_token.contract_key limit 1;
  v_max_request := least(coalesce((v_contract->>'max_request_bytes')::integer, 0), 32768);
  if v_contract is null or octet_length(p_request::text) > v_max_request then
    raise check_violation using message = 'inference request violates frozen contract';
  end if;
  insert into public.experiment_inference_requests (
    id, token_id, experiment_id, run_id, action_id, specification_hash,
    contract_key, contract, request, request_sha256, poll_token_hash,
    expires_at
  ) values (
    p_request_id, v_token.id, v_token.experiment_id, v_token.run_id, v_run.action_id,
    v_token.specification_hash, v_token.contract_key, v_contract, p_request,
    lower(p_request_sha256), lower(p_poll_token_hash),
    least(v_run.hard_deadline_at + interval '15 minutes', now() + interval '75 minutes')
  ) returning * into v_request;
  update public.experiment_inference_tokens
  set consumed_at = now(), request_id = v_request.id
  where id = v_token.id;
  return jsonb_build_object(
    'request_id', v_request.id,
    'state', v_request.status,
    'expires_at', v_request.expires_at
  );
end;
$$;

create or replace function public.poll_sandbox_inference_request(
  p_request_id uuid,
  p_poll_token_hash text
)
returns jsonb
language sql
security definer
set search_path = ''
stable
as $$
  select jsonb_build_object(
    'request_id', requests.id,
    'state', requests.status,
    'result', case when requests.status = 'completed' then requests.response else null end,
    'error', case when requests.status in ('blocked', 'cancelled')
      then coalesce(requests.public_error_code, 'inference_unavailable') else null end,
    'expires_at', requests.expires_at
  )
  from public.experiment_inference_requests requests
  where requests.id = p_request_id
    and requests.poll_token_hash = lower(p_poll_token_hash)
    and requests.expires_at > now();
$$;

create or replace function public.claim_next_sandbox_inference_request(
  p_worker_id text,
  p_lease_seconds integer default 300,
  p_max_call_cny numeric default 1,
  p_run_max_cny numeric default 5
)
returns setof public.experiment_inference_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_request public.experiment_inference_requests;
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_available numeric := 0;
  v_reservation numeric := least(greatest(coalesce(p_max_call_cny, 0), 0), 5);
  v_run_spend numeric := 0;
  v_invocation public.experiment_llm_invocations;
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 or v_reservation <= 0 then
    return;
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select requests.* into v_request
  from public.experiment_inference_requests requests
  join public.experiment_runs runs on runs.id = requests.run_id
  join public.idea_experiments experiments on experiments.id = requests.experiment_id
  where (
      (requests.status in ('queued', 'recovering')
        and coalesce(requests.next_retry_at, now()) <= now())
      or (requests.status = 'running' and (
        requests.lease_expires_at is null or requests.lease_expires_at <= now()
      ))
    )
    and requests.expires_at > now()
    and runs.status = 'running' and runs.hard_deadline_at > now()
    and experiments.cancellation_requested = false
    and experiments.deletion_requested_at is null
    and (
      (requests.action_id is null and runs.action_id is null
        and experiments.status = 'running'
        and experiments.lease_expires_at > now())
      or (requests.action_id is not null
        and runs.action_id = requests.action_id
        and exists (
          select 1 from public.experiment_actions actions
          where actions.id = requests.action_id
            and actions.experiment_id = requests.experiment_id
            and actions.status = 'running'
            and actions.lease_expires_at > now()
        ))
    )
  order by requests.created_at
  for update of requests skip locked limit 1;
  if not found then return; end if;
  select * into v_experiment from public.idea_experiments
  where id = v_request.experiment_id for update;
  if v_request.action_id is null then
    select greatest(
      v_experiment.llm_reserved_cny
        - coalesce(sum(invocations.reserved_cny), 0), 0
    ) into v_available
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = v_request.experiment_id
      and invocations.action_id is null and invocations.status = 'authorized';
  else
    select * into v_action from public.experiment_actions
    where id = v_request.action_id and experiment_id = v_request.experiment_id for update;
    select greatest(
      v_action.llm_reserved_cny
        - coalesce(sum(invocations.reserved_cny), 0), 0
    ) into v_available
    from public.experiment_llm_invocations invocations
    where invocations.experiment_id = v_request.experiment_id
      and invocations.action_id = v_request.action_id
      and invocations.status = 'authorized';
  end if;
  select coalesce(sum(requests.cost_cny + case
    when requests.status in ('queued', 'running', 'recovering')
      then requests.reserved_cny else 0 end), 0)
  into v_run_spend
  from public.experiment_inference_requests requests
  where requests.run_id = v_request.run_id and requests.id <> v_request.id;
  if v_request.invocation_id is null then
    v_reservation := least(
      v_reservation, v_available,
      greatest(least(coalesce(p_run_max_cny, 5), 5) - v_run_spend, 0)
    );
    if v_reservation <= 0 then
      update public.experiment_inference_requests
      set status = 'blocked', public_error_code = 'budget_unavailable',
          completed_at = now(), worker_id = null, lease_expires_at = null,
          updated_at = now()
      where id = v_request.id returning * into v_request;
      return next v_request;
      return;
    end if;
    v_request.invocation_id := gen_random_uuid();
    insert into public.experiment_llm_invocations (
      usage_id, experiment_id, action_id, reserved_cny
    ) values (
      v_request.invocation_id, v_request.experiment_id,
      v_request.action_id, v_reservation
    );
  else
    select * into v_invocation from public.experiment_llm_invocations
    where usage_id = v_request.invocation_id for update;
    if not found or v_invocation.status <> 'authorized' then
      update public.experiment_inference_requests
      set status = 'blocked', public_error_code = 'inference_unavailable',
          completed_at = now(), worker_id = null, lease_expires_at = null,
          updated_at = now()
      where id = v_request.id returning * into v_request;
      return next v_request;
      return;
    end if;
    v_reservation := v_invocation.reserved_cny;
  end if;
  update public.experiment_inference_requests
  set status = 'running', reserved_cny = v_reservation,
      worker_id = p_worker_id,
      lease_expires_at = now() + make_interval(
        secs => least(greatest(coalesce(p_lease_seconds, 300), 60), 1800)
      ),
      next_retry_at = null, started_at = coalesce(started_at, now()),
      updated_at = now()
  where id = v_request.id returning * into v_request;
  return next v_request;
end;
$$;

create or replace function public.renew_sandbox_inference_lease(
  p_request_id uuid, p_worker_id text, p_lease_seconds integer default 300
)
returns boolean
language sql
security definer
set search_path = ''
as $$
  update public.experiment_inference_requests
  set lease_expires_at = now() + make_interval(
        secs => least(greatest(coalesce(p_lease_seconds, 300), 60), 1800)
      ), updated_at = now()
  where id = p_request_id and worker_id = p_worker_id and status = 'running'
    and lease_expires_at > now() and expires_at > now()
  returning true;
$$;

create or replace function public.mark_sandbox_inference_provider_started(
  p_request_id uuid, p_worker_id text
)
returns boolean
language sql
security definer
set search_path = ''
as $$
  update public.experiment_inference_requests
  set provider_started_at = coalesce(provider_started_at, now()), updated_at = now()
  where id = p_request_id and worker_id = p_worker_id and status = 'running'
    and lease_expires_at > now()
  returning true;
$$;

create or replace function public.schedule_sandbox_inference_retry(
  p_request_id uuid, p_worker_id text, p_retry_seconds integer default 30
)
returns public.experiment_inference_requests
language plpgsql
security definer
set search_path = ''
as $$
declare v_request public.experiment_inference_requests;
begin
  update public.experiment_inference_requests
  set status = 'recovering', retry_count = retry_count + 1,
      next_retry_at = now() + make_interval(
        secs => least(greatest(coalesce(p_retry_seconds, 30), 1), 600)
      ), worker_id = null, lease_expires_at = null, updated_at = now()
  where id = p_request_id and worker_id = p_worker_id and status = 'running'
    and lease_expires_at > now() and provider_started_at is null
  returning * into v_request;
  if not found then
    raise serialization_failure using message = 'sandbox inference lease lost';
  end if;
  return v_request;
end;
$$;

create or replace function public.finish_sandbox_inference_request(
  p_request_id uuid,
  p_worker_id text,
  p_status text,
  p_response jsonb default null,
  p_response_sha256 text default null,
  p_provider text default 'deepseek',
  p_model text default 'deepseek-v4-flash',
  p_input_tokens bigint default 0,
  p_output_tokens bigint default 0,
  p_cost_cny numeric default null,
  p_settlement_kind text default 'exact_usage',
  p_public_error_code text default null
)
returns public.experiment_inference_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_request public.experiment_inference_requests;
  v_experiment public.idea_experiments;
  v_action public.experiment_actions;
  v_invocation public.experiment_llm_invocations;
  v_delta numeric := 0;
  v_remaining numeric := 0;
begin
  if p_status not in ('completed', 'blocked', 'cancelled') then
    raise check_violation using message = 'invalid sandbox inference final state';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_request from public.experiment_inference_requests
  where id = p_request_id for update;
  if not found then raise no_data_found using message = 'inference request not found'; end if;
  if v_request.status in ('completed', 'blocked', 'cancelled') then return v_request; end if;
  if v_request.worker_id is distinct from p_worker_id
    or v_request.status <> 'running'
    or v_request.lease_expires_at is null
    or v_request.lease_expires_at <= now() then
    raise serialization_failure using message = 'sandbox inference lease lost';
  end if;
  select * into v_invocation from public.experiment_llm_invocations
  where usage_id = v_request.invocation_id for update;
  if not found or v_invocation.status <> 'authorized' then
    raise check_violation using message = 'sandbox inference invocation is unavailable';
  end if;
  v_delta := round(greatest(coalesce(p_cost_cny, v_invocation.reserved_cny), 0), 6);
  if v_delta > v_invocation.reserved_cny then
    raise check_violation using message = 'sandbox inference exceeded its hard reservation';
  end if;
  if p_status = 'completed' then
    if p_response is null or jsonb_typeof(p_response) <> 'object'
      or lower(coalesce(p_response_sha256, '')) !~ '^[0-9a-f]{64}$'
      or octet_length(p_response::text)
        > least(coalesce((v_request.contract->>'max_response_bytes')::integer, 0), 65536) then
      raise check_violation using message = 'sandbox inference response violates frozen contract';
    end if;
  end if;
  select coalesce(sum(invocations.reserved_cny), 0) into v_remaining
  from public.experiment_llm_invocations invocations
  where invocations.experiment_id = v_request.experiment_id
    and invocations.action_id is not distinct from v_request.action_id
    and invocations.status = 'authorized'
    and invocations.usage_id <> v_request.invocation_id;
  select * into v_experiment from public.idea_experiments
  where id = v_request.experiment_id for update;
  update public.idea_experiments
  set llm_cost_cny = llm_cost_cny + v_delta,
      llm_reserved_cny = case when v_request.action_id is null
        then greatest(llm_reserved_cny - v_delta, v_remaining, 0)
        else llm_reserved_cny end,
      updated_at = now(), last_activity_at = now()
  where id = v_request.experiment_id returning * into v_experiment;
  if v_request.action_id is not null then
    select * into v_action from public.experiment_actions
    where id = v_request.action_id for update;
    update public.experiment_actions
    set llm_cost_cny = llm_cost_cny + v_delta,
        llm_reserved_cny = greatest(llm_reserved_cny - v_delta, v_remaining, 0),
        updated_at = now()
    where id = v_request.action_id;
  end if;
  update public.experiment_runs
  set llm_cost_cny = llm_cost_cny + v_delta
  where id = v_request.run_id;
  if not exists (
    select 1 from public.provider_usage usage
    where usage.metadata->>'experiment_usage_id' = v_request.invocation_id::text
  ) then
    insert into public.provider_usage (
      job_id, provider, model, input_tokens, output_tokens, requests,
      estimated_cny, metadata
    ) values (
      v_experiment.job_id, coalesce(nullif(trim(p_provider), ''), 'deepseek'),
      coalesce(nullif(trim(p_model), ''), 'deepseek-v4-flash'),
      greatest(coalesce(p_input_tokens, 0), 0),
      greatest(coalesce(p_output_tokens, 0), 0), 1, v_delta,
      jsonb_build_object(
        'experiment_usage_id', v_request.invocation_id::text,
        'sandbox_inference_request_id', v_request.id::text,
        'experiment_run_id', v_request.run_id::text,
        'transport', 'claude_code',
        'stage', 'experiment_sandbox_inference',
        'claude_cli_model', 'claude-sonnet-4-5',
        'settlement_kind', left(coalesce(p_settlement_kind, 'exact_usage'), 80)
      )
    );
  end if;
  update public.experiment_llm_invocations
  set status = 'settled', settled_cny = v_delta,
      settlement_kind = left(coalesce(p_settlement_kind, 'exact_usage'), 120),
      settled_at = now(), updated_at = now()
  where usage_id = v_request.invocation_id;
  update public.experiment_inference_requests
  set status = p_status, response = case when p_status = 'completed' then p_response else null end,
      response_sha256 = case when p_status = 'completed' then lower(p_response_sha256) else null end,
      cost_cny = v_delta, input_tokens = greatest(coalesce(p_input_tokens, 0), 0),
      output_tokens = greatest(coalesce(p_output_tokens, 0), 0),
      public_error_code = case when p_status = 'completed' then null
        else left(coalesce(p_public_error_code, 'inference_unavailable'), 80) end,
      worker_id = null, lease_expires_at = null, completed_at = now(), updated_at = now()
  where id = v_request.id returning * into v_request;
  insert into public.experiment_global_cost_ledger (
    source_token, source_kind, llm_cost_cny, updated_at
  ) values (
    v_experiment.cost_ledger_token, 'llm', v_experiment.llm_cost_cny, now()
  ) on conflict (source_token) do update
  set llm_cost_cny = greatest(
        public.experiment_global_cost_ledger.llm_cost_cny,
        excluded.llm_cost_cny
      ), updated_at = now();
  return v_request;
end;
$$;

revoke all on function public.replace_sandbox_inference_tokens(uuid, uuid, text, uuid, text, jsonb, timestamptz) from public, anon, authenticated;
revoke all on function public.inspect_sandbox_inference_token(text) from public, anon, authenticated;
revoke all on function public.consume_sandbox_inference_token(text, uuid, jsonb, text, text) from public, anon, authenticated;
revoke all on function public.poll_sandbox_inference_request(uuid, text) from public, anon, authenticated;
revoke all on function public.claim_next_sandbox_inference_request(text, integer, numeric, numeric) from public, anon, authenticated;
revoke all on function public.renew_sandbox_inference_lease(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.mark_sandbox_inference_provider_started(uuid, text) from public, anon, authenticated;
revoke all on function public.schedule_sandbox_inference_retry(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.finish_sandbox_inference_request(uuid, text, text, jsonb, text, text, text, bigint, bigint, numeric, text, text) from public, anon, authenticated;

grant execute on function public.replace_sandbox_inference_tokens(uuid, uuid, text, uuid, text, jsonb, timestamptz) to service_role;
grant execute on function public.inspect_sandbox_inference_token(text) to service_role;
grant execute on function public.consume_sandbox_inference_token(text, uuid, jsonb, text, text) to service_role;
grant execute on function public.poll_sandbox_inference_request(uuid, text) to service_role;
grant execute on function public.claim_next_sandbox_inference_request(text, integer, numeric, numeric) to service_role;
grant execute on function public.renew_sandbox_inference_lease(uuid, text, integer) to service_role;
grant execute on function public.mark_sandbox_inference_provider_started(uuid, text) to service_role;
grant execute on function public.schedule_sandbox_inference_retry(uuid, text, integer) to service_role;
grant execute on function public.finish_sandbox_inference_request(uuid, text, text, jsonb, text, text, text, bigint, bigint, numeric, text, text) to service_role;
