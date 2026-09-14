"""Bounded shared recording, retrieval, and bootstrap contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from teamcomms.comms.schemas import Request


class RecordDialog(Request):
    session_id: UUID
    source_id: str = Field(min_length=1, max_length=240)
    source_sequence: int = Field(ge=0)
    occurred_at: datetime
    run_id: str = Field(default="", max_length=160)
    role: Literal["human", "assistant", "peer", "system", "gap"]
    phase: Literal["message", "commentary", "final"] = "message"
    content: str = Field(min_length=1, max_length=16000)
    topic: str = Field(default="", max_length=160)
    message_id: UUID | None = None

    @model_validator(mode="after")
    def provenance(self):
        if self.occurred_at.utcoffset() is None:
            raise ValueError("Event time requires a timezone")
        if self.message_id and self.role != "peer":
            raise ValueError("Canonical incoming messages require peer role")
        return self


class DialogQuery(Request):
    host: str | None = Field(default=None, min_length=1, max_length=160)
    participant_id: UUID | None = None
    session_id: UUID | None = None
    topic: str | None = Field(default=None, min_length=1, max_length=160)
    since: datetime | None = None
    before: datetime | None = None
    before_id: int | None = Field(default=None, ge=1)
    event_id: int | None = Field(default=None, ge=1)
    limit: int = Field(default=40, ge=1, le=100)
    max_chars: int = Field(default=16000, ge=1000, le=30000)

    @model_validator(mode="after")
    def times(self):
        if any(t is not None and t.utcoffset() is None for t in (self.since, self.before)):
            raise ValueError("Time bounds require a timezone")
        if self.since and self.before and self.since >= self.before:
            raise ValueError("since must precede before")
        return self


class Bootstrap(DialogQuery):
    hours: int = Field(default=24, ge=1, le=720)
    guidance_entry_ids: list[UUID] = Field(default_factory=list, max_length=10)
