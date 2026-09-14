# Entries

Entries provides versioned notes and documents through shared HTTP and MCP
operations. Each entry has a stable UUID, an optional unique lowercase slug,
an immutable kind, current state, and an ordered revision history. The
implementation uses the central service's authenticated participant and team.

## Storage and integrity

`entries/models.py` defines `Entry`, `Revision`, and `RevisionReference`.
Entry creation records revision 1. Updates lock the entry row, validate the
caller's expected revision, and save the current state, new snapshot, and
relationship references in one transaction. Each snapshot records its author,
creation time, base revision, and optional restoration source.

The pattern derives from TJAI's `Entry`/`EntryVersion` models, transactional
content services, and search implementation at revision
`653a19639d47332f89983066ebbb10f0052dcc5f`. TeamComms adds explicit revision
preconditions, authenticated authorship, and foreign keys for fixed references.
Attribution is recorded in [NOTICE](../NOTICE).

The generic interface accepts kinds `note` and `document`. Both use the state
schema below. Dialog, communications, and task kinds require their component
interfaces and integrity rules as those components are implemented.

| State field | Contract |
|---|---|
| `title` | Text, up to 240 characters |
| `content` | Text, up to 40,000 characters; whitespace and final newlines are preserved |
| `metadata` | JSON object; nested values are permitted |
| `tags` | Up to 50 distinct strings, each 1–80 characters |
| `status` | `active` or `archived` |
| `priority` | Positive integer or null |
| `relations` | Up to 50 distinct typed references |

The encoded state is limited to 48,000 bytes. Unknown fields are rejected.
Updates name the fields to change; all others remain intact. A supplied metadata
object, tag list, or relationship list replaces that field in full. The surgical
operations below preserve unnamed metadata keys and individual relationships.

A stale update returns HTTP 409 with the expected and current revision numbers,
or an MCP tool error. It creates no partial state or history. Restoration copies
an earlier snapshot into a new, attributable revision after the same precondition
check. Earlier snapshots remain unchanged.

PostgreSQL rejects UPDATE and DELETE on revision rows and their saved reference
rows. Unique constraints serialize revision numbers and entry slugs. Foreign
keys preserve referenced entries and revisions; a composite foreign key ensures
that a fixed target revision belongs to the named target entry.

## Relationships

A relationship contains `entry_id`, `relation` (default `related`), and optional
`revision`. Omitting the revision follows the target's current state. Supplying
it pins a specific version, for example:

```json
{"entry_id": "8f0c35ac-f7dd-44af-9f61-15fa7bc55e01", "revision": 3, "relation": "reviews"}
```

Targets must exist and be accessible within the same team. The source's
relationship list is part of its versioned state. Subsequent edits preserve it
unless explicitly changed; historical versions retain their original links.
Search by relationship uses the source's current revision.

The [browser editor](interface.md) uses these same APIs, with local recovery,
explicit conflict reconciliation and version comparison/restoration.

## Interfaces

`entries:read` permits content, revision, and search reads. `entries:write`
permits creation, updates, and restoration. Administrators can grant these
scopes to member credentials through the central service. Existing credentials
retain their scopes after upgrade; installation operators can use the local
`issue-token` command to provision credentials with newly introduced scopes.

| HTTP | MCP tool | Input |
|---|---|---|
| `POST /api/entries` | `create_entry` | `state`, optional `kind` and `slug` |
| `GET /api/entries/read` | `get_entry` | `entry_id`, optional `revision`, `content_offset`, `max_content_length` |
| `POST /api/entries/update` | `update_entry` | `entry_id`, `expected_revision`, `changes` |
| `POST /api/entries/restore` | `restore_entry` | `entry_id`, `expected_revision`, source `revision` |
| `GET /api/entries/revisions` | `get_entry_revisions` | `entry_id`, `limit`, `offset` |
| `GET /api/entries` or `POST /api/entries/search` | `search_entries` | Search filters, `limit`, `offset` |

MCP creation, updates, restoration, and search take their fields under `request`.
`get_entry` and `get_entry_revisions` take top-level arguments. HTTP POST bodies
are JSON objects. GET queries support scalar filters; structured metadata filters
use POST search or MCP.

Mutation results contain identifiers, revision numbers, and authorship without
repeating the document. Reads default to 10,000 content characters and return
`content_length` and `next_content_offset`; callers pin the returned revision
when continuing a multi-part read. Revision lists return metadata without content.
Search and revision pages default to 25 records, allow at most 100, and return
`next_offset` when more records remain.

## Surgical edits and atomic bulk plans

All editing tools use authenticated Entries permissions, preserve revision
history, and accept fields under `request` in MCP. HTTP bodies take those fields
directly. The service's 64 KiB incoming request bound also applies to bulk plans.

| HTTP | MCP tool | Contract |
|---|---|---|
| `POST /api/entries/target` | `read_entry_target` | Entry/revision plus range or `section: {heading, level?, occurrence?}` |
| `POST /api/entries/edit` | `edit_entry` | `operation_id`, `entry_id`, `expected_revision`, `edits` |
| `POST /api/entries/edits/preview` | `preview_entry_edits` | `operation_id`, explicit `entries` list |
| `POST /api/entries/edits/apply` | `apply_entry_edits` | Saved `operation_id` |
| `GET /api/entries/edits/read` | `get_entry_edit` | `operation_id`, optional `entry_id`, `diff_offset`, `max_diff_chars` |

Each target in `entries` has `entry_id`, `expected_revision`, and 1–50 ordered
`edits`. A plan selects 1–20 distinct entries; larger or query-based application
is not supported. Resolve search results to explicit IDs/revisions before
previewing. Preview fixes the selection and complete proposed states without
writing Entry revisions. Each operation validates the resulting State bounds.

| `op` | Additional fields and semantics |
|---|---|
| `replace` | Nonempty `old_text`, `new_text`; empty replacement deletes |
| `insert` | Nonempty `anchor`, `text`, `position: before` or `after` |
| `section` | Exact `heading`, `content`, optional heading `level` and 1-based `occurrence`; replaces body, preserving heading |
| `append` | `content`, optional `separator` (default two newlines; used only between nonempty values) |
| `set_content` | Explicit whole `content` replacement |
| `fields` | `changes` naming title, tags, status or priority |
| `metadata` | `set` object and `remove` key list; unnamed keys and nested values remain intact |
| `relations` | `add` and `remove` lists of exact typed references; preserves others |

Replace/insert require a unique literal, nonoverlapping match by default. An
explicit 1-based `occurrence` selects one, or `all_matches: true` selects all;
the two cannot be combined. Missing or ambiguous targets return HTTP 409/MCP
error. Sections recognize ATX (`#`) headings outside backtick/tilde fences,
include subsections until the next heading of equal or lower level, and reject
ambiguous headings. Setext headings are not section selectors. Provide desired
blank-line padding explicitly; a nonempty replacement before a following heading
must end in a newline so that heading retains its structure.

`read_entry_target` uses `content_offset` relative to the selected section body
(or whole document). Its result includes absolute `target_start`, `target_end`
and `content_offset`; `next_content_offset` is relative to that same target.
Pin the returned revision and repeat the selector when continuing. Reads retain
the document's total `content_length`; content defaults to 10,000 characters.

For example, one atomic edit replaces a section and adds a metadata key:

```json
{
  "operation_id": "6de715cb-96a2-4416-8c25-bf63efb9c94c",
  "entry_id": "8f0c35ac-f7dd-44af-9f61-15fa7bc55e01",
  "expected_revision": 3,
  "edits": [
    {"op": "section", "heading": "Plan", "content": "\nReviewed plan.\n\n"},
    {"op": "metadata", "set": {"reviewed": true}}
  ]
}
```

Application locks all selected entries in UUID order and checks every revision
and relationship before writing. It is all-or-none: each changed entry receives
one revision; unchanged entries receive none. A conflict produces a durable
`status: conflicted` result, with `conflicted` or `not_applied` per entry and no
Entry writes. This is an HTTP 200 operation **outcome**, not successful editing;
clients must inspect `status` and `result.entries`. Preview validation errors
return ordinary HTTP errors and do not create a plan. Direct `edit_entry` uses
the same prepare/apply path, so it can also return a conflicted outcome if a
writer changes the document between preparation and application.

Reuse the exact UUID and request after a lost response. Plans belong to their
authenticated author and team; another author cannot inspect or apply them.
PostgreSQL preserves plan inputs and terminal outcomes. A retry returns the saved
outcome, even after later document edits. To reconcile a conflict, read the latest
revisions, review a new preview, and use a fresh operation UUID; no implicit
rebase occurs. Permissions are checked again on application and retry.

Previews contain up to 1,000 diff characters per selected entry. Retrieve a
complete diff with `get_entry_edit`, selecting its entry and following offsets;
pages default to 8,000 and allow at most 20,000 characters. Diffs include content
and other state fields and identify missing final newlines. Plans and receipts
are retained with Entries; this release does not prune them.

## Search

Current title, content, and slug are indexed using PostgreSQL full-text search
with English stemming. Queries support quoted phrases, exclusions, and `OR`.
Slashes become spaces in both indexed text and queries. Punctuation-only queries
use literal substring matching. Database triggers maintain the GIN search index
when state changes.

Filters include kind, slug, tag, status, JSON metadata containment, target entry
and relation type, and modification time. Times require an explicit timezone;
`modified_since` is inclusive and `modified_before` exclusive. Results sort by
modification time descending, then UUID. Offset pagination is bounded but can
shift when concurrent edits change the ordering.

## Retention and attachments

Entries and all revision snapshots are retained indefinitely. Archiving changes
status and retains history. There is no automatic pruning or hard-delete API;
fixed review and task references therefore remain retrievable.

Attachment bytes are outside the initial Entries API. The storage contract for
binary integration uses an installation-managed object store, stable object IDs,
content hashes, and versioned descriptors in entry metadata. Object retention
must cover every retained revision that references the bytes. Backups must
include both PostgreSQL and the associated object store. The current API does
not upload, fetch, validate, or delete attachment bytes.

## Verification

Run the [isolated PostgreSQL suite](../tests/readme.md). Entries checks cover
HTTP/MCP roundtrips, exact text preservation, stale updates, competing writers,
revision attribution, restoration, pinned references, database immutability,
search/index maintenance, field bounds, and credential scopes.
