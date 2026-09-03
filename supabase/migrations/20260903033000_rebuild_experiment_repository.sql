-- Replace a superseded generated repository without deleting its audit trail.
-- Only service-role maintenance may invoke this after the prior E2B runtime
-- has been physically destroyed and durably marked destroyed.

create or replace function public.requeue_experiment_repository_rebuild(
  p_experiment_id uuid,
  p_reason text default 'generated_repository_failed_quality_gate'
)
returns public.idea_experiments
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_experiment public.idea_experiments;
  v_checkpoint jsonb;
  v_runtime public.experiment_runtime;
  v_remaining_cny numeric;
begin
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_repository_rebuild')
  );
  select * into v_experiment
  from public.idea_experiments
  where id = p_experiment_id
  for update;
  if not found then
    raise no_data_found using message = 'experiment not found';
  end if;
  if v_experiment.cancellation_requested
    or v_experiment.deletion_requested_at is not null then
    raise check_violation using message = 'experiment is being cancelled or deleted';
  end if;
  if coalesce(v_experiment.pilot_specification, '{}'::jsonb) = '{}'::jsonb then
    raise check_violation using message = 'experiment specification is not ready';
  end if;

  select * into v_runtime
  from public.experiment_runtime
  where experiment_id = p_experiment_id
  for update;
  if found and v_runtime.state in ('creating', 'running', 'destroying') then
    raise check_violation using message = 'active experiment runtime must be destroyed first';
  end if;

  update public.experiment_runs
  set status = 'cancelled', outcome = 'cancelled',
      safe_error = coalesce(safe_error, 'superseded by repository quality rebuild'),
      completed_at = coalesce(completed_at, now())
  where experiment_id = p_experiment_id
    and status in ('queued', 'running', 'recovering');

  v_checkpoint := jsonb_build_object(
    'pilot_specification', coalesce(
      v_experiment.checkpoint->'pilot_specification',
      v_experiment.pilot_specification
    ),
    'pilot_specification_hash', coalesce(
      v_experiment.checkpoint->'pilot_specification_hash',
      to_jsonb(v_experiment.pilot_specification_hash)
    ),
    'pilot_compilation_attempts', coalesce(
      v_experiment.checkpoint->'pilot_compilation_attempts',
      '[]'::jsonb
    ),
    'superseded_repository_fallback', jsonb_build_object(
      'manifest', v_experiment.checkpoint->'manifest',
      'file_batches', v_experiment.checkpoint->'file_batches',
      'source', v_experiment.checkpoint->'repository_generation_source',
      'revision_id', to_jsonb(v_experiment.current_revision_id),
      'run_id', to_jsonb(v_experiment.latest_run_id),
      'superseded_at', to_jsonb(now()),
      'reason', left(coalesce(p_reason, 'repository quality rebuild'), 300)
    ),
    'repository_rebuild_requested_at', to_jsonb(now()),
    'updated_at', to_jsonb(now())
  );
  v_remaining_cny := greatest(5 - v_experiment.llm_cost_cny, 0);

  update public.idea_experiments
  set status = 'recovering', stage = 'repo_generation', progress = 8,
      outcome = 'pending', public_summary = '{}'::jsonb,
      checkpoint = v_checkpoint,
      baseline_revision_id = null, current_revision_id = null,
      latest_run_id = null, repair_count = 0,
      llm_reserved_cny = least(v_remaining_cny, 5),
      worker_id = null, lease_expires_at = null,
      next_retry_at = now(), retry_count = 0,
      last_recovery_at = now(), completed_at = null,
      updated_at = now()
  where id = p_experiment_id
  returning * into v_experiment;
  return v_experiment;
end;
$$;

revoke all on function public.requeue_experiment_repository_rebuild(uuid, text)
  from public, anon, authenticated;
grant execute on function public.requeue_experiment_repository_rebuild(uuid, text)
  to service_role;
