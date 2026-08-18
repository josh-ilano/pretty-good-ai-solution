"""Filesystem-safe names for captured scenario runs."""

from __future__ import annotations

import re
import secrets
import unicodedata


def build_run_directory_name(topic: str, identifier: str | None = None) -> str:
    """Return ``short-id_topic_words`` with unsafe characters removed."""
    short_id = identifier or secrets.token_hex(4)
    ascii_topic = unicodedata.normalize("NFKD", topic).encode(
        "ascii", "ignore"
    ).decode("ascii")
    readable_topic = re.sub(r"[^A-Za-z0-9]+", "_", ascii_topic).strip("_")
    readable_topic = re.sub(r"_+", "_", readable_topic)[:80].rstrip("_")
    return f"{short_id}_{readable_topic or 'scenario'}"
