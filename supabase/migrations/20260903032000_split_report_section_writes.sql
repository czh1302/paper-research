-- The complete V4 payload and its four lazy-loaded report sections contain
-- overlapping evidence data.  Writing all five JSON documents plus the large
-- pipeline checkpoint in one statement can monopolize the hosted database for
-- several minutes.  Keep the generation/report/checkpoint switch atomic, but
-- let the idempotent worker persist each UI section in a separate request.
create or replace function public.save_v4_report_generation(
  p_job_id uuid,
  p_generation_id uuid,
  p_content jsonb,
  p_markdown text,
  p_summary jsonb,
  p_checkpoint jsonb,
  p_sections jsonb default '{}'::jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
set statement_timeout = '180s'
as $$
declare
  v_report_id uuid;
begin
  perform 1 from public.jobs where id = p_job_id for update;
  if not found then raise no_data_found using message = 'job not found'; end if;
  if p_generation_id is null
    or p_content->>'generation_id' <> p_generation_id::text
    or p_content #>> '{presentation,generation_id}' <> p_generation_id::text then
    raise check_violation using message = 'report generation mismatch';
  end if;
  if jsonb_typeof(coalesce(p_sections, '{}'::jsonb)) <> 'object' then
    raise check_violation using message = 'report sections must be an object';
  end if;

  insert into public.reports (job_id, generation_id, content, markdown, summary)
  values (p_job_id, p_generation_id, p_content, p_markdown, p_summary)
  on conflict (job_id) do update
  set generation_id = excluded.generation_id,
      content = excluded.content,
      markdown = excluded.markdown,
      summary = excluded.summary,
      updated_at = now()
  returning id into v_report_id;

  update public.jobs
  set checkpoint = p_checkpoint,
      updated_at = now()
  where id = p_job_id;
  return v_report_id;
end;
$$;

revoke all on function public.save_v4_report_generation(
  uuid, uuid, jsonb, text, jsonb, jsonb, jsonb
) from public, anon, authenticated;
grant execute on function public.save_v4_report_generation(
  uuid, uuid, jsonb, text, jsonb, jsonb, jsonb
) to service_role;
