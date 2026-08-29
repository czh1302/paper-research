import json
import zipfile
from pathlib import Path

import pytest
from paper_research.document import chunk_blocks, normalize_mineru_zip
from paper_research.models import DocumentBlock


def test_normalize_mineru_content_list(tmp_path: Path) -> None:
    archive_path = tmp_path / "result.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("full.md", "# Paper\n\nHello")
        archive.writestr(
            "paper_content_list.json",
            json.dumps([{"type": "text", "text": "Hello", "page_idx": 0, "bbox": [1, 2, 3, 4]}]),
        )
    document = normalize_mineru_zip(archive_path, tmp_path / "out", "paper", "Paper")
    assert document.page_count == 1
    assert document.blocks[0].page == 1
    assert document.blocks[0].bbox == [1, 2, 3, 4]


def test_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.md", "bad")
    with pytest.raises(ValueError, match="Unsafe path"):
        normalize_mineru_zip(archive_path, tmp_path / "out", "paper", "Paper")


def test_chunk_blocks_respects_byte_limit() -> None:
    blocks = [DocumentBlock(id=f"b{i}", paper_id="p", text="abcd") for i in range(3)]
    chunks = chunk_blocks(blocks, max_bytes=8)
    assert [len(chunk) for chunk in chunks] == [2, 1]
