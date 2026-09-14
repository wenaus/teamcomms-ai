# Comms

Comms provides a shared session directory, routing groups and subscriptions,
a durable mailbox, independent destination receipts, and authenticated streaming
and replay. HTTP and MCP use the same application services and PostgreSQL store.
Client injection is implemented by the [Connectors component](connectors.md).

## Source mapping

The publication and receipt services adapt `tjai_app/comms.py` at TJAI revision
`653a19639d47332f89983066ebbb10f0052dcc5f`. Participant-bound authorization,
canonical resources, subscriptions, cursor ordering, receipt history, and HTTP
streaming extend that baseline. Attribution is retained in [NOTICE](../NOTICE).

| Source behavior | TeamComms location |
|---|---|
| Session registration, freshness, resource discovery | `comms/directory.py` |
| Transactional mailbox, duplicate publication checks, per-destination receipts | `comms/operations.py` |
| PostgreSQL notification and durable recovery | `comms/operations.py`, `comms/stream.py` |
| Session, message, and delivery records | `comms/models.py` and migrations |

Paths are relative to `src/teamcomms/`. Canonical Dialog capture is a subsequent
implementation step described in the [design](design.md).

## Identity and directory

A session belongs to the authenticated participant's membership. Registration
uses `(participant, host, client, native_id)` as its stable identity; repeating it
refreshes metadata and preserves the session ID and mailbox. A participant can
operate several sessions. Other participants cannot heartbeat, subscribe, read,
or receipt those sessions, including through an administrator credential.
Host receivers use the credentials of the participants whose sessions they serve.

The directory records name, host, client, native ID, workspace, model, effort,
capabilities, delivery mode, activity, and resource IDs. Registration supplies the
complete metadata and resource set. Heartbeats change activity and optionally
name, model, and effort. States are `idle`, `active`, `unknown`, and `offline`.
Online discovery requires active membership, a non-offline state, and a heartbeat
within 90 seconds. Availability and delivery mode are distinct fields.

Canonical resources have a unique installation key and a stable UUID. Kinds are
`project`, `checkout`, and `service`. A checkout names a host, a project resource,
and path aliases; these aliases cannot belong to another resource on that host.
The registering administrator supplies verified host paths. Registration accepts
identical retries and rejects conflicting definitions. A checkout's project ID
connects its sessions to the same repository on other machines.

Groups have an administrator-provisioned key and display name. Participants can
enable or disable group and exact-topic subscriptions for their own sessions.
Routing groups are shared team destinations. Resource and group definitions are
created through the API; editing those definitions is outside this interface.

## Publication

`send_message` requires a client-generated `message_id` UUID, `content`, and an
explicit `audience`. The credential establishes the author. An optional
`sender_session_id` must belong to that author. Programs can publish directly
under their participant identity without creating an interactive session.

Audience fields are arrays: `session_ids`, `participant_ids`, `hosts`,
`group_ids`, `resource_ids`, and `topics`. Their union determines the destinations.
Topic routing selects exact subscriptions; the separate message `topic` field
labels the conversation. Explicit sessions can receive while offline. Other
selectors match online sessions. Resource routing includes sessions registered
with a checkout of the addressed project. The sending session is excluded when
provided. An empty result is a visible conflict; more than 100 destinations is
rejected. Overlapping selectors create one delivery per destination.

Publication stores the envelope, authenticated author snapshot, fixed destination
list, and entry references in one transaction. Retries with the same message ID
and envelope return the existing publication. A changed envelope or author
conflicts. Audience changes after publication leave its destination list intact.
An upstream watcher retains and retries its original UUID until it has the result.

The envelope includes kind (`notification`, `conversation`, or `offer`), content,
topic, optional timezone-aware observation time, reply reference, reply-requested
flag, and entry/version references. The service adds schema version, author
identity, and creation time. An `offer` records communication; Inflight supplies
its execution lifecycle. External-platform authorship is supplied by the future
platform connector contract; callers cannot substitute a message author.

A reply must name a message delivered to its sending session. Publishing that
reply also acknowledges the original delivery to that session. Other destinations
retain their independent state. Reading never acknowledges. Message and receipt
records are immutable in PostgreSQL, and fixed entry references are protected by
foreign keys. Original messages remain available after session expiry.

## Delivery and receipts

Each delivery has its own ID, monotonically increasing session sequence, current
transport state, revision, diagnostic detail, consideration time, and
acknowledgment time. Consideration and acknowledgment are recorded together by
`acknowledge_message`; this reports the participant's consideration rather than
inferring it from transport acceptance.

| Current transport state | Allowed next states |
|---|---|
| `pending` | `uncertain`, `failed` |
| `uncertain` | `written`, `accepted`, `failed` |
| `written` | `accepted`, `uncertain` |
| `failed` | `pending` |
| `accepted` | Terminal transport state |

Before attempting client injection, a receiver records `uncertain` using the
current revision. Competing receivers conflict on that revision. A receiver
records `written` for a completed transport write or `accepted` for a client
receipt. Acceptance alone leaves model consideration and acknowledgment unset.
A crash during dispatch retains uncertainty for reconciliation.

A `failed` report requires diagnostic detail and means the receiver has established
that acceptance did not occur. `failed` can return to `pending` for retry.
Uncertain deliveries require reconciliation before they can enter this retry path.
An acknowledged delivery cannot return to pending. Late transport reports preserve
acknowledgment and must use the current revision.

Every transport report requires a stable `receipt_id`, `delivery_id`,
`expected_revision`, and proposed state. Exact receipt retries return the saved
result; reusing an ID with changed input conflicts. History retains each report,
author, time, and resulting state. Sender status queries expose all destination
receipts; receiving participants see their own deliveries. Unrelated participants
cannot read the message or receipt history.

## Streaming and recovery

A receiver opens an outbound HTTPS `GET /api/comms/stream` with its participant's
bearer credential and `session_id`. The reverse proxy must forward streaming
responses without buffering. The service also sends `X-Accel-Buffering: no`.
The standalone loopback development server uses HTTP as described in
[service setup](service.md).

Streams use Server-Sent Events (SSE), last at most 25 seconds, and reconnect using
`Last-Event-ID` or the `after` parameter. Delivery sequence numbers are local to a
session. Transactions lock destination sessions in UUID order before allocating
sequences, so a cursor cannot skip an earlier uncommitted publication.

| Event | Receiver action |
|---|---|
| `ready` | Connection is subscribed and authorized |
| `message` | Persist the delivery locally before advancing its sequence cursor |
| `refresh` | Reconcile stored pending/uncertain deliveries and current receipts |
| `reconnect` | Resume the stream from the last locally persisted cursor |
| `error` | Record the error; reconnect after recovery or credential renewal |

`message` events carry a sequence in the SSE `id` field and the complete message
and destination record in `data`. Other events leave the publication cursor
unchanged. A retry transition for an older delivery triggers `refresh`; the
receiver checks its stored queue or rereads the inbox for unresolved deliveries.
The stream itself neither claims injection nor changes transport status.

The listener commits `LISTEN` before reading durable rows, following PostgreSQL's
[subscribe/read ordering](https://www.postgresql.org/docs/current/sql-listen.html).
Transactional notifications wake listeners across service processes. Each open
stream holds one dedicated PostgreSQL connection; installation capacity must
allow for the concurrent receivers. A five-second fallback read recovers missed
notifications and checks credential expiry, revocation, membership, and read
scope. Database failure is reported through HTTP before streaming begins or an
SSE error after it begins. Disconnection closes the listener.

The inbox replay interface supports the same `after` cursor with at most 25
messages per page. `pending_only=true` means unacknowledged, including failed and
uncertain deliveries. It does not mean safe to inject. Receivers inspect transport
state and their local dispatch record before acting. Replay returns `next_after`
and `has_more`; reconnecting with an older cursor can repeat messages, which local
receivers deduplicate by delivery ID. Reads and stream writes leave receipts intact.

## Interfaces and bounds

The following paths have the prefix `/api/comms`. MCP tools take a `request`
object with the same fields as HTTP JSON bodies or GET parameters.

| HTTP | MCP tool | Input |
|---|---|---|
| `POST /sessions` | `register_session` | Native ID, client, host, name; optional metadata/resources |
| `GET /sessions` | `list_sessions` | Host, participant, resource, include-offline, pagination |
| `POST /sessions/heartbeat` | `heartbeat_session` | Session ID, state; optional name/model/effort |
| `POST /resources` | `register_resource` | Key, kind, name; host/project/aliases for checkouts |
| `GET /resources` | `list_resources` | Pagination |
| `POST /groups` | `register_group` | Key, name |
| `GET /groups` | `list_groups` | Pagination |
| `POST /subscriptions` | `subscribe` | Session ID, one group ID or topic, active flag |
| `POST /messages` | `send_message` | Message ID, content, audience; optional envelope fields |
| `GET /messages` | `get_messages` | Session ID, after, limit, pending-only |
| `GET /messages/read` | `get_message` | Message ID, destination pagination |
| `POST /messages/acknowledge` | `acknowledge_message` | Session ID, message ID |
| `POST /deliveries` | `record_delivery` | Receipt ID, delivery ID, expected revision, state, detail |
| `GET /deliveries/history` | `get_delivery_history` | Delivery ID, pagination |
| `GET /stream` | — | Session ID, after, limit, pending-only, duration |

Directory and history pages default to 25 and allow up to 100 rows. Inbox and
stream pages default to 20 and allow up to 25 messages. Message content is bounded
to 16,000 characters, all Comms request objects to 48,000 encoded bytes, and
transport bodies to 64 KiB. Unknown fields, invalid references, and invalid
pagination are rejected. Content whitespace and Unicode are preserved.

`directory:read` permits directory queries. `directory:write` with administrator
membership provisions groups and resources. `sessions:write` permits registration,
heartbeats, and subscriptions for the credential's participant. `comms:write`
permits publication and that participant's receipts and acknowledgments.
`comms:read` permits reading authored/received messages and receiving streams.
Entry references additionally require `entries:read` when publishing.
Existing credentials gain no scopes automatically; [local provisioning](service.md)
can issue appropriately scoped credentials after an upgrade.

## Verification

The [isolated test runner](../tests/readme.md) exercises concurrent publication
retries, fixed fan-out, destination-specific receipts, version-checked dispatch,
cursor ordering, immutable evidence, authorization, and bounded inputs. Its
stream check starts two service processes against the temporary database and
verifies cross-process notification, pre-connection recovery, cursor reconnect,
and revocation of an active stream. Production services and client sessions are
outside these checks.
