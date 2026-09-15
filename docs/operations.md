# Installation and recovery

TeamComms uses one authoritative PostgreSQL database per team. The package
contains the service, browser assets, migrations and optional connectors. An
embedded deployment uses its host's accounts, database and service lifecycle;
SWF uses devcloud authentication and the monitor database. Standalone bootstrap
applies only to a separate installation with its own identity authority.

## Release inventory

Record the package Git revision, built wheel SHA-256, Python and PostgreSQL
versions, installed dependency versions, host application revision, migration
list, public URL/prefix and process-manager entrypoints. Retain the previous
wheel/environment and host release until recovery acceptance finishes. The
development version `0.1.0.dev0` alone cannot distinguish package revisions.
An upgrade between these revisions requires explicit reinstall or a fresh venv.

Keep private configuration outside checkouts and release trees: database access,
host authentication configuration, connector token paths, native session IDs,
SQLite state directories, worker journals and resource guard configuration.
Use owner-only directories (0700) and secret files (0600). An inventory contains
paths and identifiers, never secret contents. Guard configuration for privileged
commands remains outside directories whose deployment changes ownership.

## Independent installation

Requirements are Python 3.11+, PostgreSQL 14+ and a database provisioned by the
installation operator. Build a wheel from the selected clean revision and retain
its checksum. Install it into an isolated venv, without a personal-system checkout
or personal configuration on its import path:

```sh
python3 -m venv /srv/teamcomms/releases/RELEASE/venv
/srv/teamcomms/releases/RELEASE/venv/bin/python -m pip install /path/to/teamcomms_ai-WHEEL.whl
/srv/teamcomms/releases/RELEASE/venv/bin/python -m pip check
```

The deployment supplies `TEAMCOMMS_DATABASE_URL`, `TEAMCOMMS_SECRET_KEY` and
explicit `TEAMCOMMS_ALLOWED_HOSTS`. Put secrets in the host's existing secret
store or private service environment, rather than shell history. Run `teamcomms
migrate`, then the one-time `teamcomms bootstrap` described in [service setup](service.md).
Use the API to provision participants and the local `issue-token` command to
grant explicit component scopes. The HTTP credential request accepts at most
eight scopes per credential; local provisioning supports the full allowed set.

Serve on loopback with `teamcomms serve`, under the installation's process manager.
Terminate HTTPS at its proxy, set explicit public hosts and stream timeouts,
disable stream buffering, and restrict backend access. `/health` is process
liveness, not database readiness. Verify an authenticated database-backed read
and a bundled asset through the final URL. Standalone browser authentication
needs the installation's chosen host/session integration; the shipped browser
must not receive a bearer credential through browser storage.

The [embedded guide](embedded.md) defines app registration, ASGI lifespan,
identity mapping, CSRF and proxy trust. Apply all seven TC apps' migrations through
the host. Do not run standalone bootstrap, mint TC credentials, or create a second
database for an existing SWF installation. Browser assets travel in the wheel;
the host provides the browser CSRF endpoint and public prefix.

## Connectors and staged enablement

Install the same pinned wheel with `[connectors]` dependencies on a selected
client host. Configure the public URL, existing participant token file, host and
private state directory. Check `whoami` before registration. Native client
versions and exported socket/RPC capabilities must match the
[compatibility evidence](connectors.md#verification).

Start one explicitly selected receiver or wrapper. Record its TC session ID,
native ID, socket, process-manager label and saved cursor. Receiver restart for
the same native session preserves its state. A new native session registers a
new TC session; it must not impersonate the departed session to inherit delivery.
Historical Dialog can be requested explicitly with provenance and bounded context.

Enable subscriptions, Dialog capture/bootstrap, Mattermost routes, attention
controls and execution profiles individually. Their defaults and recovery rules
are in the respective component guides. Enrollment does not enable recording,
import history or launch work. The `execution` extra adds pinned wrangle-ai;
workers require explicit profiles and offers. Preserve the existing coordination
channel until the replacement has accepted a message and reported consideration.

## Consistent backup

Back up the entire authoritative database, including host tables in embedded
mode. TC tables have cross-component foreign keys and integrity triggers; a
selected-table JSON export is not a service backup. Back up host identity state
with its established procedure and retain stable `(provider, subject)` mappings.
Attachment storage, if supplied by the installation, needs matching retention.

For a coordinated recovery point:

1. Announce the bounded write pause through the surviving coordination channel.
   Drain workers and establish that guarded external mutations have stopped.
   Preserve held/uncertain claims for reconciliation; expiry alone is insufficient.
2. Pause affected producers, bridges, receivers and recorders using their documented
   controls; retain pending outboxes, accepted receipts and execution journals.
   Quiesce host writes for a cross-system recovery point. Record the UTC cut time.
3. Dump the database with its matching PostgreSQL client:
   `pg_dump --format=custom --file=database.dump DATABASE_NAME`.
   Supply credentials through the host's private connection configuration.
   Do not put passwords in command arguments. An online dump is transactionally
   consistent for PostgreSQL; it does not atomically snapshot external systems.
4. Back up each stopped connector's entire state directory, configuration and
   worker journals with owner/mode preservation. If SQLite remains live, use its
   backup API; copying only `state.sqlite3` can omit committed WAL data. Preserve
   external post mappings, recorder offsets and exact prepared requests.
5. Record checksums, database/server versions and release inventory alongside the
   backup. Store the secret-bearing archive with restricted access and encryption
   under the installation's backup policy. Resume the paused services.

## Restore and reconciliation

Restore first into a new isolated database with no public route, live receivers,
workers or Mattermost connections. Never run two writable authorities for the
same team. Provision the required roles/extensions, then use
`pg_restore --exit-on-error --no-owner --no-privileges --dbname=NEW_DATABASE database.dump`
when mapping ownership to the isolated database's role. Production ownership and
privileges must be reapplied by the host's deployment policy.

Use the matching retained package, check migration state, compare stable team,
participant, Entry/revision, Pouch, work/generation, session, delivery and receipt
IDs, and verify pinned references. Check an immutable revision and protected
binding still reject updates. Verify pending delivery and saved execution results
without dispatching to a real native client or rerunning work.

Restore to production only after fencing the former authority and reconciling
effects after the cut time. A restored database may predate an external command,
native injection, token revocation or Mattermost post. Preserve newer local journals
and external evidence; do not reset cursors, clear uncertainty, or republish from
the older snapshot. Reapply host revocations made after the backup before access
resumes. A live client reporting acceptance is not proof that a model considered
the message. Resolve uncertain dispatch with native evidence; resolve work only
after confirming it stopped. Restore one selected connector first, then the rest.

## Upgrade and rollback

Coordinate package/host changes with affected maintainers. Preserve their local
edits; record clean pushed revisions before the host's deployment. Install the
candidate wheel in a fresh environment, check dependencies and migration plan,
and verify restore against the pre-upgrade backup before enabling writes.
Apply migrations through the host's normal deployment. Check the imported package
path/revision, authenticated read, bundled assets and preserved canonical references.
Restart only affected components, keeping a coordination path available.

A code-only rollback uses the retained release only if it is compatible with the
current schema. Do not blindly reverse integrity migrations. If compatibility is
unknown, quiesce writers and restore the pre-upgrade backup into a new database
with the old release, then perform the reconciliation above. Writes after that
backup must be accounted for; rollback cannot silently discard accepted work.
The isolated rehearsal below verifies this procedure without changing production.

## Removal

Disable the selected routes/subscriptions and drain explicitly enabled workers.
Stop only TC-owned receiver/recorder/bridge jobs using their recorded lifecycle;
mark sessions offline where available. Leave native clients, their settings and
other communication bridges running. Revoke TC-specific host tokens through the
existing host account system when retiring their use. Keep the database, local
state, journals, exports and release inventory according to retention policy.
Removing a package or navigation link does not authorize deleting history,
reassigning work, unlinking resource locks or dropping database schema.

## Isolated rehearsal

`scripts/rehearse_recovery.py` accepts separately installed old/candidate Python
interpreters and creates a new private, socket-only PostgreSQL cluster. It creates
synthetic document revisions, a pinned work reference, Pouch and pending delivery,
upgrades their database, restores a full dump and checks the same records. It also
restores the pre-upgrade dump under the old package for rollback evidence. Processes
are bounded and stopped on exit; the private evidence directory is retained.
It never connects to deployment databases, native clients or model providers.
It is a focused operational rehearsal, not a suite runner. For a selected
old and candidate wheel, provision two fresh venvs, install each wheel, and run:

```sh
python3 scripts/rehearse_recovery.py \
  --old-python /path/to/old-env/bin/python \
  --candidate-python /path/to/candidate-env/bin/python \
  --evidence-dir /tmp/tc-recovery-UNIQUE
```

Use a short absolute evidence path because PostgreSQL Unix-socket paths have an
OS length limit. The directory must not exist. PostgreSQL tools must be installed
and discoverable through `pg_config`. The old release must support the document,
Pouch, Inflight and Comms fixture contracts; this is an upgrade rehearsal from a
selected supported baseline, not a universal migration test.

`scripts/measure_delivery.py` publishes one frozen marker to explicit session
IDs and reports its receipts separately from acknowledgment. It uses an existing
connector configuration, writes no credentials into evidence, and never polls:

```sh
python scripts/measure_delivery.py publish --config CONNECTOR.json \
  --evidence-dir NEW_PRIVATE_DIRECTORY --session-id UUID --session-id UUID \
  --content-file SELECTED_MARKER.txt
python scripts/measure_delivery.py report --config CONNECTOR.json \
  --evidence-dir NEW_PRIVATE_DIRECTORY
```

Run with the connector-enabled venv. After a lost publication response, repeat
`publish` with only the same config and evidence directory; it reuses the saved
UUID/body. Confirmed publication cannot be sent again by this command. The marker
must identify its purpose and acknowledgment route. Detection time is synthetic
request preparation; source watcher polling is a separate measurement. A server
receipt bounds native write/acceptance reporting, while model acknowledgment
depends on client scheduling and tool availability. Five-second healthy delivery
is a transport target, not a promise of five-second model consideration.
