"""Bounded Comms contracts shared by HTTP and MCP."""

from datetime import datetime
import json
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Label = Annotated[str, Field(min_length=1, max_length=160, pattern=r"\S")]
State = Literal["idle", "active", "unknown", "offline"]
Transport = Literal["pending", "written", "accepted", "uncertain", "failed"]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def bounded(self):
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
        if "\\u0000" in payload or len(payload.encode("utf-8")) > 48000:
            raise ValueError("Invalid text or request exceeds 48000 bytes")
        return self


class RegisterSession(Request):
    native_id: Label
    client: str = Field(min_length=1, max_length=80)
    host: Label
    name: Label
    workspace: str = Field(default="", max_length=4096)
    model: str = Field(default="", max_length=120)
    effort: str = Field(default="", max_length=40)
    capabilities: list[Label] = Field(default_factory=list, max_length=30)
    delivery_mode: Literal["pull", "stream", "claude_socket", "codex_app_server", "codex_queue"] = "pull"
    state: State = "unknown"
    resource_ids: list[UUID] = Field(default_factory=list, max_length=30)


class Heartbeat(Request):
    session_id: UUID
    state: State
    name: Label | None = None
    model: str | None = Field(default=None, max_length=120)
    effort: str | None = Field(default=None, max_length=40)


class Page(Request):
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Sessions(Page):
    host: Label | None = None
    participant_id: UUID | None = None
    resource_id: UUID | None = None
    include_offline: bool = False


class NewResource(Request):
    key: str = Field(min_length=1, max_length=240)
    kind: Literal["project", "checkout", "service"]
    name: Label
    host: str = Field(default="", max_length=160)
    project_id: UUID | None = None
    aliases: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(default_factory=list, max_length=30)


class NewGroup(Request):
    key: Label
    name: Label


class Subscribe(Request):
    session_id: UUID
    group_id: UUID | None = None
    topic: Label | None = None
    active: bool = True

    @model_validator(mode="after")
    def destination(self):
        if (self.group_id is None) == (self.topic is None):
            raise ValueError("Specify one group or topic")
        return self


class Audience(Request):
    session_ids: list[UUID] = Field(default_factory=list, max_length=100)
    participant_ids: list[UUID] = Field(default_factory=list, max_length=100)
    hosts: list[Label] = Field(default_factory=list, max_length=30)
    group_ids: list[UUID] = Field(default_factory=list, max_length=30)
    resource_ids: list[UUID] = Field(default_factory=list, max_length=30)
    topics: list[Label] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def canonical(self):
        if not any(getattr(self, key) for key in type(self).model_fields):
            raise ValueError("An explicit audience is required")
        for key in type(self).model_fields:
            setattr(self, key, sorted(set(getattr(self, key))))
        return self


class Reference(Request):
    entry_id: UUID
    revision: int | None = Field(default=None, ge=1)


class ExternalSource(Request):
    platform: Literal["mattermost"] = "mattermost"
    authority: Literal["connector-reported"] = "connector-reported"
    server: str = Field(min_length=1, max_length=2048)
    channel_id: Label
    post_id: Label
    thread_id: str = Field(default="", max_length=160)
    user_id: Label
    username: Label
    kind: Literal["human", "bot", "service"]


class SendMessage(Request):
    message_id: UUID
    sender_session_id: UUID | None = None
    audience: Audience
    content: str = Field(min_length=1, max_length=16000)
    kind: Literal["notification", "conversation", "offer"] = "conversation"
    topic: str = Field(default="", max_length=160)
    reply_to: UUID | None = None
    reply_requested: bool = False
    observed_at: datetime | None = None
    references: list[Reference] = Field(default_factory=list, max_length=30)
    external_source: ExternalSource | None = None
    notify_llm: bool = False
    notify_llm_reason: str = Field(default="", max_length=1000)
    notify_llm_source: str = Field(default="", max_length=2048)
    notify_llm_event_id: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def aware(self):
        if self.observed_at is not None and self.observed_at.utcoffset() is None:
            raise ValueError("Observation time requires a timezone")
        intent = (self.notify_llm_reason, self.notify_llm_source, self.notify_llm_event_id)
        if self.notify_llm:
            if self.kind != "notification" or not all(v.strip() for v in intent) or self.observed_at is None:
                raise ValueError("Notify LLM requires a notification, reason, source, event ID and observation time")
        elif any(intent):
            raise ValueError("Notify LLM attribution requires explicit notify_llm=true")
        return self


class NotifyLLM(SendMessage):
    kind: Literal["notification"] = "notification"
    notify_llm: Literal[True] = True


class Inbox(Request):
    session_id: UUID
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=25)
    pending_only: bool = False


class Stream(Inbox):
    duration: int = Field(default=25, ge=1, le=25)


class MessageQuery(Page):
    message_id: UUID


class Report(Request):
    receipt_id: UUID
    delivery_id: UUID
    expected_revision: int = Field(ge=1)
    state: Transport
    detail: str = Field(default="", max_length=2000)


class Acknowledge(Request):
    session_id: UUID
    message_id: UUID


class DeliveryHistory(Page):
    delivery_id: UUID
