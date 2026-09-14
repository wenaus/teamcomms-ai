# Inflight

Inflight records work, coordination and incidents under the existing team identity.
The browser lives at `/inflight` and `/inflight/<entry UUID>` under the configured
public mount. SWF links it from System, alongside Pouch. `?revision=N` is a saved,
read-only view. It reuses the Entries editor, renderer, draft recovery and history.

## Authority and lifecycle

`inflight:read` reads team work and history; `inflight:write` permits creation and
authorized mutations. Neither grants production rights. New work is planned and
owned by its authenticated creator. The owner is a stable participant, separate
from an optional executor and that executor's registered session. Disconnection
never clears ownership or authorizes another executor. Resource associations are
informational until an explicit [claim and reservation](claims.md) acquires
them. Guarded commands use the configured cooperating entrypoints.

Owners and team administrators control lifecycle, dependencies and assignment.
The current executor may edit descriptions and report active/blocked progress or
completion. All mutations require the current revision and ownership generation. Changing
the executor also increments that generation, fencing prior assignments.
Completed, failed and canceled work remains in Done with its owner. Reopening is
an explicit owner/admin operation with a reason; ordinary progress cannot reopen
terminal work. Completion needs an outcome and evidence, and all direct subtasks
and dependencies must be completed. Failed/canceled prerequisites are unresolved.

A handoff offer names a successor, reason and optional timezone-aware expiry.
The owner remains accountable until that exact offer is accepted by its named
successor. Acceptance increments the ownership generation, clears prior executor
assignment and removes the offer atomically. Rejection, cancellation or expiry
leaves the owner intact. New offers supersede old ones. Terminal work cannot be
handed off. Message text cannot change any of these records.

## Durable records and retries

Work uses an Entry of kind `inflight`, plus typed current ownership/lifecycle data.
Every change writes one immutable Entry revision containing both description and
control state in reserved `metadata.inflight`. Ordinary Entries mutation, restore
and bulk editing reject this kind. Work edits cannot set that reserved metadata.
References pin document/Entry revisions when supplied. Comms and Dialog links are
validated against records the actor can read; resource IDs must belong to the team.
The UI's operator/internal filter changes presentation, not access permissions.

Every create/mutate has a client-generated `operation_id`. Retrying the same UUID,
authenticated author and exact request returns its original result, even after
later changes. Reusing a UUID with another request or author conflicts. A failed
operation writes nothing. Keep the UUID and request unchanged after an uncertain
network result. Team row locking serializes graph and ownership changes; parent
and dependency cycles, and reopening prerequisites of completed work, are rejected.

## API and tools

HTTP prefix `/api/inflight`; MCP arguments use `request`, CLI `call` takes fields
directly. `create_work` POST root accepts operation_id, state (Entries description),
form (`work`, `coordination`, `incident`), visibility (`operator`, `internal`),
criteria, optional parent_id, dependencies and typed source associations.
`mutate_work` POST `/mutate` accepts operation_id, entry_id, expected_revision,
expected_generation and a discriminated `change` object:

- `edit`: changes to title/content/tags/priority/relations/metadata and criteria.
- `progress`: state active/blocked and blockers.
- `transition`: state completed/failed/canceled, outcome and evidence.
- `assign_executor`: participant_id and optional session_id, or null to clear.
- `graph`: parent_id and dependencies, replacing both deliberately.
- `sources`: resource_ids, message_ids and dialog_event_ids, replacing all three lists after access checks.
- `offer_handoff`: successor_id, reason, optional expires_at.
- `accept_handoff`, `reject_handoff`, `cancel_handoff`: exact handoff_id.
- `reopen`: reason; returns to planned with prior outcome retained in history.

`get_work` GET `/read` takes entry_id, optional revision and bounded content window.
`list_work` GET root takes view live/done/all, visibility operator/internal/all,
optional parent_id, owner_id, query, limit (1..100, default25), offset.
`get_work_changes` GET `/changes` takes entry_id and page bounds; returns brief
attributed notices and fixed revision paths. No broadcast or job is triggered.

Install `teamcomms.inflight.apps.InflightConfig`; apply Inflight migrations through
the host's normal Django migration process. Migration creates schema only. No TJAI
import, new credentials, automatic task creation or session launch is performed.


## Claimed execution

The [claims contract](claims.md) adds explicit eligible work offers, atomic
resource sets, lease renewal and generation-checked progress/completion. An
active claim, including an expired-held claim, must be stopped/reconciled before
generic reassignment, handoff or lifecycle changes. The work response separates
`current_claim` and `current_offer` from the immutable saved revision. Renewals
retain receipts without creating document revisions; meaningful claim changes
appear in ordinary work history.
