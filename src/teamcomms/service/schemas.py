"""Shared request validation; identity fields cannot be supplied as authorship."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NewParticipant(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["human", "ai", "program", "connector"]
    operator_id: UUID | None = None


class NewCredential(RequestModel):
    participant_id: UUID
    scopes: list[str] = Field(min_length=1, max_length=3)
    expires_at: datetime | None = None


class CredentialReference(RequestModel):
    credential_id: UUID


class DirectoryQuery(RequestModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
