"""Tests for chunker: structural labelling, accumulation, and chapter detection."""

from gutenbit.chunker import chunk_text

# ------------------------------------------------------------------
# Basic splitting and accumulation
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
    assert [c.position for c in chunks] == list(range(len(chunks)))


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
# Accumulation behaviour
# ------------------------------------------------------------------


def test_accumulates_short_blocks():
    """Multiple short blocks are merged into one paragraph chunk."""
    text = "'Hello.'\n\n'Hi there.'\n\n'How are you today?'\n"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].kind == "paragraph"
    assert "'Hello.'" in chunks[0].content
    assert "'Hi there.'" in chunks[0].content
    assert "'How are you today?'" in chunks[0].content
    assert "\n\n" in chunks[0].content


def test_accumulation_emits_at_threshold():
    """Once accumulated text reaches minimum length, a chunk is emitted."""
    text = (
        "This is a real paragraph with enough content to be worth indexing.\n"
        "\n"
        "'Yes,' he said.\n"
        "\n"
        "Another real paragraph with sufficient length to be indexed properly.\n"
    )
    chunks = chunk_text(text)
    assert chunks[0].kind == "paragraph"
    assert chunks[0].content.startswith("This is a real")
    assert len(chunks) == 2
    assert "'Yes,' he said." in chunks[1].content
    assert "Another real paragraph" in chunks[1].content


def test_trailing_below_min_emitted_at_section_break():
    """Short text before a heading is emitted as its own chunk."""
    text = (
        "CHAPTER I\n"
        "\n"
        "A long paragraph with enough text to be emitted on its own merits.\n"
        "\n"
        "'My dear.'\n"
        "\n"
        "CHAPTER II\n"
        "\n"
        "Another long paragraph with enough text to be emitted on its own.\n"
    )
    chunks = chunk_text(text)
    kinds = [c.kind for c in chunks]
    assert kinds == ["heading", "paragraph", "paragraph", "heading", "paragraph"]
    assert chunks[2].content == "'My dear.'"
    assert chunks[2].div2 == "CHAPTER I"


def test_trailing_below_min_emitted_at_end():
    """Short text at end of document is emitted as its own chunk."""
    text = "A long paragraph with enough text to be emitted on its own merits.\n\nOk.\n"
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[1].content == "Ok."


# ------------------------------------------------------------------
# Headings and chapter tracking
# ------------------------------------------------------------------


def test_heading_kind():
    text = (
        "CHAPTER I\n\nIt was a bright cold day in April, and the clocks were striking thirteen.\n"
    )
    chunks = chunk_text(text)
    assert chunks[0].kind == "heading"
    assert chunks[0].content == "CHAPTER I"
    assert chunks[1].kind == "paragraph"


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
    assert paragraphs[0].div2 == "CHAPTER I"
    assert paragraphs[1].div2 == "CHAPTER II"


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
    assert paragraphs[0].div2 == "Chapter 1"
    assert paragraphs[1].div2 == "Chapter 1"


def test_no_chapter_gives_empty_string():
    text = "A paragraph without any preceding chapter heading, long enough to index.\n"
    chunks = chunk_text(text)
    assert chunks[0].div1 == "" and chunks[0].div2 == ""


def test_heading_variants():
    # Maps heading keyword → (div_field, expected_normalised_value)
    _cases = [
        ("BOOK III", "div1", "BOOK III"),
        ("Part 2", "div1", "Part 2"),
        ("ACT IV", "div1", "ACT IV"),
        ("SCENE 1", "div2", "SCENE 1"),
        ("Section 5", "div3", "Section 5"),
        ("STAVE I", "div2", "STAVE I"),
        ("CHAPTER. XVIII.", "div2", "CHAPTER. XVIII."),
    ]
    content = "Some content that is long enough to pass the minimum length filter."
    for heading, div_field, expected in _cases:
        text = f"{heading}\n\n{content}\n"
        chunks = chunk_text(text)
        heading_chunks = [c for c in chunks if c.kind == "heading"]
        assert len(heading_chunks) == 1, heading
        assert heading_chunks[0].content == heading
        paragraphs = [c for c in chunks if c.kind == "paragraph"]
        assert getattr(paragraphs[0], div_field) == expected, heading


def test_stave_heading_with_colon_title():
    """STAVE I:  Subtitle (A Christmas Carol style) is detected as a heading."""
    text = (
        "STAVE I:  MARLEY'S GHOST\n"
        "\n"
        "Marley was dead: to begin with. There is no doubt whatever about that. "
        "Old Marley was as dead as a door-nail.\n"
    )
    chunks = chunk_text(text)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "STAVE I:  MARLEY'S GHOST"  # raw block preserved
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert paragraphs[0].div2 == "STAVE I: MARLEY'S GHOST"  # normalised (double space collapsed)


def test_heading_with_trailing_bracket():
    """Chapter headings ending with ']' (split illustration tags) are detected
    and the ']' is stripped from the normalised chapter label."""
    text = (
        "Chapter I.]\n"
        "\n"
        "It is a truth universally acknowledged, that a single man in possession "
        "of a good fortune must be in want of a wife.\n"
    )
    chunks = chunk_text(text)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "Chapter I.]"  # raw block preserved
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert paragraphs[0].div2 == "Chapter I."  # ']' stripped from label


# ------------------------------------------------------------------
# Front matter and TOC detection
# ------------------------------------------------------------------


def test_front_matter_before_chapter():
    """Title page content before the first real chapter is front_matter."""
    text = (
        "HARD TIMES\n"
        "\n"
        "By CHARLES DICKENS\n"
        "\n"
        "LONDON: CHAPMAN & HALL\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "Now, what I want is, Facts. Teach these boys and girls nothing but "
        "Facts. Facts alone are wanted in life. Plant nothing else, and root "
        "out everything else.\n"
    )
    chunks = chunk_text(text)
    kinds = [c.kind for c in chunks]
    assert kinds == ["front_matter", "front_matter", "front_matter", "heading", "paragraph"]
    assert chunks[0].content == "HARD TIMES"
    assert chunks[3].content == "CHAPTER I"


def test_toc_detected():
    """CONTENTS section is labelled as toc, preceding text as front_matter."""
    text = (
        "HARD TIMES\n"
        "\n"
        "By CHARLES DICKENS\n"
        "\n"
        "CONTENTS\n"
        "\n"
        "CHAPTER I\n"
        "_The One Thing Needful_                3\n"
        "\n"
        "CHAPTER II\n"
        "_Murdering the Innocents_              4\n"
        "\n"
        "BOOK THE FIRST\n"
        "_SOWING_\n"
        "\n"
        "CHAPTER I\n"
        "THE ONE THING NEEDFUL\n"
        "\n"
        "Now, what I want is, Facts. Teach these boys and girls nothing but "
        "Facts. Facts alone are wanted in life. Plant nothing else, and root "
        "out everything else.\n"
    )
    chunks = chunk_text(text)
    kinds = [c.kind for c in chunks]
    # Title, author = front_matter; CONTENTS + entries = toc; then body
    assert kinds[:2] == ["front_matter", "front_matter"]
    toc_chunks = [c for c in chunks if c.kind == "toc"]
    assert len(toc_chunks) >= 1
    assert any("CONTENTS" in c.content for c in toc_chunks)
    # Body starts with heading
    body = [c for c in chunks if c.kind in ("heading", "paragraph")]
    assert body[0].kind == "heading"


def test_table_of_contents_variant():
    """'TABLE OF CONTENTS' also triggers toc labelling."""
    text = (
        "A TALE OF TWO CITIES\n"
        "\n"
        "TABLE OF CONTENTS\n"
        "\n"
        "Book I — Recalled to Life\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "It was the best of times, it was the worst of times, it was the age "
        "of wisdom, it was the age of foolishness, it was the season of light.\n"
    )
    chunks = chunk_text(text)
    assert chunks[0].kind == "front_matter"
    assert chunks[1].kind == "toc"
    assert chunks[1].content == "TABLE OF CONTENTS"


def test_no_front_matter_when_chapter_first():
    """Text starting with a chapter heading has no front_matter."""
    text = (
        "CHAPTER I\n\nIt was a bright cold day in April, and the clocks were striking thirteen.\n"
    )
    chunks = chunk_text(text)
    kinds = [c.kind for c in chunks]
    assert "front_matter" not in kinds
    assert "toc" not in kinds
    assert kinds == ["heading", "paragraph"]


def test_body_start_not_stolen_by_toc_heading_near_prose():
    """A long TOC ending with chapter entries near the first prose must not
    push body_start back into the TOC (War and Peace regression).

    'BOOK ONE: 1805' is now a valid heading (word-ordinal form), so the body
    starts there; the TOC CHAPTER VII/VIII/IX entries must NOT be pulled in.
    """
    # TOC ends: ...CHAPTER VIII, CHAPTER IX; then body starts with BOOK ONE.
    toc_chapters = "\n\n".join(f"CHAPTER {n}" for n in ["VII", "VIII", "IX"])
    body = (
        "BOOK ONE: 1805\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "Well, Prince, so Genoa and Lucca are now just family estates of the "
        "Buonapartes. But I warn you, if you don't tell me that this means war, "
        "if you still try to defend the infamies and horrors perpetrated by that "
        "Antichrist—I really believe he is Antichrist—I will have nothing more "
        "to do with you.\n"
    )
    text = f"CONTENTS\n\n{toc_chapters}\n\n{body}"
    chunks = chunk_text(text)
    headings = [c for c in chunks if c.kind == "heading"]
    # Body starts at BOOK ONE (div1), not at the TOC CHAPTER VII/VIII/IX entries
    assert headings[0].content == "BOOK ONE: 1805"
    assert headings[1].content == "CHAPTER I"
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert all(c.div1 == "BOOK ONE: 1805" for c in paragraphs)
    assert all(c.div2 == "CHAPTER I" for c in paragraphs)


def test_part_heading_before_chapter_included_in_body():
    """A PART heading immediately before CHAPTER I must be body, not front matter
    (Crime and Punishment regression: PART I was stranded as front_matter)."""
    text = (
        "Translator's preface with enough content to be a real prose block here.\n"
        "\n"
        "PART I\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "On an exceptionally hot evening early in July a young man came out of "
        "the garret in which he lodged in S. Place and walked slowly, as though "
        "in hesitation, towards K. bridge.\n"
    )
    chunks = chunk_text(text)
    headings = [c for c in chunks if c.kind == "heading"]
    # PART I must be in the body as a heading, not swallowed into front_matter
    assert headings[0].content == "PART I"
    assert headings[1].content == "CHAPTER I"
    # The preface is legitimately front_matter; PART I must not be
    front_matter_contents = [c.content for c in chunks if c.kind == "front_matter"]
    assert not any("PART I" in s for s in front_matter_contents)


# ------------------------------------------------------------------
# End matter detection
# ------------------------------------------------------------------


def test_footnotes_labelled_as_end_matter():
    """Content starting with FOOTNOTES is labelled as end_matter."""
    text = (
        "CHAPTER I\n"
        "\n"
        "The last paragraph of the story, long enough to pass the minimum threshold.\n"
        "\n"
        "FOOTNOTES\n"
        "\n"
        "{0} This is a footnote about the text.\n"
    )
    chunks = chunk_text(text)
    kinds = [c.kind for c in chunks]
    assert kinds == ["heading", "paragraph", "end_matter"]
    assert "FOOTNOTES" in chunks[2].content
    assert "footnote about" in chunks[2].content


def test_appendix_labelled_as_end_matter():
    text = (
        "CHAPTER I\n"
        "\n"
        "A paragraph with enough text to be emitted as a proper paragraph chunk.\n"
        "\n"
        "APPENDIX\n"
        "\n"
        "Additional material.\n"
    )
    chunks = chunk_text(text)
    end = [c for c in chunks if c.kind == "end_matter"]
    assert len(end) == 1
    assert "APPENDIX" in end[0].content


# ------------------------------------------------------------------
# Reconstruction
# ------------------------------------------------------------------


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
    assert "CHAPTER I" in reconstructed
    assert "'Yes.'" in reconstructed
    assert "* * *" in reconstructed


def test_all_text_preserved():
    """Nothing is discarded — all text appears in some chunk."""
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
    reconstructed = "\n\n".join(c.content for c in chunks)
    assert "Hi" in reconstructed
    assert "A full paragraph" in reconstructed
    assert "* * *" in reconstructed
    assert "Ok" in reconstructed
    kinds = {c.kind for c in chunks}
    assert kinds <= {"paragraph", "heading", "front_matter", "toc", "end_matter"}


# ------------------------------------------------------------------
# Dickens excerpts — realistic literary structure
# ------------------------------------------------------------------


# Pickwick Papers (PG 580) — chapter opening with quoted speech
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

    # heading, long para, long quoted speech, "* * *" accumulated with next speech
    assert kinds == ["heading", "paragraph", "paragraph", "paragraph"]
    assert chunks[0].content == "CHAPTER I"
    assert all(c.div2 == "CHAPTER I" for c in chunks)
    assert "Pickwick Club" in chunks[1].content
    assert "G.C.M.P.C." in chunks[2].content


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

    # heading, long paragraph, then two accumulated dialogue groups
    assert kinds == ["heading", "paragraph", "paragraph", "paragraph"]
    assert chunks[0].content == "CHAPTER I"

    assert "What's your name?" in chunks[2].content
    assert "The boy hesitated." in chunks[2].content
    assert "Oliver Twist." in chunks[2].content

    assert "Where do you come from?" in chunks[3].content
    assert "I have none, sir." in chunks[3].content

    assert all(c.div2 == "CHAPTER I" for c in chunks)


# Old Curiosity Shop (PG 700) — chapter with dinkus and trailing short text
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

    # heading, long para, "* * *" accumulated with dialogue, trailing narration
    assert kinds == ["heading", "paragraph", "paragraph", "paragraph"]
    assert chunks[0].content == "CHAPTER I"

    # Dialogue pair accumulated into one chunk
    assert "where do you come from" in chunks[2].content.lower()
    assert "a long way from here" in chunks[2].content

    # Trailing short text emitted as its own chunk
    assert chunks[3].content == "She said no more."


# Nicholas Nickleby (PG 967) — multi-chapter with trailing text at boundaries
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
    kinds = [c.kind for c in chunks]

    # Two chapters with trailing short text at boundaries
    assert kinds == [
        "heading",
        "paragraph",
        "paragraph",  # trailing "'My dear,'" flushed before CHAPTER II
        "heading",
        "paragraph",
        "paragraph",  # "---" accumulated with "He was a money-lender."
    ]

    assert chunks[2].div2 == "CHAPTER I"
    assert chunks[2].content == "'My dear,' said Mrs. Nickleby."

    assert chunks[5].div2 == "CHAPTER II"
    assert "He was a money-lender." in chunks[5].content


def test_dickens_all_positions_unique():
    """Across all excerpts, positions are unique and sequential."""
    excerpts = [_PICKWICK_EXCERPT, _OLIVER_EXCERPT, _CURIOSITY_SHOP_EXCERPT, _NICKLEBY_EXCERPT]
    for excerpt in excerpts:
        chunks = chunk_text(excerpt)
        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))


def test_dickens_full_reconstruction():
    """All text can be reconstructed from chunks across all excerpts."""
    excerpts = [_PICKWICK_EXCERPT, _OLIVER_EXCERPT, _CURIOSITY_SHOP_EXCERPT, _NICKLEBY_EXCERPT]
    for excerpt in excerpts:
        chunks = chunk_text(excerpt)
        reconstructed = "\n\n".join(c.content for c in chunks)
        for line in excerpt.splitlines():
            line = line.strip()
            if line:
                assert line in reconstructed, f"Missing: {line!r}"


# ------------------------------------------------------------------
# Full book structure — front matter + TOC + body + end matter
# ------------------------------------------------------------------


_FULL_BOOK_EXCERPT = """\
HARD TIMES

By CHARLES DICKENS

LONDON: CHAPMAN & HALL, LD.

1905

CONTENTS

CHAPTER I
_The One Thing Needful_                3

CHAPTER II
_Murdering the Innocents_              4

BOOK THE FIRST
_SOWING_

CHAPTER I
THE ONE THING NEEDFUL

Now, what I want is, Facts. Teach these boys and girls nothing but
Facts. Facts alone are wanted in life. Plant nothing else, and root
out everything else. You can only form the minds of reasoning animals
upon Facts: nothing else will ever be of any service to them.

'In this life, we want nothing but Facts, sir; nothing but Facts!'

CHAPTER II
MURDERING THE INNOCENTS

Thomas Gradgrind, sir. A man of realities. A man of facts and
calculations. A man who proceeds upon the principle that two and two
are four, and nothing over, and who is not to be talked into allowing
for anything over.

FOOTNOTES

{0} Reprinted Pieces was released as a separate eText by Project
Gutenberg, and is not included in this eText.
""".strip()


def test_full_book_structure():
    """A full book excerpt is split into front_matter, toc, heading, paragraph, end_matter."""
    chunks = chunk_text(_FULL_BOOK_EXCERPT)

    # Front matter: title, author, publisher, year
    fm = [c for c in chunks if c.kind == "front_matter"]
    assert len(fm) == 4
    assert fm[0].content == "HARD TIMES"

    # TOC: CONTENTS + entries
    toc = [c for c in chunks if c.kind == "toc"]
    assert len(toc) >= 1
    assert any("CONTENTS" in c.content for c in toc)

    # Body headings and paragraphs
    headings = [c for c in chunks if c.kind == "heading"]
    assert any("CHAPTER I" in c.content for c in headings)
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
    assert any("Facts" in c.content for c in paragraphs)

    # End matter
    em = [c for c in chunks if c.kind == "end_matter"]
    assert len(em) == 1
    assert "FOOTNOTES" in em[0].content

    # All text preserved
    reconstructed = "\n\n".join(c.content for c in chunks)
    for line in _FULL_BOOK_EXCERPT.splitlines():
        line = line.strip()
        if line:
            assert line in reconstructed, f"Missing: {line!r}"


# ------------------------------------------------------------------
# Hierarchical divisions (div1–div4)
# ------------------------------------------------------------------


def test_part_sets_div1_chapter_sets_div2():
    """PART I sets div1; CHAPTER I below it sets div2; both appear on paragraphs."""
    text = (
        "PART I\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "On an exceptionally hot evening early in July a young man came out of "
        "the garret in which he lodged in S. Place and walked slowly, as though "
        "in hesitation, towards K. bridge.\n"
    )
    chunks = chunk_text(text)
    part_heading = chunks[0]
    chapter_heading = chunks[1]
    para = chunks[2]

    assert part_heading.kind == "heading"
    assert part_heading.div1 == "PART I"
    assert part_heading.div2 == ""

    assert chapter_heading.kind == "heading"
    assert chapter_heading.div1 == "PART I"
    assert chapter_heading.div2 == "CHAPTER I"

    assert para.kind == "paragraph"
    assert para.div1 == "PART I"
    assert para.div2 == "CHAPTER I"


def test_new_part_clears_chapter():
    """When a new PART heading is seen, div2 resets to empty string."""
    text = (
        "PART I\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "First part prose, long enough to pass the minimum length threshold here.\n"
        "\n"
        "PART II\n"
        "\n"
        "CHAPTER I\n"
        "\n"
        "Second part prose, long enough to pass the minimum length threshold here.\n"
    )
    chunks = chunk_text(text)
    part2_heading = next(c for c in chunks if c.content == "PART II")
    assert part2_heading.div1 == "PART II"
    assert part2_heading.div2 == ""  # cleared when PART II started

    para2 = chunks[-1]
    assert para2.div1 == "PART II"
    assert para2.div2 == "CHAPTER I"
