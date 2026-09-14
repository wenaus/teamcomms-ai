# TeamComms AI

**Distributed messaging, shared documents, dialog, and coordinated work for humans, AIs, and systems.**

Design draft · 2026-09-13

## Purpose

TeamComms AI is a distributed collaboration system for people, AI sessions, programmatic systems, and connected communication platforms. Participants on different machines exchange messages, develop documents, consult recorded conversations, coordinate tasks, and follow operational events through a shared central service.

The first implementation supports Anthropic and OpenAI/ChatGPT LLMs through Claude Code and Codex on Linux and macOS.

The software derives from Torre Wenaus's personal TJAI AI assistant system. It is designed for general development and operations teams.

A watcher can report a stalled job to subscribed AI sessions and a Mattermost channel. An assistant can investigate the event, consult related work, and propose a response to its operator. Participants can develop the response in the Pouch, track implementation in Inflight, and follow the topic through Capcom. The shared record connects the event, discussion, documents, work, and results.

This document describes the proposed system and its implementation requirements.

## Components

The project uses one repository and an integrated installation. Internal components have defined interfaces and share identity, authentication integration, record references, and storage infrastructure.

| Component | Responsibility |
|---|---|
| Entries | Typed entries and versioned documents; metadata, relationships, search, human editing, and surgical and bulk AI editing |
| Dialog | Capture, storage, retrieval, and presentation of human–AI conversations and related events |
| Comms | Participant directory, messaging, routing, subscriptions, delivery, and receipts |
| Pouch | The team's single shared working document, supported by Entries |
| Inflight | Tasks, subtasks, incidents, dependencies, ownership, handoffs, and resource reservations |
| Capcom | Topical threads presenting current work, operational events, decisions, and results |
| Connectors | Integration with AI clients, execution workers, Mattermost, and other communication platforms |

Dialog, documents, messaging, and work records are available through the integrated interface and service APIs. Their internal interfaces support selective use by host applications.

The execution layer uses `wrangle-ai` through a backend interface. That integration requires distributed claims, renewal, and validation of ownership generations on completion.

## Participants and resources

A team includes four forms of participation:

| Participant | Examples | Interaction |
|---|---|---|
| Human | Operator, developer, reviewer | Messages, documents, decisions, and work through a client or connected channel |
| AI | Interactive or headless assistant | Messaging, investigation, document editing, and task execution |
| Programmatic system | Watcher, scheduled job, service, worker | Events, progress, acknowledgments, work offers, and results |
| Communication platform | Mattermost channel or bot stream | Routed conversations and proactive publication |

Stable participant identities are separate from sessions, connections, and runs. An AI session records its operator association. Message authorship identifies the actual author. Connectors preserve the external author alongside the service identity used for transport.

The directory records participant and session identifiers, display name, host, workspace, repositories, client, native session identifier, model and effort where applicable, capabilities, groups, activity, heartbeat, and delivery mode. Model metadata follows changes during a session. Availability and delivery capability are reported separately.

Groups include explicit teams, hosts, and repositories. A canonical project identifier connects checkouts of the same repository across machines. Each shared checkout also has a resource identifier that resolves path aliases to the same underlying directory. Deployment targets and services can be registered as resources.

Interactive assistants introduce themselves once at session startup with identity, host, and current work when known. They coordinate through TeamComms across providers. Background jobs register without social broadcasts. Relevant dialog and current task records provide context for handoffs and investigations.

## Distributed operation

The system comprises a central service and database, connectors on participating machines, and connected applications and communication platforms. Human interfaces and AI clients access the same team records through the central service. Participants can operate concurrently across hosts and model providers.

Each team has one authoritative database. It stores the shared entries, revisions, dialog, messages, deliveries, tasks, and ownership records. Applications belonging to the same team connect to that authority. Separate team databases represent separate teams; cross-installation federation is outside the initial scope.

Host connectors use outbound authenticated HTTPS connections. Streams deliver pending messages promptly, and a bounded replay interface supports reconnection. This connection model supports laptops and worker machines behind network address translation without public inbound listeners.

A message is committed to the central store before publication succeeds. The delivery process signals the destination host, whose connector records receipt and injects the message into the appropriate local client. Client acceptance and participant acknowledgment are reported separately to the service. Concurrent workers consult the same task revision and reservation state before shared operations.

Local connector state includes receive cursors, pending outgoing messages, and per-session injection status. Reconnection reconciles that state with the central record using stable message identifiers. Connection failures and uncertain deliveries remain visible. New coordinated mutations require validated ownership; ongoing external work follows its configured lease and execution guards.

## Implementation

### Central service

The central Python service exposes HTTP APIs, streaming delivery, and Model Context Protocol (MCP) tools. Application services implement directory and membership operations, entry editing, dialog capture, publication and receipts, task transitions, claims, and reservations. Human interfaces, programs, and AI connectors use the same validation and authorization rules.

Django 5.2 supplies the ORM and schema migrations. The standalone ASGI application
uses Starlette for HTTP routing and the official MCP Python SDK for MCP tools.
Both interfaces call the same synchronous application services through an async
database boundary. Configuration, identity, and authorization are shared.
The [service reference](service.md) documents the implemented endpoints and setup.

The service can run independently or be integrated into a host application. Host integration supplies authentication, configuration, interface mounting, and operational policy. The monorepo contains the server, internal components, connectors, and shared interfaces under one development and release workflow.

### Database

PostgreSQL is the authoritative store. Persistent records cover participant and resource identity, entries and their revisions and relationships, captured dialog, messages and destination deliveries, and Inflight tasks, claims, and reservations.

Transactions update current state and its associated history together. Unique identifiers prevent duplicate message publication and duplicate delivery rows. Expected-revision checks protect document and task updates. Claims and resource reservations validate ownership generations; a multi-resource acquisition succeeds for the full set or leaves ownership unchanged.

PostgreSQL notifications wake the delivery and work processes after durable state is available. Missed notifications are recovered by querying that state. Indexed, bounded queries support directory lookup, pending delivery, work discovery, dialog retrieval, and topical views.

### Host connectors and workers

A receiver on each participating host maintains its authenticated service connection and dispatches messages to registered local sessions. Client connectors provide session registration, injection, receipt reporting where available, and dialog capture. Their supported behavior is recorded in the client compatibility matrix.

Execution workers claim eligible work through the central service, report progress, renew claims, and submit results against their ownership generation. The `wrangle-ai` backend supplies execution primitives through the common task interface. Host configuration supplies the worker environment and permissions. Interactive sessions and optional headless workers share the claim and reservation contracts.

Local persistence protects receive cursors, unsent messages, and uncertain injections across restarts. The central database determines shared task state and ownership. Restart and upgrade procedures preserve the connection needed to coordinate the change.

## Entries

Entries provide stable identity, typed content, metadata, relationships, and revision history. An entry has an immutable identifier, an optional readable identifier or name, a kind with a defined schema, content, relevant status and priority fields, topic and tag metadata, and a current revision.

Relationships connect documents, conversations, work, subtasks, incidents, evidence, and decisions. References can follow the current entry or identify an immutable version. Search supports text, kind, metadata, relationship, and time filters with bounded, paginated results.

Version history records authorship, time, changes, and the base revision. It supports comparison, recovery, and explicit restoration. Exported documents retain version references and applicable attribution.

Entry kinds have distinct integrity rules. Working documents support revision. Captured dialog and original communications preserve source authorship, with auditable corrections or permitted redactions. Task state, ownership, and reservations change through validated operations. Content and metadata tools respect these rules.

### Human editing

The human editor supports long documents and short notes with readable typography, a resizable workspace, keyboard navigation and shortcuts, search, selection, undo and redo, and editing of headings, lists, links, tables, and code. Rendered and source views preserve document structure and formatting through editing round trips.

Save state is visible: saved, saving, unsaved, or conflicted. Autosave and local recovery protect ongoing work. Save failures retain the user's buffer and expose recovery options. Concurrent changes preserve the active buffer and focus; the editor presents the intervening revision for reconciliation. Version comparison and restoration are accessible from the document.

### AI editing

AI tools support precise edits with bounded context and revision checks:

| Operation | Contract |
|---|---|
| Read | Retrieves an entry, section, or range with revision and target-location context |
| Patch | Replaces, inserts, or deletes an exact target; absent or ambiguous matches return a conflict; applying to all matches is explicit |
| Section update | Changes a specified section while preserving surrounding content and structure |
| Metadata or relationship edit | Changes named fields or links while preserving the remainder and validating entry-kind rules |
| Multiple edits | Applies a set of edits to one entry atomically against an expected revision |
| Full replacement | Replaces content explicitly, with concurrency checks and recoverable history |
| Bulk operation | Applies a defined operation to an explicit or previewed entry set with exact per-entry outcomes |

A bulk preview fixes the selection and shows affected entries and proposed changes. Application validates revisions, permissions, and kind-specific rules. Bounded batches can be atomic. Larger operations declare any per-entry transaction behavior and report successes, conflicts, and failures separately. Retry identifiers prevent duplicate application.

Results include compact summaries, new revisions, and references to detailed diffs and history. Callers can request the complete diff or affected content. Preview and execution follow existing user instructions and client permissions. Human and AI editing use the same revision and integrity rules.

## Pouch

Each team has one Pouch: a canonical shared working document for developing a design, plan, investigation, or handoff. It is directly accessible from the shared interface. Other documents are ordinary entries in the document system.

The Pouch has a stable identity, current revision, editor attribution, and version history. Its content can be associated with a topic or task. Reviews, decisions, and work instructions can reference a fixed Pouch version; links to the current document follow the evolving draft.

Human and AI edits use the shared document tools. Updates specify an expected revision. Concurrent changes produce a conflict for reconciliation, preserving the newer document and the editor's proposed changes. Reading leaves the document intact. Replacing its content preserves prior versions and their references.

A Pouch update normally produces a brief change notice and a link. The document history provides full text and diffs. Export to a repository or another destination is an explicit operation identifying the document version and retaining applicable attribution. Authorization for work referenced in a document remains governed by the task and client policies.

## Shared dialog

Dialog captures human turns, assistant during-turn updates, final responses, and incoming peer or system messages. Host policy determines whether tool activity is stored as structured events or linked artifacts.

Records carry actor and role, session and run identifiers, source event identifier or sequence, event time, capture time, and related message, task, and document-version references. Stable source identifiers deduplicate replay. Native transcript capture reconciles incoming peer messages with their canonical message references.

Delivery is recorded independently of acknowledgment. Missing transcript coverage and recorder outages appear as explicit gaps.

A bounded `get_dialog` interface filters by team, host, participant, session, topic, and time window. Handoffs combine relevant history with the current task and document revisions. Assessment and other consumers access the same authorized record; their analysis policies belong to the consuming application.

### Session bootstrap

Dialog supplies session-start context. At registration, a Claude Code or Codex connector can request recent dialog for its host, participant, or topic, so a new session begins with the team's recent history. This opt-in capability also supports explicit context reloads during a session.

Startup configuration selects the history window and context budget, with continuation links for further reading. Returned context preserves authorship, timestamps, source references, and coverage gaps. Installations can supplement history with applicable guidance entries and references to current work and the Pouch; historical conversation retains its role as context.

## Messages and delivery

Messages distinguish notifications, conversation, and explicit work offers. Each carries a stable identifier, schema version, author, source, creation and observation times, content, audience, conversation and reply references, and optional topic, task, resource, incident, or document-version references. State notices identify the revision they describe and can reference a superseded notice. Attachments and evidence links provide detail beyond the compact envelope.

Group publication snapshots eligible membership and creates one delivery per destination. Overlapping groups and subscriptions deduplicate within each destination. Matching alarms reach every subscribed running session and configured channel. Receipt, response, or task ownership in one session leaves delivery to the others unchanged.

Publication persists messages and deliveries before returning success. Producers use a transaction or durable outbox and reuse message identifiers on retry. Receivers persist incoming data before advancing their cursors and track injection separately for each local session.

Transport state distinguishes pending, written, accepted by the client, uncertain, and failed. Model consideration and participant acknowledgment are separate records. Reads preserve acknowledgment state; a reply can acknowledge its referenced message. Task acceptance, completion, and incident resolution have their own transitions.

Known pre-acceptance failures can be retried. Ambiguous outcomes require reconciliation where the client supplies receipts; otherwise the delivery remains visibly uncertain. Failure at one destination leaves other deliveries independent. Attempts and errors remain accessible.

Reconnects resume pending deliveries. New sessions can receive a bounded snapshot of active matching incidents, labeled as catch-up. Current task and reservation revisions determine whether delayed work instructions remain actionable. Historical notifications preserve their original content and references.

Registration, heartbeats, routing, polling, retries, and receipt bookkeeping use ordinary code. The service exposes delivery latency, failures, destination counts, and model activity induced by notifications.

## AI clients and prompt presentation

Client connectors support registration, delivery, receipt reporting where available, dialog capture, and access to shared work and documents. A compatibility matrix identifies tested versions and capabilities, including busy-session scheduling, visible envelopes, background receipts, provenance, and restart recovery.

Claude Code integration evaluates the supported channel interface and the existing socket integration under the installed client and organization configuration. Codex immediate delivery uses the app-server that owns the active session. Queued delivery awaiting a human prompt is identified as a compatibility mode.

Client connectors preserve client settings, permissions, active conversations, and running commands. Busy-session scheduling follows the client contract. Session setup supplies concise receipt and coordination instructions.

Prompt presentation distinguishes three levels:

| Level | Content | Presentation |
|---|---|---|
| Transport | Heartbeats, receipts, retry metadata | Service records and delivery diagnostics |
| Coordination | Assignments, handoffs, subtask progress, routine checks | Current task and topic records; relevant workers receive actionable updates |
| Operator attention | Questions, decisions, material blockers, failures, substantive results | Concise notices in the relevant conversation and Capcom thread, with detail links |

Delivery, model attention, visible output, and durable recording are separate capabilities. Where supported, a client connector records and receipts routine traffic without a model call or conversational response. Inbox and activity views provide access to background coordination.

Presentation filtering preserves the underlying record and its access rules. Exact duplicate injections and receipt replies are suppressed. Routine status updates can be coalesced by topic and revision while retaining the events. Distinct alarms, human instructions, and substantive work remain individually recorded and appropriately delivered. User controls cover subscriptions, urgency, routine batching, and quiet periods, with deferred and failed delivery visible.

Client-enforced wrappers and other rendering limits appear in the compatibility matrix. Client connectors minimize supplied envelopes and avoid repeating setup instructions. Capcom provides persistent access to decisions and results beyond the terminal scrollback.

## Delegation and authorization

An assistant can accept delegated work within its existing task scope and tool permissions. The communication channel alone introduces no additional approval requirement. Authorization is evaluated for the requested action; requirements for committing or deploying apply to those actions.

The installation and receiving client define delegation scope. Requests beyond that scope retain a blocked state with the affected action and applicable restriction. Client permission settings and explicit refusals remain binding within their scope.

Human instructions preserve authenticated authorship, source, time, target task, and intended audience. References to the original instruction provide context across sessions. An assistant's quotation or summary retains assistant authorship. Later decisions identify the action and scope they supersede.

A client requiring direct local approval declares that limitation. The task record identifies the necessary interaction, destination session, action, and reason. Blocker evidence distinguishes tool rejection, an applicable instruction, missing capability, and unresolved scope. Required human decisions are presented with a concrete next step.

Alarm delivery concludes at the receiving interfaces. The operator and assistant continue through their ordinary client interaction. TeamComms leaves execution authorization and interpretation of approval responses to that client. Explicit work offers use the configured executor's authorization policy.

## Inflight

Inflight maintains the current state of coordinated work, linked to its discussion, documents, resources, and evidence. Messages announce record changes; workers validate the current revision before acting on delayed instructions.

| Form | Purpose | Examples |
|---|---|---|
| Work | Deliver an outcome through tasks and subtasks | Feature implementation, investigation, migration |
| Coordination | Arrange assignments, dependencies, and shared access | Checkout reservation, handoff, host verification |
| Incident | Follow an operational condition and response | Stalled job, service failure, recovery |

Presentation is independent of task form. Operator-facing work appears in the default view. Internal coordination and implementation subtasks remain available in the authorized record. A material blocker, decision, or outcome can bring an internal item into the operator's attention view.

Task fields include an accountable owner, state and revision, description, completion criteria, optional parent and dependencies, affected resources, blockers, and dialog or document references. States distinguish planned, active, blocked, completed, failed, and canceled work. Updates validate the expected revision and retain actor and history.

Every task has an accountable owner assigned at creation and retained through closure. The creator or designated coordinator remains responsible while an offer awaits a worker. Stable participant identity preserves accountability across session disconnections. The executing worker is recorded separately. Owner unavailability is visible and follows the configured reassignment process.

Handoff retains the current owner until a successor accepts. Transfer changes ownership and its generation atomically. Stale acceptances and releases cannot reverse the transfer. Departing participants reassign or close their active work; closed records retain the final accountable owner.

Completion records the stated outcome and evidence. Coordinators aggregate subtask or host results for the operator. Reopening is an explicit transition; delayed progress messages cannot change a completed task's state.

## Claims and resource reservations

An explicit work offer identifies its Inflight task, eligible audience, required capabilities, deadline or timeout, and execution policy. The task has an accountable owner before an executor claims it.

Atomic claims identify one current executor, its ownership generation, and renewal deadline. Renewal and completion validate that generation. Results from an obsolete claim are rejected.

Managed resources retain an accountable custodian or coordinating task owner while idle and active. A reservation identifies the participant permitted to perform a particular mutation under a task. Conflicting reservations are excluded, and a multi-resource request acquires its complete set atomically. Handoffs and release retain an identified accountable owner.

Ownership changes occur through validated transactions. Message text alone leaves the ownership record unchanged. Stop and cancellation requests identify the affected task or execution and are reconciled with its current state.

Heartbeat expiry establishes lost contact. Reassignment of external work requires the configured execution guard or confirmation that the preceding mutation has stopped. External effects use appropriate idempotency, locking, or fencing mechanisms. Database claims alone cannot establish exclusive access by unintegrated tools.

Participating connectors and scripts validate reservations at protected operations and reject stale generations. The interface distinguishes enforced exclusion from advisory reservations. Basic ownership and reservation support accompanies the initial coordinated-work implementation.

## Capcom

Capcom presents topical threads over Inflight, messages, incidents, documents, and dialog. A thread brings together current status, accountable owner, subtasks, discussion, relevant document versions, outstanding decisions, and completion evidence. Task state comes from the authoritative work records.

Threads can begin with a notification, a conversation, or a work item. A subsequent investigation links to the originating thread. Operational event notices and sampled service state remain distinguishable.

Default views emphasize decisions, material blockers, substantive progress, and recent outcomes. Routine internal coordination is folded into the thread and available on demand. Filters and subscriptions support following selected topics. New events can return a topic to the attention view.

Current state is presented alongside history. Superseded blockers and instructions remain visibly historical. Reading, acknowledgment, work progress, and incident resolution are separate states. Completing a task establishes incident resolution only when the incident's resolution criteria are met.

Threads can be opened, followed, searched, revisited, and linked from conversations. Compact prompt notices connect the operator to this persistent context across participating sessions.

## Mattermost and other connectors

Mattermost is included in the initial integration scope. Registered destinations map to configured channels or bot streams. Authorized participants can publish proactively through these routes.

The connector records external post identifiers, channels, threads, delivery results, and original authors. Replies preserve thread relationships in both systems. Destination formatting derives from the canonical message.

Inbound routing applies to configured channels and rules. Human, bot, and service authorship is retained. Canonical message identifiers, external post identifiers, connector identity, and route history prevent reflection loops. Human replies and quotations remain separately authored contributions.

An existing bot can use TeamComms for publication, routing, threading, and delivery records. Domain-specific commands remain with the bot or service implementing them. Additional communication platforms integrate through the same connector contracts.

## Headless execution and model policy

An offer can permit a headless executor when no eligible interactive worker claims it within a configured interval. Both use the same atomic claim path, including when an interactive session becomes available during worker startup.

Executor configuration defines host, workspace, tools, credentials, timeout, and spending limit. Eligibility follows capability, access, and authorization. Model and effort preferences select an eligible worker within budget.

Fallback is opt-in for explicit offers. Unanswered notifications remain in the communication record. Execution follows the same ownership, reservation, and authorization rules as interactive work.

## Interface and lifecycle

Dark and light modes are initial interface requirements. Both cover navigation, forms, document editing and reading, dialog, tables, code, diffs, notices, selection, and focus states. Each mode provides readable contrast and consistent semantic colors. Keyboard operation, visible focus, and status indicators remain usable in either mode. Saved theme and document-view preferences persist across visits; the initial theme can follow the operating-system preference.

Team membership and scoped credentials control directory, document, dialog, and task access, publication destinations, subscriptions, and offer eligibility. Authentication binds requests to permitted participants and routes. External human identity remains distinct from connector credentials.

Enrollment supports revocable credentials and automatic session registration. Session exit and heartbeat expiry update live discovery while preserving records and unfinished-work accountability. Retention, attachment storage, and redaction follow the installation's policy, with recording gaps exposed to consumers.

Installation documentation covers the service endpoint, database, receivers and launchers, client compatibility, connectors, credentials, upgrades, and removal. Upgrades preserve local edits and working entry points, validate runtime paths, and restart affected processes through documented controls. Restart sequencing preserves the communication needed to coordinate the change.

## Implementation stages and acceptance

The implementation proceeds through integrated stages:

1. Shared entry and identity services, document storage, directory, mailbox, client connectors, and complete dialog capture.
2. Human and AI editing, the Pouch, Inflight lifecycle, continuous ownership, explicit handoffs, and basic reservations.
3. Capcom topical views, prompt-attention controls, Mattermost publication and inbound routing, and a watcher integration demonstrating subscriber fan-out.
4. Execution-backend integration with renewable claims, generation-validated results, resource guards, and optional headless fallback.

| Acceptance case | Required evidence |
|---|---|
| Independent operation | Installation, document collaboration, task coordination, and delivery without the originating personal system |
| Cross-provider delivery | A matching event reaches all subscribed Claude and Codex sessions across machines; receipt or ownership in one session preserves other deliveries |
| Proactive delivery | Idle and busy sessions receive without a human prompt, with scheduling and compatibility delays visible |
| Prompt presentation | Routine transport requires no model calls; coordination avoids acknowledgment loops; current decisions and results remain discoverable |
| Documents and Pouch | One canonical Pouch; preserved versions and review references; human and AI edits reject stale or ambiguous changes |
| Editing | Reliable save state and recovery, preserved structure and active buffers, scoped bulk selection, and exact conflict and partial-result reporting |
| Dialog | Human turns, during-turn updates, final responses, and peer provenance survive replay without duplicate counting |
| Session bootstrap | Fresh Claude Code and Codex sessions receive configured recent history at registration within a bounded budget, with provenance and incomplete coverage visible; reload retrieves updated context |
| Delegation | Work within existing scope is accepted; actual restrictions are identified; human authorship remains verifiable |
| Ownership | Unclaimed, blocked, disconnected, and handing-off tasks retain an owner; stale transfers and results are rejected |
| Reservations | Concurrent requests produce one valid holder; protected operations reject stale generations; advisory limitations are visible |
| Current state | Delayed messages cannot regress task state or retake ownership; completion carries evidence |
| Capcom | Related work and discussion form navigable threads; current state and historical changes remain distinguishable |
| Mattermost | Proactive publication, preserved authorship and thread references, configured inbound routing, and loop prevention |
| Recovery | Pending deliveries survive restart; failed destinations remain independent; upgrades preserve local work and required session continuity |
| Access | Revoked or unauthorized participants cannot impersonate senders or access unrelated records |
| Fallback | Only opted-in offers launch workers, within configured capability, authorization, and budget |

Verification combines focused checks of concurrency and integrity contracts with bounded end-to-end demonstrations. A multi-host change can exercise preserved local edits, worker refusal or disconnection, accepted handoff, delayed messages, and verified results.

Healthy connected delivery has an initial target of five seconds. Measurements separate producer detection, transport delivery, client acceptance, and model consideration. Presentation measurements cover injected text, coordination-induced model calls, repeated human interactions, and access to current decisions and results.

Future extensions include federation, additional connectors, and Agent2Agent (A2A) interoperability through a protocol connector. Each extension requires demonstrated compatibility with its advertised contract.

## Project information

The repository is `wenaus/teamcomms-ai`, maintained by Torre Wenaus, with Apache-2.0 licensing and preserved attribution for reused code. The monorepo has a coordinated development and release workflow.

The HEP Software Foundation (HSF) may be a natural option for future repository hosting. Its hosting of iDDS provides a relevant precedent.
