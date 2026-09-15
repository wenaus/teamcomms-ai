# Claims and resource reservations

Inflight work keeps its accountable owner. An explicit offer names eligible
participants, reported session capabilities, a deadline, lease duration and the
complete set of resources needed. Claiming assigns one executor and increments
the work's generation. Acquisition of every resource is atomic. A conflict on
one resource acquires none. Claims do not grant production credentials or rights.

Canonical resources use the existing Comms directory. Administrators bind each
managed resource to a persistent custodian and a protection mode: `advisory` or
`local_flock`. A resource's custodian remains named while idle or reserved.
Custodian transfer requires an offer and the successor's acceptance while idle.
It never silently replaces the holder of an active reservation.

## Lifecycle and lost contact

An active claim has a participant, optional registered session, ownership
generation and renewal deadline. Claimed progress and completion validate the
claim, generation, current work revision and lease. Renewal uses the claim and
generation; it does not add heartbeat noise to document revisions. Every mutation
has an immutable, authenticated operation receipt for exact retries.

Expiry is displayed as `expired_held`. It denies renewal, new guarded commands
and completion but retains all reservations. There is no automatic takeover.
Unconfirmed guarded runs also remain held after a wrapper crash.
The holder explicitly releases after stopping its work, or the accountable
owner/admin records `confirm_stopped` with evidence after establishing that the
preceding mutation has stopped. Both preserve the owner, release the entire set
and advance the generation. A successor then needs a new offer and claim.
Completion records outcome/evidence and releases all resources atomically.
Generic executor assignment, handoff acceptance or lifecycle changes cannot
bypass an active claim, including an expired claim.

## Guarded commands

`teamcomms-connect guard` wraps one foreground command using a pre-existing
claim. It loads the existing connector credential and an explicit local guard
configuration, acquires persistent POSIX file locks in resource UUID order,
validates the claim again, records a durable guard-run admission, then starts
the command. Only one unresolved guard run is allowed per claim. It periodically renews the
lease. Authority loss, renewal failure or a stop signal terminates the command's
process group before releasing locks. The child inherits lock descriptors so a
wrapper crash does not immediately unlock a surviving child. Lock files are
never unlinked. Locks are acquired without waiting; partial acquisition unwinds.

```sh
teamcomms-connect --config CONNECTOR.json guard \
  --guard-config GUARDS.json --claim-id UUID --generation N \
  --resource UUID --resource UUID -- command argument
```

A local guard configuration declares `host`, one shared absolute `lock_dir`, and
an allowlist `resource_ids`. All cooperating users of the protected entrypoint
must use that same directory and canonical IDs. The wrapper rejects other hosts,
unknown IDs, advisory resources and missing/expired claims. Configuration and
locks must be owned by the invoking OS account and inaccessible to other users.
The guarded command does not receive the TC bearer token in arguments or env.
Lease renewal never changes the original claim generation. The wrapper does not
release the server reservation automatically: after it returns, the caller
records completion or stopped/released work with its result and evidence.

`local_flock` means exclusion for cooperating foreground commands on the named
host and entrypoints configured with this guard. It is not a filesystem sandbox,
cluster-wide lock or exclusion against raw shell commands, daemons, detached
children or tools which close their inherited locks and bypass the guard.
Privileged commands must run the guard itself under the same privileged OS
account, with its private connector configuration, so it can stop every process
in the protected group. Wrapping sudo from an unprivileged guard does not provide
that stop guarantee. Direct, unintegrated deployment commands remain advisory. Resource names alone
cannot establish enforced coverage. A host's optional deployment wrapper uses
this guard before invoking its established deployment script; it still checks
committed revisions, clean package trees and operator authorization separately.

## Interfaces

Existing `inflight:read` and `inflight:write` govern work/claim access. Provisioning
resource custody/protection additionally requires `directory:write` and admin.
Custodians can offer/cancel a transfer; only its named successor can accept.
All paths below are relative to `/api/inflight` and all mutation requests carry
`operation_id` (reuse the UUID and exact request on retry).

| Tool | HTTP | Fields |
|---|---|---|
| manage_work_resource | POST /resources/manage | action provision/offer_transfer/accept_transfer/cancel_transfer, resource_id, expected_generation, custodian_id, protection, successor_id, transfer_id, reason |
| list_work_resources | GET /resources | limit, offset |
| offer_work | POST /offers | entry_id, expected_revision, expected_generation, eligible_participant_ids, resource_ids, required_capabilities, expires_at, lease_seconds, policy |
| claim_work | POST /claims | offer_id, expected_revision, expected_generation, optional session_id |
| update_claim | POST /claims/update | claim_id, expected_generation, action renew/progress/release/confirm_stopped/complete, expected_revision for state changes, reason/state/outcome/evidence |
| get_claim | GET /claims/read | claim_id |
| validate_claim | POST /claims/validate | claim_id, expected_generation, resource_ids |
| record_guard_run | POST /claims/guard | claim_id, expected_generation, run_id, action start/finish, resource_ids, command_sha256 or stopped/exit_code |

Lease duration is 30–3600 seconds. Offers require 1–50 explicit eligible
participants and at most 30 distinct resources; capability selectors use at most
20 labels. Session capabilities are participant-reported selection data, not
independently verified qualifications. An offer with required capabilities needs
a registered claiming session containing them. Guarded offers require at least
one resource and every resource to have `local_flock` protection. Pagination is
bounded to 100 rows. Claims and resource status appear in the work view; history
and receipts retain actor, generation and outcome. Migration creates schema only.


## Trusted host provisioning

The optional Django management command `provision_work_resource` is a local
administration entrypoint requiring the host's database access. It registers
canonical resources and explicit custody, using the selected active custodian
as the provisioning audit identity. It does not change that participant's role
or credentials and is not exposed through embedded HTTP/MCP administration.
Provide `--custodian-id`, `--key`, `--kind`, `--name`, `--host` and `--protection`.
Checkouts also require `--project-id` and one or more verified `--alias` paths.
Use the same exact definitions on retry. A different custodian/protection for an
existing resource conflicts; changing custody uses accepted transfer.

Offers capture resource generations as well as work generation. A resource
change after the offer requires a fresh offer, so delayed offers cannot acquire
changed resources silently. Exact retry receipts describe their original result;
they are never a substitute for fresh validation before an external mutation.

## Execution workers

[The wrangle-ai adapter](execution.md) extends offers with optional execution
profiles and headless eligibility time. Absent execution fields preserve existing
interactive claims and exact retry receipts. An execution admission, including
an unconfirmed admission after a crash, blocks ordinary claim release/completion.
Confirmed successful results must match completion outcome/evidence. Explicit
owner stopped-work reconciliation closes unresolved execution records as well as
guard runs; lease expiry alone still releases nothing.
