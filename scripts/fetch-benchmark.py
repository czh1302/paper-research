#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark" / "benchmark_v1.json"


def verify(destination: Path, paper: dict[str, object]) -> str:
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    expected_digest = paper.get("sha256")
    if expected_digest and digest != expected_digest:
        raise RuntimeError(f"Checksum mismatch for {destination}")
    pages = len(PdfReader(destination).pages)
    expected_pages = paper.get("pages")
    if expected_pages and pages != expected_pages:
        raise RuntimeError(f"Page-count mismatch for {destination}: {pages}")
    return digest


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for paper in manifest["papers"]:
        destination = ROOT / paper["local_path"]
        if destination.exists():
            digest = verify(destination, paper)
            print(f"ready {paper['id']} {digest}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            paper["pdf_url"],
            headers={"User-Agent": "PaperResearchBenchmark/0.1 contact=research@example.invalid"},
        )
        print(f"fetching {paper['id']} from arXiv")
        with urllib.request.urlopen(request, timeout=120) as response:
            content = response.read(50 * 1024 * 1024 + 1)
        if len(content) > 50 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            raise RuntimeError(f"Invalid benchmark PDF for {paper['id']}")
        destination.write_bytes(content)
        print(f"saved {destination} {verify(destination, paper)}")
        time.sleep(3)


if __name__ == "__main__":
    main()
