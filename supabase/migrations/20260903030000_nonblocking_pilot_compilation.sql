-- Reports are research artifacts.  A missing executable pilot contract must
-- not prevent publishing one, and the experiment worker can compile it before
-- repository generation without creating an E2B sandbox.

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
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('research_atlas_experiment_budget')
  );
  select * into v_report from public.reports where id = p_report_id;
  if not found then raise no_data_found using message = 'report not found'; end if;
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

  select * into v_experiment
  from public.idea_experiments
  where report_id = p_report_id
    and report_generation_id = v_report.generation_id
    and idea_key = p_idea_key;
  if found then return v_experiment; end if;

  if public.current_experiment_llm_commitment_cny()
      + least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
    > greatest(coalesce(p_global_llm_max_cny, 200), 0) then
    raise check_violation using message = 'global experiment inference budget reached';
  end if;

  select coalesce(content->'ideas', '[]'::jsonb) into v_ideas
  from public.report_sections
  where report_id = p_report_id and section = 'ideas';
  if coalesce(jsonb_typeof(v_ideas), 'null') <> 'array'
    or jsonb_array_length(v_ideas) = 0 then
    v_ideas := coalesce(v_report.content #> '{presentation,ideas}', '[]'::jsonb);
  end if;
  select value into v_idea from jsonb_array_elements(v_ideas)
  where value->>'key' = p_idea_key limit 1;
  if v_idea is null then raise no_data_found using message = 'idea not found in report'; end if;
  if coalesce(v_idea->>'verdict', '') not in ('recommended', 'alternative') then
    raise check_violation using message = 'only delivered report ideas can be validated';
  end if;

  select value into v_full_idea
  from jsonb_array_elements(
    case when jsonb_typeof(v_report.content #> '{presentation,ideas}') = 'array'
      then v_report.content #> '{presentation,ideas}' else '[]'::jsonb end
  )
  where value->>'key' = p_idea_key limit 1;
  v_spec := coalesce(v_full_idea->'pilot_specification', '{}'::jsonb);
  if jsonb_typeof(v_spec) <> 'object' then v_spec := '{}'::jsonb; end if;
  if p_automatic and coalesce((v_idea->>'rank')::integer, 1) <> 1 then
    raise check_violation using message = 'only the primary idea may start automatically';
  end if;

  insert into public.idea_experiments (
    report_id, report_generation_id, job_id, user_id, idea_key, idea_rank,
    idea_snapshot, pilot_specification, pilot_specification_hash,
    pilot_compilation_required, automatic_initial_run, llm_reserved_cny
  ) values (
    p_report_id, v_report.generation_id, v_report.job_id, p_user_id,
    p_idea_key, greatest(1, least(3, coalesce((v_idea->>'rank')::integer, 1))),
    v_idea, v_spec, null, v_spec = '{}'::jsonb, p_automatic,
    least(greatest(coalesce(p_llm_reservation_cny, 5), 0), 5)
  )
  on conflict (report_id, report_generation_id, idea_key) do nothing
  returning * into v_experiment;
  if v_experiment.id is null then
    select * into v_experiment from public.idea_experiments
    where report_id = p_report_id
      and report_generation_id = v_report.generation_id
      and idea_key = p_idea_key;
  end if;
  return v_experiment;
end;
$$;

revoke all on function public.enqueue_idea_experiment(
  uuid, text, uuid, boolean, numeric, numeric, numeric
) from public, anon, authenticated;
grant execute on function public.enqueue_idea_experiment(
  uuid, text, uuid, boolean, numeric, numeric, numeric
) to service_role;
