import json
from pathlib import Path

from paper_research.clients.local import LocalCheckpointRepository


def test_recovers_checkpoint_from_completed_report(tmp_path: Path) -> None:
    report = {
        "problem_statements": [{"paper_id": "paper-1", "title": "Paper"}],
        "joint_problem_statement": None,
        "related_papers": [{"canonical_id": "doi:10.1/example", "title": "Example"}],
        "rounds": [{"summary_en": "Summary"}],
        "search_audit": [
            {"round": 1, "source": "crossref", "query": "query", "count": 1}
        ],
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")

    repository = LocalCheckpointRepository(
        tmp_path / ".checkpoint.json",
        "expected-fingerprint",
        tmp_path / "usage.jsonl",
    )

    assert repository.state["fingerprint"] == "expected-fingerprint"
    assert repository.state["problems"][0]["paper_id"] == "paper-1"
    assert repository.state["candidates"][0]["content"]["title"] == "Example"
    assert repository.state["rounds"][0]["queries"]["audit"][0]["source"] == "crossref"
