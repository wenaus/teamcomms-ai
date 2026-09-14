"""HTTP and MCP bindings for the shared Entries operations."""

from uuid import UUID
from starlette.routing import Route

from teamcomms.service.dispatch import invoke
from . import operations
from .schemas import CreateEntry, ListRevisions, ReadEntry, RestoreEntry, SearchEntries, UpdateEntry
from . import editing
from .edit_schemas import EditEntry, PreviewEdits, ApplyEdits, ReadEdit, ReadTarget


def register(mcp, endpoint):
    @mcp.tool()
    async def edit_entry(request: EditEntry) -> dict:
        """Apply surgical edits atomically. Reuse the exact operation ID/request on retry; inspect result.status."""
        return await invoke(editing.edit_entry, request)

    @mcp.tool()
    async def preview_entry_edits(request: PreviewEdits) -> dict:
        """Freeze at most 20 explicit entry/revision targets and preview changes without changing entries."""
        return await invoke(editing.preview_edits, request)

    @mcp.tool()
    async def apply_entry_edits(request: ApplyEdits) -> dict:
        """Apply a saved plan all-or-none. status=conflicted means no entries changed; retry returns saved outcome."""
        return await invoke(editing.apply_edits, request)

    @mcp.tool()
    async def get_entry_edit(request: ReadEdit) -> dict:
        """Read your durable edit outcome and optionally a bounded complete diff for a selected entry."""
        return await invoke(editing.read_edit, request)

    @mcp.tool()
    async def read_entry_target(request: ReadTarget) -> dict:
        """Read a character range or exact ATX section body, with absolute target offsets and pinned revision."""
        return await invoke(editing.read_target, request)

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
        Route("/api/entries/edit", endpoint({"POST": (editing.edit_entry, EditEntry)}), methods=["POST"]),
        Route("/api/entries/edits/preview", endpoint({"POST": (editing.preview_edits, PreviewEdits)}), methods=["POST"]),
        Route("/api/entries/edits/apply", endpoint({"POST": (editing.apply_edits, ApplyEdits)}), methods=["POST"]),
        Route("/api/entries/edits/read", endpoint({"GET": (editing.read_edit, ReadEdit)}), methods=["GET"]),
        Route("/api/entries/target", endpoint({"POST": (editing.read_target, ReadTarget)}), methods=["POST"]),
        Route("/api/entries", endpoint({"GET": (operations.search_entries, SearchEntries),
                                       "POST": (operations.create_entry, CreateEntry)}), methods=["GET", "POST"]),
        Route("/api/entries/read", endpoint({"GET": (operations.read_entry, ReadEntry)}), methods=["GET"]),
        Route("/api/entries/update", endpoint({"POST": (operations.update_entry, UpdateEntry)}), methods=["POST"]),
        Route("/api/entries/restore", endpoint({"POST": (operations.restore_entry, RestoreEntry)}), methods=["POST"]),
        Route("/api/entries/revisions", endpoint({"GET": (operations.list_revisions, ListRevisions)}), methods=["GET"]),
        Route("/api/entries/search", endpoint({"POST": (operations.search_entries, SearchEntries)}), methods=["POST"]),
    ]
