"""Benchmark the `gutenbit add` hot path and report per-phase timings."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from gutenbit.catalog import Catalog
from gutenbit.db import Database
from gutenbit.download import download_html
from gutenbit.html_chunker import chunk_html


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", type=int, help="Project Gutenberg book id")
    parser.add_argument(
        "--db",
        default="/tmp/gutenbit-benchmark.db",
        help="SQLite database path used for the storage phase",
    )
    parser.add_argument(
        "--cache-dir",
        default="",
        help="Override XDG_CACHE_HOME for the benchmark process",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None

    timings: dict[str, float | int | str] = {
        "book_id": args.book_id,
        "db": str(Path(args.db).resolve()),
    }
    if cache_dir is not None:
        timings["cache_dir"] = str(cache_dir)

    t0 = time.perf_counter()
    catalog = Catalog.fetch(cache_dir=cache_dir)
    t1 = time.perf_counter()
    record = catalog.get(args.book_id)
    if record is None:
        raise SystemExit(f"Book {args.book_id} is outside the current catalog policy.")
    timings["catalog_fetch_s"] = round(t1 - t0, 3)

    html = download_html(args.book_id)
    t2 = time.perf_counter()
    timings["download_html_s"] = round(t2 - t1, 3)
    timings["html_chars"] = len(html)

    chunks = chunk_html(html)
    t3 = time.perf_counter()
    timings["chunk_html_s"] = round(t3 - t2, 3)
    timings["chunks"] = len(chunks)

    with Database(args.db) as db:
        db._store(record, chunks)
    t4 = time.perf_counter()
    timings["store_s"] = round(t4 - t3, 3)
    timings["total_s"] = round(t4 - t0, 3)

    print(json.dumps(timings, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
