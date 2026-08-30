import math

from paper_research.clients.supabase import _postgres_json


def test_postgres_json_removes_null_characters_and_non_finite_numbers() -> None:
    payload = {
        "quote": "before\x00after",
        "bboxes": [[0.0, math.nan, math.inf, 1000.0]],
    }

    assert _postgres_json(payload) == {
        "quote": "beforeafter",
        "bboxes": [[0.0, None, None, 1000.0]],
    }
