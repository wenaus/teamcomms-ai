"""Canonical document lookup, explicit initialization and attributed export."""

import hashlib

from django.db import transaction
from starlette.routing import Route

from teamcomms.entries.operations import create_entry, read_entry, list_revisions
from teamcomms.entries.schemas import CreateEntry, ReadEntry, State, Request, Page, ListRevisions
from teamcomms.service.access import AccessError
from teamcomms.service.dispatch import invoke
from teamcomms.service.models import Team
from pydantic import Field
from .models import Pouch


class ReadPouch(Request):
    revision: int | None = Field(default=None, ge=1)
    content_offset: int = Field(default=0, ge=0)
    max_content_length: int = Field(default=10000, ge=1, le=40000)


class ExportPouch(Request):
    revision: int = Field(ge=1)


def binding(actor):
    actor.require("entries:read")
    pouch = Pouch.objects.filter(team_id=actor.team_id).first()
    if pouch is None:
        raise AccessError("Pouch has not been initialized", 404)
    return pouch


def paths(revision):
    return {"current_path": "/pouch", "revision_path": f"/pouch?revision={revision}"}


def get_pouch(actor, request):
    pouch = binding(actor)
    result = read_entry(actor, ReadEntry(entry_id=pouch.entry_id, **request.model_dump()))
    return {**result, **paths(result["revision"]), "canonical": True}


@transaction.atomic
def initialize_pouch(actor, request=None):
    actor.require("entries:read")
    actor.require("entries:write")
    Team.objects.select_for_update().get(pk=actor.team_id)
    pouch = Pouch.objects.filter(team_id=actor.team_id).first()
    created = pouch is None
    if created:
        entry = create_entry(actor, CreateEntry(kind="document", state=State(title="Pouch")))
        Pouch.objects.create(team_id=actor.team_id, entry_id=entry["entry_id"])
    return {**get_pouch(actor, ReadPouch()), "created": created}


def pouch_changes(actor, request):
    pouch = binding(actor)
    result = list_revisions(actor, ListRevisions(entry_id=pouch.entry_id, **request.model_dump()))
    return {"changes": [{**r, **paths(r["revision"]),
                          "notice": f"Pouch revision {r['revision']} by {r['author_id']}"}
                         for r in result["revisions"]], "next_offset": result["next_offset"]}


def export_pouch(actor, request):
    saved = get_pouch(actor, ReadPouch(revision=request.revision, max_content_length=40000))
    content = saved["state"]["content"]
    return {"entry_id": saved["entry_id"], "revision": saved["revision"],
            "revision_id": saved["revision_id"], "author_id": saved["author_id"],
            "created_at": saved["created_at"], "revision_path": saved["revision_path"],
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "state": saved["state"], "format": "teamcomms-pouch-export-v1"}


def register(mcp, endpoint):
    @mcp.tool(name="get_pouch")
    async def read(request: ReadPouch) -> dict:
        """Read the team's canonical Pouch, optionally pinned to a revision. Never creates it."""
        return await invoke(get_pouch, request)

    @mcp.tool(name="initialize_pouch")
    async def initialize() -> dict:
        """Explicitly create an empty Pouch once; concurrent calls return the same document."""
        return await invoke(initialize_pouch)

    @mcp.tool(name="get_pouch_changes")
    async def changes(request: Page) -> dict:
        """Read brief durable change notices with authors and fixed revision links."""
        return await invoke(pouch_changes, request)

    @mcp.tool(name="export_pouch")
    async def export(request: ExportPouch) -> dict:
        """Export an explicit saved revision with attribution, state and a content hash."""
        return await invoke(export_pouch, request)

    return [Route("/api/pouch", endpoint({"GET": (get_pouch, ReadPouch)}), methods=["GET"]),
            Route("/api/pouch/initialize", endpoint({"POST": (initialize_pouch, None)}), methods=["POST"]),
            Route("/api/pouch/changes", endpoint({"GET": (pouch_changes, Page)}), methods=["GET"]),
            Route("/api/pouch/export", endpoint({"GET": (export_pouch, ExportPouch)}), methods=["GET"])]
