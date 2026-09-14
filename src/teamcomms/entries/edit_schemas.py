"""Bounded surgical edits and durable, explicitly selected bulk plans."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .schemas import Request, Relation, ReadEntry, State


class Match(Request):
    occurrence: int | None = Field(default=None, ge=1)
    all_matches: bool = False

    @model_validator(mode="after")
    def selection(self):
        if self.occurrence is not None and self.all_matches:
            raise ValueError("Choose occurrence or all_matches")
        return self


class Replace(Match):
    op: Literal["replace"]
    old_text: str = Field(min_length=1, max_length=40000)
    new_text: str = Field(max_length=40000)


class Insert(Match):
    op: Literal["insert"]
    anchor: str = Field(min_length=1, max_length=40000)
    text: str = Field(max_length=40000)
    position: Literal["before", "after"]


class Section(Request):
    heading: str = Field(min_length=1, max_length=500)
    level: int | None = Field(default=None, ge=1, le=6)
    occurrence: int | None = Field(default=None, ge=1)


class ReplaceSection(Section):
    op: Literal["section"]
    content: str = Field(max_length=40000)


class Append(Request):
    op: Literal["append"]
    content: str = Field(max_length=40000)
    separator: str = Field(default="\n\n", max_length=100)


class SetContent(Request):
    op: Literal["set_content"]
    content: str = Field(max_length=40000)


class SetFields(Request):
    op: Literal["fields"]
    changes: dict[str, JsonValue]

    @model_validator(mode="after")
    def named_fields(self):
        if not self.changes or not self.changes.keys() <= State.model_fields.keys() - {"content", "metadata", "relations"}:
            raise ValueError("Use fields for title, tags, status, or priority")
        return self


class Metadata(Request):
    op: Literal["metadata"]
    set: dict[str, JsonValue] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def disjoint(self):
        if self.set.keys() & set(self.remove):
            raise ValueError("Cannot set and remove the same metadata key")
        return self


class Relations(Request):
    op: Literal["relations"]
    add: list[Relation] = Field(default_factory=list, max_length=50)
    remove: list[Relation] = Field(default_factory=list, max_length=50)


Edit = Annotated[Replace | Insert | ReplaceSection | Append | SetContent | SetFields | Metadata | Relations,
                 Field(discriminator="op")]


class EntryEdits(Request):
    entry_id: UUID
    expected_revision: int = Field(ge=1)
    edits: list[Edit] = Field(min_length=1, max_length=50)


class EditEntry(EntryEdits):
    operation_id: UUID


class PreviewEdits(Request):
    operation_id: UUID
    entries: list[EntryEdits] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_entries(self):
        if len({e.entry_id for e in self.entries}) != len(self.entries):
            raise ValueError("Each entry appears once in a plan")
        return self


class ApplyEdits(Request):
    operation_id: UUID


class ReadEdit(ApplyEdits):
    entry_id: UUID | None = None
    diff_offset: int = Field(default=0, ge=0)
    max_diff_chars: int = Field(default=8000, ge=1, le=20000)


class ReadTarget(ReadEntry):
    section: Section | None = None
