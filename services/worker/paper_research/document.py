from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentBlock, DocumentIR


def _first_matching(root: Path, pattern: str) -> Path | None:
    return next(iter(sorted(root.rglob(pattern))), None)


def _page_from_item(item: dict[str, Any]) -> int | None:
    value = item.get("page_idx", item.get("page", item.get("page_id")))
    if isinstance(value, int):
        return value + 1 if "page_idx" in item else value
    return None


def _text_from_item(item: dict[str, Any]) -> str:
    for key in ("text", "content", "caption", "html", "latex"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_mineru_zip(zip_path: Path, output_dir: Path, paper_id: str, title: str) -> DocumentIR:
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        if sum(member.file_size for member in archive.infolist()) > 500 * 1024 * 1024:
            raise ValueError("MinerU archive expands beyond the 500 MB safety limit")
        for member in archive.infolist():
            destination = (output_dir / member.filename).resolve()
            if (
                output_dir.resolve() not in destination.parents
                and destination != output_dir.resolve()
            ):
                raise ValueError("Unsafe path in MinerU archive")
        archive.extractall(output_dir)

    markdown_file = _first_matching(output_dir, "full.md") or _first_matching(output_dir, "*.md")
    content_file = _first_matching(output_dir, "*_content_list.json")
    markdown = markdown_file.read_text(encoding="utf-8", errors="replace") if markdown_file else ""
    blocks: list[DocumentBlock] = []
    page_count: int | None = None

    if content_file:
        content = json.loads(content_file.read_text(encoding="utf-8"))
        items = content if isinstance(content, list) else content.get("content_list", [])
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = _text_from_item(item)
            if not text:
                continue
            page = _page_from_item(item)
            if page:
                page_count = max(page_count or 0, page)
            bbox = item.get("bbox")
            blocks.append(
                DocumentBlock(
                    id=f"{paper_id}:b{index}",
                    paper_id=paper_id,
                    kind=str(item.get("type", "text")),
                    text=text,
                    page=page,
                    section=item.get("section") or item.get("heading"),
                    bbox=bbox if isinstance(bbox, list) else None,
                )
            )

    if not blocks and markdown:
        sections = re.split(r"(?m)(?=^#{1,4}\s+)", markdown)
        for index, section in enumerate(part for part in sections if part.strip()):
            heading = section.splitlines()[0].lstrip("# ") if section.startswith("#") else None
            blocks.append(
                DocumentBlock(
                    id=f"{paper_id}:m{index}",
                    paper_id=paper_id,
                    text=section.strip(),
                    section=heading,
                )
            )

    if not markdown:
        markdown = "\n\n".join(block.text for block in blocks)
    if not markdown.strip():
        raise ValueError("MinerU archive did not contain readable Markdown or JSON content")

    return DocumentIR(
        paper_id=paper_id,
        title=title,
        markdown=markdown,
        blocks=blocks,
        page_count=page_count,
        metadata={"mineru_archive": zip_path.name},
    )


def validate_pdf(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        magic = source.read(5)
    if path.suffix.casefold() != ".pdf" or magic != b"%PDF-":
        raise ValueError(f"Only genuine PDF files are supported: {path.name}")
    size = path.stat().st_size
    if size > 50 * 1024 * 1024:
        raise ValueError(f"PDF exceeds 50 MB: {path.name}")
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError(f"Encrypted PDF is not supported: {path.name}")
    pages = len(reader.pages)
    if pages > 100:
        raise ValueError(f"PDF exceeds 100 pages: {path.name}")
    return size, pages


def chunk_blocks(
    blocks: Iterable[DocumentBlock], max_bytes: int = 7_000_000
) -> list[list[DocumentBlock]]:
    chunks: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_size = 0
    for block in blocks:
        size = len(block.text.encode("utf-8"))
        if size > max_bytes:
            encoded = block.text.encode("utf-8")
            for offset in range(0, len(encoded), max_bytes):
                part = encoded[offset : offset + max_bytes].decode("utf-8", errors="ignore")
                if current:
                    chunks.append(current)
                    current, current_size = [], 0
                chunks.append(
                    [block.model_copy(update={"id": f"{block.id}:{offset}", "text": part})]
                )
            continue
        if current and current_size + size > max_bytes:
            chunks.append(current)
            current, current_size = [], 0
        current.append(block)
        current_size += size
    if current:
        chunks.append(current)
    return chunks


def blocks_as_prompt(blocks: list[DocumentBlock]) -> str:
    return "\n\n".join(
        f"[EVIDENCE {block.id}; page={block.page or 'unknown'}; section={block.section or 'unknown'}]\n"
        f"{block.text}"
        for block in blocks
    )
