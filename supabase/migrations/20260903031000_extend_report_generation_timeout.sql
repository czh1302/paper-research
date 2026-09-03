-- V4 reports deliberately persist the complete payload, lazy-loaded sections,
-- and the final pipeline checkpoint in one atomic generation switch.  Large
-- evidence-grounded reports can exceed the hosted role's short default
-- statement timeout even though the write is making progress.  Scope the
-- longer timeout to this idempotent RPC only; ordinary API requests retain the
-- platform defaults.
alter function public.save_v4_report_generation(
  uuid,
  uuid,
  jsonb,
  text,
  jsonb,
  jsonb,
  jsonb
)
set statement_timeout = '180s';
