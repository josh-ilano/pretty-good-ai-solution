#!/usr/bin/env python3
"""Search the local medical-call policy index."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


DEFAULT_INDEX = Path(__file__).resolve().parent / "data" / "policy_index.sqlite3"


def fts_query(user_query: str) -> str:
    """Convert free text into a safe FTS OR query with prefix matching."""
    terms = re.findall(r"[A-Za-z0-9]+", user_query.casefold())
    if not terms:
        raise ValueError("Query must contain at least one letter or number")
    return " OR ".join(f'"{term}"*' for term in terms)


def search(index: Path, query: str, limit: int) -> list[dict]:
    connection = sqlite3.connect(index)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                c.chunk_id, c.title, c.text, c.pdf_page, c.source_page,
                c.source_file, c.source_title, c.word_count,
                bm25(chunks_fts, 2.0, 1.0) AS rank
            FROM chunks_fts
            JOIN chunks AS c ON c.rowid = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query(query), limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.index.is_file():
        raise SystemExit(f"Index not found: {args.index}. Run ingest.py first.")
    results = search(args.index, args.query, args.limit)
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    for position, result in enumerate(results, 1):
        source_page = result["source_page"] or result["pdf_page"]
        print(f"[{position}] {result['title']} (source page {source_page})")
        print(result["text"])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

