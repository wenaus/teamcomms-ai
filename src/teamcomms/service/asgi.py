"""Authenticated HTTP/MCP assembly, adapted from TJAI's standalone MCP pattern."""

from contextlib import asynccontextmanager
import os
from urllib.parse import urlsplit

import django
from django.apps import apps
from django.conf import settings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


def create_app():
    """Return an ASGI app; an initialized host Django project supplies its settings."""
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teamcomms.service.settings")
        django.setup()

    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from . import operations
    from .access import AccessError, authenticate, current_principal
    from .dispatch import database_call, invoke
    from .schemas import CredentialReference, DirectoryQuery, NewCredential, NewParticipant

    hosts = settings.ALLOWED_HOSTS
    if not hosts or any("*" in h for h in hosts):
        raise ValueError("TeamComms requires explicit ALLOWED_HOSTS")
    mcp = FastMCP(
        "TeamComms AI", stateless_http=True, json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=[item for h in hosts for item in (h, f"{h}:*")],
            allowed_origins=[f"{scheme}://{h}:*" for h in hosts for scheme in ("http", "https")]
                + [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")],
        ),
    )

    @mcp.tool()
    async def whoami() -> dict:
        """Return the authenticated participant, team, and credential scopes."""
        return await invoke(operations.whoami)

    @mcp.tool()
    async def list_participants(limit: int = 50, offset: int = 0) -> dict:
        """Read a bounded page of team participants."""
        return await invoke(operations.list_participants, DirectoryQuery(limit=limit, offset=offset))

    @mcp.tool()
    async def create_participant(request: NewParticipant) -> dict:
        """Create a team member; requires directory:write and admin membership."""
        return await invoke(operations.create_participant, request)

    @mcp.tool()
    async def issue_credential(request: NewCredential) -> dict:
        """Issue a scoped credential; returns its secret once to an authorized admin."""
        return await invoke(operations.issue_credential, request)

    @mcp.tool()
    async def revoke_credential(request: CredentialReference) -> dict:
        """Revoke a credential belonging to this team; requires an authorized admin."""
        return await invoke(operations.revoke_credential, request)

    def response(value, status=200):
        return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})

    async def health(request):
        return response({"status": "ok"})

    async def http_operation(request):
        method = "GET" if request.method == "HEAD" else request.method
        operation, schema = request.scope["endpoint_contract"][method]
        try:
            if method == "GET":
                data = dict(request.query_params)
            else:
                data = await request.json()
            if schema is None and data:
                return response({"error": "Unexpected parameters"}, 400)
            argument = schema.model_validate(data) if schema else None
            return response(await invoke(operation, argument))
        except ValueError:
            return response({"error": "Invalid request parameters"}, 400)
        except AccessError as error:
            return response({"error": str(error)}, error.status)

    def endpoint(contract):
        async def handler(request):
            request.scope["endpoint_contract"] = contract
            return await http_operation(request)
        return handler

    mcp_app = mcp.streamable_http_app()
    from teamcomms.entries.api import register as register_entries
    entry_routes = register_entries(mcp, endpoint)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/api/whoami", endpoint({"GET": (operations.whoami, None)}), methods=["GET"]),
        Route("/api/participants", endpoint({
            "GET": (operations.list_participants, DirectoryQuery),
            "POST": (operations.create_participant, NewParticipant),
        }), methods=["GET", "POST"]),
        Route("/api/credentials", endpoint({"POST": (operations.issue_credential, NewCredential)}), methods=["POST"]),
        Route("/api/credentials/revoke", endpoint({"POST": (operations.revoke_credential, CredentialReference)}), methods=["POST"]),
        *entry_routes,
        Mount("/mcp", mcp_app),
    ], lifespan=lifespan)

    class Guard:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or scope["path"] == scope.get("root_path", "") + "/health":
                return await app(scope, receive, send)
            headers = scope.get("headers", [])
            auth = [v.decode("latin1") for k, v in headers if k.lower() == b"authorization"]
            if len(auth) != 1 or not auth[0].startswith("Bearer "):
                return await response({"error": "Bearer credential required"}, 401)(scope, receive, send)
            origins = [v.decode("latin1") for k, v in headers if k.lower() == b"origin"]
            try:
                origin = urlsplit(origins[0]) if origins else None
                origin_allowed = len(origins) <= 1 and (origin is None or (
                    origin.hostname in hosts and origin.scheme in ("http", "https")
                    and not origin.username and not origin.password
                ))
            except ValueError:
                origin_allowed = False
            if not origin_allowed:
                return await response({"error": "Origin not allowed"}, 403)(scope, receive, send)
            try:
                actor = await database_call(authenticate, auth[0][7:])
            except AccessError as error:
                return await response({"error": str(error)}, error.status)(scope, receive, send)
            # Bound both API and MCP requests before either parser receives them.
            body = bytearray()
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if len(body) > 65536:
                    return await response({"error": "Request too large"}, 413)(scope, receive, send)
                if not event.get("more_body", False):
                    break
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            async def no_cache(message):
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = list(message.get("headers", [])) + [(b"cache-control", b"no-store")]
                await send(message)

            token = current_principal.set(actor)
            try:
                await app(scope, replay, no_cache)
            finally:
                current_principal.reset(token)

    return Starlette(routes=[Mount("/", Guard())], lifespan=lifespan,
                     middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts)])
