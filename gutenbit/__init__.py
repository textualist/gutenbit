"""gutenbit — Download, parse, and store Project Gutenberg texts."""

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - only used from an unbuilt source checkout
    __version__ = "0.dev0+unknown"

from gutenbit.catalog import BookRecord, Catalog
from gutenbit.db import Database, SearchResult
from gutenbit.html_chunker import Chunk, chunk_html

__all__ = [
    "BookRecord",
    "Catalog",
    "Chunk",
    "Database",
    "SearchResult",
    "__version__",
    "chunk_html",
]
