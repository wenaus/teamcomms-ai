"""HTTP and MCP bindings for the shared Entries operations."""

from uuid import UUID
from starlette.routing import Route

from teamcomms.service.dispatch import invoke
from . import operations
from .schemas import CreateEntry, ListRevisions, ReadEntry, RestoreEntry, SearchEntries, UpdateEntry


def register(mcp, endpoint):
    @mcp.tool()
    async def create_entry(request: CreateEntry) -> dict:
        """Create a note or document with an attributable first revision."""
        return await invoke(operations.create_entry, request)

    @mcp.tool()
    async def get_entry(entry_id: UUID, revision: int | None = None,
                        content_offset: int = 0, max_content_length: int = 10000) -> dict:
        """Read current or fixed revision content in bounded character ranges."""
        return await invoke(operations.read_entry, ReadEntry(entry_id=entry_id, revision=revision,
            content_offset=content_offset, max_content_length=max_content_length))

    @mcp.tool()
    async def update_entry(request: UpdateEntry) -> dict:
        """Update named state fields against an expected revision; preserve others."""
        return await invoke(operations.update_entry, request)

    @mcp.tool()
    async def restore_entry(request: RestoreEntry) -> dict:
        """Copy a historical state into a new revision after checking current revision."""
        return await invoke(operations.restore_entry, request)

    @mcp.tool()
    async def get_entry_revisions(entry_id: UUID, limit: int = 25, offset: int = 0) -> dict:
        """List bounded revision metadata without repeating document contents."""
        return await invoke(operations.list_revisions, ListRevisions(entry_id=entry_id, limit=limit, offset=offset))

    @mcp.tool()
    async def search_entries(request: SearchEntries) -> dict:
        """Search current entries by text, metadata, tags, relationships, and time."""
        return await invoke(operations.search_entries, request)

    return [
        Route("/api/entries", endpoint({"GET": (operations.search_entries, SearchEntries),
                                       "POST": (operations.create_entry, CreateEntry)}), methods=["GET", "POST"]),
        Route("/api/entries/read", endpoint({"GET": (operations.read_entry, ReadEntry)}), methods=["GET"]),
        Route("/api/entries/update", endpoint({"POST": (operations.update_entry, UpdateEntry)}), methods=["POST"]),
        Route("/api/entries/restore", endpoint({"POST": (operations.restore_entry, RestoreEntry)}), methods=["POST"]),
        Route("/api/entries/revisions", endpoint({"GET": (operations.list_revisions, ListRevisions)}), methods=["GET"]),
        Route("/api/entries/search", endpoint({"POST": (operations.search_entries, SearchEntries)}), methods=["POST"]),
    ]
