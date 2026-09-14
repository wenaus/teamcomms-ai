# Pouch

Each team has one canonical shared working document. Open **Pouch** in the
TeamComms navigation, or `<public-prefix>/pouch`. On SWF the direct address is
`https://epic-devcloud.org/prod/teamcomms/pouch`; it uses the existing devcloud
login and Entries editor. Other notes and documents remain ordinary Entries.

## Initialization and integrity

Install `teamcomms.pouch.apps.PouchConfig` alongside Entries and apply the normal
host migrations. `teamcomms_entries` adds `0003_editplan` and
`0004_editplan_integrity`; `teamcomms_pouch` adds `0001_initial` and
`0002_binding_integrity`, the latter depending on Entries 0004. Migration creates
schema only: it does not initialize a document or import existing TJAI content.

An authenticated participant with `entries:read` and `entries:write` explicitly
initializes the Pouch using the browser button, MCP `initialize_pouch`, or
`POST /api/pouch/initialize` with `{}`. It locks the team row and atomically creates
an empty document titled Pouch and its canonical binding. Concurrent/retried
calls return the same entry; `created` distinguishes the first initialization.
An existing unrelated document is never adopted based on its title or slug.

GET requests never create or modify data. Before initialization, Pouch reads
return 404 and the browser offers creation to writers. The database prevents
binding deletion/replacement, cross-team/non-document binding, and later changes
to the bound Entry's team or kind. Generic Entry operations cannot change the
binding. Archiving the Entry preserves its canonical route and history; there is
no hard-delete or rebind API.

## Editing, references and review

Human and AI edits use the [Entries operations](entries.md) and expected revision
checks. The browser retains drafts and exposes conflict comparison. AI tools can
read a section or range, make exact edits, and preview/apply atomic bulk changes.
Each edit preserves the document UUID and earlier revisions. Relations and
metadata can associate the Pouch with topics, reviews or work.

`/pouch` follows the evolving current document. `/pouch?revision=N` opens a saved
revision read-only, with a link back to the current editor. The editor's **Link to
revision** and history links pin the revision; structured Entry relations can pin
the same `entry_id` and revision number. Restoration copies an older state into
a new revision and never rewrites those references.

Each revision also supplies a durable brief change notice through
`GET /api/pouch/changes` / `get_pouch_changes`: author, time, revision, and links.
These notices are derived directly from committed revision history and paginated
with the Entries limits (default 25, maximum 100). This release does not
automatically broadcast Pouch edits to Comms or Mattermost. To share a change,
publish an explicit brief Comms message with the fixed revision reference and
the intended audience, using the existing communication permissions.

## APIs and export

| HTTP | MCP | Input |
|---|---|---|
| `GET /api/pouch` | `get_pouch` | Optional revision, content_offset, max_content_length |
| `POST /api/pouch/initialize` | `initialize_pouch` | Empty object for HTTP; no MCP arguments |
| `GET /api/pouch/changes` | `get_pouch_changes` | limit, offset |
| `GET /api/pouch/export` | `export_pouch` | Explicit revision number |

MCP read/change/export tools take their fields under `request`. Read/change/export
require `entries:read`; initialization additionally requires `entries:write`.
No new scopes, credentials or production permissions are introduced. Returned
`current_path` and `revision_path` are relative to the installation's public
prefix, not to the website root. Reads share Entries' bounded content behavior.

**Export saved revision** downloads a JSON package containing the complete saved
state, Entry/revision IDs, author, timestamp, revision link and SHA-256 of the
UTF-8 content. The browser includes the absolute source URL. The API requires an
explicit revision, so later edits cannot silently alter an export. The ordinary
**Download draft** button still downloads the current local buffer separately.
Publishing an export to a repository or external destination remains an explicit
action; this interface does not perform it. Document text does not grant work
authorization.

## Focused verification

`tests/check_editing_pouch.py` uses a disposable, private Unix-socket PostgreSQL
cluster to check only these new operations: concurrent initialization, permanent
bindings, surgical transforms, atomic conflicts, exact retries, revision links,
export and authorization, plus their HTTP/MCP registration. It does not invoke
pytest, standalone startup, streaming or native connectors. The full suite and
`tests/run_postgres.py` still require separate explicit approval.
