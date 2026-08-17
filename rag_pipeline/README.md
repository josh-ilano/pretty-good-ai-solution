# Medical Call Policy RAG Pipeline

This directory is intentionally independent from the call-capture code. The
ingestion stage converts a policy PDF into:

- `chunks.jsonl`: portable, page-aware records for embedding or SignalWire
  `sw-search` ingestion later.
- `policy_index.sqlite3`: a local SQLite FTS5 index for immediate BM25 search.
- `manifest.json`: provenance, configuration, counts, and source fingerprint.

## Setup

```bash
python -m pip install -r rag_pipeline/requirements.txt
```

## Ingest the filtered policy PDF

```bash
python rag_pipeline/ingest.py \
  output/pdf/medical_practice_call_workflows_rag_extract.pdf
```

By default, artifacts are written to `rag_pipeline/data/`. Re-running ingestion
replaces only the generated files in that directory.

## Test retrieval

```bash
python rag_pipeline/search.py "How should prescription refill calls be handled?"
python rag_pipeline/search.py "appointment cancellation rescheduling" --limit 3 --json
```

## Chunk schema

Each JSONL record contains:

- `chunk_id`: stable hash derived from source fingerprint, page, and content.
- `document_id`: SHA-256 fingerprint of the source PDF.
- `title`: inferred page/section title.
- `text`: normalized source text; no generated summaries are mixed into it.
- `pdf_page`: page number in the filtered PDF.
- `source_page`: page number in the original PDF, when provenance metadata is
  available.
- `source_file`, `source_title`, and `word_count`.

The original filtered PDF declares source pages `36-45, 64-67, 125`; ingestion
uses this metadata to retain those page references.

