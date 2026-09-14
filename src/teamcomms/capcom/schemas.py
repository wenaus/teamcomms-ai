from datetime import datetime
from typing import Annotated,Literal
from uuid import UUID
from pydantic import Field,model_validator
from teamcomms.entries.schemas import Request,Page
from teamcomms.comms.schemas import Audience,Reference


class Links(Request):
    entries: list[Reference] = Field(default_factory=list,max_length=30)
    dialog_event_ids: list[Annotated[int,Field(ge=1)]] = Field(default_factory=list,max_length=30)


class Mutation(Request):
    operation_id: UUID


class CreateTopic(Mutation):
    operation: Literal['create_topic']='create_topic'
    key: str = Field(min_length=1,max_length=160,pattern=r'^[a-z0-9][a-z0-9._:-]*$')
    title: str = Field(min_length=1,max_length=240)
    links: Links = Field(default_factory=Links)


class UpdateTopic(Mutation):
    operation: Literal['update_topic']='update_topic'
    topic_id: UUID
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1,max_length=240)
    links: Links


class TopicRead(Request):
    topic_id: UUID


class TopicPage(TopicRead,Page):
    pass


class Topics(Page):
    query: str = Field(default='',max_length=240)
    view: Literal['all','followed','attention']='attention'


class Notices(TopicRead):
    before: int | None = Field(default=None,ge=1)
    limit: int = Field(default=25,ge=1,le=100)


class PublishNotice(Mutation):
    operation: Literal['publish_notice']='publish_notice'
    notice_id: UUID
    topic_id: UUID
    kind: Literal['event','state','progress','result','decision','discussion','routine']='event'
    urgency: Literal['routine','normal','urgent','alarm']='normal'
    content: str = Field(min_length=1,max_length=8000)
    source: str = Field(default='',max_length=240)
    observed_at: datetime
    audience: Audience | None = None
    sender_session_id: UUID | None = None

    @model_validator(mode='after')
    def timezone_required(self):
        if self.observed_at.utcoffset() is None:raise ValueError('Observation time requires timezone')
        if self.kind=='state' and not self.source.strip():raise ValueError('Sampled state requires source')
        return self


class ResolveDecision(Mutation):
    operation: Literal['resolve_decision']='resolve_decision'
    notice_id: UUID
    expected_revision: int = Field(ge=1)
    resolution: str = Field(min_length=1,max_length=4000)


class FollowTopic(Mutation):
    operation: Literal['follow_topic']='follow_topic'
    message_ids: list[UUID] = Field(default_factory=list,max_length=100)
    topic_id: UUID
    following: bool | None = None
    read_sequence: int | None = Field(default=None,ge=0)
    session_id: UUID | None = None


class SessionRead(Request):
    session_id: UUID


class SetAttention(Mutation,SessionRead):
    operation: Literal['set_attention']='set_attention'
    expected_revision: int = Field(ge=0)
    routine_mode: Literal['immediate','record','batch']='immediate'
    batch_seconds: int = Field(default=30,ge=5,le=300)
    quiet_until: datetime | None = None


class PlanAttention(SessionRead):
    delivery_ids: list[UUID] = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len(self.delivery_ids)!=len(set(self.delivery_ids)):raise ValueError('Duplicate deliveries')
        return self
