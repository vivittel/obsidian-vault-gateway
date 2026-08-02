# ADR-0003: Permit `os.replace()` for inbox append, keep it banned for creation

- Status: Accepted
- Date: 2026-08-02
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0001-switch-primary-interface-to-mcp.md`](0001-switch-primary-interface-to-mcp.md)
  - [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) section 12
  - [`docs/PHASE1_PLAN.md`](../PHASE1_PLAN.md) section 4.4
  - [`docs/PHASE2_PLAN.md`](../PHASE2_PLAN.md) section 6

## Context

`docs/IMPLEMENTATION_PLAN.md` section 12 states, without qualification, that
`os.replace()` is not used, because it silently overwrites whatever already
sits at the destination — exactly what section 6.6's no-overwrite rule for
*creating* a note forbids. `docs/PHASE1_PLAN.md` section 4.4 repeats this in
bold. Phase 1's `create_inbox_note` honours it: the write path is temp file +
`os.link()` (atomic, fails with `FileExistsError` if the destination already
exists), never `os.replace()`.

Phase 2 adds `append_inbox_note`, which updates a note that is already known
to exist — `resolve_inbox_append_path` (`app/services/path_security.py`)
requires the target to resolve successfully before any write is attempted.
The operation this needs is different in kind from creation: not "write this
content without ever clobbering something already there" but "atomically
replace this file's content with its own prior content plus an appended
tail, without a reader ever observing a half-written file, and without
silently discarding a change that landed on it since it was read."
`os.link()` cannot do this — a link only ever adds a new name for an
existing inode, it cannot swap out the content behind a name that must keep
its identity. `os.replace()` is the correct primitive: a single atomic
`rename(2)` that swaps in a new inode's content at an existing path.

Read literally, section 12's "does not use `os.replace()`" would block this
implementation entirely. That was written to describe *creation only* —
section 6.6, which section 12 is protecting, is itself scoped to "新規作成"
(new-note creation). Applying the same sentence to append would misread its
own stated purpose.

## Decision

`os.replace()` is used in exactly one place: `app/services/inbox_service.py`'s
`append_inbox_note`, to atomically swap a hidden temp file (containing the
target's prior bytes plus the appended tail) onto the target's existing
path.

Everywhere else the ban stands exactly as written:

- `create_inbox_note` continues to use `os.link()` and never `os.replace()`.
  No code path can create a new note by way of `os.replace()`.
- `append_inbox_note` only ever replaces a path that
  `resolve_inbox_append_path` has already confirmed exists, is a regular
  file, is not a symlink, and sits directly inside the inbox
  (`VAULT_INBOX_RELATIVE_PATH`) — never a path that would otherwise be a
  creation.

The no-overwrite invariant these two rules jointly protect is restated
precisely as: **a new note can never be created by overwriting an existing
one.** That invariant is unchanged by this ADR. What changes is that an
*existing* note directly inside the inbox may now have its own prior content
atomically replaced with that same content plus an appended tail — a
narrower, additive operation, not a relaxation of the no-overwrite rule
itself.

Before the replace, `append_inbox_note` re-reads the target's identity
(device, inode, mtime, size via `os.lstat`) and compares it against what was
observed when the append began; a mismatch — including the target having
become a symlink — raises `NoteModifiedError` rather than replacing anything
(section 6, "対象が検証時から変更されていないことを確認"). This does not
close every TOCTOU window (see Consequences), but it turns the common case —
a concurrent edit — into a rejected request rather than a silently discarded
change.

## Consequences

### Positive

- `append_inbox_note` gets a genuinely atomic single-syscall replace: readers
  (Obsidian, LiveSync, a concurrent `read_note` call) never observe a
  partially written file, matching the same durability guarantee
  `create_inbox_note` already has via `os.link()`.
- The no-overwrite invariant for *creation* is untouched — `os.link()`
  remains the only way a new file name comes into existence in the inbox,
  and it still fails closed (`FileExistsError`) rather than overwriting.
- The distinction is enforced by two different service functions using two
  different primitives, not by a runtime flag on one function — there is no
  code path where the same call could go either way depending on input.

### Negative

- `os.replace()` does not preserve the destination's ownership. The kernel's
  `rename(2)` swaps in the *source* inode wholesale, and the temp file
  `append_inbox_note` builds is created by the gateway's own container
  process — so after a successful append, the note's owning UID/GID become
  whatever the container process runs as, not whatever they were before
  (typically the UID/GID LiveSync or Obsidian created the file with on the
  host side, if that differs from the container's). File **mode** is
  preserved explicitly (`os.fchmod` on the temp file before the replace,
  copied from the pre-append `stat()` of the target), but ownership is not,
  because `cap_drop: ALL` in `compose.yaml` removes `CAP_CHOWN`, so
  `os.fchown()` to restore the original owner is not available to the
  container. If the container's UID/GID differs from the host-side writer's,
  an appended note's ownership silently changes on every append. This is a
  known, unresolved side effect of using `os.replace()` at all — not
  something a different implementation of append could avoid while staying
  atomic and single-syscall, short of running the container as the exact
  UID/GID the host-side vault directory already uses.
- The `os.replace()` ban text in `docs/IMPLEMENTATION_PLAN.md` section 12 and
  `docs/PHASE1_PLAN.md` section 4.4 now needs a forward reference to this ADR
  so a future reader does not conclude append is a plan violation.
- A residual TOCTOU window remains between the identity re-check and the
  `os.replace()` call itself — both are needed back-to-back in userspace,
  and nothing makes that gap zero-width. In practice `os.replace()`'s own
  `rename(2)` semantics limit the damage even if something changes the
  target's directory entry in that gap: `rename()` replaces whatever
  directory entry currently exists at the destination path — including
  swapping out a symlink placed there in the interim — it never follows a
  destination-side symlink to write through it. So the worst case this gap
  allows is losing a very-narrowly-timed concurrent write, which
  `NoteModifiedError`'s check already narrows to a small window, not an
  arbitrary-file overwrite.

### Neutral

- The append lock (`app/services/inbox_service.py`'s
  `.append.lock`, opened with `O_NOFOLLOW` and confirmed to be a regular
  file) only serialises append requests reaching this gateway process. It
  says nothing about a host-side tool — LiveSync CLI, or Obsidian itself —
  writing the same file at the same time; that class of conflict is instead
  caught (not prevented) by the identity re-check described above.
- This ADR does not reopen ADR-0001's "no delete endpoint / no move endpoint
  / no rename endpoint / no arbitrary-path write" invariants. Append cannot
  create a file, cannot target a path outside the inbox, and cannot target a
  subdirectory of it — it can only extend one already-existing file directly
  inside `00_Inbox/ChatGPT`.

## Alternatives considered

### 1. Keep append inside the `os.link()` pattern (write a new file, link it in)

Rejected. `os.link()` only ever attaches a new name to an inode; it cannot
make an *existing* name point at different content. Simulating "replace this
file's content" with link semantics would require unlinking the original
name and relinking a new inode onto it — which is exactly what
`os.replace()` does atomically in one syscall, except done by hand across
two syscalls with a window between them where the name would not exist at
all. That is strictly worse, not a way to avoid `os.replace()`.

### 2. Lock at the file level (a lock file per target note) instead of one inbox-wide lock

Rejected. Locking on the target's own path stops working the moment
`os.replace()` swaps that path's inode out from under the lock (the next
append would be locking a different inode than the one it's replacing, or
locking a stale one). A separate, fixed-name lock file per target would
either need to live outside the vault (extra state to manage, and this
gateway has no writable location outside the inbox) or inside it (dotfiles
accumulating per note, one per ever-appended-to note, never cleaned up). A
single inbox-wide lock file serialises append requests reaching this
process at the cost of not allowing concurrent appends to *different* notes
— acceptable, since appends are expected to be occasional, not a
high-throughput operation.

### 3. Skip the identity re-check and rely on the lock alone

Rejected. The lock only covers concurrent requests to this same gateway
process. LiveSync CLI and Obsidian itself write the same vault directory
from the host side, entirely outside any lock this container holds. Without
the re-check, a host-side edit landing between this request's read and its
replace would be silently discarded — exactly the "内容を失わない" (do not
lose content) requirement PHASE2_PLAN section 10 lists as a required test.
The re-check cannot prevent that race, but it converts it into a detected,
rejected request (`NoteModifiedError`, 409) instead of a silent loss.

## References

- [`rename(2)` — Linux manual page](https://man7.org/linux/man-pages/man2/rename.2.html)
- [Python `os.replace()` documentation](https://docs.python.org/3/library/os.html#os.replace)
