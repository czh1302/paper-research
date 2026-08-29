import pytest
from paper_research.security import redact, safe_filename, validate_public_url


def test_redacts_common_secret_shapes() -> None:
    output = redact(
        "Authorization: Bearer exampleSecret12345 password=hunter2 "
        "https://upload.example/file?X-Amz-Signature=signed-value"
    )
    assert "exampleSecret" not in output
    assert "hunter2" not in output
    assert "signed-value" not in output


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1/a", "file:///etc/passwd"])
def test_rejects_non_public_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_safe_filename_removes_path_and_shell_characters() -> None:
    assert safe_filename("../../paper $(bad).pdf") == "paper_bad_.pdf"
