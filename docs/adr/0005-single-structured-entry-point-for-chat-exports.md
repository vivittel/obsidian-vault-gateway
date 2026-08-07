# ADR-0005: `create_inbox_note` becomes the single structured entry point for chat exports

- Status: Accepted
- Date: 2026-08-07
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) sections 9 and 12
  - [`README.md`](../../README.md) "Tools" and "REST (secondary interface)"
  - GitHub issue #12 ("P1: Extend create_inbox_note with structured chat
    export formatting")
  - GitHub issue #13 ("P2: Add automatic related-note linking to structured
    chat exports"), depends on #12

## Context

`create_inbox_note` previously took a bare title, a free-form Markdown
`content` string, and an optional flat `frontmatter` dict, and wrote them
verbatim (`app/services/inbox_service.py`'s `_render_note`). A plain
"summarise this conversation and save it" instruction therefore produced a
structurally different note every time — heading names, heading order, empty
sections, and frontmatter key order all varied with whatever the calling LLM
happened to produce.

Issue #12 asks for a fixed responsibility split instead: the LLM/client reads
the conversation, chooses an export mode, and extracts content into
structured fields; the Gateway validates that structured input and renders it
through a deterministic, mode-specific formatter that owns section names,
section order, empty-state placeholders, and the frontmatter schema. The
Gateway performs no semantic summarisation.

A separate `export_chat_note` tool was considered and rejected. Two write
tools would double the surface a client's approval policy has to cover, and
"summarise and save" would become ambiguous between them — exactly what the
issue's acceptance criteria forbid ("A plain 'summarise and save' request
maps unambiguously to this tool"). Extending the existing tool keeps one
write tool, one approval row, and one formatter.

## Decision

1. **MCP is structured-only; REST keeps both.** The MCP tool
   `create_inbox_note` drops its `content`/`frontmatter` parameters entirely
   and exposes only `title` plus a structured `export` parameter
   (`app.models.ChatExport`). REST's `POST /api/v1/inbox/notes` keeps its
   existing `content`/`frontmatter` fields for backward compatibility and
   gains the same `export` field; exactly one of `content` or `export` is
   required, and `export` combined with `frontmatter` is rejected (the
   formatter owns `export`'s frontmatter).

2. **All seven modes ship together**: `summary` (default), `technical`,
   `history`, `full`, `procedure`, `issue`, `reference`. Their allowed and
   required fields are defined once in `app/services/chat_export.py`'s
   `_MODE_SECTIONS` / `_MODE_REQUIRED`.

3. **The file-name convention is unchanged.** `sanitise_title` +
   `note_file_name` still produce `Title.md`, `Title-2.md`, … No date prefix
   is added.

4. **Rendered headings are Japanese; JSON field names stay English
   snake_case.** The mapping from the issue's English section names to the
   rendered headings, fixed for every mode:

   | # | Issue's English name | JSON field | Heading |
   |---|---|---|---|
   | 1 | TL;DR | `tldr` | `## 要約` |
   | 2 | Decisions | `decisions` | `## 決定事項` |
   | 3 | *(mode-specific block)* | | |
   | 4 | Unresolved Issues | `unresolved_issues` | `## 未解決の論点` |
   | 5 | Next Actions | `next_actions` | `## 次のアクション` |
   | 6 | Related Notes | *(no field yet — see decision 8)* | `## 関連ノート` |
   | 7 | Sources | `sources` | `## 出典` |

   Mode-specific block, in this order per mode:

   | Mode | Fields (render order) | Headings |
   |---|---|---|
   | `summary` | `overview`, `key_points` | `## 概要`, `## 要点` |
   | `technical` | `context`, `design`, `implementation_notes`, `verification` | `## 背景`, `## 設計`, `## 実装メモ`, `## 検証` |
   | `history` | `timeline`, `turning_points` | `## 経緯`, `## 転換点` |
   | `full` | `topics` | `## トピック` (each topic → `### {heading}`) |
   | `procedure` | `prerequisites`, `steps`, `verification`, `rollback` | `## 前提条件`, `## 手順`, `## 検証`, `## ロールバック` |
   | `issue` | `symptom`, `environment`, `investigation`, `root_cause`, `workaround` | `## 症状`, `## 環境`, `## 調査`, `## 原因`, `## 回避策` |
   | `reference` | `definitions`, `facts`, `examples` | `## 用語`, `## 事実`, `## 例` |

   Every heading listed for the selected mode is always emitted, whether or
   not its field was supplied — "same mode → same heading set and order"
   holds independently of the input. `verification` is shared between
   `technical` and `procedure` (same meaning, same heading, same rendering);
   renaming it per mode would push formatter bookkeeping into the
   client-facing API.

5. **A flat model with a `mode` default, not a discriminated union.**
   `mode` must default to `"summary"` so a bare "summarise and save" maps to
   it, and a pydantic discriminated union requires its tag to be present.
   `app.services.chat_export.render_chat_export` rejects, after
   normalisation, any field that does not belong to the selected mode.

6. **Validation is split across two layers by necessity, not preference.**
   Type shape and per-field bounds live on the pydantic models
   (`app/models.py`): min/max lengths, `extra="forbid"`. Everything that
   depends on *combinations* of fields — which fields a mode allows, which
   fields a mode requires, whether required text survived normalisation —
   is checked in `app/services/chat_export.py`, from inside the tool body,
   so the rejection is a `GatewayError` (`ValidationError`, coded
   `VALIDATION_ERROR`) that `_McpCall` converts into a sanitised `MCPError`.
   A pydantic `model_validator` cannot do this: the MCP SDK runs argument-
   schema validation *before* the tool body, so a validator-raised error
   would surface as a raw, unsanitised `ToolError` instead
   (`tests/test_mcp_tools.py`'s existing nested-frontmatter test already
   documents and accepts this trade-off for schema-level rejections).

7. **Checks run against normalised data, never the raw pydantic input.**
   `min_length=1` on a string accepts `"\n"` or `" "`; only after
   `_one_line`-normalisation can "is this field actually empty" be answered
   correctly. A plain string list (e.g. `steps`) can shrink to `[]` after
   normalisation and is then rejected by the mode's required-field check. A
   nested model's required subfield (e.g. `TopicSection.heading`) is
   rejected immediately, item by item, rather than silently dropped — a
   dropped item would let the client believe something was saved that
   was not.

8. **`related_notes` is deliberately not part of this change.** Issue #13
   (which depends on #12) owns verified related-note wikilinks: it requires
   an existence check against the Vault, which `app.services.chat_export`
   — a pure function with no filesystem access — cannot perform. This
   change fixes only the `## 関連ノート` heading, its position (position 6
   in the common-section order), and its placeholder (always `なし` for
   now); issue #13 adds the `related_notes` input field and the wikilink
   rendering on top of that without moving either.

9. **Frontmatter key order and ownership**, built as a plain `dict` handed to
   the existing `_render_note` (`sort_keys=False` preserves insertion
   order — no second serialiser):

   | # | Key | Value | Owner |
   |---|---|---|---|
   | 1 | `title` | normalised `title` argument | formatter |
   | 2 | `created` | `now.isoformat(timespec="seconds")` | formatter |
   | 3 | `updated` | same value as `created` | formatter |
   | 4 | `source` | `"chatgpt"` (fixed constant) | formatter |
   | 5 | `export_mode` | `export.mode` | formatter, value from client |
   | 6 | `project` | normalised value; **key omitted** if absent/blank | client, optional |
   | 7 | `conversation_type` | same treatment | client, optional |
   | 8 | `tags` | normalised list; `tags: []` when empty | client, normalised by formatter |

   `project`/`conversation_type` are omitted rather than emitted as `null`
   when absent, so "stable key order" means "the keys that are present
   appear in this relative order" without a middle key ever being `null` in
   Obsidian's Properties UI. `source: chatgpt` is a hardcoded
   formatter-owned constant — the only place an actual client identity
   exists is the MCP `initialize` `clientInfo`, and plumbing that into the
   application layer would break `app/application.py`'s transport-neutral
   invariant.

10. **Client-facing error messages are built from a fixed vocabulary, never
    from client-supplied text.** `GatewayError`'s client-facing `message`
    must be passed as the exception's first argument — passing only
    `log_detail` yields the class's generic default message instead. Every
    rejection message in `chat_export.py` is composed only of the Gateway's
    own field/mode names and, for a nested-item rejection, a zero-based
    array index; no `tldr` sentence, tag, or `project` value is ever
    echoed back. The list of offending fields in a "wrong mode" message is
    read from an explicit `tuple` (`_ALL_MODE_FIELDS_IN_ORDER`, built via
    `dict.fromkeys`), not from `frozenset` iteration — a `frozenset`'s
    iteration order depends on Python's per-process string-hash
    randomisation and would make the message's field ordering
    non-deterministic.

11. **`CreatedNoteResponse.title` is documented as the sanitised file-name
    stem, not "the title".** For `title="a/b:c*d"`, the stored frontmatter
    `title` and the note's H1 are `a/b:c*d`; the file is `a-b-c-d.md`; and
    `CreatedNoteResponse.title` is `a-b-c-d`. That return value is
    unchanged — only its field description was corrected, since a
    structured export is the first case where "the stored title" and "the
    file-name stem" visibly diverge.

The three decisions below were added responding to code review on the pull
request implementing this ADR, before merge — the review found real gaps in
decisions 7 and 10 above and in the trade-off decision 1 assumed about a
client sending `content`.

12. **Every rendering path escapes a value that would otherwise open a new
    Markdown block, not only `tldr`'s bare paragraph.** A bullet or numbered
    prefix ("- "/"N. ") does not stop a client value from opening a *nested*
    block inside that list item — CommonMark list items may contain
    arbitrary block content, so `decisions: ["# forged"]` rendered a real
    `<h1>` inside the `<li>`, and `tldr: ["<script>"]` opened an unclosed
    HTML block that swallowed every following heading as raw content
    (verified against `markdown-it-py`, not a project dependency, during
    review). `_escape_block_start` (renamed from `_escape_paragraph`) now
    runs as the last step before every bullet, numbered item,
    `timeline`/`definitions` combined line, and `topics.points` item gets
    its prefix. The hazard set also grew to cover a leading `<` (every
    CommonMark HTML block type starts with one) and a leading `[` (a link
    reference definition, `[foo]: url`, silently deletes the list item's
    visible text and can rewire any other `[foo]` reference in the same
    note; `[ ]`/`[x]` is a GFM/Obsidian task-list checkbox). A digit+
    punctuation prefix (`"1. nested"`) is escaped by inserting the backslash
    before the punctuation (`"1\. nested"`), not before the value — a
    backslash before a digit is not a CommonMark escape sequence at all and
    would render literally. Heading text (`### {heading}`, the H1) is still
    never escaped: it is inline content of an already-open heading and
    cannot itself open a new block regardless of its leading character.

13. **`create_inbox_note` fails closed on an unexpected top-level MCP
    argument**, via `_StrictCreateInboxNoteArgumentsMiddleware`
    (`mcp.server.context.ServerMiddleware`, registered on
    `MCPServer(..., middleware=[...])`). The SDK's dynamically-generated
    per-tool argument model has no supported way to set `extra="forbid"`
    (verified: `ArgModelBase.model_config` only sets
    `arbitrary_types_allowed=True`, and neither `@mcp.tool()`, `add_tool()`,
    nor `Tool.from_function()` expose a strict-mode option), so a stray
    `content`/`frontmatter` alongside a valid `export` was silently dropped
    rather than rejected — user-visible data loss on a write tool, not a
    compatibility nicety, since the caller believes it saved `content` and
    it never reaches the note. `ServerMiddleware` runs before argument
    validation over the *raw* JSON-RPC params, which is what makes seeing
    the unrecognised key possible at all. This only protects requests that
    go through the real dispatch (the mounted `/mcp` transport); the
    `mcp.call_tool(...)` convenience method `tests/test_mcp_tools.py` uses
    for direct, transport-free tool tests bypasses `ServerRunner` and its
    middleware chain entirely, which is fine — that path is an internal
    Python API a remote client can never reach, not a wire boundary. The
    middleware logs the same `mcp_call` audit line `_McpCall` would have
    (via a `_log_mcp_call` helper both now share), so a rejected write
    attempt still leaves an audit trail even though the tool body — and
    therefore `_McpCall` — never runs.

14. **`tags` uses a dedicated `Tag` type (`Annotated[str, Field(max_length=200)]`),
    not the `Label` type `TopicSection.heading`/`TermDefinition.term` use.**
    `Label` sets `min_length=1`; sharing it for `tags` meant
    `ChatExport(tags=[""])` was rejected by pydantic before
    `chat_export._normalise_tags` — which drops empty/whitespace-only tags —
    ever ran, silently contradicting the "pydantic allows it, the formatter
    drops it" convention every other simple list (`Line`, no `min_length`)
    already follows for exactly this reason. `Tag` restores that
    consistency. Because `ChatExport.tags` is also reachable from REST's
    `InboxNoteCreateRequest.export`, this is a real (if minor) OpenAPI
    change: `ChatExport.tags.items.minLength` (previously `1`) is gone from
    the generated schema.

## Consequences

### Positive

- One write tool, one approval row (`create_inbox_note`, still `write` /
  `prompt` in the tools table), one formatter.
- Determinism is testable byte-for-byte: `render_chat_export` is a pure
  function of `(export, title, now)`, so `tests/test_chat_export.py` can pin
  exact expected Markdown and frontmatter for a fixed clock.
- The generated MCP argument schema — `ChatExport`'s per-field
  `Field(description=...)` text — is itself the mode-selection guidance the
  calling LLM sees, without needing to grow `SERVER_INSTRUCTIONS` (which is
  already at its 512-character budget).

### Negative

- Any MCP client holding a cached tool schema that still sends `content`
  alongside `export` gets that call rejected outright — not silently
  ignored — by `_StrictCreateInboxNoteArgumentsMiddleware` (decision 14
  below); a client that sends no `export` at all gets a schema rejection
  (`ToolError`, "Field required"). Accepted: MCP clients re-read
  `tools/list` each session, and this tool has never had a stable contract
  outside this repository.
- The argument schema grows to roughly thirty `ChatExport` fields plus three
  `$defs` (`TimelineEntry`, `TopicSection`, `TermDefinition`), costing more
  tokens on every `tools/list` and creating a real risk that a model fills in
  fields belonging to the wrong mode. Mitigated by every mode-specific
  description naming its owning mode(s), by the tool description stating the
  rule explicitly, and by the formatter's rejection message naming the exact
  offending fields so a model can self-correct in one round trip. Worth
  watching in the first real MCP-client session.
- Adding an eighth mode now means touching the model, `_MODE_SECTIONS` /
  `_MODE_REQUIRED` / `_HEADINGS` / `_FIELD_OWNER_MODES`, and the test suite
  together — there is no single place a new mode can be added in isolation.

### Neutral

- Determinism means "given the same `now`" — `datetime.now()` is read in
  exactly one place in `app/` (`GatewayApplication.create_chat_export_note`);
  `render_chat_export` never reads the clock itself.
- Note creation still has no byte cap of its own; the only backstop is the
  transport's `MAX_REQUEST_BYTES` (2 MiB default), enforced pre-parse on both
  transports. A `full`-mode export with every list at its configured maximum
  is the first realistic way to approach that limit. Recorded as an accepted
  gap, not fixed here (see Alternatives and the parent PR's scope notes).
- PyYAML quotes ISO timestamps (`created: '2026-08-07T00:00:00+09:00'`) so
  they do not re-resolve as YAML timestamp nodes, and folds an overly long
  title that contains spaces across two lines at its default `width=80` —
  both are pre-existing `_render_note` behaviour, both are valid YAML, and a
  fold still round-trips to the original single-line string through
  `yaml.safe_load`.
- `updated` is initialised to the same value as `created` at export time.
  The existing `append_inbox_note` operation writes `existing + appended`
  bytes directly and does not parse or rewrite frontmatter, so appending to
  a chat-export note does not currently advance its `updated` field. The key
  exists so a future append-aware rewrite *could* update it; this change
  does not add that behaviour.

## Alternatives considered

1. **A separate `export_chat_note` tool.** Rejected — see Context.
2. **A discriminated union on `mode`.** Rejected — a discriminated union
   requires its tag field to be present, which conflicts with `mode`
   defaulting to `"summary"`.
3. **English section headings.** Rejected — the vault's existing convention
   and the issue's context are Japanese-first; the JSON field names already
   carry the English vocabulary for API consumers.
4. **Validating mode/field combinations with a pydantic `model_validator` on
   `ChatExport`.** Rejected — the MCP SDK evaluates argument-schema
   validation, including `model_validator`s, before the tool body runs, so
   the rejection would bypass `_McpCall` and surface as an unsanitised
   `ToolError` rather than a coded `MCPError`.
5. **Widening `FrontmatterValue` to accept nested maps** so `related_notes`
   or export metadata could ride through the existing free-form
   `frontmatter` field. Rejected outright — the type comment in
   `app/models.py` calls this the injection boundary; nested YAML structures
   in client-supplied frontmatter are not a tradeable convenience.
6. **Squeezing mode names into `SERVER_INSTRUCTIONS`.** Rejected — its first
   512 characters are already at budget for the constraints
   `tests/test_mcp_tools.py` pins, and the tool `description=` plus
   `ChatExport.mode`'s own description are delivered to the model alongside
   the very schema it is filling in, which is a better place for this
   guidance regardless of budget.
7. **Publishing `related_notes` now as a free-form `list[str]` and tightening
   its type in issue #13.** Rejected — that would mean shipping a public
   schema change now and immediately making a breaking change to it in the
   very next issue, for no benefit over waiting until #13 can define the
   field with its actual (verified-path) semantics from the start.
8. **A `raw` / `structured` model `Union` for the REST request, to get an
   `oneOf` in the generated OpenAPI schema.** Rejected for this change — it
   would restructure `InboxNoteCreateRequest`'s existing single-model shape
   and its `extra="forbid"` test coverage. The exclusion rule is instead
   documented in the model's docstring (which FastAPI surfaces as the
   schema's `description`) and pinned by a test
   (`tests/test_openapi.py::test_inbox_note_create_request_documents_the_content_export_exclusion`).

For decision 13's fail-closed argument check, three SDK-level alternatives
were considered and rejected before settling on `ServerMiddleware`:

9. **A `**kwargs` catch-all parameter on the tool function.** Rejected — the
   SDK's parameter walk (`func_metadata`) has no special case for
   `inspect.Parameter.VAR_KEYWORD`; it would just add a field named
   `kwargs` to the dynamic model, not a pydantic `extra` catch-all, so
   client-supplied extra JSON keys still never reach it.
10. **Mutating the registered tool's `arg_model.model_config` after
    construction** to add `extra="forbid"`. Rejected — pydantic v2 compiles
    a model's core validation schema at class-creation time; whether a
    post-hoc `model_config` mutation (with or without `model_rebuild()`)
    reliably changes validation behaviour for a `create_model()`-built class
    is pydantic-internals-dependent and not something to rely on.
11. **Wrapping `title`/`export` into one parameter of a caller-defined model**
    (e.g. `payload: CreateInboxNoteInput`, itself `extra="forbid"`).
    Rejected — the SDK builds one top-level schema field per Python
    parameter regardless of that parameter's own type (confirmed: this is
    exactly how `export: ChatExport` already works, as a named field
    pointing at a `$defs` entry, not inlined), so this would change the
    tool's top-level keys to `{"payload"}` instead of preserving
    `{"title", "export"}`.

## References

- Issue #12: "P1: Extend create_inbox_note with structured chat export
  formatting"
- Issue #13: "P2: Add automatic related-note linking to structured chat
  exports" (depends on #12)
- `app/services/chat_export.py`, `app/models.py`'s `ChatExport` and related
  models
- `tests/test_chat_export.py`, `tests/test_mcp_tools.py`, `tests/test_inbox.py`
- ADR-0003 (`docs/adr/0003-allow-os-replace-for-inbox-append.md`) — the
  precedent for scoping a single, precisely bounded exception into an
  existing invariant rather than adding a parallel mechanism
