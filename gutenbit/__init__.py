"""gutenbit — Download, parse, and store Project Gutenberg texts."""

from importlib.metadata import PackageNotFoundError, version as package_version

try:
    __version__ = package_version("gutenbit")
except PackageNotFoundError:  # pragma: no cover - only used from an uninstalled checkout
    __version__ = "0.1.5.dev0"

from gutenbit.catalog import BookRecord, Catalog
from gutenbit.db import Database, SearchResult
from gutenbit.html_chunker import Chunk, chunk_html

__all__ = ["BookRecord", "Catalog", "Chunk", "Database", "SearchResult", "__version__", "chunk_html"]
