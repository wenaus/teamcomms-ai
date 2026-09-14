# Embedded operation

TeamComms can use an existing application's database, authentication, URL
namespace, and deployment. The host validates browser sessions, client tokens,
and program identities. TC maps verified identities to participants and applies
component permissions to each request.

## Application setup

Include the [TC Django applications](service.md#host-integration) in the host's
`INSTALLED_APPS` and apply migrations through its deployment. Host provisioning
creates the installation's `Team` row and retains its ID. The database holds one
team; embedded setup does not require the standalone credential bootstrap.

After initializing the host's Django settings, construct the ASGI application:

```python
from teamcomms.service.asgi import create_app
from teamcomms.service.embedded import HostAuthentication

# Async callbacks supplied by the hosting application.
authentication = HostAuthentication(
    provider="host-accounts",
    team_id=team_id,
    resolve=resolve_host_request,
    revalidate=revalidate_host_request,
    check_csrf=check_host_csrf,
)
application = create_app(host_auth=authentication, mount_path="/ops/teamcomms")
```

Run this application in the host's ASGI service or mount it in its ASGI router.
A parent application enters `application.router.lifespan_context(application)`
during its own lifespan so the MCP session manager starts and stops correctly.
Use either `mount_path` or the parent router's mount prefix, applying it once.

Embedded HTTP and MCP interfaces omit participant creation and credential
issuance/revocation. Participant discovery remains available. Authentication uses
the host callbacks exclusively; standalone TC credentials cannot authenticate
to the embedded app.

## Host callbacks

All callbacks are async. Use the host's async database boundary for synchronous
account/session operations. `scope` is the ASGI request scope; `body` is the
bounded request body as bytes.

| Callback | Contract |
|---|---|
| `resolve(scope)` | Validate the host session, token, or trusted proxy identity; return `HostIdentity` or raise `AccessError` |
| `revalidate(scope, original_identity)` | Recheck that request's authentication against current host state; return current identity and permissions or raise `AccessError` |
| `check_csrf(scope, body, identity)` | Validate the host's CSRF protection for an unsafe browser-session request; return exactly `True` on success |

CSRF validation can use a verified middleware result or the host's normal
header/form token and origin/referer checks. The default identity requires this
callback on unsafe methods, including MCP POSTs. Missing or unsuccessful CSRF
validation returns 403. See [Django's CSRF contract](https://docs.djangoproject.com/en/5.2/ref/csrf/).

For an independently validated bearer token or program credential, the host sets
`session_authenticated=False`. Cookie authentication retains the default `True`.
Trusted proxy identity must preserve the original authentication method and the
host's browser protections.

```python
from teamcomms.service.embedded import HostIdentity

identity = HostIdentity(
    subject=str(account.id),
    name=account.display_name,
    kind="human",
    role="member",
    scopes=frozenset({"directory:read", "entries:read", "entries:write",
                      "sessions:write", "comms:read", "comms:write"}),
    active=account.is_active,
    session_authenticated=True,
)
```

The host derives permissions from its account policy. The example scopes are
illustrative; grant those appropriate to the request. `credentials:write` is
unavailable in embedded identities. Account roles are `member` and `admin`;
component administration also requires its corresponding scope. TC access grants
no host production-operation permissions.

Raise `AccessError(message, 401)` for invalid, expired, or revoked authentication,
403 for denied access, and 503 for an unavailable authority. Unexpected callback
failures return 503 and log their exception type without exposing credentials.
Duplicate Authorization headers are rejected.

## Identity and lifecycle

`(provider, subject)` maps to a stable TC membership and participant. The provider
identifies an account namespace; the subject is an immutable host account or
actor ID. Names, email addresses, and token values are unsuitable mapping keys.
First-use enrollment is transactional across processes. Display name, account
role, and active state follow verified host results; scopes come from each request.

Kinds are `human`, `ai`, `program`, and `connector`. An AI has its own subject and
can supply an `operator` containing the associated human's `HostIdentity`.
The host establishes these associations through verified context, preserving AI
authorship and its operator while using host authentication. A bound subject's
kind and operator association cannot silently change.

An inactive host identity is denied and its mapped membership becomes inactive.
A later active host result reactivates it. Revoking an individual token denies
that token while other valid account sessions can continue. Existing records
retain their participant IDs.

## Streaming and proxying

Streams call `revalidate` before database reads and during the five-second idle
recheck. They verify the same subject and participant and apply current scopes.
Revocation, account deactivation, loss of read permission, identity substitution,
or an unavailable authentication authority ends the stream with an error event.
Revalidation must consult current host authority; a cached admission-time user
object is insufficient. An operation already admitted may finish.

The host supplies HTTPS, its proxy, and any tunnel. Forward methods, bodies,
query strings, authentication context, and `Last-Event-ID`. Preserve the public
Host and original scheme through explicitly trusted proxy configuration. TC
retains host/origin validation and does not interpret arbitrary forwarded
identity headers. The host strips caller-supplied assertions before adding
verified proxy identity and restricts backend trust to its designated path.

Relay stream chunks immediately, disable response caching/buffering, and allow
a read timeout longer than the five-second idle interval. Streams reconnect at
most every 25 seconds. Preserve status codes and stream errors. Forward the
configured prefix intact: MCP is at `<prefix>/mcp/`, HTTP operations at
`<prefix>/api/...`, and liveness at `<prefix>/health`. Verify redirects against
the external scheme, host, and prefix after deployment.

Connectors use the host's external base URL and existing bearer token file.
The host integration validates that token; renewal and revocation remain in
the host account system.

## Verification

[Service tests](../tests/readme.md) cover host session/token identity, CSRF,
concurrent mapping, AI/operator attribution, permissions, disabled credential
provisioning, and nested mounting. A real local HTTP proxy check verifies immediate
stream delivery, disconnect/replay, and termination on host token revocation,
permission loss, identity change, and authority failure. Tests use synthetic
accounts and a temporary PostgreSQL cluster. The installing application's
callbacks, external proxy, and tunnel require verification on that host.
