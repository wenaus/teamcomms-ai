"""Shared HTTP and MCP Dialog bindings."""

from starlette.routing import Route
from teamcomms.service.dispatch import invoke
from teamcomms.service.access import AccessError, current_authentication, current_principal
from teamcomms.service.dispatch import database_call
from starlette.responses import JSONResponse
from . import operations, sharing
from .schemas import (Bootstrap, DialogQuery, RecordDialog, SharingQuery, SharingNotice,
                      HumanSharingChoice)


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

    @mcp.tool()
    async def get_dialog_sharing(request: SharingQuery) -> dict:
        """Read one owned session's sharing notice/state; a read never grants consent."""
        return await invoke(sharing.get_dialog_sharing, request)

    @mcp.tool()
    async def record_sharing_notice(request: SharingNotice) -> dict:
        """Record an offered session notice, never a human decision or confirmed reading."""
        return await invoke(sharing.record_sharing_notice, request)

    async def choose(request):
        try:
            body = HumanSharingChoice.model_validate(await request.json())
            interactive = current_authentication.get().human_interactive
            def change(actor, body):
                return sharing.choose_human_sharing(actor, body, human_interactive=interactive)
            result = await database_call(change, current_principal.get(), body)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ValueError:
            return JSONResponse({"error": "Invalid sharing choice"}, status_code=400)
        except AccessError as error:
            return JSONResponse({"error": str(error)}, status_code=error.status)

    contracts = [("", {"GET": (operations.get_dialog, DialogQuery)}),
                 ("/events", {"POST": (operations.record_dialog, RecordDialog)}),
                 ("/bootstrap", {"POST": (operations.session_bootstrap, Bootstrap)}),
                 ("/sharing", {"GET": (sharing.get_dialog_sharing, SharingQuery)}),
                 ("/sharing/notice", {"POST": (sharing.record_sharing_notice, SharingNotice)})]
    return [Route("/api/dialog" + path, endpoint(contract), methods=list(contract)) for path, contract in contracts] + [
        Route("/api/dialog/sharing/choice", choose, methods=["POST"])]
