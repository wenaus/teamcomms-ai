"""HTTP and MCP use the same authenticated Inflight operations."""
from starlette.routing import Route
from teamcomms.service.dispatch import invoke
from . import operations as ops
from .schemas import CreateWork, MutateWork, ReadWork, ListWork, Changes


def register(mcp, endpoint):
    @mcp.tool()
    async def create_work(request: CreateWork) -> dict:
        """Create work owned by you. Retry the same operation UUID and exact request after uncertain delivery."""
        return await invoke(ops.create_work, request)

    @mcp.tool()
    async def mutate_work(request: MutateWork) -> dict:
        """Explicit revision/generation-checked work edit, progress, assignment, handoff, completion or reopen."""
        return await invoke(ops.mutate_work, request)

    @mcp.tool()
    async def get_work(request: ReadWork) -> dict:
        """Read current or immutable historical work; presence is separately labeled current."""
        return await invoke(ops.get_work, request)

    @mcp.tool()
    async def list_work(request: ListWork) -> dict:
        """Bounded Live/Done team work with accountable owner, executor and blockers."""
        return await invoke(ops.list_work, request)

    @mcp.tool()
    async def get_work_changes(request: Changes) -> dict:
        """Read durable attributed work change notices and fixed-revision links; does not broadcast."""
        return await invoke(ops.get_work_changes, request)

    return [Route("/api/inflight", endpoint({"GET": (ops.list_work, ListWork), "POST": (ops.create_work, CreateWork)}), methods=["GET", "POST"]),
        Route("/api/inflight/read", endpoint({"GET": (ops.get_work, ReadWork)})),
        Route("/api/inflight/changes", endpoint({"GET": (ops.get_work_changes, Changes)})),
        Route("/api/inflight/mutate", endpoint({"POST": (ops.mutate_work, MutateWork)}), methods=["POST"])]
