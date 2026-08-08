# ADR-0007: Scoped duplicate-note detection before structured chat export

- Status: Accepted
- Date: 2026-08-08
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — the `create_inbox_note` this feature runs *before*, unchanged by it
  - [`docs/adr/0006-verified-related-note-wikilinks.md`](0006-verified-related-note-wikilinks.md)
    — the closest precedent: a client-supplied candidate list, re-verified
    against the Vault, that must never block the write it precedes
  - [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) sections 9 and 12
  - [`README.md`](../../README.md) "Tools"
  - GitHub issue #14 ("P3: Add duplicate-note detection before structured
    chat export"), depends on #12 and #13

## Context

Repeated conversations about the same topic can create multiple near-duplicate
notes in `00_Inbox/ChatGPT`. Issue #14 asks for a decision flow — new note,
append to an existing one, or cancel — that fires only when a credible
candidate exists, so a normal export with no duplicate never gets an extra
prompt.

The issue's initial scope is four signals inside `00_Inbox/ChatGPT` only:
exact title, normalized title, `project` metadata, and a compact keyword set;
exact-content fingerprinting and semantic search are explicitly deferred. Its
safety constraints are unambiguous: never silently append or merge, never
guess among ambiguous candidates, never modify an existing note without
explicit approval, and — the one most relevant to how this ADR is
structured — **similarity is advisory, not write authorization.**

That last constraint is what decision 1 below is really about: if detection
lived inside `create_inbox_note` itself, "advisory" would be something the
implementation has to keep proving true forever. Making it a separate,
read-only tool makes it true by construction.

## Decision

1. **A new, independent, read-only MCP tool — `find_duplicate_candidates` —
   not a check embedded in `create_inbox_note`.** `app/services/
   duplicate_notes.py` is a new service; `GatewayApplication.
   find_duplicate_candidates` is a new application-layer method;
   `create_inbox_note`/`append_inbox_note` are unmodified except for their
   own documentation (decision 11). This is what makes "the Gateway never
   infers write approval from similarity" structural rather than a rule the
   write path has to remember: `create_inbox_note` has no code path that
   even reads this module's output. It is also what keeps issue #14's
   "search failure and no-match cases do not block normal export" true by
   construction — a tool that never runs cannot block anything downstream of
   it, and a client that never calls it (or ignores its result) still writes
   normally.

2. **Two separate title signals — `exact_title` and `normalized_title` —
   never both reported for the same match.** Issue #14 asks for these as
   distinct initial-scope signals, and `search_service.fold()` (NFKC +
   casefold) — this codebase's one existing title-normalisation function —
   is already a *normalized* comparison, not an exact one; reusing it
   unchanged for "exact" would have silently collapsed the two into one
   signal. `exact_title_key` instead delegates to `app/services/
   chat_export.one_line` (renamed from `_one_line` and made public for this
   reuse) — the exact function a structured export's own title goes through
   when written — so "exact" means "matches what is actually on disk,"
   never a second, independently-drifting normalisation of the same idea.
   `normalized_title_key` is `fold()` plus whitespace-collapse, the looser
   comparison issue #14 also asks for.

   The two are mutually exclusive per candidate: when `exact_title_key`
   matches, `normalized_title_key` is never separately checked, so a
   candidate's `matched_signals` never contains both and the internal score
   never double-counts the same title match as two signals. Title precedence
   is `exact_title > normalized_title > none`.

3. **No punctuation-stripped "loose" title key.** An early design considered
   stripping all non-alphanumeric characters for a third, looser tier.
   Rejected: it collapses `"C++"` and `"C"`, `"C#"` and `"C"`, `"Node.js"`
   and `"Nodejs"` onto the same key — genuinely different titles issue #14
   never asked to be conflated. `normalized_title_key` folds case and width
   and collapses whitespace; it does not touch punctuation.

4. **The de-duplication sequence suffix (`"-2"`, `"-3"`, ...) is stripped only
   from a *filename-fallback* title, never from a real frontmatter `title`.**
   `app/services/filenames.py`'s `note_file_name` appends this suffix to the
   *file name* when `create_inbox_note` de-duplicates — never to the
   structured export's own `title`, which is preserved verbatim in
   frontmatter/H1. So when a candidate has a frontmatter title, comparison
   uses it exactly as written, full stop — a genuine title like `"Issue-2"`
   must never be rounded down to `"Issue"`. Only when a note has **no**
   frontmatter title (the file-name stem is the fallback display title) does
   `find_duplicate_candidates` additionally compare a suffix-stripped
   variant of that stem — as an added comparison, not a replacement, so the
   un-stripped stem is still checked too.

5. **`project` metadata: `None` never matches `None`.** Issue #14 says "same
   `project` metadata *when available*" — not "both notes happen to omit
   it." `project_match` therefore requires both `project_key(input)` and
   `project_key(candidate)` to be non-`None` and equal
   (`project_key` = NFKC + casefold + whitespace-collapse; blank collapses to
   `None`). Letting two absent values count as a match would have turned
   "same project" into "any two notes that both skipped this optional
   field" — in a vault where most notes have no `project` at all, that
   silently upgrades a bare `normalized_title` match to `high` for the
   common case, exactly the over-eager behaviour issue #14's "advisory, not
   authorization" framing is trying to avoid.

6. **A `project` match with no other signal is never reported.** Without
   this, every note that happens to share a `project` would become a
   "duplicate candidate" regardless of topic — the opposite of a *scoped*
   check. `project_match` only raises confidence when paired with a title
   or keyword signal (decision 8's confidence table).

7. **Keywords need a stricter bar alone than combined with a project
   match.** Combined with `project_match`, two or more matched keywords is
   enough for `medium`. Alone — no title signal, no project match — the bar
   is `max(2, ceil(total_keywords / 2))`: keywords are this module's weakest
   signal (they check only a candidate's title and tags, never its body —
   decision 12), so on their own they need to be numerous relative to what
   was asked for, not merely present.

8. **Confidence levels and the `recommendation` they drive:**

   | Confidence | Condition |
   |---|---|
   | `high` | `exact_title`, or `normalized_title` **and** `project_match` |
   | `medium` | `normalized_title` alone, or `project_match` **and** ≥2 matched keywords |
   | `low` | keywords alone, meeting decision 7's threshold |
   | *(dropped)* | `project_match` with fewer than 2 matched keywords (0 or 1 — "alone" is the 0 case), or keywords below threshold with no other signal |

   | `recommendation` | Condition (over the *full*, pre-`limit` candidate set) |
   |---|---|
   | `create` | zero `high`/`medium` candidates — **`low`-only never blocks this** |
   | `confirm` | exactly one `high`, and no `medium` |
   | `choose` | more than one `high`/`medium` combined, or `medium`-only (even a single one) |

   The `low`-only → `create` rule is deliberate: issue #14 explicitly warns
   against turning every export into a confirmation-heavy workflow, and
   `low` candidates are exactly the ones this module is least confident
   about. A client can still show them for context — they are present in
   the response — but the Gateway's own recommendation never asks for a
   decision on keyword overlap alone.

9. **`recommendation`/`candidate_count` are computed from every matching
   candidate, before `limit` slices the list; `truncated` is derived, not
   stored.** Deciding these *after* slicing would let a small `limit` hide a
   second `high` candidate and silently understate real ambiguity — a
   `limit=1` request with two `high` matches would otherwise report
   `confirm` (one candidate returned) instead of `choose` (two exist). The
   service-level result (`app/services/duplicate_notes.py`) carries
   `candidate_count` (the pre-slice total); `truncated` is computed once, at
   the application layer, as `candidate_count > len(candidates)` — a boolean
   view of the same fact, not new state to keep in sync.

10. **The internal `score` never appears in any response.** Sorting needs a
    total order (`(-score, -mtime, path)`, the same shape `search_service`
    already uses), but the weights themselves (400 / 300 / 100 / 20) are
    this module's own tuning, not a contract. Publishing `score` would make
    that tuning a de facto API surface — harder to adjust later, and an
    invitation for a client to threshold on it directly instead of using
    `confidence`/`recommendation`, which are the intended, stable outputs.
    `app/models.py`'s `DuplicateCandidate` simply has no `score` field.

11. **A candidate path is validated with `path_security.
    normalise_relative_path` before it is ever offered as a candidate — not
    `resolve_inbox_append_path`.** `path_security.iter_directory` only
    excludes hidden entries, symlinks, and non-`.md` files; it does not
    reject every name `append_inbox_note` itself would reject on the wire
    (e.g. a literal backslash in a file name, legal on this filesystem but
    rejected by `normalise_relative_path`'s own syntax check, or a name
    whose percent-decoded form contains a traversal sequence). Without this
    check, `find_duplicate_candidates` could hand back a `path` that
    `append_inbox_note` would then refuse — a candidate that looks safe to
    act on but is not. Every excluded name is counted in `skipped_count`,
    never silently dropped. `resolve_inbox_append_path` itself is
    deliberately not reused here: it targets the *write* mount
    (`inbox_root`, a different bind mount than the read-only `read_root` this
    scan already runs against) and additionally re-`stat()`s the file — both
    unnecessary, since the mismatch being guarded against here is syntax
    only, and the prefix/depth check `resolve_inbox_append_path` also
    performs is already structurally satisfied by scanning `00_Inbox/ChatGPT`
    non-recursively in the first place.

12. **Only frontmatter is read — via the existing bounded, streaming
    `markdown_parser.read_frontmatter_text`, never a note's body.** The
    signals issue #14 specifies (title, `project`, keywords-against-
    title/tags) never need a note's body, and skipping it is not just an
    optimisation: it is what makes "never expose note contents" true for
    this feature without a separate redaction step, and it reuses exactly
    the read path `summarise_vault` already established rather than a new
    one. A new `markdown_parser.parse_frontmatter_metadata` (a thin,
    published wrapper around the existing `_split_frontmatter`) gives this
    module the raw metadata mapping; `parse_frontmatter_tags` is rewired
    through it so both callers keep degrading identically on malformed YAML.

13. **A directory-level scan failure (`os.scandir` itself raising) is
    distinguished from "scanned and found nothing," and raised as an
    `InternalError` rather than an empty result.** `path_security.
    iter_directory` previously had no way to report this — an `OSError`
    from `scandir` simply ended the iterator, indistinguishable from a
    directory with no matching children. `WalkStats` (already used by
    `iter_vault_notes`) gains a `scan_failed` flag that only `iter_directory`
    sets; `iter_directory` gains an optional `stats` parameter so existing
    callers (`get_vault_tree`) that omit it see no behavioural change at
    all. Collapsing "could not scan" into "no duplicates" would have made a
    transient permissions problem look identical to a clean bill of health —
    exactly the confusion issue #14's decision flow names explicitly ("no
    credible candidate" vs. "search failure"). An individual unreadable note
    (a `stat()` failure, or `read_frontmatter_text` raising `OSError`) is not
    escalated the same way — that note is simply excluded and counted in
    `skipped_count`, matching `summarise_vault`'s existing degradation for
    the same class of per-note failure.

14. **The Gateway does not gate `create_inbox_note`/`append_inbox_note` on
    this tool's output; the decision-flow gating is a documented
    client-workflow contract, not an invariant this module or the write
    tools enforce.** This is the direct reading of issue #14's "similarity is
    advisory, not write authorization" — enforcing it in the Gateway would
    mean the Gateway deciding, from a similarity score, that a write should
    be blocked, which is exactly the inference the issue forbids. Instead,
    `app/mcp_server.py`'s `SERVER_INSTRUCTIONS` and the `create_inbox_note`/
    `append_inbox_note`/`find_duplicate_candidates` tool descriptions
    document the same contract redundantly (whichever tool a client reads
    first, it reaches the same rule): on `confirm`/`choose`, ask the user to
    choose new/append/cancel *before* calling either write tool, and act on
    that choice; on `create` (including when only `low` candidates exist),
    proceed to `create_inbox_note` without asking; if
    `find_duplicate_candidates` itself fails and the user has not asked for
    strict duplicate checking, proceed with `create_inbox_note` as normal.
    `tests/test_mcp_tools.py` proves both halves separately: a `confirm`/
    `choose` result does not stop `create_inbox_note` from *technically*
    succeeding (the Gateway side), and the tool descriptions/instructions
    actually state the contract (the documentation side) — a real MCP client
    cannot be tested for compliance from inside this repository, only
    pointed at a clearly documented rule.

15. **Exact-content fingerprinting is deferred, not implemented.** Issue #14
    lists it as optional initial scope; this ADR narrows the first
    implementation to the four non-optional signals (exact title, normalized
    title, project, keywords) and defers fingerprinting to a later
    iteration. GitHub issue #14's body and acceptance criteria have been
    updated to match (removing the "exact fingerprints" acceptance-criteria
    line and the initial-scope bullet) rather than left to silently
    contradict this ADR.

16. **MCP receives `keywords` as a JSON array; REST receives it as a
    comma-separated query string, split into a list by the router before
    calling the shared application method.** This mirrors `/search`'s
    existing `tags` parameter shape exactly — REST query strings have no
    native array type, MCP's JSON-RPC does — and keeps the actual
    fold/dedupe logic (`duplicate_notes.normalise_keywords`) running exactly
    once, in the service layer, for both transports. Neither transport
    reimplements matching; only the wire shape differs, and only at the
    router's edge.

## Consequences

### Positive

- `create_inbox_note`/`append_inbox_note`'s own write path is completely
  unmodified — every existing invariant about atomic, non-overwriting
  creation and inbox-only append continues to hold with no new interaction
  to reason about.
- A client that never calls `find_duplicate_candidates`, or calls it and
  ignores the result, gets exactly today's behaviour. Nothing about export
  changes unless a client opts into reading this tool's output.
- The `scan_failed`/`skipped_count` distinction (decision 13) is reusable:
  any future feature that walks a single directory rather than the whole
  Vault inherits the same "could not scan" vs. "nothing found" clarity for
  free.

### Negative

- The client-workflow contract (decision 14) is enforced nowhere in code —
  only in tool descriptions and server instructions. A client that ignores
  them can still append/create around a `confirm`/`choose` recommendation;
  this ADR accepts that gap because enforcing it in the Gateway would
  require inferring write approval from similarity, which issue #14
  forbids outright.
- A second read path into `00_Inbox/ChatGPT` (alongside `get_vault_tree`/
  `search_notes`, which can already list/search it) adds one more place a
  future contributor must remember the "frontmatter only, never body" rule
  for.
- Eight MCP tools instead of seven is one more entry every tool-set
  assertion across the test suite has to enumerate exactly
  (`tests/test_mcp_tools.py`, `tests/test_mcp_protocol.py`).

### Neutral

- `matched_keywords` in the response preserves the client's original
  casing/width (dedup is decided by the folded key, the returned strings are
  not folded) — a minor asymmetry with `matched_signals`/`confidence`, which
  are Gateway-computed labels, not echoes of client input.
- `find_duplicate_candidates` and the Phase 4-planned `find_duplicate_titles`
  (`docs/IMPLEMENTATION_PLAN.md` section 9) are unrelated features that
  happen to share a word: this one is a pre-write, `00_Inbox/ChatGPT`-only
  check; that one is a whole-Vault audit tool with no relationship to
  export timing. Nothing here reuses or blocks on the other.

## Alternatives considered

1. **A duplicate-check parameter on `create_inbox_note` itself (e.g.
   `check_duplicates: bool`).** Rejected — see decision 1. It would make
   "advisory, not authorization" a rule the write path has to keep honouring
   correctly forever, rather than true because the write path never sees
   this module's output at all.
2. **Reusing `search_service.fold()` unchanged for "exact" title matching.**
   Rejected — see decision 2. `fold()` is NFKC + casefold, which is exactly
   the *normalized* comparison issue #14 also asks for as a separate,
   looser signal; using it for both would have collapsed two distinct
   signals into one.
3. **A punctuation-stripped "loose" title tier as a third confidence
   signal.** Rejected — see decision 3. It merges titles that differ in
   meaningful ways (`"C++"`/`"C"`).
4. **Treating `project: None` on both sides as a match.** Rejected — see
   decision 5. In a vault where most notes omit `project`, this would
   silently upgrade the common case to `high` confidence.
5. **Deciding `recommendation` after applying `limit`.** Rejected — see
   decision 9. A small `limit` would then hide real ambiguity by reporting
   `confirm` when a second `high` candidate exists but was not returned.
6. **Exposing the internal `score` in the response.** Rejected — see
   decision 10. It would turn an internal, freely-tunable weighting into a
   de facto API contract.
7. **Reusing `path_security.resolve_inbox_append_path` to validate candidate
   paths.** Rejected — see decision 11. It targets the write mount and
   performs a redundant `stat()`; the gap being closed here is syntax-only,
   and the prefix/depth guarantee it also provides is already satisfied by
   this module's non-recursive, `00_Inbox/ChatGPT`-only scan.
8. **Implementing exact-content fingerprinting now, since issue #14 lists
   it.** Rejected for this iteration — see decision 15. It is explicitly
   optional in the issue, and deferring it keeps the first implementation
   scoped to the four signals with clear, testable thresholds.
9. **Enforcing the new/append/cancel decision inside the Gateway (e.g.
   requiring a `duplicate_check_token` from a prior `find_duplicate_
   candidates` call before `create_inbox_note` will proceed).** Rejected —
   see decision 14. This would be the Gateway inferring write authorization
   from similarity, which is exactly what issue #14's safety constraints
   forbid; it would also block a client that has its own, out-of-band reason
   to know a write is not a duplicate.

## References

- Issue #14: "P3: Add duplicate-note detection before structured chat
  export" (depends on #12, #13)
- `app/services/duplicate_notes.py`, `app/services/path_security.py`
  (`WalkStats`, `iter_directory`), `app/services/markdown_parser.py`
  (`parse_frontmatter_metadata`), `app/services/chat_export.py` (`one_line`),
  `app/models.py` (`DuplicateCandidate`, `DuplicateCandidatesResponse`),
  `app/application.py` (`GatewayApplication.find_duplicate_candidates`),
  `app/mcp_server.py` (`find_duplicate_candidates` tool,
  `SERVER_INSTRUCTIONS`), `app/routers/inbox.py`
  (`GET /api/v1/inbox/duplicate-candidates`)
- `tests/test_duplicate_notes.py`, `tests/test_path_security.py`,
  `tests/test_application.py`, `tests/test_mcp_tools.py`,
  `tests/test_mcp_protocol.py`, `tests/test_inbox.py`,
  `tests/test_vault_scan_concurrency.py`
- ADR-0006 (`docs/adr/0006-verified-related-note-wikilinks.md`) — the
  precedent this ADR follows most closely: a client-supplied candidate list,
  re-verified against the Vault, whose failures must never block the write
  it precedes
