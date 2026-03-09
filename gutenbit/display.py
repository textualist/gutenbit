"""Human-readable CLI rendering for gutenbit."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

from rich import box
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

_THEME = Theme(
    {
        "accent": "cyan",
        "dim": "bright_black",
        "error": "red",
        "muted": "bright_black",
        "number": "cyan",
        "panel.border": "bright_black",
        "success": "green",
        "title": "bold",
        "warning": "yellow",
    }
)

TOC_OPENING_PREVIEW_CHARS = 56
TOC_SECTION_MAX_CHARS = 72
TOC_OVERVIEW_LIST_MAX_ITEMS = 7
EMPTY_DISPLAY = "-"
BOOK_ID_LABEL = "Book ID"
BOOK_ID_KEY = "book_id"


def _format_int(value: int) -> str:
    return f"{value:,}"


def _preview(text: str, limit: int) -> str:
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "..."


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _display_text(value: Any) -> str:
    text = _single_line(str(value)) if value is not None else ""
    return text or EMPTY_DISPLAY


def _display_words(words: int | None, *, with_label: bool = False) -> str | None:
    if words is None:
        return None
    if int(words) <= 0:
        return f"{EMPTY_DISPLAY} words" if with_label else EMPTY_DISPLAY
    shown = _format_int(int(words))
    return f"{shown} words" if with_label else shown


def _display_read(
    read: str | None,
    *,
    words: int | None = None,
    with_label: bool = False,
) -> str | None:
    normalized = _single_line(str(read)) if read is not None else ""
    if (words is not None and int(words) <= 0) or not normalized or normalized.lower() == "n/a":
        return f"{EMPTY_DISPLAY} read" if with_label else EMPTY_DISPLAY
    return f"{normalized} read" if with_label else normalized


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


def _section_label(value: Any) -> str:
    label = str(value).strip()
    return label or "(unsectioned opening)"


def _toc_section_label(label: str) -> str:
    """Render a section path as a depth-indented TOC row label."""
    parts = label.split(" / ")
    if len(parts) > 1:
        return f"{'  ' * (len(parts) - 1)}{parts[-1]}"
    return label


def _truncate_single_line(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    keep = max(1, width - 3)
    return text[:keep] + "..."


def _compact_section_label(label: str, width: int) -> str:
    """Fit a TOC section path on one line, preferring the deepest level."""
    return _truncate_single_line(_toc_section_label(label), width)


def _plural(value: int, singular: str, plural: str | None = None) -> str:
    count = _format_int(int(value))
    if value == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def format_summary_stats(
    *,
    sections: int | None = None,
    paragraphs: int | None = None,
    words: int | None = None,
    chars: int | None = None,
    read: str | None = None,
) -> list[str]:
    stats: list[str] = []
    if sections is not None:
        stats.append(_plural(int(sections), "section"))
    if paragraphs is not None:
        stats.append(_plural(int(paragraphs), "paragraph"))
    if words is not None:
        shown_words = _display_words(int(words), with_label=True)
        if shown_words:
            stats.append(shown_words)
    if chars is not None:
        stats.append(f"{_format_int(int(chars))} chars")
    if read:
        shown_read = _display_read(read, words=words, with_label=True)
        if shown_read:
            stats.append(shown_read)
    return stats


def format_search_summary_count(*, shown_results: int, total_results: int) -> str:
    if shown_results == total_results:
        return _plural(total_results, "result")
    return f"{_format_int(shown_results)} shown"


def format_search_footer_stats(
    *,
    shown_results: int,
    total_results: int,
    order: str,
) -> list[str]:
    stats = [_plural(total_results, "result")]
    if shown_results != total_results:
        stats.append(f"{_format_int(shown_results)} shown")
    stats.append(f"{order} order")
    return stats


def _section_summary_stats(overview: dict[str, Any]) -> list[str]:
    return format_summary_stats(
        sections=int(overview["sections_total"]),
        paragraphs=int(overview["paragraphs_total"]),
        words=int(overview["est_words"]),
        chars=int(overview["chars_total"]),
        read=str(overview["est_read_time"]),
    )


def _section_meta_bits(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    bits: list[tuple[str, Any]] = [(BOOK_ID_LABEL, payload[BOOK_ID_KEY])]
    if payload.get("section"):
        bits.append(("Section", payload["section"]))
    if payload.get("section_number") is not None:
        bits.append(("No.", payload["section_number"]))
    if payload.get("position") is not None:
        bits.append(("Position", payload["position"]))
    if payload.get("forward") is not None:
        bits.append(("Forward", payload["forward"]))
    if payload.get("radius") is not None:
        bits.append(("Radius", payload["radius"]))
    if payload.get("all"):
        bits.append(("Scope", "Full text"))
    return bits


def _passage_header(payload: dict[str, Any]) -> str:
    parts = [
        f"{BOOK_ID_KEY}={payload[BOOK_ID_KEY]}",
        f"title={payload['title']}",
    ]
    if payload.get("author"):
        parts.append(f"author={payload['author']}")
    if payload.get("section"):
        parts.append(f"section={payload['section']}")
    if payload.get("section_number") is not None:
        parts.append(f"section_number={payload['section_number']}")
    if payload.get("position") is not None:
        parts.append(f"position={payload['position']}")
    if payload.get("forward") is not None:
        parts.append(f"forward={payload['forward']}")
    if payload.get("radius") is not None:
        parts.append(f"radius={payload['radius']}")
    if payload.get("all"):
        parts.append("all")
    return "  ".join(parts)


def _indent_block(text: str, prefix: str = "    ") -> str:
    lines = text.splitlines()
    if not lines:
        return prefix if text else ""
    return "\n".join(f"{prefix}{line}" if line else "" for line in lines)


@dataclass(frozen=True)
class _TocRow:
    number: str
    section: str
    position: str
    words: str
    read: str
    opening: str


def _toc_rows(sections: list[dict[str, Any]]) -> list[_TocRow]:
    rows: list[_TocRow] = []
    for section in sections:
        est_words = int(section["est_words"])
        rows.append(
            _TocRow(
                number=str(section["section_number"]),
                section=_toc_section_label(_section_label(section["section"])),
                position=_format_int(int(section["position"])),
                words=_display_words(est_words) or EMPTY_DISPLAY,
                read=_display_read(str(section["est_read"]), words=est_words) or EMPTY_DISPLAY,
                opening=_display_text(section["opening_line"]),
            )
        )
    return rows


def _toc_widths(rows: list[_TocRow], *, total_width: int | None = None) -> dict[str, int]:
    number_width = max(len("#"), max((len(row.number) for row in rows), default=1))
    section_width = max(len("Section"), max((len(row.section) for row in rows), default=7))
    section_width = min(section_width, TOC_SECTION_MAX_CHARS)
    position_width = max(len("Position"), max((len(row.position) for row in rows), default=8))
    words_width = max(len("Words"), max((len(row.words) for row in rows), default=5))
    read_width = max(len("Read"), max((len(row.read) for row in rows), default=4))
    opening_width = min(
        TOC_OPENING_PREVIEW_CHARS,
        max(len("Opening"), max((len(row.opening) for row in rows), default=7)),
    )

    if total_width is not None:
        gutter_width = 10
        fixed_width = number_width + position_width + words_width + read_width
        available_width = max(
            len("Section") + len("Opening"),
            total_width - fixed_width - gutter_width,
        )
        min_opening_width = max(len("Opening"), 16)
        overflow = section_width + opening_width - available_width
        if overflow > 0:
            shrink_opening = min(overflow, max(0, opening_width - min_opening_width))
            opening_width -= shrink_opening
            overflow -= shrink_opening
        if overflow > 0:
            section_width = max(len("Section"), section_width - overflow)

    return {
        "number": number_width,
        "section": section_width,
        "position": position_width,
        "words": words_width,
        "read": read_width,
        "opening": opening_width,
    }


def _toc_separator(widths: dict[str, int]) -> str:
    return (
        f"{'-' * widths['number']}  "
        f"{'-' * widths['section']}  "
        f"{'-' * widths['position']}  "
        f"{'-' * widths['words']}  "
        f"{'-' * widths['read']}  "
        f"{'-' * widths['opening']}"
    )


def _print_key_value_table(
    stream: TextIO,
    rows: list[tuple[str, str]],
    *,
    show_header: bool = True,
    key_header: str = "Field",
    value_header: str = "Value",
) -> None:
    if not rows:
        return
    key_width = max(len(key_header), max(len(key) for key, _ in rows))
    if show_header:
        print(f"  {key_header:<{key_width}}  {value_header}", file=stream)
        print(f"  {'-' * key_width}  {'-' * len(value_header)}", file=stream)
    for key, value in rows:
        shown = _display_text(value)
        print(f"  {key:<{key_width}}  {shown}", file=stream)


def _print_table(
    stream: TextIO,
    headers: list[str],
    rows: list[list[str]],
    *,
    right_align: set[int],
) -> None:
    if not headers:
        return
    widths = []
    for idx, header in enumerate(headers):
        widest = len(header)
        for row in rows:
            widest = max(widest, len(row[idx]))
        widths.append(widest)

    def _fmt(cell: str, idx: int) -> str:
        width = widths[idx]
        if idx in right_align:
            return f"{cell:>{width}}"
        return f"{cell:<{width}}"

    print("  " + "  ".join(_fmt(header, i) for i, header in enumerate(headers)), file=stream)
    print("  " + "  ".join("-" * width for width in widths), file=stream)
    for row in rows:
        print("  " + "  ".join(_fmt(cell, i) for i, cell in enumerate(row)), file=stream)


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


@dataclass
class CliDisplay:
    """Render human-readable CLI output with TTY-aware styling."""

    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    interactive: bool | None = None
    color: bool | None = None
    width: int | None = None

    def __post_init__(self) -> None:
        if self.interactive is None:
            self.interactive = _is_tty(self.stdout)
        if self.color is None:
            self.color = self.interactive
        self._out = Console(
            file=self.stdout,
            theme=_THEME,
            width=self.width,
            force_terminal=self.interactive,
            no_color=not self.color,
            color_system="auto" if self.color else None,
        )
        self._err = Console(
            file=self.stderr,
            theme=_THEME,
            width=self.width,
            force_terminal=self.interactive,
            no_color=not self.color,
            color_system="auto" if self.color else None,
        )
        self._heading_count = 0

    def _section_heading(self, title: str) -> Text:
        return Text(title, style="accent")

    def _summary_text(self, items: list[str]) -> Text:
        text = Text()
        for idx, item in enumerate(item for item in items if item):
            if idx:
                text.append(" · ", style="muted")
            text.append(item)
        return text

    def _meta_text(self, bits: list[tuple[str, Any]]) -> Text:
        text = Text()
        for idx, (label, value) in enumerate(bits):
            if idx:
                text.append(" · ", style="muted")
            text.append(f"{label} ", style="muted")
            text.append(str(value))
        return text

    def _record_header(
        self,
        *,
        title: str,
        author: str = "",
        bits: list[tuple[str, Any]] | None = None,
        index: int | None = None,
    ) -> Group:
        title_line = Text()
        if index is not None:
            title_line.append(f"{index}. ", style="number")
        title_line.append(title, style="title")

        lines: list[Any] = [title_line]
        if author:
            lines.append(Text(author))
        if bits:
            lines.append(self._meta_text(bits))
        return Group(*lines)

    def _passage_text(self, content: str) -> Text:
        return Text(content, overflow="fold", no_wrap=False)

    def _footer(self, *, stats: list[str], commands: list[str] | None = None) -> None:
        commands = [command for command in (commands or []) if command]
        self._out.print()
        self._out.print(self._summary_text(stats), style="muted")
        if not commands:
            return

        self._out.print()
        self._out.print(Text("Next", style="muted"))
        for command in commands:
            self._out.print(Text(f"  {command}", style="muted"))

    def _print_text(self, text: str, *, style: str | None = None, err: bool = False) -> None:
        if self.interactive:
            console = self._err if err else self._out
            console.print(Text(text, style=style))
        else:
            print(text, file=self.stderr if err else self.stdout)

    def _begin_output(self) -> None:
        if self.interactive:
            self._out.print()
        else:
            print(file=self.stdout)
        self._heading_count = 0

    def _print_heading(self, title: str) -> None:
        if self._heading_count:
            if self.interactive:
                self._out.print()
            else:
                print(file=self.stdout)

        if self.interactive:
            self._out.print(self._section_heading(title))
            self._out.print()
        else:
            print(title, file=self.stdout)
            print(file=self.stdout)

        self._heading_count += 1

    def _toc_header_text(self, widths: dict[str, int]) -> Text:
        text = Text()
        text.append(f"{'#':>{widths['number']}}", style="muted")
        text.append("  ")
        text.append(f"{'Section':<{widths['section']}}", style="muted")
        text.append("  ")
        text.append(f"{'Position':>{widths['position']}}", style="muted")
        text.append("  ")
        text.append(f"{'Words':>{widths['words']}}", style="muted")
        text.append("  ")
        text.append(f"{'Read':>{widths['read']}}", style="muted")
        text.append("  ")
        text.append(f"{'Opening':<{widths['opening']}}", style="muted")
        return text

    def _toc_row_text(self, row: _TocRow, widths: dict[str, int]) -> Text:
        section = _truncate_single_line(row.section, widths["section"])
        opening = _truncate_single_line(row.opening, widths["opening"])

        text = Text()
        text.append(f"{row.number:>{widths['number']}}", style="accent")
        text.append("  ")
        text.append(f"{section:<{widths['section']}}")
        text.append("  ")
        text.append(f"{row.position:>{widths['position']}}")
        text.append("  ")
        text.append(f"{row.words:>{widths['words']}}")
        text.append("  ")
        text.append(f"{row.read:>{widths['read']}}")
        text.append("  ")
        text.append(f"{opening:<{widths['opening']}}")
        return text

    def status(self, message: str) -> None:
        self._print_text(message, style="muted")

    def success(self, message: str) -> None:
        self._print_text(message, style="success")

    def warning(self, message: str) -> None:
        self._print_text(message, style="warning")

    def error(self, message: str, *, err: bool = False) -> None:
        self._print_text(message, style="error", err=err)

    def books(self, books: list[Any], *, db_path: str) -> None:
        if not self.interactive:
            self._books_plain(books, db_path=db_path)
            return

        self._begin_output()
        table = Table(box=box.SIMPLE_HEAD, header_style="muted", pad_edge=False)
        table.add_column("ID", justify="right", style="accent", no_wrap=True)
        table.add_column("Authors", max_width=40)
        table.add_column("Title", style="title")
        for book in books:
            authors = _summarize_semicolon_list(book.authors, max_items=2)[:40]
            table.add_row(str(book.id), authors, _single_line(book.title))
        self._out.print(table)
        self._out.print(Text(f"{len(books)} book(s) stored in {db_path}", style="muted"))

    def _books_plain(self, books: list[Any], *, db_path: str) -> None:
        self._begin_output()
        print(f"  {'ID':>6}  {'AUTHORS':<40s}  TITLE", file=self.stdout)
        print(
            f"  {'------':>6}  {'----------------------------------------':<40s}  -----",
            file=self.stdout,
        )
        for book in books:
            authors = _summarize_semicolon_list(book.authors, max_items=2)[:40]
            title = _single_line(book.title)
            print(f"  {book.id:>6}  {authors:<40s}  {title}", file=self.stdout)
        print(f"\n{len(books)} book(s) stored in {db_path}", file=self.stdout)

    def catalog(self, books: list[Any], *, remaining_count: int) -> None:
        if not self.interactive:
            self._catalog_plain(books, remaining_count=remaining_count)
            return

        self._begin_output()
        table = Table(box=box.SIMPLE_HEAD, header_style="muted", pad_edge=False)
        table.add_column("ID", justify="right", style="accent", no_wrap=True)
        table.add_column("Authors", max_width=40)
        table.add_column("Title", style="title")
        for book in books:
            authors = _summarize_semicolon_list(book.authors, max_items=2)[:40]
            table.add_row(str(book.id), authors, _single_line(book.title))
        self._out.print(table)
        if remaining_count > 0:
            self._out.print(
                Text(
                    f"... and {remaining_count} more (use --limit to show more)",
                    style="muted",
                )
            )

    def _catalog_plain(self, books: list[Any], *, remaining_count: int) -> None:
        self._begin_output()
        print(f"  {'ID':>6}  {'AUTHORS':<40s}  TITLE", file=self.stdout)
        print(
            f"  {'------':>6}  {'----------------------------------------':<40s}  -----",
            file=self.stdout,
        )
        for book in books:
            authors = _summarize_semicolon_list(book.authors, max_items=2)[:40]
            title = _single_line(book.title)
            print(f"  {book.id:>6}  {authors:<40s}  {title}", file=self.stdout)
        if remaining_count > 0:
            print(f"  ... and {remaining_count} more (use --limit to show more)", file=self.stdout)

    def section_summary(self, summary: dict[str, Any]) -> None:
        if not self.interactive:
            self._section_summary_plain(summary)
            return

        self._begin_output()
        book = summary["book"]
        overview = summary["overview"]
        sections = summary["sections"]
        quick_actions = summary["quick_actions"]

        book_grid = Table.grid(padding=(0, 2))
        book_grid.add_column(style="muted", justify="right", no_wrap=True)
        book_grid.add_column()
        book_rows: list[tuple[str, str]] = [
            ("Title", str(book["title"])),
            ("Gutenberg ID", str(book["id"])),
        ]
        if book.get("authors"):
            book_rows.append(("Authors", str(book["authors"])))
        if book.get("language"):
            book_rows.append(("Language", str(book["language"])))
        if book.get("issued"):
            book_rows.append(("Issued", str(book["issued"])))
        if book.get("type"):
            book_rows.append(("Type", str(book["type"])))
        if book.get("locc"):
            book_rows.append(("LoCC", str(book["locc"])))
        subjects = _summarize_semicolon_list(
            ";".join(book.get("subjects", [])),
            max_items=TOC_OVERVIEW_LIST_MAX_ITEMS,
        )
        shelves = _summarize_semicolon_list(
            ";".join(book.get("bookshelves", [])),
            max_items=TOC_OVERVIEW_LIST_MAX_ITEMS,
        )
        if subjects:
            book_rows.append(("Subjects", subjects))
        if shelves:
            book_rows.append(("Shelves", shelves))
        for label, value in book_rows:
            value_text = Text(value, style="title" if label == "Title" else None)
            book_grid.add_row(Text(label, style="muted"), value_text)

        toc_rows = _toc_rows(sections)
        toc_widths = _toc_widths(toc_rows, total_width=self._out.width)

        self._print_heading("Overview")
        self._out.print(book_grid)
        self._print_heading("Contents")
        if sections:
            self._out.print(self._toc_header_text(toc_widths))
            self._out.print(Text(_toc_separator(toc_widths), style="muted"))
            for row in toc_rows:
                self._out.print(self._toc_row_text(row, toc_widths))
        else:
            self._out.print(Text("(no headings found)", style="muted"))
        self._footer(
            stats=_section_summary_stats(overview),
            commands=[
                quick_actions["search"],
                quick_actions["view_first_section"],
                quick_actions["view_by_position"],
                quick_actions["view_all"],
            ],
        )

    def _section_summary_plain(self, summary: dict[str, Any]) -> None:
        self._begin_output()
        book = summary["book"]
        overview = summary["overview"]
        sections = summary["sections"]
        quick_actions = summary["quick_actions"]

        authors = str(book.get("authors", ""))
        subjects = _summarize_semicolon_list(
            ";".join(book.get("subjects", [])),
            max_items=TOC_OVERVIEW_LIST_MAX_ITEMS,
        )
        shelves = _summarize_semicolon_list(
            ";".join(book.get("bookshelves", [])),
            max_items=TOC_OVERVIEW_LIST_MAX_ITEMS,
        )

        book_rows: list[tuple[str, str]] = []
        book_rows.append(("Title", str(book["title"])))
        book_rows.append(("Gutenberg ID", str(book["id"])))
        if authors:
            book_rows.append(("Authors", authors))
        if book.get("language"):
            book_rows.append(("Language", str(book["language"])))
        if book.get("issued"):
            book_rows.append(("Issued", str(book["issued"])))
        if book.get("type"):
            book_rows.append(("Type", str(book["type"])))
        if book.get("locc"):
            book_rows.append(("LoCC", str(book["locc"])))
        if subjects:
            book_rows.append(("Subjects", subjects))
        if shelves:
            book_rows.append(("Shelves", shelves))

        self._print_heading("Overview")
        _print_key_value_table(self.stdout, book_rows, show_header=False)

        self._print_heading("Contents")
        if not sections:
            print("  (no headings found)", file=self.stdout)
        else:
            toc_rows = _toc_rows(sections)
            toc_widths = _toc_widths(toc_rows)

            print(
                f" {'#':>{toc_widths['number']}}  {'Section':<{toc_widths['section']}}  "
                f"{'Position':>{toc_widths['position']}}  {'Words':>{toc_widths['words']}}  "
                f"{'Read':>{toc_widths['read']}}  "
                f"{'Opening':<{toc_widths['opening']}}",
                file=self.stdout,
            )
            print(
                f" {_toc_separator(toc_widths)}",
                file=self.stdout,
            )

            for row in toc_rows:
                section_label = _truncate_single_line(row.section, toc_widths["section"])
                opening = _truncate_single_line(row.opening, toc_widths["opening"])
                print(
                    f" {row.number:>{toc_widths['number']}}  "
                    f"{section_label:<{toc_widths['section']}}  "
                    f"{row.position:>{toc_widths['position']}}  "
                    f"{row.words:>{toc_widths['words']}}  "
                    f"{row.read:>{toc_widths['read']}}  "
                    f"{opening:<{toc_widths['opening']}}",
                    file=self.stdout,
                )

        print(
            "\n" + " · ".join(_section_summary_stats(overview)),
            file=self.stdout,
        )

        print("\nQuick actions", file=self.stdout)
        if quick_actions["search"]:
            print(f"  {quick_actions['search']}", file=self.stdout)
        if quick_actions["view_first_section"]:
            print(f"  {quick_actions['view_first_section']}", file=self.stdout)
        if quick_actions["view_by_position"]:
            print(f"  {quick_actions['view_by_position']}", file=self.stdout)
        if quick_actions["view_all"]:
            print(f"  {quick_actions['view_all']}", file=self.stdout)

    def search_results(
        self,
        *,
        query: str,
        order: str,
        items: list[dict[str, Any]],
        total_results: int | None = None,
    ) -> None:
        total_results = len(items) if total_results is None else total_results
        if not self.interactive:
            self._search_results_plain(
                query=query,
                order=order,
                items=items,
                total_results=total_results,
            )
            return

        self._begin_output()
        self._print_heading("Search")
        summary = Text()
        summary.append("Query ", style="muted")
        summary.append(f'"{query}"')
        summary.append(" · ", style="muted")
        summary.append(order)
        summary.append(" · ", style="muted")
        summary.append(
            format_search_summary_count(
                shown_results=len(items),
                total_results=total_results,
            )
        )
        self._out.print(summary)
        self._out.print()
        for idx, item in enumerate(items, start=1):
            bits = _section_meta_bits(item)
            if item.get("score") is not None:
                bits.append(("Score", f"{float(item['score']):.2f}"))
            self._out.print(
                self._record_header(
                    title=str(item["title"]),
                    author=str(item.get("author", "")),
                    bits=bits,
                    index=idx,
                )
            )
            self._out.print()
            self._out.print(self._passage_text(str(item["content"])))
            if idx != len(items):
                self._out.print()
                self._out.print()
        self._footer(
            stats=format_search_footer_stats(
                shown_results=len(items),
                total_results=total_results,
                order=order,
            )
        )

    def _search_results_plain(
        self,
        *,
        query: str,
        order: str,
        items: list[dict[str, Any]],
        total_results: int,
    ) -> None:
        self._begin_output()
        self._print_heading("Search")
        print(
            f"query={query!r}  order={order}  total_results={total_results}  "
            f"shown_results={len(items)}",
            file=self.stdout,
        )
        for idx, item in enumerate(items, start=1):
            print(f"\n{idx:>2}. {_passage_header(item)}", file=self.stdout)
            print(f"    score={item['score']:.2f}", file=self.stdout)
            print(_indent_block(str(item["content"])), file=self.stdout)
            print(file=self.stdout)
        print(
            " · ".join(
                format_search_footer_stats(
                    shown_results=len(items),
                    total_results=total_results,
                    order=order,
                )
            ),
            file=self.stdout,
        )

    def passage(
        self,
        payload: dict[str, Any],
        *,
        action_hints: dict[str, str] | None = None,
        footer_stats: list[str] | None = None,
    ) -> None:
        if not self.interactive:
            self._passage_plain(payload, action_hints=action_hints, footer_stats=footer_stats)
            return

        self._begin_output()
        self._print_heading("View")
        self._out.print(
            self._record_header(
                title=str(payload["title"]),
                author=str(payload.get("author", "")),
                bits=_section_meta_bits(payload),
            )
        )
        self._out.print()
        self._out.print(self._passage_text(str(payload["content"])))
        if action_hints or footer_stats:
            self._footer(
                stats=footer_stats or [],
                commands=[
                    action_hints.get("toc", "") if action_hints else "",
                    action_hints.get("view_first_section", "") if action_hints else "",
                    action_hints.get("view_all", "") if action_hints else "",
                    action_hints.get("search", "") if action_hints else "",
                ],
            )

    def _passage_plain(
        self,
        payload: dict[str, Any],
        *,
        action_hints: dict[str, str] | None = None,
        footer_stats: list[str] | None = None,
    ) -> None:
        self._begin_output()
        self._print_heading("View")
        print(_passage_header(payload), file=self.stdout)
        print(file=self.stdout)
        print(str(payload["content"]), file=self.stdout)
        if footer_stats:
            print("\n" + " · ".join(item for item in footer_stats if item), file=self.stdout)
        if action_hints:
            print("\nQuick actions", file=self.stdout)
            for key in [
                "toc",
                "view_first_section",
                "view_all",
                "search",
            ]:
                cmd = action_hints.get(key, "")
                if cmd:
                    print(f"  {cmd}", file=self.stdout)

    def examples(self, message: str, *, examples: list[str], tip: str | None = None) -> None:
        if not self.interactive:
            print(message, file=self.stdout)
            if examples:
                print("Available sections include:", file=self.stdout)
                for section in examples:
                    print(f"  {section}", file=self.stdout)
            if tip:
                print(f"Tip: run `{tip}` to list all sections.", file=self.stdout)
            return

        self._out.print(Text(message, style="warning"))
        if examples:
            self._out.print()
            self._out.print(Text("Available sections include:", style="muted"))
            examples_table = Table.grid(padding=(0, 0))
            examples_table.add_column()
            for section in examples:
                examples_table.add_row(Text(f"  {section}"))
            self._out.print(examples_table)
        if tip:
            self._out.print()
            tip_text = Text("Tip: ", style="muted")
            tip_text.append(tip, style="accent")
            self._out.print(tip_text)
