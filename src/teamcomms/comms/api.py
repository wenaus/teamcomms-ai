"""HTTP and MCP bindings for Comms; streaming is an HTTP receiver interface."""

from starlette.routing import Route
from teamcomms.service.dispatch import invoke
from . import directory, operations
from .schemas import (Acknowledge, DeliveryHistory, Heartbeat, Inbox, MessageQuery, NewGroup,
                      NewResource, Page, RegisterSession, Report, SendMessage, Sessions, Subscribe, NotifyLLM)
from .stream import stream_messages


def register(mcp, endpoint):
    @mcp.tool()
    async def register_session(request: RegisterSession) -> dict:
        """Register or refresh a session belonging to the authenticated participant."""
        return await invoke(directory.register_session, request)

    @mcp.tool()
    async def heartbeat_session(request: Heartbeat) -> dict:
        """Update session availability and current model metadata."""
        return await invoke(directory.heartbeat_session, request)

    @mcp.tool()
    async def list_sessions(request: Sessions) -> dict:
        """Discover bounded team sessions with host, participant, and resource filters."""
        return await invoke(directory.list_sessions, request)

    @mcp.tool()
    async def register_resource(request: NewResource) -> dict:
        """Register a canonical project, checkout, or service; requires directory admin."""
        return await invoke(directory.register_resource, request)

    @mcp.tool()
    async def list_resources(request: Page) -> dict:
        """List canonical resources and their host/path aliases."""
        return await invoke(directory.list_resources, request)

    @mcp.tool()
    async def register_group(request: NewGroup) -> dict:
        """Register a team routing group; requires directory admin."""
        return await invoke(directory.register_group, request)

    @mcp.tool()
    async def list_groups(request: Page) -> dict:
        """List available team routing groups."""
        return await invoke(directory.list_groups, request)

    @mcp.tool()
    async def subscribe(request: Subscribe) -> dict:
        """Enable or disable a session's group or exact-topic subscription."""
        return await invoke(directory.subscribe, request)

    @mcp.tool()
    async def send_message(request: SendMessage) -> dict:
        """Publish content to a fixed audience using a stable UUID for retries."""
        return await invoke(operations.send_message, request)

    @mcp.tool()
    async def notify_llm(request: NotifyLLM) -> dict:
        """Deliberately notify selected AI sessions; requires reason, source and event identity."""
        return await invoke(operations.notify_llm, request)

    @mcp.tool()
    async def get_messages(request: Inbox) -> dict:
        """Read a bounded session inbox without acknowledging; resume using next_after."""
        return await invoke(operations.get_messages, request)

    @mcp.tool()
    async def get_message(request: MessageQuery) -> dict:
        """Read an authored or received message and bounded destination status."""
        return await invoke(operations.get_message, request)

    @mcp.tool()
    async def record_delivery(request: Report) -> dict:
        """Record a version-checked transport receipt; reserve uncertain before injection."""
        return await invoke(operations.record_delivery, request)

    @mcp.tool()
    async def acknowledge_message(request: Acknowledge) -> dict:
        """Record consideration without sending a reply or altering transport status."""
        return await invoke(operations.acknowledge_message, request)

    @mcp.tool()
    async def get_delivery_history(request: DeliveryHistory) -> dict:
        """Read bounded transport receipt history for an authored or received delivery."""
        return await invoke(operations.delivery_history, request)

    contracts = [
        ("/sessions", {"GET": (directory.list_sessions, Sessions), "POST": (directory.register_session, RegisterSession)}),
        ("/sessions/heartbeat", {"POST": (directory.heartbeat_session, Heartbeat)}),
        ("/resources", {"GET": (directory.list_resources, Page), "POST": (directory.register_resource, NewResource)}),
        ("/groups", {"GET": (directory.list_groups, Page), "POST": (directory.register_group, NewGroup)}),
        ("/subscriptions", {"POST": (directory.subscribe, Subscribe)}),
        ("/messages", {"GET": (operations.get_messages, Inbox), "POST": (operations.send_message, SendMessage)}),
        ("/notify-llm", {"POST": (operations.notify_llm, NotifyLLM)}),
        ("/messages/read", {"GET": (operations.get_message, MessageQuery)}),
        ("/messages/acknowledge", {"POST": (operations.acknowledge_message, Acknowledge)}),
        ("/deliveries", {"POST": (operations.record_delivery, Report)}),
        ("/deliveries/history", {"GET": (operations.delivery_history, DeliveryHistory)}),
    ]
    return [Route("/api/comms" + path, endpoint(contract), methods=list(contract)) for path, contract in contracts] + [
        Route("/api/comms/stream", stream_messages, methods=["GET"])]
