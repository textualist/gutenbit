from copy import deepcopy
from io import StringIO

from gutenbit.cli import _build_section_summary, _passage_payload
from gutenbit.display import (
    CliDisplay,
    _toc_rows,
    format_search_footer_stats,
    format_search_summary_count,
    format_summary_stats,
)
from tests.test_search import _make_db


def test_format_summary_stats_uses_consistent_ordering():
    assert format_summary_stats(
        sections=53,
        paragraphs=3834,
        words=175889,
        chars=879444,
        read="11h 44m",
    ) == [
        "53 sections",
        "3,834 paragraphs",
        "175,889 words",
        "879,444 chars",
        "11h 44m read",
    ]


def test_format_summary_stats_uses_dash_for_zero_words_and_read():
    assert format_summary_stats(paragraphs=0, words=0, read="n/a") == [
        "0 paragraphs",
        "- words",
        "- read",
    ]


def test_format_search_stats_show_total_and_shown_when_limited():
    assert format_search_summary_count(shown_results=10, total_results=237) == "10 shown"
    assert format_search_footer_stats(
        shown_results=10,
        total_results=237,
        order="ranked",
    ) == [
        "237 results",
        "10 shown",
        "ranked order",
    ]


def test_toc_rows_use_dash_for_empty_section_metrics():
    rows = _toc_rows(
        [
            {
                "section_number": 1,
                "section": "PART ONE",
                "position": 0,
                "est_words": 0,
                "est_read": "n/a",
                "opening_line": "",
            }
        ]
    )

    assert rows[0].words == "-"
    assert rows[0].read == "-"
    assert rows[0].opening == "-"


def test_toc_rows_indent_nested_section_paths():
    rows = _toc_rows(
        [
            {
                "section_number": 1,
                "section": "BOOK ONE: 1805 / CHAPTER XII",
                "position": 0,
                "est_words": 42,
                "est_read": "1m",
                "opening_line": "Opening line.",
            },
            {
                "section_number": 2,
                "section": "PLAY TITLE / ACT I / SCENE II",
                "position": 1,
                "est_words": 84,
                "est_read": "1m",
                "opening_line": "Second opening.",
            },
        ]
    )

    assert rows[0].section == "  CHAPTER XII"
    assert rows[1].section == "    SCENE II"


def test_rich_search_results_use_visual_header(tmp_path):
    _make_db(tmp_path)
    item = _passage_payload(
        book_id=1,
        title="Moby Dick",
        author="Melville, Herman",
        section="CHAPTER 1",
        section_number=1,
        position=1,
        forward=None,
        radius=None,
        content="Call me Ishmael.",
        extras={"score": 1.2},
    )
    out = StringIO()

    CliDisplay(stdout=out, interactive=True, color=False, width=100).search_results(
        query="Ishmael",
        order="ranked",
        items=[item],
        total_results=12,
    )

    rendered = out.getvalue()
    assert "Search" in rendered
    assert 'Query "Ishmael" · ranked · 1 shown' in rendered
    assert "query='Ishmael'" not in rendered
    assert "Score 1.20" in rendered
    assert "Score 1.20\n\nCall me Ishmael." in rendered
    assert "Call me Ishmael." in rendered
    assert "12 results · 1 shown · ranked order" in rendered


def test_rich_passage_separates_title_from_metadata(tmp_path):
    _make_db(tmp_path)
    payload = _passage_payload(
        book_id=1,
        title="Moby Dick",
        author="Melville, Herman",
        section="CHAPTER 1",
        section_number=1,
        position=0,
        forward=3,
        radius=None,
        content="CHAPTER 1\n\nCall me Ishmael.",
    )
    out = StringIO()

    CliDisplay(stdout=out, interactive=True, color=False, width=100).passage(
        payload,
        action_hints={
            "toc": "gutenbit toc 1",
            "view_first_section": "gutenbit view 1 --section 1 --forward 20",
            "view_all": "gutenbit view 1 --all",
            "search": "gutenbit search <query> --book 1",
        },
    )

    rendered = out.getvalue()
    assert "View" in rendered
    assert "Moby Dick" in rendered
    assert "title=Moby Dick" not in rendered
    assert "Book ID 1 · Section CHAPTER 1 · No. 1 · Position 0 · Forward 3" in rendered
    assert "Forward 3\n\nCHAPTER 1" in rendered
    assert "\nNext\n" in rendered
    assert "gutenbit toc 1" in rendered


def test_plain_passage_shows_footer_stats(tmp_path):
    _make_db(tmp_path)
    payload = _passage_payload(
        book_id=1,
        title="Moby Dick",
        author="Melville, Herman",
        section="CHAPTER 1",
        section_number=1,
        position=0,
        forward=1,
        radius=None,
        content="CHAPTER 1",
    )
    out = StringIO()

    CliDisplay(stdout=out, interactive=False).passage(
        payload,
        footer_stats=format_summary_stats(paragraphs=0, words=0, read="n/a"),
    )

    rendered = out.getvalue()
    assert "0 paragraphs · - words · - read" in rendered


def test_rich_section_summary_uses_simple_section_layout(tmp_path):
    db = _make_db(tmp_path)
    summary = _build_section_summary(db, 1)
    assert summary is not None
    out = StringIO()

    CliDisplay(stdout=out, interactive=True, color=False, width=100).section_summary(summary)

    rendered = out.getvalue()
    assert "Overview" in rendered
    assert "Contents" in rendered
    assert "CHAPTER 1" in rendered
    assert "Position" in rendered
    assert "Words" in rendered
    assert "2 sections · 3 paragraphs · 151 words · 756 chars · 1m read" in rendered
    assert "\nNext\n" in rendered
    assert "gutenbit search <query> --book 1" in rendered
    assert "gutenbit view 1 --position 0 --forward 20" in rendered
    assert "gutenbit view 1 --all" in rendered


def test_rich_section_summary_indents_long_nested_section_paths(tmp_path):
    db = _make_db(tmp_path)
    summary = _build_section_summary(db, 1)
    assert summary is not None
    summary = deepcopy(summary)
    summary["sections"][0]["section_number"] = 12
    summary["sections"][0]["section"] = (
        "BOOK ONE: 1805 / CHAPTER I. The Unexpectedly Long Chapter Title"
    )
    out = StringIO()

    CliDisplay(stdout=out, interactive=True, color=False, width=90).section_summary(summary)

    rendered = out.getvalue()
    assert "12" in rendered
    assert "CHAPTER I. The Unexpected" in rendered
    assert ".../" not in rendered
    assert "BOOK ONE: 1805 / CHAPTER I." not in rendered


def test_plain_section_summary_indents_nested_section_paths(tmp_path):
    db = _make_db(tmp_path)
    summary = _build_section_summary(db, 1)
    assert summary is not None
    summary = deepcopy(summary)
    summary["sections"][0]["section_number"] = 12
    summary["sections"][0]["section"] = "BOOK ONE: 1805 / CHAPTER XII"
    out = StringIO()

    CliDisplay(stdout=out, interactive=False).section_summary(summary)

    rendered = out.getvalue()
    assert "CHAPTER XII" in rendered
    assert ".../" not in rendered
    assert "BOOK ONE: 1805 / CHAPTER XII" not in rendered
    assert "#  Section" in rendered
    assert "\n 12" in rendered
