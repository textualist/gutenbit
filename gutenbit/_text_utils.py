"""Pure text utility functions shared across CLI and display modules."""

from __future__ import annotations


def _format_int(value: int) -> str:
    return f"{value:,}"


def _preview(text: str, limit: int) -> str:
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _single_line(text: str) -> str:
    """Collapse all whitespace so tabular CLI output stays on one line."""
    return " ".join(text.split())


def _split_semicolon_list(raw: str) -> list[str]:
    return [_single_line(part) for part in raw.split(";") if part.strip()]


def _summarize_semicolon_list(raw: str, *, max_items: int) -> str:
    items = _split_semicolon_list(raw)
    if not items:
        return ""
    if len(items) <= max_items:
        return "; ".join(items)
    shown = "; ".join(items[:max_items])
    return f"{shown}; +{len(items) - max_items} more"


def _indent_block(text: str, prefix: str = "    ") -> str:
    lines = text.splitlines()
    if not lines:
        return prefix if text else ""
    return "\n".join(f"{prefix}{line}" if line else "" for line in lines)
