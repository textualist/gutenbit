"""Tests for paragraph chunking, kind labelling, and chapter detection."""

from gutenbit.chunker import chunk_text

# ------------------------------------------------------------------
# Basic splitting
# ------------------------------------------------------------------


def test_splits_on_blank_lines():
    text = (
        "First paragraph with enough text to pass the minimum length filter easily.\n"
        "\n"
        "Second paragraph also with enough text to pass the minimum length filter here.\n"
    )
    chunks = chunk_text(text)
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert len(paragraphs) == 2
    assert paragraphs[0].content.startswith("First paragraph")
    assert paragraphs[1].content.startswith("Second paragraph")


def test_positions_are_sequential():
    text = "\n\n".join(f"Paragraph {i} has enough content to clear the filter." for i in range(5))
    chunks = chunk_text(text)
    assert [c.position for c in chunks] == [0, 1, 2, 3, 4]


def test_multiple_blank_lines():
    text = (
        "First paragraph with enough text to be indexed by the chunker module.\n"
        "\n\n\n"
        "Second paragraph with enough text to also be indexed by the chunker.\n"
    )
    chunks = chunk_text(text)
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert len(paragraphs) == 2


def test_empty_text():
    assert chunk_text("") == []


def test_whitespace_only():
    assert chunk_text("   \n\n   \n  ") == []


# ------------------------------------------------------------------
# Kind labelling
# ------------------------------------------------------------------


def test_short_blocks_preserved_as_short():
    text = (
        "This is a real paragraph with enough content to be worth indexing.\n"
        "\n"
        "'Yes,' he said.\n"
        "\n"
        "Another real paragraph with sufficient length to be indexed properly.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 3
    assert chunks[0].kind == "paragraph"
    assert chunks[1].kind == "short"
    assert chunks[1].content == "'Yes,' he said."
    assert chunks[2].kind == "paragraph"


def test_separator_detected():
    text = (
        "Some content that is long enough to pass the minimum length filter.\n"
        "\n"
        "* * *\n"
        "\n"
        "More content that is long enough to pass the minimum length filter.\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 3
    assert chunks[1].kind == "separator"
    assert chunks[1].content == "* * *"


def test_separator_variants():
    for sep in ["* * *", "***", "---", "===", "* * * * *", "----------"]:
        text = (
            "Content before the separator is long enough to be a paragraph.\n"
            "\n"
            f"{sep}\n"
            "\n"
            "Content after the separator is long enough to be a paragraph too.\n"
        )
        chunks = chunk_text(text)
        separators = [c for c in chunks if c.kind == "separator"]
        assert len(separators) == 1, f"Expected separator for {sep!r}"


def test_heading_kind():
    text = (
        "CHAPTER I\n\nIt was a bright cold day in April, and the clocks were striking thirteen.\n"
    )
    chunks = chunk_text(text)
    assert chunks[0].kind == "heading"
    assert chunks[0].content == "CHAPTER I"
    assert chunks[1].kind == "paragraph"


def test_all_blocks_preserved():
    """Nothing is discarded — all text blocks get a position."""
    text = (
        "CHAPTER I\n"
        "\n"
        "Hi\n"
        "\n"
        "A full paragraph with enough text to be classified as a real paragraph.\n"
        "\n"
        "* * *\n"
        "\n"
        "Ok\n"
    )
    chunks = chunk_text(text)
    assert len(chunks) == 5
    kinds = [c.kind for c in chunks]
    assert kinds == ["heading", "short", "paragraph", "separator", "short"]


def test_reconstruct_text_from_chunks():
    """Joining all chunk contents reproduces the original (modulo blank lines)."""
    text = (
        "CHAPTER I\n"
        "\n"
        "'Yes.'\n"
        "\n"
        "A paragraph with enough content to clear the minimum length threshold.\n"
        "\n"
        "* * *\n"
    )
    chunks = chunk_text(text)
    reconstructed = "\n\n".join(c.content for c in chunks)
    # Each original block should appear in the reconstruction.
    assert "CHAPTER I" in reconstructed
    assert "'Yes.'" in reconstructed
    assert "* * *" in reconstructed


# ------------------------------------------------------------------
# Chapter detection
# ------------------------------------------------------------------


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
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert len(paragraphs) == 2
    assert paragraphs[0].chapter == "CHAPTER I"
    assert paragraphs[1].chapter == "CHAPTER II"


def test_chapter_label_persists():
    text = (
        "Chapter 1\n"
        "\n"
        "First paragraph of chapter one, long enough to clear the minimum filter.\n"
        "\n"
        "Second paragraph of chapter one, also long enough to clear the filter.\n"
    )
    chunks = chunk_text(text)
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert len(paragraphs) == 2
    assert paragraphs[0].chapter == "Chapter 1"
    assert paragraphs[1].chapter == "Chapter 1"


def test_no_chapter_gives_empty_string():
    text = "A paragraph without any preceding chapter heading, long enough to index.\n"
    chunks = chunk_text(text)
    assert chunks[0].chapter == ""


def test_heading_variants():
    for heading in ["BOOK III", "Part 2", "ACT IV", "SCENE 1", "Section 5"]:
        content = "Some content that is long enough to pass the minimum length filter."
        text = f"{heading}\n\n{content}\n"
        chunks = chunk_text(text)
        heading_chunks = [c for c in chunks if c.kind == "heading"]
        assert len(heading_chunks) == 1
        assert heading_chunks[0].content == heading
        paragraphs = [c for c in chunks if c.kind == "paragraph"]
        assert paragraphs[0].chapter == heading


# ------------------------------------------------------------------
# Dickens excerpts — realistic literary structure
# ------------------------------------------------------------------


# Pickwick Papers (PG 580) — chapter opening with short dialogue
_PICKWICK_EXCERPT = """\
CHAPTER I

The first ray of light which illumines the gloom, and converts into a
dazzling brilliancy that obscurity in which the earlier history of the
public career of the immortal Pickwick would appear to be involved, is
derived from the perusal of the following entry in the Transactions of
the Pickwick Club.

'That this Association has heard read, with feelings of unmingled
satisfaction, and unqualified approval, the Paper communicated by
Samuel Pickwick, Esq., G.C.M.P.C.'

* * *

'Mr. Pickwick observed (says the Secretary) that fame was dear to
the heart of every man. Poetic fame was dear to the heart of his
friend Snodgrass; the fame of conquest was equally dear to his
friend Tupman; and the desire of earning fame in the service of
humanity was paramount in his own breast.'
""".strip()


def test_pickwick_excerpt():
    chunks = chunk_text(_PICKWICK_EXCERPT)
    kinds = [c.kind for c in chunks]

    assert kinds[0] == "heading"
    assert chunks[0].content == "CHAPTER I"
    assert "paragraph" in kinds
    assert "separator" in kinds
    # The quoted speech blocks are long enough to be paragraphs here
    assert all(c.chapter == "CHAPTER I" for c in chunks)


# Oliver Twist (PG 730) — chapter with short dialogue lines
_OLIVER_EXCERPT = """\
CHAPTER I

Among other public buildings in a certain town, which for many reasons
it will be prudent to refrain from mentioning, and to which I will
assign no fictitious name, there is one anciently common to most towns,
great or small: to wit, a workhouse.

'What's your name?'

The boy hesitated.

'Oliver Twist.'

'Where do you come from? Who are your parents?'

'I have none, sir.'
""".strip()


def test_oliver_excerpt():
    chunks = chunk_text(_OLIVER_EXCERPT)
    kinds = [c.kind for c in chunks]

    # Heading, paragraph, then a mix of short dialogue and short narration
    assert kinds[0] == "heading"
    assert kinds[1] == "paragraph"
    # Short dialogue lines are preserved
    short_chunks = [c for c in chunks if c.kind == "short"]
    assert any("Oliver Twist" in c.content for c in short_chunks)
    assert any("What's your name" in c.content for c in short_chunks)
    # Everything is under CHAPTER I
    assert all(c.chapter == "CHAPTER I" for c in chunks)


# Old Curiosity Shop (PG 700) — chapter with dinkus and brief paragraphs
_CURIOSITY_SHOP_EXCERPT = """\
CHAPTER I

Night is generally my time for walking. In the summer I often leave
home early in the morning, and roam about fields and lanes all day, or
even escape for days or weeks together; but, saving in the country, I
seldom go out until after dark, though, Heaven be thanked, I go abroad
in all seasons.

* * *

'And where do you come from?' I asked.

'Oh, a long way from here,' she replied.

She said no more.
""".strip()


def test_curiosity_shop_excerpt():
    chunks = chunk_text(_CURIOSITY_SHOP_EXCERPT)
    kinds = [c.kind for c in chunks]

    assert kinds[0] == "heading"
    assert kinds[1] == "paragraph"
    assert "separator" in kinds
    # Short dialogue is preserved
    short_chunks = [c for c in chunks if c.kind == "short"]
    assert any("where do you come from" in c.content.lower() for c in short_chunks)
    assert any("She said no more" in c.content for c in short_chunks)
    # Total block count — nothing lost
    assert len(chunks) == 6  # heading, paragraph, separator, 2 dialogue, narration


# Nicholas Nickleby (PG 967) — multi-chapter with short blocks
_NICKLEBY_EXCERPT = """\
CHAPTER I

There once lived, in a sequestered part of the county of Devonshire,
one Mr. Godfrey Nickleby: a worthy gentleman, who, taking it into his
head rather late in life that he must get married, and not being young
enough or rich enough to aspire to the hand of a lady of fortune,
had wedded an old flame out of mere attachment.

'My dear,' said Mrs. Nickleby.

CHAPTER II

Mr. Ralph Nickleby was not, strictly speaking, what you would call a
merchant, neither was he a banker, nor an attorney, nor a special
pleader, nor a notary. He was certainly not a tradesman, and still
less could he lay any claim to the title of a professional gentleman;
for it would have been impossible to mention any recognised profession
to which he belonged.

---

He was a money-lender.
""".strip()


def test_nickleby_excerpt():
    chunks = chunk_text(_NICKLEBY_EXCERPT)

    # Check chapters advance
    ch1_chunks = [c for c in chunks if c.chapter == "CHAPTER I"]
    ch2_chunks = [c for c in chunks if c.chapter == "CHAPTER II"]
    assert len(ch1_chunks) >= 2  # heading + paragraph + dialogue
    assert len(ch2_chunks) >= 2  # heading + paragraph + separator + short

    # The short dialogue is preserved
    assert any(c.kind == "short" and "My dear" in c.content for c in chunks)

    # The separator is detected
    assert any(c.kind == "separator" and "---" in c.content for c in chunks)

    # Short final line is preserved
    assert any(c.kind == "short" and "money-lender" in c.content for c in chunks)


def test_dickens_all_positions_unique():
    """Across all excerpts, positions are unique and sequential."""
    excerpts = [_PICKWICK_EXCERPT, _OLIVER_EXCERPT, _CURIOSITY_SHOP_EXCERPT, _NICKLEBY_EXCERPT]
    for excerpt in excerpts:
        chunks = chunk_text(excerpt)
        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))
