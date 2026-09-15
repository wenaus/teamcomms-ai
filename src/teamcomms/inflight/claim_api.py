from starlette.routing import Route
from teamcomms.service.dispatch import invoke
from teamcomms.entries.schemas import Page
from . import claims
from .claim_schemas import ManageResource, OfferWork, ClaimWork, UpdateClaim, ReadClaim, ValidateClaim, GuardRunRequest, AvailableOffers, ExecutionRequest


def register(mcp, endpoint):
    @mcp.tool()
    async def manage_work_resource(request: ManageResource) -> dict:
        """Provision persistent resource custody or explicitly offer/accept/cancel its transfer while idle."""
        return await invoke(claims.manage_work_resource, request)

    @mcp.tool()
    async def list_work_resources(request: Page) -> dict:
        """List custodians, protection coverage and held reservations, including expired claims."""
        return await invoke(claims.list_work_resources, request)

    @mcp.tool()
    async def offer_work(request: OfferWork) -> dict:
        """Offer owned work to an explicit eligible audience with resources, policy and deadline."""
        return await invoke(claims.offer_work, request)

    @mcp.tool()
    async def claim_work(request: ClaimWork) -> dict:
        """Atomically claim offered work and all resources. A conflict acquires none; owner remains accountable."""
        return await invoke(claims.claim_work, request)

    @mcp.tool()
    async def update_claim(request: UpdateClaim) -> dict:
        """Renew, report progress, complete, release stopped work or reconcile with stopped-work evidence. Expiry never frees resources."""
        return await invoke(claims.update_claim, request)

    @mcp.tool()
    async def get_claim(request: ReadClaim) -> dict:
        """Read claim holder, generation, deadline and retained resource reservations."""
        return await invoke(claims.get_claim, request)

    @mcp.tool()
    async def validate_claim(request: ValidateClaim) -> dict:
        """Validate current holder/generation/lease and requested resources before a cooperating protected operation."""
        return await invoke(claims.validate_claim, request)

    @mcp.tool()
    async def record_guard_run(request: GuardRunRequest) -> dict:
        """Record one cooperating command start/confirmed stop. An unconfirmed run blocks reuse and release."""
        return await invoke(claims.record_guard_run, request)

    from .execution import list_work_offers as available,record_execution as record
    from .execution_stream import stream_offers
    @mcp.tool()
    async def list_work_offers(request: AvailableOffers) -> dict:
        """Read eligible explicit execution offers for an owned session; discovery never claims work."""
        return await invoke(available,request)
    @mcp.tool()
    async def record_execution(request: ExecutionRequest) -> dict:
        """Record admitted execution or a confirmed stopped result against current claim generation."""
        return await invoke(record,request)

    contracts=[('/offers/available','GET',available,AvailableOffers),('/executions','POST',record,ExecutionRequest),('/claims/guard','POST',claims.record_guard_run,GuardRunRequest),('/resources/manage','POST',claims.manage_work_resource,ManageResource),
        ('/resources','GET',claims.list_work_resources,Page),('/offers','POST',claims.offer_work,OfferWork),
        ('/claims','POST',claims.claim_work,ClaimWork),('/claims/update','POST',claims.update_claim,UpdateClaim),
        ('/claims/read','GET',claims.get_claim,ReadClaim),('/claims/validate','POST',claims.validate_claim,ValidateClaim)]
    return [Route('/api/inflight/offers/stream',stream_offers,methods=['GET']), *[Route('/api/inflight'+path,endpoint({method:(operation,schema)}),methods=[method]) for path,method,operation,schema in contracts]]
