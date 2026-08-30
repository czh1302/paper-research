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


def _strings_from_value(value: Any, field: str = "") -> list[str]:
    if isinstance(value, str):
        if field not in {"type", "url", "image_path", "img_path", "format"} and value.strip():
            return [value.strip()]
        return []
    if isinstance(value, list):
        return [text for child in value for text in _strings_from_value(child, field)]
    if isinstance(value, dict):
        return [
            text
            for child_field, child in value.items()
            for text in _strings_from_value(child, child_field)
        ]
    return []


def _text_from_item(item: dict[str, Any]) -> str:
    for key in ("text", "content", "caption", "html", "latex"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (dict, list)):
            fragments = _strings_from_value(value, key)
            if fragments:
                return " ".join(dict.fromkeys(fragments))
    return ""


def _content_items(content: Any) -> list[tuple[dict[str, Any], int | None]]:
    if isinstance(content, dict):
        content = content.get("content_list", [])
    if not isinstance(content, list):
        return []
    if content and all(isinstance(page, list) for page in content):
        return [
            (item, page_index + 1)
            for page_index, page in enumerate(content)
            for item in page
            if isinstance(item, dict)
        ]
    return [
        (item, _page_from_item(item))
        for item in content
        if isinstance(item, dict)
    ]


def _normalized_bbox(item: dict[str, Any]) -> list[float] | None:
    value = item.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        box = [float(number) for number in value]
    except (TypeError, ValueError):
        return None
    page_size = item.get("page_size") or item.get("page_size_wh")
    if isinstance(page_size, list) and len(page_size) == 2:
        try:
            width, height = float(page_size[0]), float(page_size[1])
            if width > 0 and height > 0 and (max(box[0], box[2]) > 1000 or max(box[1], box[3]) > 1000):
                box = [box[0] / width * 1000, box[1] / height * 1000, box[2] / width * 1000, box[3] / height * 1000]
        except (TypeError, ValueError):
            pass
    elif max(abs(number) for number in box) <= 1:
        box = [number * 1000 for number in box]
    box = [max(0.0, min(number, 1000.0)) for number in box]
    return box if box[2] > box[0] and box[3] > box[1] else None


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
    content_file = _first_matching(output_dir, "*_content_list_v2.json") or _first_matching(output_dir, "*_content_list.json")
    markdown = markdown_file.read_text(encoding="utf-8", errors="replace") if markdown_file else ""
    blocks: list[DocumentBlock] = []
    page_count: int | None = None

    if content_file:
        content = json.loads(content_file.read_text(encoding="utf-8"))
        items = _content_items(content)
        current_section: str | None = None
        for index, (item, nested_page) in enumerate(items):
            text = _text_from_item(item)
            if not text:
                continue
            page = nested_page or _page_from_item(item)
            if page:
                page_count = max(page_count or 0, page)
            kind = str(item.get("type", "text"))
            if kind == "title" or item.get("text_level"):
                current_section = text[:200]
            blocks.append(
                DocumentBlock(
                    id=f"{paper_id}:b{index}",
                    paper_id=paper_id,
                    kind=kind,
                    text=text,
                    page=page,
                    section=item.get("section") or item.get("heading") or current_section,
                    bbox=_normalized_bbox(item),
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
