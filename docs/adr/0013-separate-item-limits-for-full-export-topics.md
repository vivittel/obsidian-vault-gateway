# ADR-0013: Raise and separate the item-count limits for `full`-export topics

- Status: Accepted
- Date: 2026-08-16
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - GitHub issue #23 ("Increase and separate structured full-export item limits")
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — the `ChatExport`/`TopicSection` shapes this ADR only re-tunes, not replaces
  - [`docs/adr/0011-rich-body-blocks-in-structured-exports.md`](0011-rich-body-blocks-in-structured-exports.md),
    [`docs/adr/0012-paragraph-first-body-blocks-in-structured-exports.md`](0012-paragraph-first-body-blocks-in-structured-exports.md)
    — generalised every `_MAX_LIST_ITEMS`-bounded field, `topics[].points`
    included, into `list[BodyItem]`; this ADR does not revisit that shape
  - `app/models.py`'s `_MAX_TOPIC_ITEMS`, `_MAX_TOPIC_POINT_ITEMS`,
    `TopicSection.points`; `app/services/chat_export.py`'s
    `_total_block_chars`
  - `tests/test_chat_export.py`, `tests/test_mcp_tools.py`

## Context

`create_inbox_note`'s `full`-mode export failed for a real conversation: 29
`topics` against `_MAX_TOPIC_ITEMS` = 20, and 37 `points` in one topic
against the shared `_MAX_LIST_ITEMS` = 30 that `TopicSection.points`
(`app/models.py`) borrowed from the 20 other body fields sharing it. The client's
only workaround was to merge topics/points together until the count fit —
distorting the conversation's actual structure to satisfy an unrelated
Gateway constant, not a real capacity problem.

`TopicSection.points` shares `_MAX_LIST_ITEMS` with 20 other fields
(`decisions`, `next_actions`, `overview`, and the rest) — all of them,
`points` included, are `list[BodyItem]` since ADR-0011/0012 generalised
every one of them from a plain `list[Line]`. The shape is not what singles
`points` out: what does is that `points` is where a `full`-mode export's
actual topic content accumulates, one entry per point covered under a
topic, so it is the field this report's 37-item conversation actually hit —
none of the other 20 were reported as too tight, and there is no reason
tied to the reported problem to widen them too.

Every `Field(max_length=...)` here also defines a `maxItems` in
`create_inbox_note`'s published MCP tool argument schema (verified via
`ChatExport.model_json_schema()`), so this is a schema/contract change, not
an internal tuning knob — hence an ADR rather than a bare constant edit.

## Decision

1. **`_MAX_TOPIC_ITEMS` rises from 20 to 50.** `topics` is one entry per
   distinct topic the conversation covered; 50 covers the reported 29 with
   real headroom for a long conversation, while still rejecting a client
   that is over-splitting topics rather than under-provisioned by the cap.

2. **`TopicSection.points` gets its own `_MAX_TOPIC_POINT_ITEMS = 100`,
   split off `_MAX_LIST_ITEMS` (30, unchanged).** Every other
   `_MAX_LIST_ITEMS`-bounded field (20 of them — `decisions`, `design`,
   `context`, `verification`, `facts`, and the rest; all `list[BodyItem]`
   since ADR-0011/0012, the same shape `points` itself has) is untouched:
   `points` is the field the production report actually hit, not the only
   one with this shape, so scoping the increase to it — rather than raising
   the shared constant — avoids loosening 20 fields that were never
   reported as too tight. 100 covers the reported 37 with headroom, sized
   the same way as `_MAX_TOPIC_ITEMS`: enough for real usage, not
   unbounded.

3. **No environment-variable configuration.** `Field(max_length=...)`
   drives both runtime validation and the published tool-argument JSON
   Schema in the same declaration; making the cap runtime-configurable
   would require generating that schema dynamically per deployment to keep
   the two in agreement, for a value that is a structural property of
   `ChatExport` (how big one export's shape is allowed to be), not a
   per-deployment resource policy the way `MAX_REQUEST_BYTES` or
   `max_note_size_bytes` are.

4. **Every other size/structure protection is unchanged**:
   `MAX_REQUEST_BYTES`, `_MAX_TOTAL_BLOCK_CHARS`, `_MAX_CODE_CHARS`,
   `_MAX_TABLE_ROWS`/`_MAX_TABLE_COLUMNS`, `_MAX_QUOTE_LINES`,
   `_MAX_BULLET_DEPTH`, and every per-string `max_length`. This ADR widens
   two item-count caps only.

## Consequences

### Positive

- The reported failure (29 topics, 37 points) succeeds without the client
  needing to merge content to fit an arbitrary count.
- `topics[].points`'s cap now reflects the field's own reported usage
  instead of borrowing a cap sized for 20 other, differently-used fields
  that happen to share its `list[BodyItem]` shape.
- The 20 other `_MAX_LIST_ITEMS` fields are untouched — this is a targeted
  widening of the two constants the production report actually hit, not a
  blanket increase.

### Negative

- **`app/services/chat_export._total_block_chars`'s existing rationale for
  not budgeting a plain bullet's `content`** — "already bounded by `Line`'s
  per-item cap and the field's own item-count cap" — describes a weaker
  bound than before, specifically for `topics[].points`. Its own
  bullet-content contribution to a `full` export's schema-level ceiling
  rises from `_MAX_TOPIC_ITEMS` (20) × old `_MAX_LIST_ITEMS` (30) × `Line`
  (1,000) = 600,000 characters to `_MAX_TOPIC_ITEMS` (50) ×
  `_MAX_TOPIC_POINT_ITEMS` (100) × `Line` (1,000) = 5,000,000 characters —
  an ~8.3x increase (both `_MAX_TOPIC_ITEMS` and `_MAX_TOPIC_POINT_ITEMS`
  rose, so the two factors compound). This is `topics[].points`' own
  contribution only, not the whole export's ceiling: the common
  `list[BodyItem]` fields every mode shares (`decisions`,
  `unresolved_issues`, `next_actions`, `sources`) can add up to
  4 × 30 × 1,000 = 120,000 more bullet-content characters on top of it,
  unaffected by this change. The combined figure remains a hard, finite
  ceiling on its own — it does not make the bullet path unbounded — but it
  is markedly larger than before, and the docstring's reasoning needed the
  concrete numbers restated, scoped correctly, so a future reader does not
  assume the old 600,000 figure still holds or mistake it for the whole
  export's ceiling.
- A client already relying on the previous, lower caps to reject an
  oversized export early sees that rejection point move later (or not
  happen at all, for a payload between the old and new caps) — the same
  kind of visible, intentional widening any cap increase produces.

### Neutral

- **`MAX_REQUEST_BYTES` is not a substitute cap this ADR is relying on.**
  `Settings.max_request_bytes` (`app/config.py`) defaults to 2 MiB and has
  a floor (`ge=1024`) but no configured ceiling, so whether it or the
  schema-level item-count ceiling above binds first on a `topics[].points`
  bullet-only payload depends on the deployment's own setting: at the
  2 MiB default, the transport-level `MAX_REQUEST_BYTES` check (enforced
  pre-parse by `/mcp`'s own SDK middleware) rejects a 5,000,000-character
  payload of that shape before it reaches `ChatExport` validation at all; a
  deployment that raises `MAX_REQUEST_BYTES` enough makes the schema-level
  ceiling above the binding one again. Either way the payload stays finite
  — this ADR does not depend on `MAX_REQUEST_BYTES`'s value to keep the
  export bounded, and does not change `MAX_REQUEST_BYTES` itself.
- `_MAX_TOTAL_BLOCK_CHARS` (100,000) remains an independent cross-block
  budget, exactly as it already was for the other 20 `_MAX_LIST_ITEMS`
  fields — this ADR does not change that relationship, only the item-count
  arithmetic's inputs. It **can** bind before either new item-count cap
  when the counted paragraph/code/table/quote content is sufficiently
  large (`app/models.py`'s comment above `_MAX_PARAGRAPH_CHARS` gives the
  worst-case arithmetic for a per-item-at-the-cap export), but the
  item-count caps can still bind first for many short blocks: 100
  one-character `ParagraphBlock`s in one topic's `points` total a few
  hundred counted characters, nowhere near 100,000, so the 101st item hits
  `_MAX_TOPIC_POINT_ITEMS` before `_MAX_TOTAL_BLOCK_CHARS` is ever
  approached. Which cap binds depends on the export's own content, not a
  fixed ordering.

## Alternatives considered

1. **Make the item-count caps environment-configurable.** Rejected — see
   decision 3; the caps drive the published MCP tool schema directly, so a
   configurable value would need the schema generated per deployment to
   stay in sync with runtime validation, for a value that is a structural
   property of the export shape rather than a deployment resource policy.
2. **Raise `_MAX_LIST_ITEMS` itself instead of splitting off
   `_MAX_TOPIC_POINT_ITEMS`.** Rejected — the production report only hit
   `topics`/`topics[].points`; raising the shared constant would loosen 20
   unrelated fields (`decisions`, `design`, `facts`, ...) that were never
   reported as too tight, for no benefit tied to the actual problem.
3. **Remove the cap on `topics[].points` entirely.** Rejected — the issue
   itself asks for a bounded increase, not unbounded input; an unbounded
   per-topic item count would also make `topics[].points`' own
   bullet-content contribution, discussed in Consequences → Negative,
   genuinely unbounded rather than merely larger.
4. **Leave the caps as-is and document the merge-topics-to-fit workaround.**
   Rejected — this keeps distorting a client's own conversation structure
   to satisfy a Gateway constant that has no relationship to the content
   being exported, the exact problem this ADR fixes.

## References

- `app/models.py`'s `_MAX_TOPIC_ITEMS`, `_MAX_TOPIC_POINT_ITEMS`,
  `TopicSection.points`
- `app/services/chat_export.py`'s `_total_block_chars` docstring
- `tests/test_chat_export.py` — topics/points boundary tests (50/51,
  100/101) and the `_MAX_LIST_ITEMS`-field non-regression tests
  (`decisions`, `next_actions`, `overview`, `facts`)
- `tests/test_mcp_tools.py` — published-schema `maxItems` assertions and
  the `create_inbox_note` over-the-cap rejection tests
- GitHub issue #23
