"""Tests for Project Gutenberg header/footer stripping."""

from gutenbit.download import strip_headers


def test_standard_gutenberg_format():
    text = (
        "The Project Gutenberg eBook of Test Book\n"
        "\n"
        "Produced by Someone\n"
        "\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***\n"
        "\n"
        "Chapter 1\n"
        "\n"
        "It was a dark and stormy night.\n"
        "\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***\n"
        "\n"
        "End of Project Gutenberg's Test Book\n"
    )
    result = strip_headers(text)
    assert result == "Chapter 1\n\nIt was a dark and stormy night."


def test_preserves_all_internal_content():
    text = (
        "*** START OF THE PROJECT GUTENBERG EBOOK FOO ***\n"
        "\n"
        "Line one.\n"
        "Line two.\n"
        "Line three.\n"
        "\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK FOO ***\n"
    )
    result = strip_headers(text)
    assert result == "Line one.\nLine two.\nLine three."


def test_no_markers_returns_original():
    text = "Just some plain text.\nNothing special here."
    assert strip_headers(text) == text


def test_case_insensitive_markers():
    text = (
        "*** Start Of The Project Gutenberg Ebook Test ***\n"
        "\n"
        "Content here.\n"
        "\n"
        "*** End Of The Project Gutenberg Ebook Test ***\n"
    )
    result = strip_headers(text)
    assert result == "Content here."


def test_only_start_marker():
    text = (
        "Some preamble.\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK TITLE ***\n"
        "\n"
        "The actual book content.\n"
        "More content.\n"
    )
    result = strip_headers(text)
    assert result == "The actual book content.\nMore content."


def test_strips_leading_trailing_whitespace():
    text = (
        "*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
        "\n"
        "\n"
        "  Content with spaces.  \n"
        "\n"
        "\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK X ***\n"
    )
    result = strip_headers(text)
    assert result == "Content with spaces."


def test_false_positive_marker_in_content_not_treated_as_delimiter():
    """A line mentioning Project Gutenberg with *** that isn't the exact delimiter
    must NOT trigger header/footer stripping of surrounding content."""
    text = (
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***\n"
        "\n"
        "Real chapter content here.\n"
        "\n"
        "*** See also Project Gutenberg's free eBooks ***\n"
        "\n"
        "More real content that must not be lost.\n"
        "\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***\n"
    )
    result = strip_headers(text)
    assert "Real chapter content here." in result
    assert "*** See also Project Gutenberg's free eBooks ***" in result
    assert "More real content that must not be lost." in result


def test_end_marker_before_start_marker_falls_back():
    """If there is no START marker, the full text is returned unchanged."""
    text = (
        "Some preamble.\n\n*** END OF THE PROJECT GUTENBERG EBOOK ORPHAN ***\n\nTrailing junk.\n"
    )
    result = strip_headers(text)
    assert result == text.strip()


def test_partial_marker_not_matched():
    """Lines that partially resemble delimiters must not match."""
    text = "*** PROJECT GUTENBERG ***\n\nSome book content.\n"
    # No valid START marker, so full text is returned
    result = strip_headers(text)
    assert result == text.strip()
