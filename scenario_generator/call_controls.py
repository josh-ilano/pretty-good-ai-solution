"""Small, dependency-free helpers for terminal call controls."""

from __future__ import annotations


CTRL_T = "\x14"


def contains_ctrl_t(text: str) -> bool:
    """Return whether terminal input contains the Ctrl+T control character."""
    return CTRL_T in text
