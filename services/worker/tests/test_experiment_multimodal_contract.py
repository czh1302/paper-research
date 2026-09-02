from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "supabase/migrations/20260902010000_experiment_multimodal_chat.sql"


def test_multimodal_chat_storage_is_private_and_cascades_with_experiment() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "create table public.experiment_chat_attachments" in sql
    assert "references public.idea_experiments(id) on delete cascade" in sql
    assert "references public.experiment_actions(id) on delete cascade" in sql
    assert "values ('experiment-chat-attachments', 'experiment-chat-attachments', false" in sql
    assert "alter table public.experiment_chat_attachments enable row level security" in sql
    assert "revoke all on public.experiment_chat_attachments from public, anon, authenticated" in sql


def test_multimodal_action_binding_is_atomic_and_service_role_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "enqueue_experiment_action_with_attachments" in sql
    assert "v_action := public.enqueue_experiment_action(" in sql
    assert "set action_id = v_action.id" in sql
    assert "status = 'bound'" in sql
    assert "grant execute on function public.enqueue_experiment_action_with_attachments" in sql
    assert ") to service_role;" in sql


def test_unbound_uploads_expire_through_the_existing_deletion_queue() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "default now() + interval '24 hours'" in sql
    assert "attachments.action_id is null" in sql
    assert "attachments.expires_at <= now()" in sql
    assert "'experiment-chat-attachments'" in sql
