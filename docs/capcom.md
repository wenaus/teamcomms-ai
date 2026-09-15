# Capcom topics and attention

Capcom is the shared topical view at `/capcom`. Topics collect explicit work,
Entry/revision and Dialog references, notices, decisions and the topic's existing
Comms conversation. Work summaries are read from current Inflight records on each
request. Old notices remain history; they cannot set a task's present state.
Sampled state is explicitly labeled with its observation time and source.

The interface adapts TJAI Capcom's feed, current-state panel, read markers and
URL filters to shared team identities. It uses the existing theme, authenticated
mount, browser CSRF and sanitized rendering. Reading does not acknowledge Comms
messages, resolve decisions or complete work. A participant marks a topic read
through an explicit sequence watermark; later notices make it unread again.
Conversation read markers name the exact displayed message IDs, so unseen or
concurrently committed messages remain unread. They never acknowledge delivery.
Following a topic is a personal list preference. Optionally subscribing an owned
session uses the existing exact-topic Comms subscription and routing contract.

## Records and access

Install `teamcomms.capcom.apps.CapcomConfig` with migrations `0001_initial`,
`0002_integrity` and `0003_conversationread`. `capcom:read`
and `capcom:write` permit topic reads and authenticated mutations. Referenced
Entries, Inflight, Dialog and private Comms records retain their own access rules.
A topic's title and authored notices are shared team content; linking a private
message never expands that message's audience. Inaccessible references are
reported as unavailable without including their content.

Topic creation and owner/admin edits use an operation UUID and expected revision.
Notices are immutable records with stable IDs, topic sequence, author, kind,
urgency, source and timezone-aware observation time. Kinds distinguish event,
sampled state, progress, result, decision, discussion and routine coordination.
A decision remains open until its author or a team administrator records an
explicit resolution against its current revision. Work completion is separate.
Exact mutation retries return immutable saved outcomes. Topic revision history
retains previously linked fixed Entry versions through protected foreign keys.

An optional notice audience publishes a canonical Comms notification in the same
transaction, reusing the notice UUID. Empty eligible audiences fail visibly.
Destinations remain fixed and independent; alarms are never merged or discarded.
A notice without an audience is recorded only in Capcom. AI destinations require
**Notify LLM**, an explicit audience and a reason; urgency, including alarm, never
selects it automatically. The browser checkbox starts off and reveals a recipient
selector when enabled. The API fields are `notify_llm` and `notify_llm_reason`;
source identity is the topic and notice UUID. See [Notify LLM](notify-llm.md).

Topic conversations read existing authorized
Comms rows; they are not copied or republished. Dialog references expose source
identity, role and capture gaps within a bounded excerpt.

## Attention policy

Attention controls are opt-in per receiver (`attention_controls=true`). Existing
receivers retain immediate delivery. Policies belong to an authenticated owned
session: `routine_mode=immediate|record|batch`, `batch_seconds` (5–300), and optional
`quiet_until` (at most 24 hours). They apply only to machine-authored, routine
Capcom notifications without a requested reply. Human-authored messages, external
human instructions, alarms, urgent notices, conversations and work offers bypass
routine policy once admitted. **Notify LLM** also bypasses routine policy.
This presentation policy never admits an unselected notification to an AI inbox;
the publication gate applies first. Direct peer conversations retain their behavior.

Record mode keeps the canonical notice and each destination record without a
model call. Batch mode waits for the fixed time window, then presents the newest
routine notice per topic, source and author in that window. Earlier routine notices retain
an explicit coalesced-into reference. A quiet period defers those routine notices;
clearing it permits the next receiver pass to resume. Deferred, recorded and
coalesced dispositions are visible separately from transport acceptance and model
consideration. They never fabricate acknowledgment or an accepted native receipt.

The receiver persists delivery data before attention planning and native dispatch.
An uncertain native dispatch follows existing reconciliation and is never filtered
retroactively. Policy/plan failures stop that dispatch pass visibly rather than
silently losing traffic. The receiver stream/reconciliation cycle revisits deferred
work, so presentation may lag its due time by one normal reconnect interval.
Routine transport and policy bookkeeping use code only. Native client busy/idle
scheduling limits still apply; no policy can remove the client's own wrappers.

## Interfaces and bounds

HTTP prefix `/api/capcom`; MCP tools take a `request` object and connector CLI
`call` takes its fields directly. Mutations carry a stable `operation_id` except
attention planning, which is idempotent per delivery.

| Tool | Path | Purpose |
|---|---|---|
| create_topic | POST /topics | Key, title and explicit component references |
| update_topic | POST /topics/update | Owner/admin revision-checked title and references |
| list_topics | GET /topics | Query, followed/attention/all, pagination |
| get_topic | GET /topics/read | Current work, selected sources and topic metadata |
| get_topic_notices | GET /notices | Sequence-paged immutable notices and decision state |
| publish_notice | POST /notices | Kind, urgency, content, observation/source, optional audience |
| resolve_decision | POST /decisions | Notice, expected revision, explicit resolution |
| follow_topic | POST /follow | Personal follow/read watermark and optional session subscription |
| get_topic_conversation | GET /conversation | Authorized canonical messages and independent receipts |
| set_attention_policy | POST /attention | Owned session policy |
| get_attention_policy | GET /attention | Policy and receiver capability |
| plan_attention | POST /attention/plan | At most 100 owned deliveries, persistent dispositions |

Pages contain at most 100 rows. Topic references are bounded to 30 Entries/work
items and 30 Dialog events; excerpts are bounded per record. Notices contain at
most 8,000 characters. Topic updates and notice publication serialize through the
team row so sequence cursors follow commit order. Routine folding is presentation
only: original rows, identities and observation times remain available.
