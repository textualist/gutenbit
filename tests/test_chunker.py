"""Tests for paragraph chunking and chapter detection."""

from gutenbit.chunker import chunk_text


def test_splits_on_blank_lines():
    text = (
        "First paragraph with enough text to pass the minimum length filter easily.\n"
        "\n"
        "Second paragraph also with enough text to pass the minimum length filter here.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0].content.startswith("First paragraph")
    assert chunks[1].content.startswith("Second paragraph")


def test_positions_are_sequential():
    text = "\n\n".join(f"Paragraph {i} has enough content to clear the filter." for i in range(5))
    chunks = chunk_text(text)
    assert [c.position for c in chunks] == [0, 1, 2, 3, 4]


def test_skips_short_blocks():
    text = (
        "This is a real paragraph with enough content to be worth indexing.\n"
        "\n"
        "Hi\n"
        "\n"
        "Another real paragraph with sufficient length to be indexed properly.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert all("real paragraph" in c.content for c in chunks)


def test_detects_chapter_heading():
    text = (
        "CHAPTER I\n"
        "\n"
        "It was a bright cold day in April, and the clocks were striking thirteen.\n"
        "\n"
        "CHAPTER II\n"
        "\n"
        "Outside, even through the shut window-pane, the world looked cold and bleak.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0].chapter == "CHAPTER I"
    assert chunks[1].chapter == "CHAPTER II"


def test_chapter_label_persists():
    text = (
        "Chapter 1\n"
        "\n"
        "First paragraph of chapter one, long enough to clear the minimum filter.\n"
        "\n"
        "Second paragraph of chapter one, also long enough to clear the filter.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0].chapter == "Chapter 1"
    assert chunks[1].chapter == "Chapter 1"


def test_no_chapter_gives_empty_string():
    text = "A paragraph without any preceding chapter heading, long enough to index.\n"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].chapter == ""


def test_multiple_blank_lines():
    text = (
        "First paragraph with enough text to be indexed by the chunker module.\n"
        "\n\n\n"
        "Second paragraph with enough text to also be indexed by the chunker.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2


def test_heading_variants():
    for heading in ["BOOK III", "Part 2", "ACT IV", "SCENE 1", "Section 5"]:
        content = "Some content that is long enough to pass the minimum length filter."
        text = f"{heading}\n\n{content}\n"
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0].chapter == heading


def test_empty_text():
    assert chunk_text("") == []


def test_whitespace_only():
    assert chunk_text("   \n\n   \n  ") == []
