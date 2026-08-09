# ADR-0009: Verbatim/structure-preserving code content in structured chat exports

- Status: Accepted
- Date: 2026-08-09
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — the mode/heading/validation contract this change extends, not replaces
  - [`docs/adr/0006-verified-related-note-wikilinks.md`](0006-verified-related-note-wikilinks.md),
    [`docs/adr/0007-scoped-duplicate-note-detection.md`](0007-scoped-duplicate-note-detection.md)
    — prior precedent for a defensive re-check inside the render path itself,
    reused here for `language` and Obsidian-specific caption escaping
  - `app/models.py`'s `TextBlock`/`CodeBlock`/`ProcedureStep`,
    `app/services/chat_export.py`
  - `tests/test_chat_export.py`, `tests/test_mcp_tools.py`, `tests/test_inbox.py`

## Context

`create_inbox_note`'s structured export (issue #12; ADR-0005) renders every
field as "one Markdown line": `app.services.chat_export.one_line` collapses
every line break to a space, strips control characters, and collapses ASCII
whitespace runs, and `_escape_block_start` escapes a leading fence marker
(```` ``` ````) into `\```` `. Between them, a `procedure.steps` entry cannot
carry a command's indentation, a config file's structure, or a code fence at
all — the three transforms between them destroy exactly the content a
step like "open `compose.yaml`, add this block, then run this command" needs
to keep. Collecting all code into a separate `## コード` section was
considered and rejected: a procedure's whole value is the *order* of its
text and code ("open the file, then edit it, then restart it"), and moving
the code out of its step throws that order away.

This ADR's contract is **verbatim/structure-preserving, not byte-level
lossless** — see decision 1. It also introduces a new **optional
supplementary section** category (decision 9) rather than treating this as
an exception to ADR-0005's decision 4, which remains unchanged.

## Decision

1. **The contract is verbatim/structure-preserving, not byte-level
   lossless.** `app.services.chat_export._canonicalise_code` still applies
   three canonicalisations before code content is ever placed inside a
   fence:
   - CRLF/CR -> LF (matching every other line-ending canonicalisation in
     this codebase, e.g. `app/services/inbox_service.py`'s `_render_note`).
   - Control characters other than tab/newline are stripped (the same set
     `one_line`'s own `_CONTROL_RE` strips for single-line text, minus the
     two characters code content must keep).
   - At most one trailing newline is removed: a closing fence already
     supplies the line break ending the code's last line, so `"a"` and
     `"a\n"` must render identically. **A second or later trailing newline
     is a deliberate blank line at the end of the content and is
     preserved**, not collapsed to zero (rejected alternative: unconditional
     `rstrip("\n")`, considered and rejected in review — it would silently
     discard an intentional trailing blank line).
   Everything else — internal indentation, internal blank lines, trailing
   whitespace on non-final lines, Markdown-significant characters, Unicode,
   backticks — passes through unchanged. Inside a `procedure` step, each
   non-empty line additionally gains the step's own list-item indent
   (decision 7); that indent is a Markdown *structural* requirement, not a
   content change, and the contract that survives it is
   `fence_token.content == _canonicalise_code(input) + "\n"` — the parsed
   fence's content, not the raw Markdown string, which the indent makes
   different by construction. `tests/test_chat_export.py` asserts against
   the parsed fence content for exactly this reason.

2. **`procedure.steps[i]` becomes an ordered list of text/code blocks,
   not a plain string.** `app.models.ProcedureStep.blocks` is
   `list[TextBlock | CodeBlock]` (a discriminated union on `type`), so one
   step can interleave "explain, run a command, explain, run another
   command" in the order the conversation actually had it. A fixed
   `text + code[]` shape was considered and rejected: a step can have
   several explanations and several code blocks in arbitrary alternation
   (the compose.yaml example in the issue has three of each), and a fixed
   shape cannot represent that without losing the interleaving that is the
   entire point of this change.

3. **Code content is never passed through `one_line` or
   `_escape_block_start`.** A fence is already structurally closed by its
   own opening/closing markers (decision 4), so the block-forgery hazards
   `_escape_block_start` exists to stop (a bullet, a heading, an HTML block,
   a link reference definition) cannot occur inside one — escaping fence
   content would only reintroduce the very loss this feature removes.

4. **A dynamically-sized fence**:
   `fence_length = max(3, longest_backtick_run_in_content + 1)`
   (`app.services.chat_export._fence_for`). A fixed three-backtick fence
   cannot represent content that itself contains three or more consecutive
   backticks (e.g. a Markdown example inside the code block) without the
   fence closing early. Verified against `markdown-it-py` for backtick runs
   up to five, parametrized in `tests/test_chat_export.py`.

5. **`language` is a validated, bounded fence info string, not a free
   string.** `app.models._LANGUAGE_PATTERN` restricts it to
   `^[A-Za-z0-9][A-Za-z0-9+#._-]{0,31}$` — no line breaks, no backtick, no
   control characters, always non-empty when present. The render path
   (`app.services.chat_export._is_safe_language`) re-checks this
   defensively before emitting the info string, mirroring
   `is_renderable_wikilink_target`'s own re-check of pydantic-validated
   input (ADR-0006) — this module stays structurally incapable of emitting
   an unsafe info string regardless of what a future caller's
   `_NormalisedCodeBlock` carries.

6. **A code block's `label` renders as a plain, literal caption line —
   never as a Markdown heading or emphasis.** `**label**` (bold) was
   considered and rejected: `_escape_block_start` only stops a value from
   opening a new *block*, it does nothing to inline emphasis/link/autolink
   syntax, so a label containing `*`, `` ` ``, `[`, or `<` would still be
   parsed as inline Markdown instead of displayed literally — the caption's
   contract is "show this client text as-is". A new function,
   `_escape_inline`, escapes a fixed character set
   (`_INLINE_ESCAPE_CHARS = "\\`*_[]<>&~=$%#^"`) before `_escape_block_start`
   runs (order matters: `_escape_inline` escapes a bare backslash, so
   running it first means a label that already starts with a hazard
   character, once escaped, no longer matches `_BLOCK_HAZARD_RE`/
   `_ORDERED_MARKER_RE` and is never double-escaped). The set covers
   CommonMark/GFM inline syntax *and* Obsidian-specific inline semantics —
   `#` (a bare hashtag becomes a live Obsidian tag), `^` (can start a
   block-ID reference), `==` (highlight), `$` (math), `%%` (comment) — since
   the note's reader is Obsidian, not only a CommonMark-conformant renderer.
   **`markdown-it-py` cannot detect any Obsidian-specific syntax**, so the
   test suite verifies the CommonMark/GFM layer by parsing, and verifies the
   Obsidian-specific layer only by asserting the fixed character set
   directly (`tests/test_chat_export.py::test_caption_escapes_obsidian_specific_inline_markers`)
   — this is a constructional guarantee, not something CI can confirm by
   parsing. Excluded from the set on purpose: `|` (significant only inside a
   GFM table, which a caption never is), `!` (only meaningful before `[`,
   already escaped), and `: ; , " ' / ? @` (no CommonMark/GFM/Obsidian
   inline meaning) — keeping these unescaped is what lets an ordinary
   caption like `docker-compose.yml` or `起動コマンド` render untouched.

7. **A procedure step's continuation indent is derived from its own marker
   width, not a fixed constant.** `app.models._MAX_STEP_ITEMS` allows up to
   50 steps, so step 10 onward has a 4-character marker (`"10. "`).
   Verified against `markdown-it-py` during design: a fixed 3-space
   continuation indent puts step 10's code fence *outside* the list item —
   CommonMark requires a continuation line to be indented to at least the
   marker's own width — which closes the list early and renumbers every
   step after it from 1. `app.services.chat_export._render_step` computes
   `indent = " " * len(f"{index}. ")` per step instead.
   `tests/test_chat_export.py::test_step_ten_and_beyond_keeps_a_single_ordered_list_with_correct_numbering`
   pins this against an 11-step export.

8. **Each step must start with a `TextBlock`.**
   `app.services.chat_export._normalise_steps` raises
   `"steps[i] must start with a text block."` for a step whose first
   surviving block is a `CodeBlock`. A code-first step was considered and
   rejected: it is only representable in CommonMark by placing the fence
   directly on the marker line with no blank line before it — a single
   blank line between the marker and the fence splits the ordered list in
   two (verified against `markdown-it-py`) — and Obsidian's own renderer is
   not guaranteed to parse that special-cased layout identically. Rejecting
   the input outright, with a client-actionable error, was judged safer
   than risking a silently mis-numbered note in the real Vault. This check
   runs after normalisation (a `TextBlock` that normalises away to nothing,
   e.g. whitespace-only, can leave a step starting with code even though
   the raw schema shape started with text — covered by
   `test_step_that_normalises_to_only_a_code_block_is_rejected`).

9. **A new optional supplementary section, not an exception to ADR-0005
   decision 4.** ADR-0005 decision 4's rule — every heading for the
   selected mode is always emitted, whether or not its field was supplied —
   is unchanged and still governs every field in `_MODE_SECTIONS`. The new
   `code_blocks` field (available in every mode, not mode-specific) is
   rendered by a separate function, `_render_supplementary_sections`, which
   `_render_section`/`_HEADINGS` never touch: it returns an empty list when
   `code_blocks` is empty, so `## コード` never appears at all for an export
   with no top-level code — not even with a `なし` placeholder. Emitting
   `## コード\n\nなし` unconditionally in every mode was considered and
   rejected: it would change all seven modes' existing golden output and
   add a placeholder-only section to the common case (no standalone code),
   which is exactly the noise ADR-0005's placeholder scheme otherwise
   avoids. The precise statement of what changed:
   "same mode -> same **required** heading set and order, plus a
   supplementary section that can appear, at a fixed position, in addition
   to them" — the mode-fixed contract itself is not touched. Position: the
   `code_blocks` section sits directly after a mode's own fields and before
   `## 未解決の論点`, so the pre-existing pins on the first two headings
   (`## 要約`, `## 決定事項`) and the last four (`## 未解決の論点`,
   `## 次のアクション`, `## 関連ノート`, `## 出典`) hold unchanged whether or
   not `code_blocks` is present.

10. **Contextual code (`steps[].blocks[]`) and standalone code
    (`code_blocks`) have a fixed, non-overlapping role, enforced only by
    field description, not by schema.** `code_blocks` is documented for
    "a finished config file, a complete script, reference code, an appendix
    log" that does not belong to any single step, and explicitly says
    "Never move a procedure step's code here" — collecting every step's
    code into `## コード` was the exact anti-pattern this whole change
    exists to avoid (see Context). There is no code-level guard against a
    client putting step-scoped code in `code_blocks` anyway; the ownership
    split is a documented convention aimed at the calling model, the same
    trust boundary ADR-0005 already accepts for e.g. `decisions` vs.
    `next_actions` never being merged.

11. **Total code-content budget, enforced on normalised data.**
    `app.services.chat_export._MAX_TOTAL_CODE_CHARS = 100_000` bounds the
    sum of every code block's content length (`steps[].blocks[]` and
    `code_blocks` together) in one export, raised as a `ValidationError`
    (`Code content exceeds the total limit of 100000 characters.`) with no
    client value in the message or `log_detail` (matching ADR-0005 decision
    10's vocabulary-only error policy). No single `Field(max_length=...)`
    can see a sum across many fields, so this — like the mode/field
    combination checks ADR-0005 decision 6 already runs from inside the
    tool body — has to live here rather than on the pydantic model. The
    three-layer sizing, from smallest to largest:
    | Layer | Bound | Rationale |
    |---|---|---|
    | One code block | `app.models._MAX_CODE_CHARS = 8_000` | 8x `Line`'s own 1000-char cap; fits a `compose.yaml`/`Dockerfile`/mid-sized script/CLI log excerpt. Worst-case UTF-8 is 4 bytes per Unicode code point (`len()` counts code points), so 8,000 chars is at most ~32 KiB. |
    | Container | `_MAX_BLOCKS_PER_STEP = 12`, `_MAX_CODE_BLOCK_ITEMS = 10` | A realistic per-step/per-export ceiling; both appear in the generated schema as a generation-time hint. |
    | Whole export | `_MAX_TOTAL_CODE_CHARS = 100_000` | ~400 KiB worst-case UTF-8; comfortably inside `Settings.max_note_size_bytes`'s default 1 MiB — the limit that matters here, since `note_service.read_note` *truncates* a note over that limit rather than the 2 MiB `max_request_bytes` pre-parse backstop rejecting it outright. A note that can be written but not read back intact is a worse failure than an upfront rejection. |
    The theoretical maximum a schema alone would allow (50 steps x 12 blocks
    x 8,000 chars) is far larger than either bound; `_MAX_TOTAL_CODE_CHARS`
    is what actually keeps a written note inside `max_note_size_bytes` in
    practice, not the per-field caps.

12. **`CodeBlock.content` gets `min_length=1`; `TextBlock.content` does
    not — a deliberate asymmetry, not an oversight.** A rich-object code
    block with empty content carries no meaning and has no
    backward-compatibility reason to be tolerated, so it is rejected at the
    schema layer. `TextBlock.content` must stay exactly as permissive as
    `Line` always was (no `min_length`): the backward-compatibility
    shorthand (decision 13) coerces a bare string into a single `TextBlock`,
    and an empty or whitespace-only string in that position must keep being
    silently dropped by `app.services.chat_export._normalise_text_block`
    exactly as `_normalise_lines` already drops one from every other plain
    string list — rejecting it at the schema layer would change existing
    `steps: ["", "second"]` behaviour. `ProcedureStep.blocks` similarly gets
    `min_length=1`: a step with zero blocks is meaningless the same way, and
    the legacy coercion (decision 13) always produces exactly one block, so
    this bound never affects it.

13. **Backward compatibility: a bare string step is a shorthand for one
    `TextBlock`.** `app.models._coerce_step` (a `BeforeValidator`) turns a
    plain string into `{"blocks": [{"type": "text", "content": <string>}]}`
    before `ProcedureStep` validation runs, so every existing
    `steps: ["do it", ...]` caller — REST or MCP — keeps working unchanged,
    and an export with no code renders byte-identical Markdown to before
    this feature existed (pinned by
    `test_procedure_with_plain_steps_still_renders_the_pre_existing_numbered_list`
    and the pre-existing worked-example tests, none of which needed to
    change). The generated schema exposes this as
    `anyOf: [string, ProcedureStep]`; because a schema alone cannot say
    *which* is preferred, `steps`'s field description states it directly:
    new exports should send a `ProcedureStep` object when the step involves
    code, and the bare string is documented as the backward-compatible
    shorthand. A stricter design — accepting only `ProcedureStep` objects
    and dropping string support — was considered and rejected: it would be
    a breaking change to every existing REST caller and MCP client with no
    compensating benefit, since the coercion has no observable cost.

## Consequences

### Positive

- A procedure like "open a file, edit it, restart it" can now be rendered
  with its actual commands and configs, in the order the conversation had
  them, instead of losing that content to line-flattening or backtick
  escaping.
- No existing export (any mode, any field) changes its rendered Markdown:
  every pre-existing test in `tests/test_chat_export.py` passes unchanged,
  and the new tests only add coverage for the new shape.
- `## コード` is fully additive and opt-in per export — a client that never
  sends `code_blocks` never sees it, in any mode.

### Negative

- `app.models`'s generated MCP schema grows by three `$defs`
  (`TextBlock`, `CodeBlock`, `ProcedureStep`), adding to the token cost
  ADR-0005's own "Negative" section already flagged for `tools/list`.
- The Obsidian-specific half of `_escape_inline`'s guarantee
  (`#`/`^`/`==`/`$`/`%%`) cannot be verified by parsing in CI — only the
  CommonMark/GFM half can. A change to `_INLINE_ESCAPE_CHARS` that silently
  narrows the set would not be caught by a Markdown-structure test, only by
  the direct character-set assertion in
  `tests/test_chat_export.py::test_caption_escapes_obsidian_specific_inline_markers`.
- `steps.items`'s OpenAPI schema is now an `anyOf` (`string` or
  `ProcedureStep`) instead of a plain string, a real (if narrow) contract
  change for REST clients that introspect the schema, though not for ones
  that only send/receive JSON matching either shape.

### Neutral

- `test_every_rendered_line_has_no_trailing_whitespace` (an existing,
  unchanged test) is not violated by this change because it only exercises
  a `summary`-mode export with no code; the invariant it checks does not,
  and should not, extend inside a fenced code block, since code content
  might legitimately need trailing whitespace preserved. This is recorded
  here rather than silently relied upon.
- `_MAX_TOTAL_CODE_CHARS` adds a new rejection reason on top of the
  pre-existing, still-unaddressed gap ADR-0005 recorded: note creation has
  no byte cap of its own beyond `max_request_bytes`'s 2 MiB pre-parse
  backstop. This change narrows that gap for code content specifically (via
  `max_note_size_bytes`, decision 11) but does not close it for the export
  as a whole.

## Alternatives considered

1. **Collect every step's code into a single `## コード` section.**
   Rejected — see Context; this is the exact design this ADR exists to
   avoid.
2. **A fixed `{text: str, code: list[CodeBlock]}` shape per step.**
   Rejected — see decision 2; cannot represent multiple alternating
   explanation/code pairs within one step.
3. **Unconditional `rstrip("\n")` on code content.** Rejected — see
   decision 1; would silently discard a deliberate trailing blank line
   instead of only collapsing the 0-vs-1 ambiguity a closing fence creates.
4. **A fixed three-backtick fence.** Rejected — see decision 4; cannot
   represent content containing three-or-more-backtick runs without closing
   early.
5. **Bold (`**label**`) captions, or reusing `_escape_block_start` alone
   for the label.** Rejected — see decision 6; neither stops inline
   Markdown/Obsidian syntax inside the label from being interpreted instead
   of shown literally.
6. **Allowing a code-first step (fence immediately after the marker, no
   text).** Rejected — see decision 8; only representable via a
   CommonMark-fragile layout, and Obsidian's renderer is not guaranteed to
   match `markdown-it-py`'s behaviour on it.
7. **Emitting `## コード` (with a `なし` placeholder) unconditionally in
   every mode, matching every other heading's always-emitted contract.**
   Rejected — see decision 9; would change all seven modes' existing golden
   output and add a placeholder-only section to the common no-code case.
8. **Accepting only `ProcedureStep` objects, dropping the legacy string
   shorthand.** Rejected — see decision 13; a breaking change to every
   existing caller with no compensating benefit.
9. **A schema-level guard preventing step-scoped code from being placed in
   `code_blocks`.** Rejected — see decision 10; no schema shape can
   distinguish "this code belongs to a step" from "this code is
   standalone", so the split is necessarily a documented convention, not an
   enforced one.

## References

- Issue #12 ("P1: Extend create_inbox_note with structured chat export
  formatting") — the original `procedure.steps` this change extends
- `app/models.py`'s `TextBlock`, `CodeBlock`, `Block`, `ProcedureStep`,
  `_coerce_step`, `StepInput`, `ChatExport.code_blocks`
- `app/services/chat_export.py`'s `_canonicalise_code`, `_fence_for`,
  `_escape_inline`, `_is_safe_language`, `_normalise_steps`,
  `_normalise_code_blocks`, `_render_step`, `_render_fenced_code`,
  `_render_supplementary_sections`
- `tests/test_chat_export.py`, `tests/test_mcp_tools.py`,
  `tests/test_inbox.py`
- ADR-0005 (`docs/adr/0005-*.md`) decisions 4, 6, 10 — the contracts this
  change extends without altering
- ADR-0006 (`docs/adr/0006-*.md`) — precedent for a defensive re-check
  inside the render path of input the schema already validated
