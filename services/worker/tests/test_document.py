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


def test_normalize_mineru_v2_content_list_with_highlight_bbox(tmp_path: Path) -> None:
    archive_path = tmp_path / "result-v2.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("full.md", "# Method\n\nEvidence-backed method")
        archive.writestr(
            "paper_content_list_v2.json",
            json.dumps(
                [
                    [
                        {
                            "type": "title",
                            "content": {
                                "title_content": [
                                    {"type": "text", "content": "Method"}
                                ],
                                "level": 1,
                            },
                            "bbox": [80, 100, 920, 150],
                        },
                        {
                            "type": "paragraph",
                            "content": {
                                "paragraph_content": [
                                    {
                                        "type": "text",
                                        "content": "Evidence-backed method",
                                    }
                                ]
                            },
                            "bbox": [100, 200, 900, 260],
                        },
                    ]
                ]
            ),
        )

    document = normalize_mineru_zip(archive_path, tmp_path / "v2-out", "paper", "Paper")

    assert document.page_count == 1
    assert [block.page for block in document.blocks] == [1, 1]
    assert document.blocks[1].text == "Evidence-backed method"
    assert document.blocks[1].section == "Method"
    assert document.blocks[1].bbox == [100, 200, 900, 260]


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
