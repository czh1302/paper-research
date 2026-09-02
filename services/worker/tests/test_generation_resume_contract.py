from __future__ import annotations

from pathlib import Path

from paper_research.main import build_parser

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "supabase/migrations/20260902020000_report_generations_and_resume.sql"
WORKER = ROOT / "services/worker/paper_research/experiment_worker.py"
PIPELINE = ROOT / "services/worker/paper_research/pipeline.py"


def test_resume_command_requires_an_explicit_idea_boundary_and_generation() -> None:
    args = build_parser().parse_args(
        [
            "resume-job",
            "--job-id",
            "08f0ca6d-abcf-42a4-9b58-6ed07996d135",
            "--from",
            "v4_ideas",
            "--new-report-generation",
        ]
    )
    assert args.resume_from == "v4_ideas"
    assert args.new_report_generation is True


def test_report_generation_and_resume_are_database_atomic_and_hash_guarded() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    save = sql[sql.index("create or replace function public.save_v4_report_generation(") :]
    resume = sql[sql.index("create or replace function public.resume_job_from_v4_ideas(") :]

    assert "unique (report_id, report_generation_id, idea_key)" in sql
    assert "report generation mismatch" in save
    assert "insert into public.report_sections" in save
    assert "update public.jobs" in save
    assert "input PDF hash mismatch" in resume
    assert "insert into public.report_generation_backups" in resume
    assert "- 'idea_attempts'" in resume
    assert "- 'pilot_specifications'" in resume
    assert "delivery_landscape" in resume


def test_repository_generation_and_assistant_answers_do_not_wait_for_e2b() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    answer = sql[
        sql.index("create or replace function public.claim_next_experiment_answer_action(") :
        sql.index("create or replace function public.prepare_queued_experiment_mutations(")
    ]
    repository = sql[
        sql.index("create or replace function public.claim_next_experiment_repository_generation(") :
        sql.index("create or replace function public.renew_experiment_action_lease(")
    ]

    assert "current_experiment_e2b_commitment" not in answer
    assert "current_experiment_e2b_commitment" not in repository
    assert "repository_generation_complete" in repository
    assert "repository_generation_complete" in worker
    assert worker.index("claim_next_experiment_answer_action") < worker.index(
        "claim_next_experiment_action"
    )
    assert worker.index("claim_next_experiment_repository_generation") < worker.index(
        "claim_next_experiment("
    )


def test_visible_landscape_is_frozen_while_idea_evidence_can_expand() -> None:
    pipeline = PIPELINE.read_text(encoding="utf-8")
    assert "delivery_landscape" in pipeline
    assert "delivery_profile_ids" in pipeline
    assert "frozen_profile_ids" in pipeline
    assert "comparison_boards" in pipeline
