# ADR-0011: Rich body blocks (tables, quotes/callouts, nested/task-list bullets) in structured chat exports

- Status: Accepted — **partially superseded by
  [`docs/adr/0012-paragraph-first-body-blocks-in-structured-exports.md`](0012-paragraph-first-body-blocks-in-structured-exports.md)**.
  Decision 1's `BodyItem` shape and decision 5's bare-string-is-a-bullet
  shorthand are superseded (a bare string is now a paragraph, not a
  bullet); decisions 2-4 and 6-8, and the rest of decision 5 (`depth`,
  `checked`, the depth-jump rule, `source_index`), stand unchanged.
- Date: 2026-08-10
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — the mode/heading/placeholder contract this change extends, not replaces
  - [`docs/adr/0009-verbatim-code-blocks-in-structured-exports.md`](0009-verbatim-code-blocks-in-structured-exports.md)
    — introduced the first rich block (`CodeBlock`) inside `procedure.steps`
    and the top-level `code_blocks` supplementary section; this ADR
    generalises the idea to every body field and adds two more block types
  - `app/models.py`'s `BulletBlock`/`TableBlock`/`QuoteBlock`/`BodyBlock`/
    `BodyItem`/`StepBlock`, `app/services/chat_export.py`
  - `tests/test_chat_export.py`, `tests/test_mcp_tools.py`, `tests/test_inbox.py`

## Context

Every plain `list[Line]` body field (`decisions`, `design`,
`topics[].points`, and eighteen others) renders as flat `- ` bullets after
`app.services.chat_export.one_line`, which collapses every line break to a
space, and `_escape_block_start`, which escapes a leading block-forgery
hazard. Between the two, none of these fields can carry a table, a
blockquote/Obsidian callout, a GFM task-list checkbox, or a nested bullet —
a client sending any of these gets it flattened into a single line of
literal `|`/`>`/`[ ]` characters.

`tasks/todo.md`'s ADR-0009 entry recorded this generalisation as
out-of-scope future work at the time: "他モードの本文フィールドへの rich
block化の拡張 — 今回は procedure.steps のみ". This ADR is that follow-up.

**Scope**: this ADR makes exactly four block types available as a body
field's rich block sequence — `BulletBlock` (with nesting `depth` and an
optional task-list `checked`, new), `CodeBlock` (pre-existing, ADR-0009,
newly usable in a body field), `TableBlock` (new), and `QuoteBlock` (new).
Blockquotes/callouts, GFM tables, task lists, and nested bullets are the
constructs addressed; math blocks (`$$…$$`), footnotes, horizontal rules,
heading blocks, and image/embed syntax are deliberately **not** addressed
here and remain future work, extensible through the same
`BodyBlock`/`StepBlock` discriminated-union pattern this ADR establishes.

ADR-0009's justification for leaving code content unescaped — "a fence is
already structurally closed by its own opening/closing markers, so the
block-forgery hazards `_escape_block_start` exists to stop cannot occur
inside one" — does **not** transfer to a table or a blockquote: neither is
self-closing. A missing GFM delimiter row degrades a table silently into an
ordinary paragraph; a row with the wrong cell count is truncated or padded
by the renderer rather than rejected. Verified against `markdown-it-py`
during design: a table immediately following a bullet list, with no blank
line between them, is swallowed whole into the list item's lazy
continuation and disappears from the rendered output entirely.

## Decision

1. **Every body field becomes a flat, ordered sequence of bullets and
   section-level blocks — never nested inside each other, and never routed
   through a `## 表`/`## 引用`-style supplementary section.**
   `app.models.BodyItem` (a bare string, or a discriminated
   `BulletBlock | CodeBlock | TableBlock | QuoteBlock`) replaces `Line` as
   the item type for twenty existing `list[Line]` fields plus
   `TopicSection.points`. **Superseded by
   [ADR-0012](0012-paragraph-first-body-blocks-in-structured-exports.md)
   decision 1: `BodyBlock` gains a fifth variant, `ParagraphBlock`, and the
   union is `ParagraphBlock | BulletBlock | CodeBlock | TableBlock |
   QuoteBlock`.** `CodeBlock` is reused from ADR-0009 unchanged —
   the same model `ProcedureStep.blocks` and the top-level `code_blocks`
   already use — not a new type introduced here.
   `app.services.chat_export._render_body_items` is the grouping renderer:
   consecutive bullets become one Markdown bullet list; a code block,
   table, or quote ends that list and becomes a section-level sibling
   block, rendered directly under the field's own heading, in the position
   the client actually placed it. A separate `## 表`/`## 引用` appendix —
   the ADR-0009 pattern for `code_blocks` — was considered and rejected for
   the new block types on the same grounds ADR-0009 already rejected it for
   code: ADR-0009's own Context calls collecting a procedure's code into
   one section "the exact design this ADR exists to avoid", because it
   discards the order and surrounding context that make the content
   meaningful; the same objection applies to a table that illustrates the
   point immediately before it, and is why a body field's own `CodeBlock`
   stays in that field rather than moving to `code_blocks` too.
   Nesting a table/quote *inside* a bullet (an indented continuation, the
   way `CodeBlock` nests inside a `ProcedureStep`) was also considered and
   rejected: a table has no continuation-indent requirement of its own to
   satisfy (unlike a fence, which must stay inside the step it belongs to),
   so nesting it would add CommonMark/Obsidian-renderer-divergence risk
   (ADR-0009 decisions 7-8's whole concern) for no benefit — a section-level
   sibling block needs no indent tracking and cannot be pushed outside its
   parent list by a marker-width mismatch.

2. **A table is structured input (`headers`/`rows`/`alignments`), never a
   raw Markdown string.** Unlike `CodeBlock.content` (ADR-0009 decision 3),
   a table is not self-closing, so accepting client-written GFM syntax as
   text would let a missing delimiter row or a wrong cell count degrade
   silently instead of being rejected. `app.services.chat_export`
   generates the table's Markdown itself from the structured input and
   raises a `ValidationError` (Gateway-vocabulary message, `field[index]`
   only, matching ADR-0005 decision 10) for any structural mismatch — an
   empty header after normalisation, an `alignments` length that does not
   match `headers`, or a row whose cell count does not match `headers` —
   rather than rendering a corrupt table. A table never silently degrades
   or drops a cell; it either renders exactly as specified or the whole
   export is rejected. `headers` requires at least one non-empty column
   name (an unnamed column defeats a table's purpose); a data cell may be
   empty (a legitimate "not applicable" value in GFM). A table with zero
   rows (header plus delimiter only) is accepted.

3. **A table cell keeps its inline Markdown live; it is escaped only for
   the one character that would otherwise be misread as a column
   separator.** `app.services.chat_export._escape_table_cell` replaces a
   backslash with two backslashes, then a pipe with an escaped pipe — in
   that order, so a cell already containing a literal `\|` is not read as
   "an escaped backslash followed by a live separator" once the pipe
   escaping is added; reversing the order lets the two escapes interact.
   Unlike a code block's `label` (ADR-0009 decision 6, escaped with
   `_escape_inline`'s full hazard set because a caption's contract is
   "show this text as-is"), a cell is ordinary body text — the same
   treatment a bullet's `content` already gets — so `**bold**` and
   `` `code` `` inside a cell render live, not as literal asterisks and
   backticks.

4. **A blockquote/Obsidian callout is `QuoteBlock`: an optional
   `> [!callout] title` header line, followed by `> line` for each of
   `lines`.** `callout` is a validated pattern
   (`^[A-Za-z][A-Za-z0-9-]{0,31}$`), not an enumerated vocabulary — the same
   choice `CodeBlock.language` already makes (ADR-0009 decision 5) — since
   Obsidian accepts both its own built-in callout types and arbitrary
   custom ones, and the Gateway has no reason to maintain a list. `title` is
   rejected outright when `callout` is absent: a plain blockquote has no
   header line to put a title on. Each `line` gets `_escape_block_start` —
   verified against markdown-it-py during design, a bare `> # forged`
   really does render a heading inside the blockquote, the same
   block-forgery hazard `_escape_block_start` already exists to stop
   everywhere else. The header's own `title` gets neither
   `_escape_block_start` (it always follows the `[!callout] ` prefix and
   can never occupy the true start of its line, so it cannot itself open a
   nested block) nor `_escape_inline` (unlike a code caption, a callout
   title is ordinary prose and should render with live formatting, the
   same choice decision 3 makes for a table cell). A quote whose every line
   normalises to nothing is silently dropped — the same
   "`min_length=1` at the schema layer, still droppable once
   whitespace-only" precedent `_normalise_code_block` (ADR-0009) already
   sets, since an empty blockquote carries no meaning and there is no
   backward-compatibility reason to keep one. A table is never dropped this
   way (decision 2): the two constructs' "empty" cases are not symmetric,
   the same asymmetry ADR-0009 decision 12 already draws between
   `CodeBlock.content` and `TextBlock.content`.

5. **A bullet is `BulletBlock` (discriminator `"bullet"`, not `"text"`),
   with a nesting `depth` and an optional task-list `checked`.** The
   discriminator is `"bullet"` rather than reusing `ProcedureStep`'s own
   `"text"` (`TextBlock`) on purpose: the two models represent different
   things — a list item versus a step's continuation paragraph — and giving
   them the same wire value for different meanings would be confusing, not
   merely redundant; `TextBlock` gains neither `depth` nor `checked`, and
   `extra="forbid"` rejects either at the schema layer with no runtime
   check required. **Superseded by
   [ADR-0012](0012-paragraph-first-body-blocks-in-structured-exports.md)
   decision 1: a bare string is now a shorthand for `{"type": "paragraph",
   "content": ...}` instead — the sentence below describing the old
   `"bullet"` shorthand is historical.** ~~A bare string is still a
   backward-compatible shorthand for `{"type": "bullet", "content": ...,
   "depth": 0}` via `app.models._coerce_body_item`, the direct analogue of
   `_coerce_step` (ADR-0009 decision 13) — every export that used a plain
   string list before this change renders byte-identical Markdown.~~
   `checked` set to
   `false`/`true` renders `- [ ] `/`- [x] ` (a GFM task-list item) in place
   of the plain `- ` marker; omitted, it renders exactly as before.
   `depth` is rendered as two spaces of indent per level (verified against
   markdown-it-py as the indent CommonMark requires under an unordered
   parent marker), and its *sequence* is validated on normalised data,
   never clamped: the first bullet in a run must be `depth == 0`, and every
   later bullet may be at most one level deeper than the bullet immediately
   before it. A table/quote that survives normalisation ends the run — the
   next bullet must restart at `depth == 0` — but a table/quote that itself
   normalises away to nothing does not, since the bullets around it are
   still adjacent in what actually gets rendered. Clamping an
   out-of-sequence depth to the nearest valid value was considered and
   rejected: the requested string content would survive, but the
   *structure* the client asked for would be silently rewritten, which
   contradicts the same fail-closed rule this ADR already applies to a
   malformed table (decision 2) — a structural problem the client can fix
   is safer than one the Gateway quietly "corrects" into something else.
   Because the check runs after bullets that normalise to empty content
   have already been dropped, `app.services.chat_export._NormalisedBullet`
   carries a `source_index` (the client's own input position) so the error
   this check raises names the item the client actually sent, not whatever
   now occupies that position in the normalised sequence.

6. **`ProcedureStep.blocks` gains the same `TableBlock`/`QuoteBlock` options
   `TextBlock`/`CodeBlock` already have (`StepBlock`), but never
   `BulletBlock`.** A table or quote inside a step is rendered indented to
   the step's own continuation width, reusing the marker-width indent
   ADR-0009 decision 7 already computes (`_render_indented_table`/
   `_render_indented_quote`, the direct analogues of
   `_render_fenced_code`'s own indent handling). ADR-0009 decision 8's rule
   — a step must start with a `TextBlock` — already generalises to both new
   block types without modification, since it checks
   `isinstance(blocks[0], _NormalisedTextBlock)` rather than enumerating
   what a step must *not* start with. `BulletBlock` is deliberately not a
   valid step block: a step's own numbered-list structure already has its
   own indent-width rules (decision 7), and a step's continuation text is
   `TextBlock`, not a bullet — mixing the two concepts inside one step
   would blur what depth `0` means in a context that is not a body field's
   flat bullet run.

7. **`_MAX_TOTAL_CODE_CHARS` becomes `_MAX_TOTAL_BLOCK_CHARS`, widened to
   count every client-supplied string inside every rich block, not only
   code content.** The budget now sums: a `CodeBlock`'s `content` **and**
   `label` (ADR-0009's own budget test counted only `content`; a code
   block's caption is exactly as much client-supplied text as its content,
   and excluding it was an oversight this ADR corrects, not a deliberate
   choice being revisited); a `TableBlock`'s `label`, every `headers` entry,
   and every cell in `rows`; a `QuoteBlock`'s `title` and every `lines`
   entry — wherever any of these appears: a body field, `topics[].points`,
   a step, or the top-level `code_blocks`. A plain bullet's `content` is
   not counted, matching ADR-0009's own scope: it was never budgeted before
   this change either, being already bounded by `Line`'s own per-item cap
   and the field's own item-count cap. The value stays `100_000`, unchanged
   from ADR-0009 — widening what counts toward an existing ceiling, not
   changing the ceiling itself. This bounds *input* payload, not the
   rendered Markdown's byte size: escaping (a table cell's `\|`, a
   dynamically-widened code fence) can only grow the rendered text further.
   The final backstop is unchanged from ADR-0009: `Settings.max_note_size_bytes`
   (default 1 MiB), the limit `note_service.read_note` truncates against
   rather than the pre-parse `max_request_bytes` backstop that rejects a
   request outright.

8. **A section-level block (code, table, or quote) is always separated
   from adjacent content by a blank line, on both sides, including from
   another section-level block immediately following it.** Verified
   against markdown-it-py during design: a table with no blank line before
   it, immediately after a bullet list, is swallowed into the preceding
   list item's lazy continuation and disappears from the rendered output
   entirely — data loss, not a cosmetic difference. A fenced code block is
   CommonMark core and always interrupts a paragraph on its own even
   without one, but the blank line is added around it too, for the same
   one-shape-fits-every-section-level-block simplicity in
   `_render_body_items` — a GFM table's or an Obsidian callout's own
   paragraph-interrupting behaviour is a renderer-specific extension, not
   something to rely on without the blank line regardless. A caption
   directly above a table, a caption above a code fence, or the callout
   header line directly above quote lines needs no blank line of its own —
   verified as producing an identical token stream either way, matching the
   existing code-caption precedent (ADR-0009 decision 6) — so
   `_render_table`/`_render_quote`/`_render_top_level_code_block` place a
   caption/header immediately above their own content, and
   `_render_body_items` is what inserts the blank line between one
   rendered block (a bullet run, a code block, a table, or a quote) and
   the next.

## Consequences

### Positive

- A comparison table, a warning callout, a checklist, a hierarchical
  breakdown, or a short code snippet that illustrates a point in a
  `design`/`decisions`/-style field — not only inside `procedure.steps` —
  can now be saved exactly where it appeared, inside the body field it
  belongs to, instead of being flattened into a line of literal
  `|`/`>`/`[ ]` characters or omitted entirely.
- No existing export changes its rendered Markdown: every pre-existing test
  in `tests/test_chat_export.py`, `tests/test_mcp_tools.py`, and
  `tests/test_inbox.py` passes unchanged, including the byte-exact golden
  outputs pinned before this ADR.
- The `BodyBlock`/`StepBlock` discriminated-union pattern this ADR
  establishes extends to a future block type (math, footnotes, images) at
  zero additional schema cost for the fields that do not use it — a field
  not sent stays exactly as small as it always was.
- `app/mcp_server.py`, `app/application.py`, and
  `app/services/inbox_service.py` needed no changes: `create_inbox_note`'s
  argument shape (`title` + `export`) and its strict-arguments allowlist
  (`{"title", "export"}`) are unaffected by what `export`'s own fields can
  now contain.

### Negative

- `app.models`'s generated MCP schema grows by three new `$defs`
  (`BulletBlock`, `TableBlock`, `QuoteBlock` — all introduced by this ADR,
  not ADR-0009), adding further to the `tools/list` token cost ADR-0005's
  own "Negative" section first flagged and ADR-0009 already added to.
  `CodeBlock` (ADR-0009) is reused, not duplicated, now that `BodyBlock`
  references it too.
- Twenty existing field descriptions do not mention tables, quotes, or code
  blocks by name (only the field's own mode-specific guidance): a calling
  model discovers rich-block support for a given field from the shared
  `BodyBlock`/`BulletBlock`/`CodeBlock`/`TableBlock`/`QuoteBlock` schema
  `$defs`
  rather than from each field's own prose, the same trade-off ADR-0005's
  "Negative" section already accepted for `steps`' code-block support.
- `_MAX_TOTAL_BLOCK_CHARS`'s widened scope is a real (if narrow) behaviour
  change for a caller close to ADR-0009's old code-only budget: an export
  that fit under the code-only limit before this change but that also
  carries a large table/quote/code-label total can now be rejected where
  it previously was not. No caller was observed depending on that gap.

### Neutral

- Math blocks, footnotes, horizontal rules, heading blocks inside a body
  field, and image/embed syntax remain unaddressed (see Context's Scope
  paragraph) — extensible later through the same pattern, not preempted by
  it.
- `_MAX_BULLET_DEPTH = 3` is a realistic ceiling on a note's own nested
  lists, not a hard Markdown constraint; the depth-*jump* rule (decision 5)
  is the one that actually protects rendered structure, and it is
  independent of this per-item cap.

## Alternatives considered

1. **A `## 表`/`## 引用` supplementary section per new block type,
   mirroring ADR-0009's `code_blocks`/`## コード`.** Rejected — see
   decision 1; ADR-0009's own Context calls this exact pattern "the design
   this ADR exists to avoid" for code, and the same order/context loss
   applies to a table or quote.
2. **Widening the existing top-level `code_blocks` into a mixed appendix
   accepting every block type.** Rejected for the same reason as
   alternative 1 — moving a table out of the field it illustrates loses
   the context that makes it meaningful, regardless of what the appendix
   is named.
3. **Nesting a table/quote inside a bullet, as `CodeBlock` nests inside a
   `ProcedureStep`.** Rejected — see decision 1; a table/quote has no
   continuation-indent requirement of its own, so nesting would add
   CommonMark/Obsidian-divergence risk (ADR-0009 decisions 7-8's concern)
   with no corresponding benefit.
4. **A raw Markdown string for `TableBlock.content`, mirroring
   `CodeBlock.content`.** Rejected — see decision 2; a table is not
   self-closing, so a malformed one degrades silently instead of raising,
   which a fenced code block's own verbatim contract cannot do.
5. **Clamping an out-of-sequence bullet `depth` to the nearest valid
   value.** Rejected — see decision 5; the client's requested *structure*
   would be silently rewritten, contradicting the fail-closed rule this ADR
   already applies to a malformed table.
6. **Reusing `ProcedureStep.TextBlock`'s `"text"` discriminator for a
   bullet, instead of a new `"bullet"` type.** Rejected — see decision 5;
   the two models represent different things (a list item versus a step's
   continuation paragraph), and sharing a discriminator value between them
   would be confusing.
7. **Two separate budgets — one for code, one for table/quote content.**
   Rejected — see decision 7; splitting the budget lets the worst case
   approach twice the combined ceiling, pushing closer to
   `max_note_size_bytes` than a single shared budget does for the same
   total client input.
8. **Allowing `BulletBlock` inside `ProcedureStep.blocks`.** Rejected — see
   decision 6; a step's own indent-width rules (ADR-0009 decision 7) and a
   bullet's own depth semantics would blur together with no clear meaning
   for what depth `0` represents inside a numbered step.

## References

- `tasks/todo.md`'s ADR-0009 entry — the recorded future-work note this ADR
  follows up on
- `app/models.py`'s `BulletBlock`, `TableBlock`, `QuoteBlock`, `BodyBlock`
  (which also references ADR-0009's `CodeBlock`), `BodyItem`,
  `_coerce_body_item`, `StepBlock`, `_MAX_TABLE_COLUMNS`, `_MAX_TABLE_ROWS`,
  `_MAX_QUOTE_LINES`, `_MAX_BULLET_DEPTH`
- `app/services/chat_export.py`'s `_normalise_body_items`,
  `_normalise_table`, `_normalise_quote`, `_check_bullet_depth`,
  `_render_body_items`, `_render_table`, `_render_quote`, `_render_bullet`,
  `_escape_table_cell`, `_total_block_chars` — plus ADR-0009's
  `_normalise_code_block`/`_render_top_level_code_block`, both reused
  as-is for a body field's own `CodeBlock`
- `tests/test_chat_export.py`, `tests/test_mcp_tools.py`,
  `tests/test_inbox.py`
- ADR-0005 (`docs/adr/0005-*.md`) decisions 4, 6, 7, 10, 12 — the contracts
  this change extends without altering
- ADR-0009 (`docs/adr/0009-*.md`) — the precedent for a rich block inside a
  structured export, and the fence-self-closing argument this ADR shows
  does not transfer to a table or a blockquote
