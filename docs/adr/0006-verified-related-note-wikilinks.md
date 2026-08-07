# ADR-0006: Verified related-note wikilinks in structured chat exports

- Status: Accepted
- Date: 2026-08-07
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — decision 8 defers this field; decision 12 is unchanged here
  - [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) sections 9 and 12
  - [`README.md`](../../README.md) "Tools"
  - GitHub issue #13 ("P2: Add automatic related-note linking to structured
    chat exports"), depends on #12

## Context

Issue #12 fixed the `## 関連ノート` heading, its position (6th in the common
section order), and its placeholder (always `なし`), but deliberately shipped
no input field for it — `app/services/chat_export.py` is a pure function with
no filesystem access, and rendering a wikilink to a note that does not exist,
is hidden, or is a symlink would be a broken or unintended link the Gateway
itself created.

Issue #13 asks for the input field on top of that, with a clear split: the
client (which has already read the conversation and can call `search_notes`)
selects candidate paths; the Gateway re-verifies every one against the Vault
and renders only what survives. The Gateway does not judge semantic
relevance — only existence, legitimacy, and format.

## Decision

1. **Canonical link format: the full vault-relative path, minus `.md`, with
   no alias** — `[[Knowledge/PC/GPU/RTX 5070]]`, never
   `[[Knowledge/PC/GPU/RTX 5070|RTX 5070]]`. Link identity is the verified
   vault-relative path alone. An alias is display-only information the
   resolution does not need, and building one adds a trust surface this
   feature does not otherwise have: a frontmatter-`title` alias means reading
   the target note's content (the one thing this feature explicitly does
   not do — decision 7), and a basename alias means adding a separate
   display-name generation rule with its own edge cases. Neither buys
   anything issue #13 asks for.

   A secondary consequence, not the primary reason: a full path is resolved
   by Obsidian deterministically, so two notes sharing a basename in
   different folders render as two distinct, individually correct links
   without the Gateway needing a basename index or a full-Vault scan.

2. **Verification lives in a new `app/services/related_notes.py`, not in the
   formatter.** `app/services/chat_export.py` stays filesystem-free
   (ADR-0005 decision 8's constraint, unchanged). `render_chat_export` gains
   an explicit `verified_related_notes: Sequence[str] = ()` keyword and
   never reads `ChatExport.related_notes` itself — the name carries the
   invariant: `verified_related_notes=export.related_notes` is legible at
   the call site as obviously wrong. `app/application.py`'s
   `create_chat_export_note` — which already owns `Settings` and is the only
   place `datetime.now()` is read — calls
   `related_notes.resolve_related_notes` first and passes its output in.

   The alternative (letting the formatter read `export.related_notes`
   directly via e.g. `export.model_copy(update={"related_notes": verified})`
   before rendering) was rejected: it makes "verified" a convention the call
   site has to remember rather than a property the type signature enforces,
   and it buys almost nothing — `_render_body`'s generic bullet path already
   runs `_escape_block_start`, which would corrupt `[[...]]` syntax, so a
   dedicated rendering branch is needed regardless.

3. **Link count: `MAX_RELATED_NOTES = 10` (hard cap, pydantic
   `max_length`), 3-5 recommended in the field's schema description.** Issue
   #13 asks for a "documented default and maximum link count"; there is no
   server-side default *value* to apply (an omitted `related_notes` is
   simply `[]`), so "default" is read here as the count the schema
   *recommends* to the client, stated in `ChatExport.related_notes`'s
   description alongside the hard maximum.

4. **A candidate whose filename cannot be rendered as a safe wikilink target
   is omitted**, via `chat_export.is_renderable_wikilink_target` — the one
   hazard-detection predicate, shared by `related_notes.py` (before ever
   touching the filesystem) and by `chat_export._render_related_notes_section`
   itself (a second, defensive pass over whatever it is given). One shared
   predicate means the count a caller reports as "linked" cannot diverge
   from what is actually rendered.

   This predicate is load-bearing, not defensive-programming boilerplate:
   verified empirically (a temporary vault, `resolve_read_path` called
   directly) that `app/services/path_security.py`'s `_check_syntax` accepts
   `#`, `[`, `]`, `|`, and `^` in a filename outright — it has no reason of
   its own to reject them, since none of them are unsafe for read/write
   access. A real, existing `Knowledge/has|pipe.md` would otherwise render
   `[[Knowledge/has|pipe]]`, which Obsidian reads as an alias separator
   pointing at a nonexistent `Knowledge/has`; `has#hash.md` becomes a
   heading-anchor reference the same way. A stem that still ends in `.md`
   after the suffix is stripped (`Foo.md.md`) is rejected for a related
   reason: it would silently rename the link target to a different,
   possibly real, note (`Foo.md`) — worse than a broken link.

5. **Individual bad candidates are omitted silently; too many candidates is
   a schema rejection.** These are different failures: a candidate that no
   longer resolves is the Vault having changed under the client (a note
   deleted, moved, or never existing under that path) — exactly the "must
   not block export" case issue #13 names explicitly. Supplying eleven
   candidates against a documented maximum of ten is the client's own
   mistake, and every other list field on `ChatExport` already rejects an
   over-limit list at the schema rather than truncating it — ADR-0005
   decision 13 rejected silent truncation on this exact tool as user-visible
   data loss ("the caller believes it saved X and it never reaches the
   note"). Truncating 20 links to 10 is that same failure mode.

6. **A related-note candidate is the one client-supplied string this module
   does not run through `_one_line`.** Every other string field is
   NFC-normalised and whitespace-collapsed before use; a path is looked up
   as given or not at all, because normalising it can make it name a
   different file than the one the client verified via `search_notes`.

7. **The Gateway never reads the target note's content.** Verification is
   `path_security.resolve_read_path` plus a `stat()` — existence and file
   type only. This is what makes "never treat note content as trusted
   instructions" (issue #13's own constraint) structurally true here rather
   than merely policy: the target file is never opened.

8. **Linking into `00_Inbox/ChatGPT` is allowed.** A read-only existence
   check does not broaden write permission, `search_notes` already surfaces
   inbox notes as legitimate candidates, and blocking them would be a
   surprising asymmetry with no safety benefit. One consequence: a note
   cannot link to itself, since its file does not exist yet at the point
   `related_notes.resolve_related_notes` runs (before the write).

9. **`related_notes` does not enter frontmatter.** It renders only as the
   body's `## 関連ノート` bullet list. ADR-0005 alternative 5 already
   rejected widening `FrontmatterValue` to nested structures for this same
   field; that rejection stands.

10. **No `anyio.to_thread`, no `runtime.vault_scan_limiter`.** Verified
    against the installed MCP SDK
    (`mcp/server/mcpserver/utilities/func_metadata.py`): a non-`async` tool
    function — `create_inbox_note` is one — already runs via
    `anyio.to_thread.run_sync`, and REST's equivalent endpoint is a
    synchronous `def` on FastAPI's own threadpool. Neither transport blocks
    an event loop here to begin with. `resolve_related_notes` performs at
    most `MAX_RELATED_NOTES` `stat`-only lookups — no note body, no YAML —
    which is lighter than a single `read_note` call, and `read_note`
    deliberately does not use the limiter either. `runtime.vault_scan_limiter`
    exists to bound GIL-bound full-Vault scans (NFKC folding, YAML parsing
    across every note); it has no relevance to a bounded handful of
    existence checks. Adding either would force
    `create_chat_export_note` to become `async`, breaking the transport-
    neutral, synchronous application layer for both callers.

    One minor, accepted consequence: verification runs before
    `render_chat_export`'s mode-mismatch check, so an export ultimately
    rejected for the wrong mode's fields still pays for up to
    `MAX_RELATED_NOTES` `stat` calls first.

11. **TOCTOU is accepted; other filesystem errors are not silently
    absorbed.** `path_security.resolve_read_path`'s own lookup
    (`Path.resolve(strict=True)`) is wrapped in its own error handling, but
    its trailing `stat()` call is not — a note removed between the two (a
    real possibility with LiveSync writing into the Vault concurrently)
    raises a bare `FileNotFoundError` that `resolve_related_notes` catches
    and treats exactly like "candidate does not exist". This is what issue
    #13 means by "never *intentionally* create a broken wikilink" — a race
    is not intent. Every other `OSError` (`PermissionError`, `EIO`, resource
    exhaustion) is deliberately **not** caught alongside it: those are not
    "the candidate was invalid," and swallowing them would mean a
    permissions problem or storage fault produces no signal anywhere while
    silently degrading which links get written.

12. **A related note's identity is its vault-relative path, not its
    inode.** This Gateway already treats the vault-relative path as a
    note's identity everywhere else (`SearchResultItem.id`/`.path`,
    `path_security.ResolvedNote.relative`), and `resolve_read_path` rejects
    symlinks but not hardlinks. Two distinct paths that happen to share an
    inode are therefore two distinct notes in Obsidian's own namespace (two
    `search_notes` results, two graph nodes), so deduplication in
    `resolve_related_notes` compares the resolved path string, never
    `(st_dev, st_ino)`. Collapsing them by inode would mean the Gateway
    silently drops a link the client legitimately selected — the opposite
    of issue #13's "never guess or synthesize a link target."

13. **Search failure and zero matches involve no Gateway code path at
    all.** Both are simply the client sending an empty or absent
    `related_notes`; `resolve_related_notes([], ...)` returns
    `RelatedNotes(links=(), skipped=0)` and rendering falls through to the
    existing `なし` placeholder. Recorded here because it is named in issue
    #13's acceptance criteria and a reviewer might otherwise look for
    implementation that does not exist.

14. **`CreatedNoteResponse` gains two required integer fields:
    `related_notes_linked` and `related_notes_skipped`.** Both transports
    return them for every created note — `0`/`0` on the raw `content` path,
    since there is no `export.related_notes` to verify. This lets the
    calling LLM report accurately what was linked instead of assuming its
    input was rendered verbatim, matching this codebase's existing
    "never claim a write succeeded unless the tool returned a successful
    result" principle. The two counts, not the skipped paths themselves, are
    returned — reporting the actual omitted paths was considered and set
    aside as a larger response-shape change than this issue calls for.

## Consequences

### Positive

- The rendered `## 関連ノート` section is always exactly what
  `related_notes_linked` says it is — there is no path by which an
  unverified string reaches the note body.
- `chat_export.py` remains filesystem-free, so `tests/test_chat_export.py`
  keeps testing it as a pure function with a fixed clock.
- No basename index, no Vault-wide walk, and no new limiter usage were
  needed for this feature.

### Negative

- `CreatedNoteResponse` grows two required fields, which is a real (if
  additive) OpenAPI and MCP structured-output change: any REST or MCP test
  asserting an exact response key set had to be updated
  (`tests/test_rest_regression.py`, `tests/test_mcp_tools.py`).
- The client gets per-request counts, not per-path feedback — it cannot
  tell *which* candidate was dropped without reading the created note back.
- `ChatExport`'s already-large argument schema (ADR-0005's Negative
  consequences already note this) gains one more field and one more
  behavioural rule ("call `search_notes` first, pass paths verbatim") for
  the calling model to follow correctly.

### Neutral

- A `related_notes` entry that fails verification for any reason —
  malformed, missing, duplicate, hazardous, or simply over the count the
  client happened to send before hitting the schema cap — is
  indistinguishable in the response from any other: it is one unit of
  `related_notes_skipped`. The reason is not reported.
- `tests/fixtures/vault/Knowledge/PC/GPU/RTX 5070.md` already contains a
  human-authored short-form link, `[[GPU比較]]`. This ADR's canonical format
  governs only links the Gateway itself renders; it does not attempt to
  rewrite or match existing hand-authored wikilink style elsewhere in the
  Vault.

## Alternatives considered

1. **`[[path|Title]]` with the alias drawn from the target's frontmatter
   `title`.** Rejected — decision 7's "never read target content" would be
   violated outright.
2. **`[[path|Title]]` with the alias drawn from the path's own basename.**
   Rejected — this does not require reading content, but it is still an
   unnecessary second display-name rule for something decision 1's
   full-path format already renders unambiguously; the earlier draft of
   this ADR justified rejecting aliases solely on the content-reading
   argument, which is not true of this variant, so this alternative is
   recorded to make clear the real reason is decision 1's "the link
   identity is the path; nothing else is needed," not merely "avoiding a
   content read."
3. **Short form `[[Note]]` when the basename is unique vault-wide.**
   Rejected on three independent grounds: it requires a full-Vault walk on
   a *write* path, reintroducing exactly the workload
   `runtime.vault_scan_limiter` exists to bound; it breaks determinism
   across time — the same `related_notes` input renders differently before
   and after a second same-named note is added elsewhere in the Vault,
   contradicting this formatter's byte-for-byte determinism guarantee; and
   proving uniqueness before rendering the short form is still "guessing a
   target that happens to be provably correct today," which is the
   behaviour issue #13 forbids outright.
4. **Deduplicating candidates by resolved inode (`st_dev`/`st_ino`) in
   addition to path.** Considered during review, then rejected — see
   decision 12. This Gateway's note identity is the vault-relative path
   everywhere else; inode-based dedup would silently drop a link a client
   legitimately selected from two hardlinked, equally valid paths, for a
   benefit (protection against a same-file duplicate on a case-insensitive
   filesystem) the deployment target (ext4/btrfs) does not need.
5. **Catching `OSError` broadly (`except (GatewayError, OSError)`)
   around the per-candidate resolution**, rather than the narrower
   `except (GatewayError, FileNotFoundError)`. Rejected — see decision 11.
   A permissions failure or storage fault is not "the candidate was
   invalid," and treating it as one would silently produce a note with
   fewer links and no signal anywhere that something is actually broken.
6. **Reporting the specific omitted paths in the response, not just
   counts.** Rejected for this change — a larger, separate response-shape
   decision than issue #13's acceptance criteria call for. `related_notes_linked`
   / `related_notes_skipped` are the minimum needed for the calling LLM to
   report accurately rather than assume.

## References

- Issue #13: "P2: Add automatic related-note linking to structured chat
  exports" (depends on #12)
- `app/services/related_notes.py`, `app/services/chat_export.py`
  (`is_renderable_wikilink_target`, `format_wikilink`,
  `_render_related_notes_section`), `app/models.py` (`ChatExport.related_notes`,
  `CreatedNoteResponse`), `app/application.py`
  (`GatewayApplication.create_chat_export_note`)
- `tests/test_related_notes.py`, `tests/test_chat_export.py`,
  `tests/test_application.py`, `tests/test_mcp_tools.py`,
  `tests/test_mcp_protocol.py`, `tests/test_inbox.py`,
  `tests/test_rest_regression.py`
- ADR-0005 (`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`)
  decision 8 — the deferral this ADR resolves — and decision 13 — the
  silent-truncation precedent decision 5 above follows
