#!/usr/bin/env python3
"""Extract a policy PDF into portable chunks and a local FTS5 search index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - exercised only without setup
    raise SystemExit(
        "Missing dependency 'pypdf'. Run: "
        "python -m pip install -r rag_pipeline/requirements.txt"
    ) from exc


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_MAX_WORDS = 360
DEFAULT_OVERLAP_WORDS = 60
SPACE_RE = re.compile(r"[ \t\f\v]+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    title: str
    text: str
    pdf_page: int
    source_page: int | None
    source_file: str
    source_title: str
    word_count: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_page_spec(value: str | None) -> list[int]:
    """Expand a page specification such as ``36-45, 64-67, 125``."""
    if not value:
        return []
    pages: list[int] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"Invalid descending page range: {part}")
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return pages


def normalize_text(raw: str) -> str:
    """Normalize extraction noise while retaining paragraph boundaries."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"(?<=\w)-\n(?=\w)", "", raw)
    lines = [SPACE_RE.sub(" ", line).strip() for line in raw.splitlines()]
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()


def infer_title(text: str, fallback: str) -> str:
    """Use a short opening line as the page title when extraction exposes one."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:6]:
        cleaned = re.sub(r"^\d+\s*", "", line).strip(" -")
        if 4 <= len(cleaned) <= 110 and len(cleaned.split()) <= 14:
            if not cleaned.lower().startswith(("best practices", "appendix")):
                return cleaned
    return fallback


def split_words(text: str, max_words: int, overlap_words: int) -> Iterable[str]:
    """Split text on sentence boundaries with deterministic word overlap."""
    if overlap_words >= max_words:
        raise ValueError("overlap_words must be smaller than max_words")
    sentences = SENTENCE_RE.split(" ".join(text.split()))
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        words = sentence.split()
        if current and current_words + len(words) > max_words:
            chunk = " ".join(current).strip()
            yield chunk
            overlap = chunk.split()[-overlap_words:] if overlap_words else []
            current = [" ".join(overlap)] if overlap else []
            current_words = len(overlap)
        if len(words) > max_words:
            step = max_words - overlap_words
            for start in range(0, len(words), step):
                window = words[start : start + max_words]
                if window:
                    yield " ".join(window)
                if start + max_words >= len(words):
                    break
            current, current_words = [], 0
        else:
            current.append(sentence)
            current_words += len(words)
    if current:
        yield " ".join(current).strip()


def extract_chunks(
    pdf_path: Path, max_words: int, overlap_words: int
) -> tuple[list[Chunk], dict]:
    reader = PdfReader(str(pdf_path))
    document_id = sha256_file(pdf_path)
    metadata = reader.metadata or {}
    source_title = str(metadata.get("/Title") or pdf_path.stem)
    source_pages = parse_page_spec(metadata.get("/SourcePages"))
    if source_pages and len(source_pages) != len(reader.pages):
        raise ValueError(
            "PDF /SourcePages metadata count does not match the PDF page count"
        )

    chunks: list[Chunk] = []
    empty_pages: list[int] = []
    for page_index, page in enumerate(reader.pages):
        pdf_page = page_index + 1
        source_page = source_pages[page_index] if source_pages else None
        text = normalize_text(page.extract_text() or "")
        if not text:
            empty_pages.append(pdf_page)
            continue
        fallback_title = f"{source_title} - page {source_page or pdf_page}"
        title = infer_title(text, fallback_title)
        for chunk_text in split_words(text, max_words, overlap_words):
            identity = f"{document_id}:{pdf_page}:{chunk_text}".encode("utf-8")
            chunks.append(
                Chunk(
                    chunk_id=hashlib.sha256(identity).hexdigest()[:24],
                    document_id=document_id,
                    title=title,
                    text=chunk_text,
                    pdf_page=pdf_page,
                    source_page=source_page,
                    source_file=pdf_path.name,
                    source_title=source_title,
                    word_count=len(chunk_text.split()),
                )
            )
    details = {
        "document_id": document_id,
        "source_path": str(pdf_path.resolve()),
        "source_file": pdf_path.name,
        "source_title": source_title,
        "source_pages": source_pages,
        "pdf_pages": len(reader.pages),
        "empty_pages": empty_pages,
    }
    return chunks, details


def write_jsonl(chunks: list[Chunk], destination: Path) -> None:
    with destination.open("w", encoding="utf-8") as stream:
        for chunk in chunks:
            stream.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")


def write_sqlite(chunks: list[Chunk], destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(
            """
            CREATE TABLE chunks (
                rowid INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL UNIQUE,
                document_id TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                pdf_page INTEGER NOT NULL,
                source_page INTEGER,
                source_file TEXT NOT NULL,
                source_title TEXT NOT NULL,
                word_count INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                title, text, content='chunks', content_rowid='rowid',
                tokenize='porter unicode61'
            );
            CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, title, text)
                VALUES (new.rowid, new.title, new.text);
            END;
            """
        )
        connection.executemany(
            """
            INSERT INTO chunks (
                chunk_id, document_id, title, text, pdf_page, source_page,
                source_file, source_title, word_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.chunk_id,
                    item.document_id,
                    item.title,
                    item.text,
                    item.pdf_page,
                    item.source_page,
                    item.source_file,
                    item.source_title,
                    item.word_count,
                )
                for item in chunks
            ],
        )
        connection.commit()
    finally:
        connection.close()


def build_manifest(details: dict, chunks: list[Chunk], args: argparse.Namespace) -> dict:
    return {
        **details,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunk_count": len(chunks),
        "total_chunk_words": sum(item.word_count for item in chunks),
        "max_words": args.max_words,
        "overlap_words": args.overlap_words,
        "outputs": {
            "chunks": "chunks.jsonl",
            "index": "policy_index.sqlite3",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Policy PDF to ingest")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    parser.add_argument("--overlap-words", type=int, default=DEFAULT_OVERLAP_WORDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if args.max_words < 100:
        print("--max-words must be at least 100", file=sys.stderr)
        return 2
    if not 0 <= args.overlap_words < args.max_words:
        print("--overlap-words must be smaller than --max-words", file=sys.stderr)
        return 2

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks, details = extract_chunks(pdf_path, args.max_words, args.overlap_words)
    if not chunks:
        print("No extractable text found in the PDF", file=sys.stderr)
        return 1

    write_jsonl(chunks, output_dir / "chunks.jsonl")
    write_sqlite(chunks, output_dir / "policy_index.sqlite3")
    manifest = build_manifest(details, chunks, args)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

