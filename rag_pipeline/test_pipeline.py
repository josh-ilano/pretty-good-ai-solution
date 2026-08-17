import tempfile
import unittest
from pathlib import Path

from ingest import parse_page_spec, split_words
from search import fts_query, search
from ingest import Chunk, write_sqlite


class PipelineTests(unittest.TestCase):
    def test_page_spec(self):
        self.assertEqual(parse_page_spec("36-38, 64, 125"), [36, 37, 38, 64, 125])

    def test_chunk_overlap(self):
        text = " ".join(f"Word{i}." for i in range(250))
        chunks = list(split_words(text, max_words=100, overlap_words=20))
        self.assertGreaterEqual(len(chunks), 3)
        self.assertLessEqual(max(len(chunk.split()) for chunk in chunks), 100)

    def test_safe_query(self):
        self.assertEqual(
            fts_query('refill OR "drop table"'),
            '"refill"* OR "or"* OR "drop"* OR "table"*',
        )

    def test_search(self):
        chunk = Chunk(
            chunk_id="abc",
            document_id="doc",
            title="Prescription refill policy",
            text="Routine prescription refill requests require physician review.",
            pdf_page=1,
            source_page=44,
            source_file="policy.pdf",
            source_title="Policy",
            word_count=7,
        )
        with tempfile.TemporaryDirectory() as folder:
            index = Path(folder) / "index.sqlite3"
            write_sqlite([chunk], index)
            results = search(index, "refill physician", 3)
        self.assertEqual(results[0]["chunk_id"], "abc")


if __name__ == "__main__":
    unittest.main()
