"""Fetch and search the Project Gutenberg CSV catalog."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO

import httpx

CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"


@dataclass(frozen=True, slots=True)
class BookRecord:
    """A book entry from the Project Gutenberg catalog."""

    id: int
    title: str
    authors: str
    language: str
    subjects: str
    locc: str
    bookshelves: str
    issued: str
    type: str


class Catalog:
    """The Project Gutenberg catalog, searchable in memory."""

    def __init__(self, records: list[BookRecord]) -> None:
        self.records = records

    @classmethod
    def fetch(cls) -> Catalog:
        """Download the CSV catalog from Project Gutenberg."""
        response = httpx.get(CATALOG_URL, follow_redirects=True, timeout=60.0)
        response.raise_for_status()
        text = response.content.decode("utf-8")

        records: list[BookRecord] = []
        for row in csv.DictReader(StringIO(text)):
            if row["Type"] != "Text":
                continue
            try:
                book_id = int(row["Text#"])
            except ValueError:
                continue
            records.append(
                BookRecord(
                    id=book_id,
                    title=row["Title"],
                    authors=row["Authors"],
                    language=row["Language"],
                    subjects=row["Subjects"],
                    locc=row["LoCC"],
                    bookshelves=row["Bookshelves"],
                    issued=row["Issued"],
                    type=row["Type"],
                )
            )
        return cls(records)

    def search(
        self,
        *,
        author: str = "",
        title: str = "",
        language: str = "",
        subject: str = "",
    ) -> list[BookRecord]:
        """Search for books matching all given criteria (case-insensitive substring match)."""
        results = self.records
        filters = {
            "authors": author,
            "title": title,
            "language": language,
            "subjects": subject,
        }
        for field, value in filters.items():
            if value:
                q = value.lower()
                results = [b for b in results if q in getattr(b, field).lower()]
        return results
