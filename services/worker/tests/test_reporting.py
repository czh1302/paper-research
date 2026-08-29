from paper_research.models import AnalysisReport
from paper_research.reporting import report_markdown


def test_report_always_contains_novelty_disclaimer() -> None:
    report = AnalysisReport(
        job_id="job",
        problem_statements=[],
        related_papers=[],
        rounds=[],
        search_audit=[],
        source_coverage={},
        limitations_zh="有限检索",
        limitations_en="Limited retrieval",
    )
    markdown = report_markdown(report)
    assert "不构成绝对新颖性证明" in markdown
    assert "not proof of absolute novelty" not in markdown
    assert "Search Audit" not in markdown
    assert "```json" not in markdown

    english = report_markdown(report, "en")
    assert "not proof of absolute novelty" in english
    assert "不构成绝对新颖性证明" not in english
