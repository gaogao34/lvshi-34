"""Extract PRC law PDFs into article-level JSONL knowledge-base chunks."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader


ARTICLE_RE = re.compile(r"^第([一二三四五六七八九十百千万零〇]+)条(?:之([一二三四五六七八九十百千万零〇]+))?\s*(.*)$")
CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十百千万零〇]+)(编|章|节)\s*(.*)$")
CONTENTS_RE = re.compile(r"^(目录|目\s*录)$")
PAGE_NUMBER_RE = re.compile(r"^(第\s*)?\d{1,4}(\s*页)?$")
WHITESPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")
NOISE_SNIPPETS = ("全国人民代表大会常务委员会公报",)


def compact_line(line: str) -> str:
    return WHITESPACE_RE.sub(" ", line.replace("\x00", "")).strip()


def extracted_pages(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    return [repair_chinese_font_encoding(page.extract_text() or "") for page in reader.pages]


def repair_chinese_font_encoding(text: str) -> str:
    """Repair PDFs whose GBK glyph bytes were decoded as UTF-8 by the extractor."""
    try:
        repaired = text.encode("utf-8").decode("gbk")
    except UnicodeError:
        return text
    chinese_before = sum("\u4e00" <= char <= "\u9fff" for char in text)
    chinese_after = sum("\u4e00" <= char <= "\u9fff" for char in repaired)
    return repaired if chinese_after > chinese_before else text


def repeated_margin_lines(pages: list[str]) -> set[str]:
    """Identify text repeated on enough pages to be a header or footer."""
    counts: Counter[str] = Counter()
    for page in pages:
        candidates = [compact_line(line) for line in page.splitlines() if compact_line(line)]
        for line in candidates[:3] + candidates[-3:]:
            if len(line) >= 3 and not ARTICLE_RE.match(line) and not CHAPTER_RE.match(line):
                counts[line] += 1
    threshold = max(3, len(pages) // 3)
    return {line for line, count in counts.items() if count >= threshold}


def clean_lines(page_text: str, margin_lines: set[str]) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        line = compact_line(raw)
        if (
            not line
            or line in margin_lines
            or PAGE_NUMBER_RE.match(line)
            or any(snippet in line for snippet in NOISE_SNIPPETS)
        ):
            continue
        lines.append(line)
    return lines


def normalize_text(lines: list[str]) -> str:
    return "".join(line.replace(" ", "") for line in lines).strip()


def slugify(name: str) -> str:
    mapping = {"劳动法": "prc-labor-law", "民法典": "prc-civil-code"}
    return mapping.get(name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "prc-law")


def split_document(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pages = extracted_pages(path)
    margin_lines = repeated_margin_lines(pages)
    title = path.stem
    document_id = slugify(title)
    hierarchy: dict[str, str] = {}
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    before_articles: list[str] = []
    in_contents = False

    for page_index, page in enumerate(pages, start=1):
        for line in clean_lines(page, margin_lines):
            if CONTENTS_RE.match(line):
                in_contents = True
                continue
            article_match = ARTICLE_RE.match(line)
            chapter_match = CHAPTER_RE.match(line)

            if in_contents:
                if article_match:
                    in_contents = False
                elif chapter_match:
                    continue
                else:
                    continue

            if chapter_match:
                hierarchy[chapter_match.group(2)] = f"第{chapter_match.group(1)}{chapter_match.group(2)}{chapter_match.group(3)}".strip()
                if chapter_match.group(2) == "编":
                    hierarchy.pop("章", None)
                    hierarchy.pop("节", None)
                elif chapter_match.group(2) == "章":
                    hierarchy.pop("节", None)
                continue

            if article_match:
                if current:
                    current["text"] = normalize_text(current.pop("_lines"))
                    chunks.append(current)
                article_no = article_match.group(1)
                if article_match.group(2):
                    article_no += f"之{article_match.group(2)}"
                current = {
                    "id": f"{document_id}-article-{article_no}",
                    "document_id": document_id,
                    "document_title": title,
                    "jurisdiction": "中华人民共和国大陆地区",
                    "source_file": path.name,
                    "source_pages": [page_index],
                    "hierarchy": hierarchy.copy(),
                    "article": f"第{article_no}条",
                    "text": "",
                    "_lines": [line],
                }
                continue

            if current:
                if current["source_pages"][-1] != page_index:
                    current["source_pages"].append(page_index)
                current["_lines"].append(line)
            else:
                before_articles.append(line)

    if current:
        current["text"] = normalize_text(current.pop("_lines"))
        chunks.append(current)

    manifest = {
        "document_id": document_id,
        "document_title": title,
        "jurisdiction": "中华人民共和国大陆地区",
        "source_file": path.name,
        "source_pages": len(pages),
        "article_chunks": len(chunks),
        "front_matter": normalize_text(before_articles),
        "removed_repeated_margin_lines": sorted(margin_lines),
    }
    return chunks, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="+", type=Path, help="Source PDF files")
    parser.add_argument("--output-dir", type=Path, default=Path("knowledge-base"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_chunks: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    for source in args.pdf:
        if not source.is_file():
            raise FileNotFoundError(f"PDF not found: {source}")
        chunks, manifest = split_document(source)
        if not chunks:
            raise ValueError(f"No article-level text could be extracted from: {source}")
        all_chunks.extend(chunks)
        manifests.append(manifest)

    jsonl_path = args.output_dir / "prc_law_articles.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in all_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n")

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"documents": manifests, "total_chunks": len(all_chunks)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(all_chunks)} article chunks to {jsonl_path}")
    print(f"Wrote source manifest to {manifest_path}")


if __name__ == "__main__":
    main()
