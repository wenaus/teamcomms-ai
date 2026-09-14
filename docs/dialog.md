# Dialog and session context

Dialog stores attributed transcript events in the installation database. Capture
and session bootstrap are opt-in connector features. Existing host authentication
and account lifecycle apply to both interfaces.

## Recording and access

`dialog:write` permits recording for a session belonging to the authenticated
participant. `dialog:read` permits shared team transcript retrieval. A recorded
human role is a transcript assertion by that recorder; it cannot establish an
authenticated human instruction. Assistant authorship follows the session's
participant. Human attribution follows its verified operator association when
available. System and coverage records retain recorder provenance.

Canonical peer links require a message delivered to the recording session. The
service supplies that message's original content and author. Retrieval of linked
messages additionally requires `comms:read` and the original sender/recipient
permission. Transcript envelopes from other transports remain labeled peer input
with unverified external authorship. They are never classified as human turns.

Events are immutable. A stable `(session_id, source_id)` identifies each native
event. Identical retries return its original record; changed retries conflict.
Source sequence, native run, event time, capture time, role, phase, and topic are
retained. Serialized publication orders retrieval cursors by commit. No automatic
retention deletion or personal-system history import is provided.

## Interfaces

HTTP paths below follow the installation prefix. MCP tools take `request` with
the same fields. Request objects reject unknown fields and exceed neither 48,000
encoded bytes nor the transport's 64 KiB bound.

| HTTP | MCP | Request |
|---|---|---|
| `POST /api/dialog/events` | `record_dialog` | `session_id`, `source_id`, `source_sequence`, `occurred_at`, `role`, `content`; optional `run_id`, `phase`, `topic`, `message_id` |
| `GET /api/dialog` | `get_dialog` | Optional `host`, `participant_id`, `session_id`, `topic`, `since`, `before`, `before_id`, `event_id`; `limit`, `max_chars` |
| `POST /api/dialog/bootstrap` | `session_bootstrap` | History filters, `hours`, `limit`, `max_chars`, `guidance_entry_ids` |

Roles are `human`, `assistant`, `peer`, `system`, and `gap`; phases are `message`,
`commentary`, and `final`. Content is at most 16,000 characters per source event;
connectors split longer visible text into stable parts. Timestamps require a
timezone. Reads default to 40 events, allow 1–100, and return newest events first.
`next_before_id` continues older records without shifting under concurrent
publication. Event contents are shortened when necessary and labeled with their
original length. Query an `event_id` with `max_chars=16000` to retrieve its full
content. The selected content budget is 1,000–30,000 characters.

Bootstrap selects the most recent 24 hours by default (1–720 hours), presents
selected records chronologically, and includes at most 10 explicitly named
Entries with their revision and attribution. Entries require `entries:read`.
Guidance and history share the `max_chars` rendered-context budget (default
16,000). Historical and recorded guidance text retain their provenance and
cannot supersede current client permissions or operator instructions. Results
include continuation, truncation, and coverage information. A missing record
does not establish complete transcript coverage.

## Connector operation

Set `dialog_capture=true` to enable visible transcript recording and set
`bootstrap` to an explicit filter/budget object to load recent context. Both
default to disabled. Claude SessionStart supplies its transcript path; Codex
uses the owning thread's reported transcript path. An explicit `record` command
supports a selected existing session and transcript without restarting its native
runtime. `reload` retrieves or injects updated context for that session.

Capture starts at the current end of a transcript and records a coverage marker.
An explicit `--from-start` on the recorder includes earlier transcript records.
Complete JSONL lines are processed in bounded chunks. The saved cursor advances
only after every event in that line is durably accepted. Restart replays stable
event identifiers. File replacement, truncation, and invalid lines produce gap
records; partial trailing lines wait for completion. Capture does not include
reasoning or tool payloads. Injected TeamComms bootstrap and connector context are
excluded from human dialog to prevent recursive history capture.

Bootstrap failure is logged and shown in context without blocking Comms
enrollment. Explicit reload can retry it. Native injection uses the existing
client interface and retains its receipt limitations. The connector reports an
uncertain injection rather than automatically repeating an ambiguous write.

## Host integration

Add `teamcomms.dialog.apps.DialogConfig` to Django `INSTALLED_APPS`, apply its
migrations, and grant the two Dialog scopes through host policy. Existing
credentials retain their scopes. Guidance entry IDs and recording enablement
belong in the installation's connector configuration. The service uses the
existing ASGI application, database, proxy, and MCP lifecycle.
