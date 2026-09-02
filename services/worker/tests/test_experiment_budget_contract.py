from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase/migrations/20260902000000_e2b_experiments.sql"
)
FUNCTIONS = Path(__file__).resolve().parents[3] / "supabase/functions"
EXPERIMENT_WORKER = (
    Path(__file__).resolve().parents[1]
    / "paper_research/experiment_worker.py"
)


def test_experiment_budget_and_slot_gates_are_database_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "current_experiment_e2b_commitment" in sql
    assert "current_experiment_llm_commitment_cny" in sql
    assert "active_experiment_slot_count" in sql
    assert sql.count("research_atlas_experiment_budget") >= 5
    assert "issue_experiment_terminal_ticket" in sql
    assert "llm_reserved_cny" in sql
    assert "validation_slot_reserved" in sql
    assert "validation_slot_consumed" in sql
    assert "experiment_validation_runtime" in sql
    assert "experiment_global_cost_ledger" in sql
    assert "cost_ledger_token" in sql
    assert "reserve_claimed_validation_runtime" in sql
    assert "attach_claimed_validation_runtime" in sql
    assert "finish_claimed_validation_runtime" in sql
    assert (
        "revoke all on function public.claim_expired_validation_runtime(integer) "
        "from public, anon, authenticated;"
    ) in sql
    assert (
        "grant execute on function public.finish_validation_runtime_lifecycle"
        "(uuid, uuid, boolean, integer, text) to service_role;"
    ) in sql


def test_cost_gate_does_not_double_count_runs_and_survives_parent_delete() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index("create or replace function public.current_experiment_e2b_commitment")
    end = sql.index("create or replace function public.current_experiment_llm_commitment_cny", start)
    commitment = sql[start:end]

    assert "experiment_global_cost_ledger" in commitment
    assert "sum(runs.e2b_cost_usd)" not in commitment
    ledger_start = sql.index("create table public.experiment_global_cost_ledger")
    ledger_end = sql.index("create table public.experiment_attempts", ledger_start)
    assert "references public." not in sql[ledger_start:ledger_end]


def test_validation_runtime_is_action_linked_and_deletion_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "experiment_runs_action_idx" in sql
    assert "runs.action_id = p_action_id" in sql
    assert "interactivePausedForValidation" not in sql  # Worker-only checkpoint.
    assert "runtime.state <> 'destroyed'" in sql
    assert "validation runtime cleanup is pending" in sql


def test_each_scientific_run_has_a_non_resetting_sixty_minute_deadline() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    table_start = sql.index("create table public.experiment_runs (")
    table_end = sql.index("create table public.experiment_actions (", table_start)
    run_table = sql[table_start:table_end]
    assert "hard_deadline_at timestamptz not null" in run_table
    assert "interval '60 minutes'" in run_table
    assert "experiment_runs_hard_deadline_check" in run_table
    assert "hard_deadline_at <= created_at + interval '60 minutes'" in run_table

    create_start = sql.index("create or replace function public.create_experiment_run(")
    create_end = sql.index(
        "create or replace function public.assert_experiment_run_within_deadline(",
        create_start,
    )
    create_run = sql[create_start:create_end]
    assert "p_max_active_seconds integer default 3600" in create_run
    assert "research_atlas_experiment_budget" in create_run
    assert "least(greatest(coalesce(p_max_active_seconds, 3600), 60), 3600)" in create_run
    assert "if found then return v_run; end if;" in create_run
    assert "hard_deadline_at" in create_run

    assert_end = sql.index(
        "create or replace function public.increment_experiment_costs(", create_end
    )
    deadline = sql[create_end:assert_end]
    assert "v_run.hard_deadline_at - now()" in deadline
    # The Worker classifies the stable ``deadline`` token as a terminal
    # scientific result rather than scheduling another recovery attempt.
    assert "experiment run deadline reached" in deadline
    assert "v_run.action_id is distinct from p_action_id" in deadline
    assert "return least(v_remaining, 3600);" in deadline
    assert (
        "revoke all on function public.assert_experiment_run_within_deadline"
        "(uuid, text, uuid) from public, anon, authenticated;"
    ) in sql
    assert (
        "grant execute on function public.assert_experiment_run_within_deadline"
        "(uuid, text, uuid) to service_role;"
    ) in sql


def test_failed_runtime_cleanup_keeps_a_reconcilable_sandbox_record() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index("create or replace function public.schedule_claimed_runtime_cleanup")
    end = sql.index("create or replace function public.claim_expired_experiment_runtime", start)
    cleanup = sql[start:end]

    assert "state = 'destroying'" in cleanup
    assert "sandbox_id = coalesce(p_sandbox_id, sandbox_id)" in cleanup
    assert "'lifecycle_action', 'destroy'" in cleanup
    assert "delete from public.idea_experiments" not in cleanup


def test_hard_spend_cap_and_deletion_accounting_survive_cascade() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    # Service-provided RPC parameters can only lower the promised global cap.
    assert sql.count(
        "least(greatest(coalesce(p_max_spend_usd, 90), 0), 90)"
    ) >= 9
    request_start = sql.index(
        "create or replace function public.request_experiment_deletion"
    )
    request_end = sql.index(
        "create or replace function public.propagate_job_deletion_to_experiments",
        request_start,
    )
    request = sql[request_start:request_end]
    assert "when status = 'running'" in request
    assert "then llm_reserved_cny" in request
    assert "settle_experiment_terminal_reservations" in sql
    assert "cancellation_with_unsettled_reservation" in sql
    assert "deletion_with_unsettled_reservation" in sql
    assert (
        "revoke all on function public.settle_experiment_terminal_reservations(uuid, text, boolean) "
        "from public, anon, authenticated;"
    ) in sql
    assert (
        "revoke all on function public.admin_request_job_deletion(uuid, uuid) "
        "from public, anon, authenticated;"
    ) in sql
    assert (
        "grant execute on function public.admin_request_job_deletion(uuid, uuid) "
        "to service_role;"
    ) in sql


def test_spend_cap_reclaims_only_expired_running_owners_before_parking() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    experiment_start = sql.index(
        "create or replace function public.claim_next_experiment("
    )
    experiment_end = sql.index(
        "create or replace function public.renew_experiment_lease", experiment_start
    )
    experiment_claim = sql[experiment_start:experiment_end]
    experiment_cap = experiment_claim.index(
        "if public.current_experiment_e2b_commitment()"
    )
    experiment_park = experiment_claim.index(
        "update public.idea_experiments\n    set status = 'waiting_resources'",
        experiment_cap,
    )
    experiment_reclaim = experiment_claim[experiment_cap:experiment_park]
    assert "where status = 'running'" in experiment_reclaim
    assert "and lease_expires_at <= now()" in experiment_reclaim
    assert "runtime.state = 'destroying'" in experiment_reclaim
    assert "return next v_experiment;" in experiment_reclaim
    assert "status = 'queued'" not in experiment_reclaim

    action_start = sql.index(
        "create or replace function public.claim_next_experiment_action("
    )
    action_end = sql.index(
        "create or replace function public.renew_experiment_action_lease", action_start
    )
    action_claim = sql[action_start:action_end]
    action_cap = action_claim.index("if public.current_experiment_e2b_commitment()")
    action_park = action_claim.index(
        "update public.experiment_actions actions\n    set status = 'recovering'",
        action_cap,
    )
    action_reclaim = action_claim[action_cap:action_park]
    assert "where actions.status = 'running'" in action_reclaim
    assert "and actions.lease_expires_at <= now()" in action_reclaim
    assert "return next v_action;" in action_reclaim
    assert "actions.status in ('queued', 'recovering')" not in action_reclaim
    # A recovered action must not count its own validation runtime as a
    # competing global slot, both at the cap and on the ordinary path.
    assert action_claim.count(
        "active_experiment_slot_count(v_action.experiment_id, v_action.id)"
    ) >= 1
    assert "active_experiment_slot_count(actions.experiment_id, actions.id)" in action_reclaim


def test_runtime_lifecycle_blocks_normal_claims_and_idle_pause_races() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    claim_start = sql.index("create or replace function public.claim_next_experiment(")
    claim_end = sql.index(
        "create or replace function public.renew_experiment_lease", claim_start
    )
    claim = sql[claim_start:claim_end]
    assert claim.count("runtime.state = 'destroying'") >= 2

    idle_start = sql.index(
        "create or replace function public.claim_idle_experiment_runtime("
    )
    idle_end = sql.index(
        "create or replace function public.finish_experiment_runtime_lifecycle(",
        idle_start,
    )
    idle = sql[idle_start:idle_end]
    assert "language plpgsql" in idle
    assert idle.index("research_atlas_experiment_budget") < idle.index("return query")
    assert "actions.status in ('queued', 'running', 'recovering')" in idle
    assert "actions.lease_expires_at > now()" not in idle

    expired_start = sql.index(
        "create or replace function public.claim_expired_experiment_runtime("
    )
    expired_end = idle_start
    expired = sql[expired_start:expired_end]
    assert "language plpgsql" in expired
    assert expired.index("research_atlas_experiment_budget") < expired.index(
        "return query"
    )


def test_runtime_save_charges_only_incremental_reservation_at_hard_cap() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index(
        "create or replace function public.save_claimed_experiment_runtime("
    )
    end = sql.index(
        "create or replace function public.register_claimed_experiment_artifact",
        start,
    )
    runtime_save = sql[start:end]

    assert "v_existing_reservation numeric := 0;" in runtime_save
    assert "v_target_reservation numeric := 0;" in runtime_save
    assert "v_incremental_reservation numeric := 0;" in runtime_save
    assert "v_target_reservation - v_existing_reservation" in runtime_save
    assert "The live primary lease always has a fallback" in runtime_save
    assert "validation_runtime.action_id = p_action_id" in runtime_save
    assert (
        "if v_current_commitment + v_incremental_reservation\n"
        "      > least(greatest(coalesce(p_max_spend_usd, 90), 0), 90) then"
    ) in runtime_save
    assert "equivalent fallback-to-row" in runtime_save
    assert "case when v_becomes_active then v_reserve_until else null end" in runtime_save


def test_action_admission_and_claim_are_revision_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    enqueue_start = sql.index(
        "create or replace function public.enqueue_experiment_action("
    )
    enqueue_end = sql.index(
        "create or replace function public.claim_next_experiment_action(",
        enqueue_start,
    )
    enqueue = sql[enqueue_start:enqueue_end]

    # An ambiguous HTTP retry returns its original row before conflict checks.
    assert enqueue.index("idempotency_key = left(p_idempotency_key, 160)") < enqueue.index(
        "base revision does not belong to experiment"
    )
    assert "'command', 'rollback', 'validation', 'restore'" in enqueue
    assert "actions.kind <> 'read_file'" in enqueue
    assert "actions.status in ('queued', 'running', 'recovering')" in enqueue
    assert enqueue.count("experiment revision conflict") >= 2

    claim_start = enqueue_end
    claim_end = sql.index(
        "create or replace function public.renew_experiment_action_lease", claim_start
    )
    claim = sql[claim_start:claim_end]
    stale = claim[: claim.index("if public.current_experiment_e2b_commitment()")]
    assert "v_stale_action public.experiment_actions" in stale
    assert "actions.base_revision_id is distinct from experiments.current_revision_id" in stale
    assert "actions.status in ('queued', 'recovering')" in stale
    assert "settle_experiment_terminal_reservations" in stale
    assert "'stale_action_revision_conflict'" in stale
    assert "set status = 'cancelled', llm_reserved_cny = 0" in stale
    assert "validation_slot_reserved = false" in stale
    # Every mutable claim, including an expired-running reclaim at the hard
    # cap, revokes browser terminal authority before returning work.
    assert claim.count("terminal_session_epoch = terminal_session_epoch + 1") >= 2
    assert claim.count("pty_session_id = null") >= 2
    assert claim.count("if v_action.kind <> 'read_file' then") >= 2
    # A tainted/lifecycle-owned primary sandbox must finish destruction before
    # the action worker can reconnect or resume against it.
    assert claim.count("runtime.state = 'destroying'") >= 2


def test_terminal_ticket_uses_incremental_budget_and_revokes_old_epoch() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index(
        "create or replace function public.issue_experiment_terminal_ticket("
    )
    end = sql.index(
        "create or replace function public.consume_experiment_terminal_ticket", start
    )
    issue = sql[start:end]

    assert "v_existing_reservation numeric := 0;" in issue
    assert "v_target_reservation numeric := 0;" in issue
    assert "v_target_reservation - v_existing_reservation" in issue
    assert "v_runtime.estimated_cost_per_second_usd" in issue
    assert "current_experiment_e2b_commitment(v_rate, p_reserve_seconds)" in issue
    assert "terminal_session_epoch = terminal_session_epoch + 1" in issue

    consume_end = sql.index(
        "create or replace function public.reserve_claimed_validation_runtime", end
    )
    consume = sql[end:consume_end]
    assert "v_runtime.terminal_session_epoch" in consume


def test_admin_cannot_read_other_users_action_conversations_via_rls() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert 'create policy "experiment_actions_select_own"' in sql
    assert 'create policy "experiment_actions_select_admin"' not in sql
    assert "public.experiment_llm_invocations, public.experiment_artifacts" in sql


def test_ambiguous_destroy_remains_fenced_and_billable_for_retry() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index(
        "create or replace function public.finish_experiment_runtime_lifecycle("
    )
    end = sql.index(
        "revoke all on function public.enqueue_idea_experiment", start
    )
    lifecycle = sql[start:end]

    assert "p_state not in ('running', 'paused', 'destroying', 'destroyed')" in lifecycle
    assert (
        "p_lifecycle_action = 'destroy' and p_state not in ('destroying', 'destroyed')"
        in lifecycle
    )
    assert "when p_state in ('running', 'destroying')" in lifecycle
    assert "when p_state = 'destroying' then greatest(" in lifecycle
    assert "'lifecycle_action', 'destroy'" in lifecycle
    assert (
        "lifecycle_lease_expires_at = case when p_state = 'destroying' then now()"
        in lifecycle
    )
    assert "p_state in ('paused', 'destroying', 'destroyed')" in lifecycle

    worker = EXPERIMENT_WORKER.read_text(encoding="utf-8")
    ambiguous_start = worker.index(
        "A kill with an ambiguous result must continue to occupy"
    )
    ambiguous_branch = worker[ambiguous_start : ambiguous_start + 500]
    assert 'state="destroying"' in ambiguous_branch
    assert 'state="running"' not in ambiguous_branch


def test_actual_usage_is_idempotently_settled_even_after_cancellation() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index("create or replace function public.increment_experiment_costs")
    end = sql.index("create or replace function public.authorize_experiment_llm_call", start)
    settlement = sql[start:end]

    assert "experiment_usage_id" in settlement
    assert "invocation guard gates the call before it starts" in settlement
    assert "experiment invocation was not authorized" in settlement
    assert "settlement_kind = 'exact_usage'" in settlement
    assert "v_remaining_guard_cny numeric := 0;" in settlement
    assert "invocations.usage_id <> p_usage_id" in settlement
    assert "v_remaining_guard_cny" in settlement
    assert "cancellation_requested" not in settlement
    assert "E2B usage must be settled by a fenced runtime meter" in settlement


def test_every_provider_retry_requires_a_durable_remaining_reservation() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index("create or replace function public.authorize_experiment_llm_call")
    end = sql.index("create or replace function public.sync_experiment_run_costs", start)
    authorization = sql[start:end]

    assert "p_usage_id uuid default null" in authorization
    assert "p_max_call_cny numeric default null" in authorization
    assert "v_available_cny < v_max_call_cny" in authorization
    assert "invocations.status = 'authorized'" in authorization
    assert "insert into public.experiment_llm_invocations" in authorization
    assert "immutable usage id is idempotent" in authorization
    assert "experiment inference budget reached" in authorization
    assert "experiment action worker lease lost" in authorization
    assert (
        "revoke all on function public.authorize_experiment_llm_call"
        "(uuid, text, uuid, uuid, numeric) "
        "from public, anon, authenticated;"
    ) in sql
    assert (
        "grant execute on function public.authorize_experiment_llm_call"
        "(uuid, text, uuid, uuid, numeric) to service_role;"
    ) in sql


def test_invocation_guard_is_private_and_ambiguous_usage_settles_one_call() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "create table public.experiment_llm_invocations" in sql
    assert "alter table public.experiment_llm_invocations enable row level security;" in sql
    assert (
        "revoke all on public.experiment_llm_invocations "
        "from public, anon, authenticated;"
    ) in sql

    start = sql.index(
        "create or replace function public.settle_experiment_llm_reservation("
    )
    end = sql.index("create or replace function public.finalize_experiment_run", start)
    settlement = sql[start:end]
    assert "p_usage_id uuid default null" in settlement
    assert "v_amount := v_invocation.reserved_cny;" in settlement
    assert "v_remaining_guard_cny numeric := 0;" in settlement
    assert "invocations.usage_id <> p_usage_id" in settlement
    assert "case when p_usage_id is null then 0 else greatest(" in settlement
    assert "experiment_usage_id" in settlement
    assert "where usage_id = p_usage_id and status = 'authorized'" in settlement
    assert (
        "revoke all on function public.settle_experiment_llm_reservation"
        "(uuid, text, uuid, text, uuid) from public, anon, authenticated;"
    ) in sql
    assert sql.count("experiment invocation settlement pending") >= 2


def test_action_cancellation_cannot_refund_consumed_validation_or_recover() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index("create or replace function public.finish_experiment_action")
    end = sql.index("create or replace function public.request_experiment_cancellation", start)
    finish = sql[start:end]

    assert "v_experiment.cancellation_requested" in finish
    assert "p_retry_seconds := 0" in finish
    assert "validation_slot_consumed = validation_slot_consumed" in finish
    assert "user_validation_count -" not in finish


def test_feature_off_revokes_paid_mutations_and_open_terminal_sessions() -> None:
    actions = (FUNCTIONS / "_shared/experiment-actions.ts").read_text(encoding="utf-8")
    files = (FUNCTIONS / "_shared/experiment-files.ts").read_text(encoding="utf-8")
    relay = (FUNCTIONS / "experiment-terminal-relay/index.ts").read_text(
        encoding="utf-8"
    )

    assert "requireExperimentPilotEnabled();" in actions
    assert 'operation !== "read" && !pilotEnabled' in files
    assert "if (!pilotEnabled)" in files
    assert "never resume a\n    // paid runtime" in files
    assert "async function terminalSessionAuthorized" in relay
    session = relay[relay.index("async function terminalSessionAuthorized") :]
    assert 'Deno.env.get("E2B_PILOT_ENABLED")' in session
    assert "terminal_session_epoch" in session


def test_manual_and_automatic_experiment_creation_are_independently_gated() -> None:
    root = MIGRATION.parents[2]
    start = (FUNCTIONS / "start-idea-experiment/index.ts").read_text(encoding="utf-8")
    listing = (FUNCTIONS / "list-report-experiments/index.ts").read_text(encoding="utf-8")
    worker = (root / "services/worker/paper_research/worker.py").read_text(encoding="utf-8")
    pipeline = (root / "services/worker/paper_research/pipeline.py").read_text(encoding="utf-8")

    assert "requireManualExperimentEnabled();" in start
    assert "manualEnabled: manualExperimentEnabled()" in listing
    assert "automaticEnabled: automaticExperimentEnabled()" in listing
    assert "self.settings.E2B_AUTO_EXPERIMENT_ENABLED" in worker
    assert "self.settings.E2B_AUTO_EXPERIMENT_ENABLED" in pipeline


def test_exhausted_repairs_and_run_deadlines_finalize_as_environment_blocked() -> None:
    worker = EXPERIMENT_WORKER.read_text(encoding="utf-8")

    assert "assert_experiment_run_within_deadline" in worker
    assert "max_active_seconds=self.settings.E2B_RUN_TIMEOUT_SECONDS" in worker
    assert "except (SandboxCommandError, ExperimentRunDeadlineExceeded) as error:" in worker
    assert "outcome=ExperimentOutcome.ENVIRONMENT_BLOCKED.value" in worker
