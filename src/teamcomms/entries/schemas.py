"""Entry kinds and bounded request contracts."""

from datetime import datetime
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Kind = Literal["note", "document"]
Label = Annotated[str, Field(min_length=1, max_length=80)]


class Request(BaseModel):
    # Content whitespace is significant, including final newlines and indentation.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Relation(Request):
    entry_id: UUID
    revision: int | None = Field(default=None, ge=1)
    relation: str = Field(default="related", pattern=r"^[a-z][a-z0-9-]{0,59}$")


class State(Request):
    title: str = Field(default="", max_length=240)
    content: str = Field(default="", max_length=40000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    tags: list[Label] = Field(default_factory=list, max_length=50)
    status: Literal["active", "archived"] = "active"
    priority: int | None = Field(default=None, ge=1)
    relations: list[Relation] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def bounded_state(self):
        serialized = json.dumps(self.model_dump(mode="json"), allow_nan=False)
        if len(serialized.encode()) > 48000:
            raise ValueError("Entry state exceeds 48000 encoded bytes")
        keys = [(r.entry_id, r.revision, r.relation) for r in self.relations]
        if len(keys) != len(set(keys)) or len(self.tags) != len(set(self.tags)):
            raise ValueError("Duplicate tags or relations")
        return self


class CreateEntry(Request):
    kind: Kind = "note"
    slug: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,119}$")
    state: State


class ReadEntry(Request):
    entry_id: UUID
    revision: int | None = Field(default=None, ge=1)
    content_offset: int = Field(default=0, ge=0)
    max_content_length: int = Field(default=10000, ge=1, le=40000)


class UpdateEntry(Request):
    entry_id: UUID
    expected_revision: int = Field(ge=1)
    changes: dict[str, JsonValue]

    @model_validator(mode="after")
    def state_fields(self):
        if not self.changes or not self.changes.keys() <= State.model_fields.keys():
            raise ValueError("Changes must name entry state fields")
        return self


class RestoreEntry(Request):
    entry_id: UUID
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class Page(Request):
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListRevisions(Page):
    entry_id: UUID


class SearchEntries(Page):
    query: str = Field(default="", max_length=500)
    kind: Kind | None = None
    slug: str | None = Field(default=None, max_length=120)
    tag: Label | None = None
    status: Literal["active", "archived"] | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    related_to: UUID | None = None
    relation: str | None = Field(default=None, max_length=60)
    modified_since: datetime | None = None
    modified_before: datetime | None = None

    @model_validator(mode="after")
    def time_bounds(self):
        for value in (self.modified_since, self.modified_before):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Time filters require a timezone")
        if self.modified_since and self.modified_before and self.modified_since > self.modified_before:
            raise ValueError("Time bounds are reversed")
        if self.relation is not None and self.related_to is None:
            raise ValueError("A relation filter requires related_to")
        if len(json.dumps(self.metadata, allow_nan=False).encode()) > 8000:
            raise ValueError("Metadata filter too large")
        return self
