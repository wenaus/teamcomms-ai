"""Read canonical authored/received publications without changing receipt state."""

import json

from django.core import signing
from django.db.models import Prefetch, Q

from teamcomms.service.access import AccessError
from teamcomms.service.models import Participant
from .models import Delivery, MessagePosition
from .operations import message_record, visible_messages

PAGE_BYTES = 1_000_000
CURSOR_SALT = "teamcomms.message-history.v1"


def identity(person):
    operator = person.operator
    return {"participant_id": str(person.id), "participant_name": person.name,
            "participant_kind": person.kind,
            "operator_id": str(operator.id) if operator else None,
            "operator_name": operator.name if operator else None}


def session_record(row):
    if row is None:
        return None
    return {"session_id": str(row.id), **{field: getattr(row, field)
            for field in ("name", "host", "client", "model", "native_id")}}


def history_record(row, actor):
    message = row.message
    deliveries = message.history_deliveries
    sent = message.author_id == actor.participant_id
    received = any(d.session.membership.participant_id == actor.participant_id for d in deliveries)
    record = message_record(message)
    record["publication_snapshot"] = record.pop("author")
    return {**record, "author_identity": identity(message.author),
            "sender_session": session_record(message.sender_session),
            "recipients": [{"delivery_id": str(d.id),
                "identity": identity(d.session.membership.participant),
                "session": session_record(d.session)} for d in deliveries],
            "viewer_relation": {"sent": sent, "received": received}}


def get_message_history(actor, query):
    actor.require("comms:read")
    binding = {"v": 1, "team": str(actor.team_id), "viewer": str(actor.participant_id),
               "direction": query.direction, "topic": query.topic}
    signer = signing.Signer(salt=CURSOR_SALT)
    after = 0
    if query.cursor:
        try:
            payload = signer.unsign_object(query.cursor)
            after = payload["after"]
            if (type(after) is not int or after < 0 or
                    payload != {**binding, "after": after}):
                raise ValueError
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise AccessError("Invalid history cursor or changed viewer/filters", 400) from None

    # Fix the upper bound before selecting rows. Even an empty filtered page may
    # advance to it, without skipping a matching publication committed mid-read.
    positions = MessagePosition.objects.filter(message__team_id=actor.team_id)
    watermark = positions.order_by("-id").values_list("id", flat=True).first() or 0
    messages = visible_messages(actor)
    if query.direction == "sent":
        messages = messages.filter(author_id=actor.participant_id)
    elif query.direction == "received":
        messages = messages.filter(pk__in=Delivery.objects.filter(
            session__membership__participant_id=actor.participant_id).values("message_id"))
    if query.topic is not None:
        messages = messages.filter(envelope__topic=query.topic)

    deliveries = Delivery.objects.filter(message__team_id=actor.team_id).filter(
        Q(message__author_id=actor.participant_id) |
        Q(session__membership__participant_id=actor.participant_id)
    ).select_related("session__membership__participant__operator").order_by("session_id")
    rows = list(positions.filter(id__gt=after, id__lte=watermark,
        message_id__in=messages.values("id")).select_related(
        "message__author__operator", "message__sender_session").prefetch_related(
        Prefetch("message__deliveries", queryset=deliveries, to_attr="history_deliveries")
    ).order_by("id")[:query.limit + 1])
    selected, size = [], 0
    for row in rows[:query.limit]:
        record = history_record(row, actor)
        length = len(json.dumps(record, ensure_ascii=True).encode("utf-8"))
        if selected and size + length > PAGE_BYTES:
            break
        selected.append(record)
        size += length
    has_more = len(rows) > len(selected)
    position = rows[len(selected) - 1].id if has_more else max(after, watermark)
    viewer = Participant.objects.select_related("operator").get(pk=actor.participant_id)
    return {"schema_version": 1, "viewer": identity(viewer), "messages": selected,
            "next_cursor": signer.sign_object({**binding, "after": position}), "has_more": has_more}
