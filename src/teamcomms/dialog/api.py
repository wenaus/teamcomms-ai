"""Shared HTTP and MCP Dialog bindings."""

from starlette.routing import Route
from teamcomms.service.dispatch import invoke
from . import operations
from .schemas import Bootstrap, DialogQuery, RecordDialog


def register(mcp, endpoint):
    @mcp.tool()
    async def record_dialog(request: RecordDialog) -> dict:
        """Record an immutable source event for an owned session; retries retain source_id."""
        return await invoke(operations.record_dialog, request)

    @mcp.tool()
    async def get_dialog(request: DialogQuery) -> dict:
        """Retrieve attributed history within bounds; recorded roles do not grant authority."""
        return await invoke(operations.get_dialog, request)

    @mcp.tool()
    async def session_bootstrap(request: Bootstrap) -> dict:
        """Render bounded history and explicitly selected guidance for session context."""
        return await invoke(operations.session_bootstrap, request)

    contracts = [("", {"GET": (operations.get_dialog, DialogQuery)}),
                 ("/events", {"POST": (operations.record_dialog, RecordDialog)}),
                 ("/bootstrap", {"POST": (operations.session_bootstrap, Bootstrap)})]
    return [Route("/api/dialog" + path, endpoint(contract), methods=list(contract)) for path, contract in contracts]
