"""Bounded offers, claims and custody contracts."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field, model_validator
from teamcomms.entries.schemas import Request, Page
from .schemas import Text, Evidence

IDs = Annotated[list[UUID], Field(max_length=30)]


class ManageResource(Request):
    operation_id: UUID
    operation: Literal["resource"] = "resource"
    action: Literal["provision", "offer_transfer", "accept_transfer", "cancel_transfer"]
    resource_id: UUID
    expected_generation: int | None = Field(default=None, ge=1)
    custodian_id: UUID | None = None
    protection: Literal["advisory", "local_flock"] = "advisory"
    successor_id: UUID | None = None
    transfer_id: UUID | None = None
    reason: str = Field(default="", max_length=4000)


class ExecutionSpec(Request):
    profile: str = Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9._-]+$')
    timeout_seconds: int = Field(default=300,ge=1,le=3600)
    permissions: Literal['read_only','workspace_write']='read_only'
    model: str = Field(default='',max_length=120)
    effort: str = Field(default='',max_length=40)
    budget_usd: float = Field(default=0,ge=0,le=100,allow_inf_nan=False)
    headless_after: datetime | None = None

    @model_validator(mode='after')
    def aware(self):
        if self.headless_after is not None and self.headless_after.utcoffset() is None:
            raise ValueError('Headless deadline requires timezone')
        return self


class OfferWork(Request):
    operation_id: UUID
    operation: Literal["offer"] = "offer"
    entry_id: UUID
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)
    eligible_participant_ids: list[UUID] = Field(min_length=1, max_length=50)
    resource_ids: IDs = Field(default_factory=list)
    required_capabilities: list[Annotated[str, Field(min_length=1,max_length=160)]] = Field(default_factory=list,max_length=20)
    expires_at: datetime
    lease_seconds: int = Field(default=300, ge=30, le=3600)
    policy: Literal["advisory", "guarded"] = "advisory"
    execution: ExecutionSpec | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.expires_at.utcoffset() is None:
            raise ValueError("Offer deadline requires timezone")
        for field in ("eligible_participant_ids", "resource_ids", "required_capabilities"):
            if len(getattr(self, field)) != len(set(getattr(self, field))):
                raise ValueError("Duplicate offer selector")
        if self.policy == "guarded" and not self.resource_ids:
            raise ValueError("Guarded work needs resources")
        return self


class ClaimWork(Request):
    operation_id: UUID
    operation: Literal["claim"] = "claim"
    offer_id: UUID
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)
    session_id: UUID | None = None
    mode: Literal["interactive","headless"] = "interactive"


class UpdateClaim(Request):
    operation_id: UUID
    operation: Literal["claim_update"] = "claim_update"
    claim_id: UUID
    expected_generation: int = Field(ge=1)
    action: Literal["renew", "progress", "release", "confirm_stopped", "complete"]
    expected_revision: int | None = Field(default=None,ge=1)
    state: Literal["active", "blocked"] = "active"
    reason: str = Field(default="", max_length=4000)
    outcome: str = Field(default="", max_length=4000)
    evidence: Evidence = Field(default_factory=list)

    @model_validator(mode="after")
    def revision_required(self):
        if self.action != "renew" and self.expected_revision is None:
            raise ValueError("Work state changes require expected_revision")
        return self


class ReadClaim(Request):
    claim_id: UUID


class ValidateClaim(ReadClaim):
    expected_generation: int = Field(ge=1)
    resource_ids: IDs = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError("Duplicate resources")
        return self


class GuardRunRequest(Request):
    operation_id: UUID
    operation: Literal["guard_run"] = "guard_run"
    action: Literal["start", "finish"]
    run_id: UUID
    claim_id: UUID
    expected_generation: int = Field(ge=1)
    resource_ids: IDs = Field(default_factory=list)
    command_sha256: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    exit_code: int | None = None
    stopped: bool = False


class AvailableOffers(Page):
    session_id: UUID
    mode: Literal['interactive','headless']='headless'
    profile: str | None = Field(default=None,min_length=1,max_length=80)
    offer_id: UUID | None = None


class ExecutionRequest(Request):
    operation_id: UUID
    operation: Literal['execution']='execution'
    claim_id: UUID
    expected_generation: int = Field(ge=1)
    run_id: UUID
    action: Literal['start','finish']
    command_sha256: str = Field(default='',pattern=r'^([a-f0-9]{64})?$')
    stopped: bool = False
    exit_code: int | None = None
    outcome: str = Field(default='',max_length=4000)
    evidence: Evidence = Field(default_factory=list)
