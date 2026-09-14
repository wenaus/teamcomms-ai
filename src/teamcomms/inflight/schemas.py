"""Bounded work requests. Control changes are explicit discriminated operations."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator
from teamcomms.entries.schemas import Request, State, ReadEntry, Page

Text = Annotated[str, Field(min_length=1, max_length=4000)]
IDs = Annotated[list[UUID], Field(max_length=50)]
Evidence = Annotated[list[Annotated[str, Field(min_length=1, max_length=2000)]], Field(max_length=20)]


class CreateWork(Request):
    operation_id: UUID
    state: State
    form: Literal["work", "coordination", "incident"] = "work"
    visibility: Literal["operator", "internal"] = "operator"
    criteria: str = Field(default="", max_length=4000)
    parent_id: UUID | None = None
    dependencies: IDs = Field(default_factory=list)
    resource_ids: IDs = Field(default_factory=list)
    message_ids: IDs = Field(default_factory=list)
    dialog_event_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=50)


class Sources(Request):
    action: Literal["sources"]
    resource_ids: IDs = Field(default_factory=list)
    message_ids: IDs = Field(default_factory=list)
    dialog_event_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=50)


class Edit(Request):
    action: Literal["edit"]
    changes: dict[str, JsonValue] = Field(default_factory=dict)
    criteria: str | None = Field(default=None, max_length=4000)
    visibility: Literal["operator", "internal"] | None = None

    @model_validator(mode="after")
    def fields(self):
        if not self.changes.keys() <= {"title", "content", "tags", "priority", "relations", "metadata"}:
            raise ValueError("Only descriptive fields may be edited")
        if not self.changes and self.criteria is None and self.visibility is None:
            raise ValueError("Empty edit")
        return self


class Progress(Request):
    action: Literal["progress"]
    state: Literal["active", "blocked"]
    blockers: str = Field(default="", max_length=4000)


class Transition(Request):
    action: Literal["transition"]
    state: Literal["completed", "failed", "canceled"]
    outcome: Text
    evidence: Evidence = Field(default_factory=list)


class Assign(Request):
    action: Literal["assign_executor"]
    participant_id: UUID | None
    session_id: UUID | None = None


class Graph(Request):
    action: Literal["graph"]
    parent_id: UUID | None
    dependencies: IDs


class Offer(Request):
    action: Literal["offer_handoff"]
    successor_id: UUID
    reason: Text
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def timezone_required(self):
        if self.expires_at is not None and self.expires_at.utcoffset() is None:
            raise ValueError("Expiry must include timezone")
        return self


class Handoff(Request):
    action: Literal["accept_handoff", "reject_handoff", "cancel_handoff"]
    handoff_id: UUID


class Reopen(Request):
    action: Literal["reopen"]
    reason: Text


class MutateWork(Request):
    operation_id: UUID
    entry_id: UUID
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)
    change: Annotated[Edit | Progress | Transition | Assign | Graph | Offer | Handoff | Reopen | Sources, Field(discriminator="action")]


class ReadWork(ReadEntry):
    pass


class ListWork(Page):
    view: Literal["live", "done", "all"] = "live"
    visibility: Literal["operator", "internal", "all"] = "operator"
    parent_id: UUID | None = None
    owner_id: UUID | None = None
    query: str = Field(default="", max_length=500)


class Changes(Page):
    entry_id: UUID
