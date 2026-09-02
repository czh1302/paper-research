-- Service-role-only reservation and activation for ordered multi-PDF benchmark cases.
-- Existing single-paper benchmark jobs continue to use reserve_benchmark_job().

create or replace function public.reserve_benchmark_case_job(
  p_owner_job_id uuid,
  p_upload_ids uuid[],
  p_benchmark_run_id uuid,
  p_case_id text,
  p_input_ids text[],
  p_max_rounds smallint default 1,
  p_languages text[] default array['zh','en']::text[],
  p_initially_waiting boolean default true
)
returns public.jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner_id uuid;
  v_job public.jobs;
  v_upload public.uploads;
  v_position integer;
  v_existing_upload_ids uuid[];
begin
  if p_benchmark_run_id is null then
    raise exception 'benchmark run id is required';
  end if;
  if char_length(coalesce(p_case_id, '')) not between 1 and 120 then
    raise exception 'invalid benchmark case id';
  end if;
  if cardinality(p_upload_ids) not between 2 and 5
     or cardinality(p_input_ids) <> cardinality(p_upload_ids) then
    raise exception 'a joint benchmark case requires two to five ordered inputs';
  end if;
  if array_position(p_upload_ids, null) is not null
     or array_position(p_input_ids, null) is not null
     or exists (
       select 1 from unnest(p_input_ids) as inputs(input_id)
       where char_length(inputs.input_id) not between 1 and 120
     ) then
    raise exception 'invalid benchmark input identity';
  end if;
  if (select count(distinct inputs.value) from unnest(p_upload_ids) as inputs(value))
     <> cardinality(p_upload_ids)
     or (select count(distinct inputs.value) from unnest(p_input_ids) as inputs(value))
     <> cardinality(p_input_ids) then
    raise exception 'benchmark inputs must be unique';
  end if;
  if p_max_rounds not between 1 and 5 then
    raise exception 'max_rounds must be between 1 and 5';
  end if;

  select jobs.user_id into v_owner_id
  from public.jobs
  where jobs.id = p_owner_job_id;
  if v_owner_id is null then
    raise exception 'owner source job was not found';
  end if;

  for v_position in 1..cardinality(p_upload_ids) loop
    select * into v_upload
    from public.uploads
    where uploads.id = p_upload_ids[v_position]
      and uploads.user_id = v_owner_id
      and uploads.status in ('uploaded', 'validated')
    for update;
    if not found then
      raise exception 'benchmark upload % is missing or not owned by the source job user',
        v_position;
    end if;
  end loop;

  select * into v_job
  from public.jobs
  where jobs.benchmark_run_id = p_benchmark_run_id
    and jobs.benchmark_paper_id = p_case_id;
  if found then
    if v_job.user_id <> v_owner_id or v_job.mode <> 'multi'::public.analysis_mode then
      raise exception 'benchmark identity belongs to an incompatible job';
    end if;
    select array_agg(job_files.upload_id order by job_files.position)
      into v_existing_upload_ids
    from public.job_files
    where job_files.job_id = v_job.id;
    if v_existing_upload_ids is distinct from p_upload_ids then
      raise exception 'benchmark identity was already reserved with different inputs';
    end if;
    return v_job;
  end if;

  insert into public.jobs (
    user_id,
    mode,
    max_rounds,
    languages,
    status,
    stage,
    next_retry_at,
    reserved_units,
    research_brief,
    checkpoint,
    benchmark_run_id,
    benchmark_paper_id
  ) values (
    v_owner_id,
    'multi'::public.analysis_mode,
    p_max_rounds,
    p_languages,
    case
      when p_initially_waiting then 'waiting_resources'::public.job_status
      else 'queued'::public.job_status
    end,
    'queued',
    case when p_initially_waiting then 'infinity'::timestamptz else null end,
    0,
    '',
    jsonb_build_object(
      'benchmark', jsonb_build_object(
        'run_id', p_benchmark_run_id::text,
        'case_id', p_case_id,
        'input_ids', to_jsonb(p_input_ids),
        'mode', 'multi',
        'semantics', 'symmetric',
        'cold', true,
        'activation_required', p_initially_waiting
      )
    ),
    p_benchmark_run_id,
    p_case_id
  )
  on conflict (benchmark_run_id, benchmark_paper_id)
    where benchmark_run_id is not null
  do nothing
  returning * into v_job;

  if v_job.id is null then
    select * into v_job
    from public.jobs
    where jobs.benchmark_run_id = p_benchmark_run_id
      and jobs.benchmark_paper_id = p_case_id;
    if not found
       or v_job.user_id <> v_owner_id
       or v_job.mode <> 'multi'::public.analysis_mode then
      raise exception 'benchmark identity belongs to an incompatible job';
    end if;
    select array_agg(job_files.upload_id order by job_files.position)
      into v_existing_upload_ids
    from public.job_files
    where job_files.job_id = v_job.id;
    if v_existing_upload_ids is distinct from p_upload_ids then
      raise exception 'benchmark identity was concurrently reserved with different inputs';
    end if;
    if v_job.checkpoint #> '{benchmark,input_ids}'
       is distinct from to_jsonb(p_input_ids) then
      raise exception 'benchmark identity was concurrently reserved with different input IDs';
    end if;
    return v_job;
  end if;

  insert into public.job_files (job_id, upload_id, position)
  select v_job.id, input.upload_id, input.position::smallint
  from unnest(p_upload_ids) with ordinality as input(upload_id, position);

  update public.uploads
  set delete_after = null
  where uploads.id = any(p_upload_ids);

  insert into public.job_events (job_id, kind, message, data)
  values (
    v_job.id,
    case when p_initially_waiting then 'waiting_resources' else 'queued' end,
    case
      when p_initially_waiting then 'Joint benchmark waiting for analysis resources'
      else 'Joint benchmark queued'
    end,
    jsonb_build_object(
      'benchmark_run_id', p_benchmark_run_id::text,
      'case_id', p_case_id,
      'input_ids', to_jsonb(p_input_ids)
    )
  );
  return v_job;
end;
$$;

create or replace function public.activate_benchmark_case_job(
  p_benchmark_run_id uuid,
  p_case_id text
)
returns public.jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job public.jobs;
begin
  select * into v_job
  from public.jobs
  where jobs.benchmark_run_id = p_benchmark_run_id
    and jobs.benchmark_paper_id = p_case_id
    and jobs.mode = 'multi'::public.analysis_mode
  for update;
  if not found then
    raise exception 'joint benchmark case was not found';
  end if;
  if v_job.status = 'waiting_resources'::public.job_status
     and v_job.next_retry_at = 'infinity'::timestamptz then
    update public.jobs
    set status = 'queued'::public.job_status,
        next_retry_at = null,
        checkpoint = jsonb_set(
          v_job.checkpoint,
          '{benchmark,activated_at}',
          to_jsonb(now()::text),
          true
        ),
        updated_at = now()
    where jobs.id = v_job.id
    returning * into v_job;

    insert into public.job_events (job_id, kind, message, data)
    values (
      v_job.id,
      'queued',
      'Joint benchmark resources released; analysis queued',
      jsonb_build_object(
        'benchmark_run_id', p_benchmark_run_id::text,
        'case_id', p_case_id
      )
    );
  end if;
  return v_job;
end;
$$;

revoke all on function public.reserve_benchmark_case_job(
  uuid, uuid[], uuid, text, text[], smallint, text[], boolean
) from public, anon, authenticated;
grant execute on function public.reserve_benchmark_case_job(
  uuid, uuid[], uuid, text, text[], smallint, text[], boolean
) to service_role;
revoke all on function public.activate_benchmark_case_job(uuid, text)
from public, anon, authenticated;
grant execute on function public.activate_benchmark_case_job(uuid, text)
to service_role;
