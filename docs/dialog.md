# Dialog and session context

Dialog stores attributed transcript events in the installation database. Each
participating native session receives a notice that its visible AI replies are
shared. Sharing typed human messages requires that human's separate choice for
that session. Session bootstrap remains opt-in. Existing host authentication and
account lifecycle apply to these interfaces.

## Per-session notice and human choice

The notice explains that visible AI replies are recorded and shared with team
members and their AI sessions, and may quote human input. It offers **Share my
messages** and **Keep my messages private** through that session's authenticated
browser page, `/dialog/sharing/SESSION_UUID`. No answer or a refusal leaves human
sharing off. This is a session choice, not a participant-wide setting.

Only the session's verified human operator, or its own human participant, can
choose through the host's signed-in browser session with CSRF protection. AI,
program, peer and bearer-token requests cannot grant consent. The choice is
HTTP-only, with no MCP or connector consent tool. Each operation has a stable
UUID and immutable audit record. The page also stops future human sharing;
already shared events are retained. Retrying an old grant cannot undo a stop.

Receivers offer the notice once per session/version through the native channel,
then record visible AI replies. A transport receipt records an offered notice,
not a claim that the human read it or agreed. Ambiguous delivery is retained for
reconciliation rather than automatically repeated. Human messages are filtered
locally before upload and independently at the service and database boundaries.
New grants establish a forward-only boundary: queued/earlier human lines,
missing-time human lines and `--from-start` cannot backfill human input. Stopping
is enforced by the server even if a recorder has cached the prior choice.
Existing events retain their attribution and never become human approvals.

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
| `GET /api/dialog/sharing` | `get_dialog_sharing` | `session_id`; read-only owned-session/operator notice and state |
| `POST /api/dialog/sharing/notice` | `record_sharing_notice` | `session_id`, `notice_version=1`, `transport` (`written`, `accepted`, `terminal`); offers no human consent |
| `POST /api/dialog/sharing/choice` | — | Browser only: `session_id`, `operation_id`, `choice` (`share`, `private`) |

Sharing state includes `notice`, `notice_version`, `notice_at`, `assistant_since`,
`human_choice` (`pending`, `share`, `private`), `human_since`, `human_changed_at`,
and `choice_path`. Reads never create consent or change the state. A publication
outside its session's sharing boundary returns
`{event_id: null, created: false, recorded: false}` without storing its content.
Existing immutable-source retries still return their original event.

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

Updated receivers attach visible-AI capture after offering the per-session
notice. A connector configured with `dialog_recording: false` offers no notice
and records nothing from its sessions. The legacy `dialog_capture` flag grants no human sharing permission;
it must not be enabled globally as a substitute for the browser choice. Set
`bootstrap` to an explicit filter/budget object to load recent context; bootstrap
defaults to disabled. Claude SessionStart supplies its transcript path; Codex
uses the owning thread's reported transcript path. An explicit `record` command
supports a selected existing session and transcript without restarting its native
runtime. `reload` retrieves or injects updated context for that session.

Capture starts at the current end of a transcript and records a coverage marker.
An explicit `--from-start` scans earlier records but cannot include human input
from before the current grant or queued before the recorder observed that grant.
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
credentials retain their scopes. Guidance entry IDs belong in the installation's
connector configuration; human sharing is a per-session human choice. The service uses the
existing ASGI application, database, proxy, and MCP lifecycle.

Migration `0003_sharing_sharingchoice` adds session state and immutable human
choice evidence. Its insertion guard also protects human/AI events from older
writers during a rolling upgrade. It does not rewrite events or infer choices
from old configuration. Install the same pinned connector package on each host.
An existing native session can keep running: attach the updated recorder with
its registered session, native ID, transcript, socket and owner PID. This leaves
the receiver, enrollment, no-greeting state and personal TJAI capture intact.
