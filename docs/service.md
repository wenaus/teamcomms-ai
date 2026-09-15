# Central service

The service provides a PostgreSQL identity store and authenticated HTTP and MCP
operations for team membership, participant discovery, and credential management.
The [Entries interface](entries.md) adds versioned content and search.
[Comms](comms.md) adds directory resources, sessions, routing, and message delivery.
[Dialog](dialog.md) adds attributed recording, history retrieval, and session context.
It uses Django 5.2 for ORM operations and migrations, Starlette for the ASGI
application, and FastMCP from the official MCP Python SDK 1.x. Component storage
and message delivery follow the [design](design.md).

## Source mapping

The foundation adapts TJAI's standalone ASGI composition and shared ORM service
pattern from revision `653a19639d47332f89983066ebbb10f0052dcc5f`:

| TJAI source | TeamComms location | Treatment |
|---|---|---|
| `tjai_project/mcp_asgi.py` | `service/asgi.py`, `service/dispatch.py` | ASGI composition, managed MCP lifespan, async access to synchronous ORM operations |
| `tjai_app/mcp.py` | `service/asgi.py` | Official SDK tool registration calling shared services |
| `tjai_app/services.py` | `service/operations.py` | Transport-independent validation and transactional operations |
| Shared personal bearer configuration | `service/access.py`, `service/models.py` | Participant-bound credentials, scopes, membership, expiry, and revocation |

Paths in the TeamComms column are relative to `src/teamcomms/`. The identity
models and authorization rules are implemented for TeamComms. Source attribution
is retained in [NOTICE](../NOTICE). Later component extraction maps the entry,
mailbox, transcript, and interface code into their respective packages.

## Setup

Python 3.11 or later and PostgreSQL 14 or later with UTF-8 encoding are required.
The database and role are provisioned by the installation operator. The role applying migrations
needs schema-creation privileges in that database.

From a checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --editable .
export TEAMCOMMS_DATABASE_URL='postgresql:///teamcomms'
export TEAMCOMMS_ALLOWED_HOSTS='localhost,127.0.0.1'
.venv/bin/teamcomms migrate
.venv/bin/teamcomms bootstrap --team 'Example team' --owner 'Operator' \
  --token-file ./owner-token
.venv/bin/teamcomms serve --host 127.0.0.1 --port 8765
```

The database URL supports PostgreSQL connection parameters such as `sslmode`.
Configuration is read from the environment; the service does not load `.env`
files or search personal installations. Keep database credentials in the
deployment's secret configuration. `TEAMCOMMS_ALLOWED_HOSTS` names explicit
hostnames without ports or wildcards.

Bootstrap creates one team, its initial human administrator, membership, and
credential. It refuses an initialized database. The secret is written to a new
file with mode 0600; stdout contains identifiers and the file path. Existing
files are never overwritten. Store that file outside version control.

The server binds to loopback by default. Remote service access uses HTTPS through
the installation's reverse proxy, with its public hostname allowed explicitly.
The default launcher does not trust forwarded headers. `/health` reports process
liveness; database operations report unavailable storage separately.

## Identity and authorization

Each database holds one team, enforced by a database constraint. Participants
have stable UUIDs and kinds `human`, `ai`, `program`, or `connector`. A membership
joins a participant to the installation's team and records `admin` or `member`
role and active status. An AI can reference an active human member as its operator.
That association records context and grants no additional permissions.

In standalone mode, endpoints except `/health` require `Authorization: Bearer <credential>`.
Embedded mode uses the host's authentication through the
[host integration interface](embedded.md).
Credentials contain 256 bits of random secret material; the database stores
SHA-256 digests, scopes, optional expiry, and revocation time. Authentication
resolves participant identity from the credential. A caller cannot select its
identity or team through request fields.

| Scope | Permission |
|---|---|
| `directory:read` | List team participants |
| `directory:write` | Create members; also requires admin membership |
| `credentials:write` | Issue or revoke team credentials; also requires admin membership |
| `entries:read` | Read entries, revision history, and search results |
| `entries:write` | Create, update, and restore notes and documents |
| `sessions:write` | Register, heartbeat, and subscribe own sessions |
| `comms:read` | Read authored/received messages and own session streams |
| `comms:write` | Publish messages and report own destination receipts |
| `dialog:read` | Read shared team transcript history and bootstrap context |
| `dialog:write` | Record transcript events for own sessions |
| `inflight:read` | Read work, ownership, offers, claims and execution results |
| `inflight:write` | Create work and perform authorized lifecycle/claim/execution changes |
| `capcom:read` | Read topics, notices and owned-session attention policy |
| `capcom:write` | Publish authorized topic changes, notices and owned-session attention policy |

Every authenticated member can inspect its own identity. Provisioned participants
start as members and can receive directory-read, Entries, session, and Comms credentials. The
bootstrap administrator can issue additional credentials to its own identity. Issuance can only grant scopes
held by the issuing credential. API operations do not promote membership roles.

An installation operator with direct database access can provision a credential
with explicit scopes, including scopes introduced by an upgrade:

```sh
.venv/bin/teamcomms issue-token --participant PARTICIPANT_UUID \
  --scope entries:read --scope entries:write --token-file ./entries-token
```

The token file must be new and is written with mode 0600. The command verifies
active membership and limits scopes to those permitted for its role. Existing
credentials remain unchanged; revocation uses the authenticated API or MCP tool.

Revocation, expiry, and inactive membership reject subsequent authenticated
requests, including MCP calls. A request already admitted may finish. The
HTTP and MCP interfaces share authorization, field validation, and operations.
Credential responses and authenticated results carry `Cache-Control: no-store`.

## Interfaces

| HTTP | MCP tool | Input |
|---|---|---|
| `GET /health` | — | None; public liveness |
| `GET /api/whoami` | `whoami` | None |
| `GET /api/participants` | `list_participants` | `limit` (1–200, default 50), `offset` (default 0) |
| `POST /api/participants` | `create_participant` | `name`, `kind`, optional `operator_id` |
| `POST /api/credentials` | `issue_credential` | `participant_id`, `scopes`, optional `expires_at` |
| `POST /api/credentials/revoke` | `revoke_credential` | `credential_id` |

HTTP POST bodies are JSON objects. MCP mutation tools accept the same object
under a `request` argument; read-tool arguments are top-level. MCP is mounted at
`/mcp/` and supports standard initialization, tool discovery, and calls. Tools
return JSON content; authorization or validation failures set MCP `isError`.
MCP uses stateless JSON responses. Comms exposes a separate authenticated HTTP
stream with durable replay; see its [interface reference](comms.md).

Directory results contain `participants` and `next_offset`. Each participant
includes its ID, name, kind, operator reference, membership role, and active
status. Credential issuance returns its ID, participant, scopes, and the secret
once. Revocation is idempotent. Expiry timestamps must be future timestamps with
an explicit timezone. Unknown fields and out-of-range pagination are rejected.

Unauthenticated or invalid credentials return HTTP 401, insufficient permission
403, missing targets 404, and invalid input 400. Requests are bounded to 64 KiB.
Host validation covers the whole application; browser-origin checks apply to
authenticated requests. No cookie or loopback authentication bypass is present.

## Host integration

An existing Django application includes `teamcomms.service.apps.ServiceConfig`
and `teamcomms.entries.apps.EntriesConfig`, plus
`teamcomms.comms.apps.CommsConfig`, `teamcomms.dialog.apps.DialogConfig`,
`teamcomms.pouch.apps.PouchConfig`, `teamcomms.inflight.apps.InflightConfig`
and `teamcomms.capcom.apps.CapcomConfig`,
in `INSTALLED_APPS` and applies their migrations to the team's authoritative
database. Host settings supply the PostgreSQL connection and explicit
`ALLOWED_HOSTS`. `teamcomms.service.asgi.create_app()` respects an already
initialized Django application and returns the HTTP/MCP ASGI application.

When mounting that application, the host enters its
`router.lifespan_context(application)` during startup so the MCP session manager
runs, and exits it during shutdown. A separate process can serve the same
application and database. Host callers use `service.operations` with an
authenticated `Principal`; trusted host code is responsible for establishing
that principal through `service.access.authenticate` or an equivalent verified
identity mapping. `create_app()` uses standalone bearer authentication;
`create_app(host_auth=..., mount_path=...)` uses verified host identities and an
optional URL prefix. The [embedded operation reference](embedded.md) specifies
callbacks, identity mapping, browser protection, stream revalidation, and proxying.

Synchronous database operations run through `service.dispatch`, which preserves
request identity across the async boundary and closes database connections after
use. See [Django's async database guidance](https://docs.djangoproject.com/en/5.2/topics/async/)
and the [MCP SDK 1.x reference](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
for the framework contracts.

## Verification

The [operating guide](operations.md) covers independent installation,
backup/restore, upgrades, rollback and removal. The [adoption record](adoption.md)
distinguishes isolated recovery evidence from live host commissioning.

[The isolated service suite](../tests/readme.md) provisions a temporary
PostgreSQL cluster, applies migrations, checks bootstrap and installed startup,
and exercises HTTP/MCP authorization with synthetic participants. It covers
credential storage, scope limits, revocation, expiry, inactive membership,
identity substitution, pagination, and transport guards.
