# ADR-0012: Paragraph-first body blocks in structured chat exports

- Status: Accepted
- Date: 2026-08-16
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — the mode/heading/placeholder contract this change extends, not replaces
  - [`docs/adr/0009-verbatim-code-blocks-in-structured-exports.md`](0009-verbatim-code-blocks-in-structured-exports.md)
    — `ProcedureStep`'s marker-width continuation indent, the reason
    `ParagraphBlock` is deliberately not a `StepBlock`
  - [`docs/adr/0011-rich-body-blocks-in-structured-exports.md`](0011-rich-body-blocks-in-structured-exports.md)
    — **partially superseded**: decision 1's `BodyItem` shape and decision
    5's bare-string-is-a-bullet shorthand are replaced; decisions 2-4 and
    6-8, and the rest of decision 5, stand unchanged
  - `app/models.py`'s `ParagraphBlock`, `ParagraphContent`, `BodyBlock`,
    `_coerce_body_item`; `app/services/chat_export.py`
  - `tests/test_chat_export.py`, `tests/test_mcp_tools.py`,
    `tests/test_inbox.py`

## Context

Every body field's rich block sequence (ADR-0011) still treated a bare
string as an ordinary bullet: `app.models._coerce_body_item` turned it into
`{"type": "bullet", "content": ...}`, and
`app.services.chat_export.one_line` collapsed every line break in it to a
single space. A client sending ordinary explanatory prose to `context`,
`design`, `verification`, or any of the other eighteen body fields
therefore always got it back as a flat `- ` bullet list of one-line
fragments — never as the paragraph the conversation actually contained.

What the Gateway needs to own is export mode, section identity, section
order, and frontmatter (ADR-0005) — not whether the client's own prose
renders as a list. ADR-0011 generalised every body field into an ordered
sequence of blocks specifically so a table, a quote, or a piece of code
could sit exactly where the client placed it; this ADR extends the same
idea to prose itself, and changes what the *bare-string shorthand* means
so that the common case (`design: ["some explanation."]`) produces a
paragraph without a client having to opt in to one explicitly.

**Scope**: this ADR adds exactly one new block type, `ParagraphBlock`, to
`BodyBlock` (`ParagraphBlock | BulletBlock | CodeBlock | TableBlock |
QuoteBlock`), and changes the bare-string shorthand's target from
`BulletBlock` to `ParagraphBlock`. It also completes
`_escape_block_start`'s hazard set (decision 6) — a change forced by
`ParagraphBlock` reaching a class of injection that a flat, one-bullet-
per-item body field could never have exercised before. `BulletBlock`,
`CodeBlock`, `TableBlock`, and `QuoteBlock` themselves are unchanged;
`ProcedureStep`/`StepBlock` are unchanged; the mode/heading/frontmatter
contract (ADR-0005) is unchanged. Out of scope, continuing ADR-0011's own
list: a raw-Markdown creation API, client-supplied frontmatter, Vault note
migration, REST surface expansion, client-chosen headings, an arbitrary
nested Markdown AST, and math/footnote/horizontal-rule/heading blocks
inside a body field.

Three pre-existing gaps in `_escape_block_start`'s hazard coverage were
found while designing this ADR, verified against `markdown-it-py`:

- **Setext heading underlines.** `_BLOCK_HAZARD_RE`'s old
  `-{3,}$|={3,}$` alternatives only caught 3-or-more-character runs. A
  shorter run — `--`, `=`, `==`, `=  ` — directly under a text line
  renders a real `<h2>`/`<h1>`. This was already live in a **multi-line
  `QuoteBlock`**: `{"lines": ["a", "=="]}` renders `> a\n> ==`, which
  markdown-it-py parses as a heading *inside* the blockquote.
- **Thematic breaks.** The old alternatives only caught unspaced
  `-{3,}`/`_{3,}` runs (plus whatever the list-marker alternative
  happened to catch). `***`, `****`, `_ _ _`, and `-  -  -` all render a
  live `<hr>`. This was already live in **`BulletBlock`** (a bullet whose
  `content` is exactly `"***"` renders an `<hr>` inside its own list
  item) and in **`tldr`** (`tldr=["***"]` renders an `<hr>` in place of
  the summary paragraph, losing the text entirely).
- **GFM table delimiter rows.** Not covered at all. A header-shaped line
  followed by a delimiter-shaped line with the same column count (e.g.
  `"a | b"` then `"--- | ---"`) forms a real table.

A single-line `QuoteBlock` cannot exercise the setext or table gaps (both
need a preceding text line), and a `BulletBlock` run cannot exercise the
setext gap either (each bullet is its own list item, so no two plain-text
lines ever sit directly adjacent) — which is why ADR-0011's own hazard
tests, parametrised only over single-line quotes and single bullets, never
caught any of this. A multi-line `ParagraphBlock` is the first place two
adjacent plain-text lines can occur in a body field at all, so it would
inherit and newly reach every one of these gaps.

## Decision

1. **A bare string in a body field is now a shorthand for
   `{"type": "paragraph", "content": ...}`, not
   `{"type": "bullet", ...}`.** `app.models._coerce_body_item` is the
   single place this equivalence is expressed, the same role it already
   played for the bullet shorthand it replaces. This is a deliberate
   breaking change to what a bare string renders as: every export using
   only bare strings today changes from a bullet list to paragraph prose.
   No migration of already-written Vault notes is performed — only new
   exports are affected. A client that needs an explicit list item must
   send `{"type": "bullet", ...}`. One concrete failure mode this
   surfaces: a bare string immediately followed by a `{"type": "bullet",
   "depth": 1, ...}` item now fails `_check_bullet_depth`'s "the first
   bullet in a run must start at 0" rule, because the paragraph in front
   of it is no longer a bullet the depth-1 item can continue — an export
   relying on that adjacency must send an explicit `{"type": "bullet",
   "depth": 0, ...}` for the first item instead.

2. **`ParagraphContent` is bounded at 8,000 characters — `CodeContent`'s
   own cap, not `Line`'s 1,000.** `Line`'s cap exists because "every
   string field renders as exactly one Markdown line" (`app/models.py`'s
   comment above `_MAX_LINE_CHARS`); a paragraph is deliberately not one
   line, so that premise does not apply, and the closest sibling is the
   other multiline-capable type. This is not the binding constraint in
   practice: 8,000 × `_MAX_LIST_ITEMS` (30) = 240,000 characters in a
   single field already exceeds `_MAX_TOTAL_BLOCK_CHARS` (100,000, see
   decision 8), so the shared budget binds first. `ParagraphContent` has
   no `min_length`, unlike `CodeContent`: it is the bare-string
   shorthand's target, so it must accept everything the old
   `list[Line]` shape accepted, including `""` (dropped by the formatter,
   never rejected by the schema) — the same reasoning `TextBlock.content`
   and `BulletBlock.content` already carry.

3. **Paragraph prose is canonicalised, never flattened, by a new
   `_canonicalise_paragraph` — never `one_line`.**
   `app.services.chat_export._canonicalise_paragraph` normalises Unicode
   to NFC, maps every line-breaking character to `"\n"` (preserving line
   breaks instead of collapsing them to spaces), strips non-tab/newline
   control characters, `rstrip()`s each line (mandatory: trailing
   whitespace both violates this module's no-trailing-whitespace
   invariant and forges a Markdown hard break), and drops leading/
   trailing blank lines. Internal blank lines are **not** collapsed:
   this module's canonicalisation only removes differences that change
   nothing about the rendered structure, and a client's own choice of how
   many blank lines to leave between two paragraphs is not one of those —
   only "same input → same output" is required, not "equivalent input →
   identical output". Internal ASCII space runs are likewise preserved
   (unlike `one_line`): inside prose, "A  B" is the author's own spacing,
   and every structural hazard is handled per line by
   `_escape_block_start`, not by collapsing whitespace. A paragraph that
   canonicalises to nothing (empty, or entirely whitespace) is dropped,
   the same "`min_length=1` at the schema layer, still droppable"
   precedent `_normalise_code_block` (ADR-0009) already sets.

4. **Leading horizontal whitespace is removed on a paragraph's
   block-start lines, and preserved on its continuation lines.** A
   "block-start line" is the first line, or the first line after an
   internal blank line — computed once, on the already-blank-line-
   normalised list decision 3 produces. Verified against markdown-it-py
   during design: 4-or-more columns of indentation at a block-start
   position opens an indented code block (real structure injection; a
   tab counts the same as 4 columns there); 1-3 columns does not open a
   code block but still leaves a hazard live (`"   # h"` is still a
   rendered heading — `_escape_block_start`'s hazard regexes are `^`-
   anchored and cannot see past the indent). On a continuation line,
   *any* amount of leading ASCII space/tab is inert: an indented code
   block cannot interrupt a paragraph, and CommonMark discards a
   continuation line's leading whitespace when rendering it anyway, so
   preserving it costs nothing and keeps the source's shape. Only ASCII
   space and tab are ever stripped — non-ASCII horizontal whitespace
   (U+3000, U+00A0) is not CommonMark indentation (verified empirically)
   and is preserved everywhere, matching `one_line`'s own deliberate
   U+3000 preservation and a real Japanese paragraph-indent convention.
   No `str.expandtabs()` is applied anywhere: nothing here needs to count
   tab-stop columns once a block-start line's indent is removed outright
   and a continuation line's indent is carried through completely
   unchanged — expanding a tab would rewrite the client's own content, a
   line this ADR's paragraph handling otherwise never does.

5. **Every paragraph line is escaped by `_escape_block_start`,
   unconditionally, on the line's own content with its leading indent
   split off first and reattached after.** `app.services.chat_export.
   _render_paragraph` computes each line's leading-whitespace run,
   escapes only what follows it, then reattaches the (possibly empty)
   indent — the same reason decision 4 requires this split: the hazard
   regexes are `^`-anchored. Escaping runs on *every* line, not only
   lines a block-start/continuation classification says could actually
   open a hazard: a `\#` that was not strictly necessary still renders as
   a literal `#`, so the cost of over-escaping is nil, and correctness
   then does not depend on getting that classification right — the same
   fail-closed choice `_render_quote` already makes for a quote line.
   `_escape_inline` is never applied to a paragraph line, unlike a code
   caption: inline Markdown (`**bold**`, `` `code` ``, `[links](url)`)
   stays live, the same choice ADR-0011 decision 3 already makes for a
   table cell.

6. **`_escape_block_start`'s hazard set gains a setext-heading underline,
   a thematic break, and a GFM table delimiter row — closing the three
   gaps Context describes, for every caller of the function, not only
   `ParagraphBlock`.** Three new regexes: `_SETEXT_UNDERLINE_RE`
   (`^(?:=+|-+)[ \t]*$` — any run of only `=` or only `-`, not a mix, since
   `-=-` is neither a setext underline nor a thematic break),
   `_THEMATIC_BREAK_RE` (`^([-_*])(?:[ \t]*\1){2,}[ \t]*$` — 3+ of the same
   character, optionally space/tab-separated), and `_TABLE_DELIMITER_RE`
   (`^[|:\- \t]*-[|:\- \t]*$` — a conservative superset of a GFM delimiter
   row: any line made only of `|`, `:`, `-`, and horizontal whitespace,
   containing at least one `-`, rather than a faithful transcription of
   GFM's exact grammar, so it cannot drift from Obsidian's own
   non-markdown-it table parser; ordinary prose such as `"A | B"`,
   `"- item"`, `"5 - 3"`, or `"a-b"` does not have this shape and is
   unaffected). `_BLOCK_HAZARD_RE`'s old `-{3,}$|={3,}$|_{3,}$`
   alternatives are removed: each is a strict subset of one of the three
   new regexes, which also cover the shorter/spaced forms the old
   alternatives never did. Escaping only the table's *delimiter* row (not
   the header row above it) is sufficient to neutralise every variant —
   verified against markdown-it-py with the table rule enabled — and this
   is the only one of the three new hazards that needs a specific row
   identified rather than any line matching. Widening the shared function
   (rather than adding a `ParagraphBlock`-only check) closes the
   `QuoteBlock`/`BulletBlock`/`tldr` gaps Context found as a side effect,
   with **no change to any output a pre-existing test pins**: the values
   affected (`--`, `=`, `==`, `=  `, `***`, `****`, `_ _ _`, table
   delimiter shapes) were never covered by an existing assertion, and
   every one renders identically once escaped — the leading backslash
   never appears in the rendered text for any of these, since `=` and
   `-` are both CommonMark-escapable ASCII punctuation.

7. **A `ParagraphBlock` is a section-level sibling, exactly like a table,
   quote, or code block — never nested inside a bullet, and always
   separated from its neighbours by a blank line.**
   `app.services.chat_export._render_body_items`'s existing grouping
   logic (ADR-0011 decision 1/8) needs no new case for this: a paragraph
   ends the current bullet run the same way a table/quote/code block
   already does, and the existing `"\n\n".join(...)` already places a
   blank line around every section-level block. Two consecutive
   `ParagraphBlock`s are therefore always rendered as two Markdown
   paragraphs; a client wanting a single Markdown paragraph with an
   internal soft line break puts `"\n"` inside one block's `content`
   instead of sending two blocks. A `ParagraphBlock` that survives
   normalisation resets the bullet-depth run the same way a table/quote/
   code block already does (ADR-0011 decision 5) — the next bullet must
   restart at `depth == 0` — while one that normalises away to nothing
   does not, since the bullets around it are still adjacent in what
   actually renders.

8. **A paragraph's characters, including the newline separators between
   its lines, count toward `_MAX_TOTAL_BLOCK_CHARS` (100,000, unchanged).**
   A new `_paragraph_chars` sums every line's length **plus** one
   character per boundary between lines (`max(len(lines) - 1, 0)`) —
   deliberately not the same shape as `_quote_chars`. `QuoteBlock.lines`
   is a `list[Line]`, so its newlines are never part of the client's
   input; `ParagraphContent` is a single string, where a newline *is*
   client-supplied content. Counting only `sum(len(line) for line in
   lines)` would let every line break in a canonicalised paragraph vanish
   from the budget: a `ParagraphContent` of exactly 8,000 characters made
   almost entirely of newlines (`"a\n" * 4000`) canonicalises to 4,000
   one-character lines, summing to only 4,000 by line length alone — 12
   such blocks would total 48,000 by that count, comfortably under the
   100,000 budget, while actually carrying 96,000 characters of input.
   Counting the separators as well gives 7,999 per block (95,988 for 12,
   103,987 for 13), so the shared budget binds as intended. This bounds
   *input* payload, not the rendered Markdown's byte size or the final
   note's size: `_MAX_TOTAL_BLOCK_CHARS` was already documented as a
   payload bound rather than a rendered-size guarantee (`app/models.py`'s
   comment above `_MAX_TOTAL_BLOCK_CHARS`), and that positioning is
   unchanged here. `Settings.max_note_size_bytes` is **not** the backstop
   this budget protects: it is checked only by `search_notes`,
   `read_note`, and `append_inbox_note` (`app/application.py`) — the
   `create_inbox_note`/`create_chat_export_note` path writes whatever
   `render_chat_export` produces with no byte cap at all
   (`app/models.py`'s existing comment already records this as an
   accepted gap, not something this ADR closes). The actual outer limits
   on note creation remain the MCP transport's own pre-parse
   `MAX_REQUEST_BYTES` and the per-field/per-item schema caps; closing the
   "a created note has no size cap" gap is out of scope for this ADR.
   Extending `_MAX_TOTAL_BLOCK_CHARS` to also cover a plain bullet's
   `content` or a `Line` field is likewise out of scope — the bullet
   exemption ADR-0011 decision 7 already established stands, since a
   bullet's `content` remains bounded by `Line`'s own 1,000-character cap
   times the field's own item-count cap, an argument that does not extend
   to `ParagraphContent`'s 8,000.

9. **`ProcedureStep`/`StepBlock` are unchanged; `ParagraphBlock` is
   deliberately not one of `StepBlock`'s variants.** A step's
   continuation lines are indented to its numbered marker's own width
   (ADR-0009 decision 7) — a different problem with its own indent rules
   this ADR does not solve. Every ADR-0009 behaviour (verbatim/structure-
   preserving code, dynamic fence width, the marker-width continuation
   indent, the "a step must start with a text block" rule) renders
   byte-identically to before this ADR.

## Consequences

### Positive

- Ordinary explanatory prose sent to any body field now renders as a
  Markdown paragraph, with its line breaks and blank lines intact,
  instead of being flattened into one-line bullet fragments — the shape
  most `design`/`context`/`verification`-style content actually has.
- A client that genuinely wants a list still gets one, unchanged: an
  explicit `{"type": "bullet", ...}` renders exactly as ADR-0011 already
  specified, with its `depth`/`checked` behaviour untouched.
- Three pre-existing Markdown-injection gaps in `_escape_block_start`
  (setext underlines, thematic breaks, GFM table delimiter rows) are
  closed for every caller — `QuoteBlock`, `BulletBlock`, and `tldr`
  included — not only for the new `ParagraphBlock` path that exposed them.
- `app/mcp_server.py`'s argument shape (`title` + `export`) and its
  strict-arguments allowlist (`{"title", "export"}`) are unaffected:
  `ParagraphBlock` is one more `BodyBlock` variant, not a new top-level
  argument.
- `ProcedureStep`'s ADR-0009 behaviour — verbatim code, dynamic fences,
  marker-width continuation indent — renders byte-identically to before
  this ADR; none of its tests needed updating.

### Negative

- **This is a deliberate breaking change.** Every export whose body
  fields used only bare strings changes from a bullet list to paragraph
  prose. One concrete failure mode: a bare string immediately followed
  by an explicit `{"type": "bullet", "depth": 1, ...}` item — previously
  valid, continuing the implicit depth-0 bullet the bare string produced
  — now fails `_check_bullet_depth`'s "the first bullet in a run must
  start at 0" rule, since the paragraph in front of it is not a bullet
  the depth-1 item can continue. `tests/test_inbox.py`'s nested-bullets
  end-to-end test hit exactly this and needed its leading bare string
  replaced with an explicit `{"type": "bullet", "depth": 0, ...}`. No
  migration of already-written Vault notes is performed.
- A `ParagraphBlock` competes for the same 100,000-character
  `_MAX_TOTAL_BLOCK_CHARS` budget as code/table/quote content in the same
  field: an export that already fills the budget with code has no room
  left for prose in the same field, and vice versa.
- The bare-string shorthand's published JSON-schema `maxLength` widens
  from `Line`'s 1,000 to `ParagraphContent`'s 8,000 — a permissive,
  non-breaking schema change, but a wire-visible one.
- A paragraph line shaped like a hazard (e.g. a line that is literally
  `"---"` or `"| a | b |"`) now carries a leading backslash in the raw
  Markdown source, even though it renders identically to the
  unescaped text — a cosmetic-only cost of the fail-closed,
  escape-every-line choice (decision 5).

### Neutral

- `_MAX_TOTAL_BLOCK_CHARS` remains a payload bound, not a guarantee about
  the rendered note's byte size — `create_inbox_note`'s write path has no
  byte cap of its own (see decision 8); closing that pre-existing gap is
  out of scope here.
- Obsidian-specific inline constructs that survive because inline
  Markdown stays live — `%%comment%%`, `$$math$$`, `==highlight==`,
  `^block-id` — are unaffected by this ADR, the same trade-off ADR-0011
  decision 3 already accepted for a table cell and a quote line.
- Math blocks, footnotes, horizontal rules, heading blocks inside a body
  field, and image/embed syntax remain unaddressed (ADR-0011's own scope
  boundary, unchanged by this ADR) — extensible later through the same
  `BodyBlock` discriminated-union pattern, not preempted by it.

## Alternatives considered

1. **Keep the bare-string shorthand targeting `BulletBlock`, and add
   `ParagraphBlock` only as an explicit opt-in type.** Rejected — see
   decision 1; this would leave the common case (a client sending plain
   prose without knowing to opt in) exactly as broken as before this ADR,
   requiring every calling model to already know to reach for
   `{"type": "paragraph", ...}` rather than getting the right shape by
   default.
2. **A `multiline: true` flag on `BulletBlock` instead of a new block
   type.** Rejected — a bullet and a paragraph are different Markdown
   constructs with different rendering rules (a marker, nesting depth, an
   optional checkbox, versus none of those); overloading one model with a
   flag that changes its fundamental shape is the same "confusing, not
   merely redundant" objection ADR-0011 decision 5 already raised against
   reusing `TextBlock`'s discriminator for a bullet.
3. **Strip leading whitespace from every paragraph line, including
   continuation lines.** Rejected — see decision 4; CommonMark already
   discards a continuation line's leading whitespace when rendering, so
   stripping it changes nothing about the output while needlessly
   diverging from the client's own source shape, and it is not needed for
   safety (only a block-start line's indent can open a hazard).
4. **Escape every `|` in paragraph prose, instead of the table's
   delimiter row specifically.** Rejected — see decision 6; escaping a
   pipe character safely requires escaping a literal backslash first (the
   same ordering `_escape_table_cell` already needs), which would also
   turn a client's own `\*` inline escape into a literal backslash
   followed by live emphasis — a real inline-fidelity regression against
   this ADR's own "inline Markdown stays live" requirement (decision 5).
   Escaping the delimiter row instead touches nothing else.
5. **Transcribe GFM's exact table-delimiter-row grammar instead of a
   conservative superset.** Rejected — see decision 6; Obsidian's own
   table parser is not markdown-it, so a grammar transcribed against one
   parser's exact rules could drift from what Obsidian itself treats as a
   delimiter row. The conservative superset costs at most an unnecessary
   escape on an already-rare line shape.
6. **A `ParagraphBlock`-specific hazard check, separate from
   `_escape_block_start`.** Rejected — see decision 6; this would leave
   the `QuoteBlock`/`BulletBlock`/`tldr` gaps open and create two
   divergent definitions of "hazard" in the same module, the exact
   single-source-of-truth problem this codebase's other shared regexes
   (`_BLOCK_HAZARD_RE` itself) exist to avoid.
7. **Collapse runs of 2+ internal blank lines to one, mirroring an
   earlier draft of this ADR.** Rejected — see decision 3; this module's
   canonicalisation only removes differences that change nothing about
   the rendered structure, and how many blank lines a client leaves
   between two paragraphs is the client's own choice to preserve, not a
   difference to collapse. Only "same input → same output" determinism
   is required, not "equivalent input → identical canonical output".
8. **Expand tabs to a fixed column width inside paragraph content.**
   Rejected — see decision 4; nothing in the paragraph-rendering path
   needs to count tab-stop columns once a block-start line's indent is
   removed outright and a continuation line's indent is carried through
   unchanged, so expanding a tab would only rewrite the client's own
   content for no safety benefit — the same "never touch code content's
   internal whitespace" principle ADR-0009's `_canonicalise_code` already
   applies.
9. **Flatten paragraph prose with `one_line`, accepting the loss of
   structure.** Rejected — this is the exact problem (Context) this ADR
   exists to fix; flattening every line break to a space is what already
   made ADR-0011's rich-block generalisation insufficient for prose.

## References

- `app/models.py`'s `_MAX_PARAGRAPH_CHARS`, `ParagraphContent`,
  `ParagraphBlock`, `BodyBlock`, `_coerce_body_item`
- `app/services/chat_export.py`'s `_PARAGRAPH_LINE_BREAK_RE`,
  `_LEADING_INDENT_RE`, `_SETEXT_UNDERLINE_RE`, `_THEMATIC_BREAK_RE`,
  `_TABLE_DELIMITER_RE`, `_canonicalise_paragraph`, `_NormalisedParagraph`,
  `_render_paragraph`, `_paragraph_chars`, `_escape_block_start`,
  `_render_body_items`, `_normalise_body_items`
- `tests/test_chat_export.py` — the "Paragraph-first body blocks" section,
  and the extended `_QUOTE_HAZARD_LINES`/new two-line quote hazard test
  that catch the pre-existing setext gap
- `tests/test_mcp_tools.py` — `ParagraphBlock` schema assertions, the
  discriminator-mapping update, and the tool-description assertions
- `tests/test_inbox.py` — the nested-bullets end-to-end test's required
  payload change (Consequences → Negative)
- ADR-0005 (`docs/adr/0005-*.md`) — the mode/heading/frontmatter contract
  this change extends without altering
- ADR-0009 (`docs/adr/0009-*.md`) — `ProcedureStep`'s marker-width
  continuation indent, the reason `ParagraphBlock` is not a `StepBlock`
  (decision 9); `_canonicalise_code`'s "never touch internal whitespace"
  precedent (Alternatives 8)
- ADR-0011 (`docs/adr/0011-*.md`) — partially superseded (decisions 1 and
  5); decisions 2-4 and 6-8 stand unchanged and this ADR reuses their
  section-level-sibling/blank-line/budget machinery directly
