"""Atomic mailbox operations adapted from TJAI's publication and receipt services."""

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from teamcomms.entries.models import Entry, Revision
from teamcomms.service.access import AccessError
from teamcomms.service.models import Participant
from .directory import lock, online, own_session, sessions
from .models import Delivery, Group, Message, MessageReference, Receipt, Resource, Session, Subscription


def notify(session_id):
    # Delivered by PostgreSQL only after this transaction commits.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_notify(%s, '')", [channel(session_id)])


def channel(session_id):
    return "tc_comms_" + session_id.hex


def delivery_record(row):
    return {"delivery_id": str(row.id), "message_id": str(row.message_id), "session_id": str(row.session_id),
        "sequence": row.sequence, "state": row.state, "revision": row.revision, "detail": row.detail,
        "updated_at": row.updated_at.isoformat(),
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "considered_at": row.considered_at.isoformat() if row.considered_at else None}


def message_record(row):
    return {"message_id": str(row.id), "schema_version": 1, "author_id": str(row.author_id),
        "author": row.author_snapshot, "created_at": row.created_at.isoformat(), **row.envelope}


def _targets(actor, request):
    audience = request.audience
    for model, ids in ((Group, audience.group_ids), (Resource, audience.resource_ids)):
        if model.objects.filter(team_id=actor.team_id, id__in=ids).count() != len(ids):
            raise AccessError("Audience group or resource not found", 404)
    all_sessions = sessions(actor).filter(membership__active=True)
    direct = set(all_sessions.filter(id__in=audience.session_ids).values_list("id", flat=True))
    if len(direct) != len(audience.session_ids):
        raise AccessError("Destination session not found", 404)
    selector = Q(pk__in=[])
    if audience.participant_ids:
        selector |= Q(membership__participant_id__in=audience.participant_ids)
    if audience.hosts:
        selector |= Q(host__in=audience.hosts)
    if audience.resource_ids:
        # Joining a checkout also participates in its canonical project audience.
        selector |= Q(resources__in=audience.resource_ids) | Q(resources__project_id__in=audience.resource_ids)
    subscriptions = Subscription.objects.filter(active=True).filter(
        Q(group_id__in=audience.group_ids) | Q(topic__in=audience.topics))
    selector |= Q(pk__in=subscriptions.values("session_id"))
    direct.update(online(all_sessions).filter(selector).values_list("id", flat=True).distinct()[:102])
    direct.discard(request.sender_session_id)
    if not direct:
        raise AccessError("Audience has no eligible destinations", 409)
    if len(direct) > 100:
        raise AccessError("Audience exceeds 100 destinations; narrow the audience", 400)
    return direct


@transaction.atomic
def send_message(actor, request):
    actor.require("comms:write")
    envelope = request.model_dump(mode="json", exclude={"message_id"})
    lock(f"tc-message:{request.message_id}")
    existing = Message.objects.filter(pk=request.message_id).first()
    if existing:
        if existing.author_id != actor.participant_id or existing.team_id != actor.team_id or existing.envelope != envelope:
            raise AccessError("Message ID already has a different envelope or author", 409)
        return publication_record(existing)
    sender = own_session(actor, request.sender_session_id) if request.sender_session_id else None
    if request.reply_to and (not sender or not Delivery.objects.filter(message_id=request.reply_to, session=sender).exists()):
        raise AccessError("Reply must reference a message delivered to the sending session", 400)
    references = []
    if request.references:
        actor.require("entries:read")
    for ref in request.references:
        entry = Entry.objects.filter(pk=ref.entry_id, team_id=actor.team_id).first()
        revision = Revision.objects.filter(entry=entry, number=ref.revision).first() if entry and ref.revision else None
        if entry is None or (ref.revision and revision is None):
            raise AccessError("Referenced entry version not found", 404)
        references.append((entry, revision))
    destinations = _targets(actor, request)
    # Serialize counters in a deterministic order, including a reply's receiving session.
    locked = list(Session.objects.select_for_update().filter(
        id__in=destinations | ({sender.id} if sender else set())).order_by("id"))
    person = Participant.objects.get(pk=actor.participant_id)
    message = Message.objects.create(id=request.message_id, team_id=actor.team_id, author_id=actor.participant_id,
        sender_session=sender, reply_to_id=request.reply_to, envelope=envelope,
        author_snapshot={"name": person.name, "kind": person.kind,
                         "session_name": sender.name if sender else None, "host": sender.host if sender else None})
    for entry, revision in references:
        MessageReference.objects.create(message=message, entry=entry, revision=revision)
    for row in locked:
        if row.id not in destinations:
            continue
        row.last_sequence += 1
        row.save(update_fields=["last_sequence"])
        Delivery.objects.create(message=message, session=row, sequence=row.last_sequence)
        notify(row.id)
    if request.reply_to:
        _acknowledge(sender.id, request.reply_to)
    return publication_record(message)


def publication_record(message):
    return {"message_id": str(message.id), "created_at": message.created_at.isoformat(),
        "deliveries": [delivery_record(d) for d in message.deliveries.order_by("session_id")]}


def get_messages(actor, query):
    actor.require("comms:read")
    row = own_session(actor, query.session_id)
    if query.after > row.last_sequence:
        raise AccessError("Cursor is ahead of this session's mailbox", 400)
    rows = Delivery.objects.filter(session=row, sequence__gt=query.after).select_related("message").order_by("sequence")
    if query.pending_only:
        rows = rows.filter(acknowledged_at__isnull=True)
    page = list(rows[:query.limit + 1])
    selected = page[:query.limit]
    return {"messages": [{**delivery_record(d), "message": message_record(d.message)} for d in selected],
            "next_after": selected[-1].sequence if selected else query.after, "has_more": len(page) > query.limit}


def get_message(actor, query):
    actor.require("comms:read")
    row = Message.objects.filter(pk=query.message_id, team_id=actor.team_id).first()
    if row is None:
        raise AccessError("Message not found", 404)
    deliveries = row.deliveries.order_by("session_id")
    if row.author_id != actor.participant_id:
        deliveries = deliveries.filter(session__membership__participant_id=actor.participant_id)
        if not deliveries.exists():
            raise AccessError("Message not found", 404)
    page = list(deliveries[query.offset:query.offset + query.limit + 1])
    return {**message_record(row), "deliveries": [delivery_record(d) for d in page[:query.limit]],
            "next_offset": query.offset + query.limit if len(page) > query.limit else None}


TRANSITIONS = {
    "pending": {"uncertain", "failed"},
    "uncertain": {"written", "accepted", "failed"},
    "written": {"accepted", "uncertain"},
    "failed": {"pending"},
    "accepted": set(),
}


@transaction.atomic
def record_delivery(actor, request):
    actor.require("comms:write")
    lock(f"tc-receipt:{request.receipt_id}")
    row = Delivery.objects.select_for_update().filter(pk=request.delivery_id,
        session__membership__participant_id=actor.participant_id, session__membership__team_id=actor.team_id).first()
    if row is None:
        raise AccessError("Delivery not found for this participant", 404)
    body = request.model_dump(mode="json")
    existing = Receipt.objects.filter(pk=request.receipt_id).first()
    if existing:
        if existing.author_id != actor.participant_id or existing.request != body:
            raise AccessError("Receipt ID already has a different report", 409)
        return existing.result
    if row.revision != request.expected_revision:
        raise AccessError("STALE_REVISION: delivery changed; read current state", 409)
    if request.state not in TRANSITIONS[row.state] or (row.acknowledged_at and request.state == "pending"):
        raise AccessError("Invalid delivery transition", 409)
    if request.state == "failed" and not request.detail.strip():
        raise AccessError("Failure requires a diagnostic detail", 400)
    row.state, row.detail = request.state, request.detail
    row.revision += 1
    row.updated_at = timezone.now()
    row.save(update_fields=["state", "detail", "revision", "updated_at"])
    result = delivery_record(row)
    Receipt.objects.create(id=request.receipt_id, delivery=row, author_id=actor.participant_id, request=body, result=result)
    notify(row.session_id)
    return result


def delivery_history(actor, query):
    actor.require("comms:read")
    row = Delivery.objects.select_related("message", "session__membership").filter(pk=query.delivery_id,
        message__team_id=actor.team_id).first()
    if row is None or actor.participant_id not in {row.message.author_id, row.session.membership.participant_id}:
        raise AccessError("Delivery not found", 404)
    events = list(row.receipts.order_by("created_at", "id")[query.offset:query.offset + query.limit + 1])
    return {"delivery": delivery_record(row), "receipts": [{"receipt_id": str(e.id), "author_id": str(e.author_id),
        "created_at": e.created_at.isoformat(), "request": e.request, "result": e.result} for e in events[:query.limit]],
        "next_offset": query.offset + query.limit if len(events) > query.limit else None}


def _acknowledge(session_id, message_id):
    row = Delivery.objects.select_for_update().filter(session_id=session_id, message_id=message_id).first()
    if row is None:
        raise AccessError("Message not found in session inbox", 404)
    if row.acknowledged_at is None:
        row.acknowledged_at = row.considered_at = row.updated_at = timezone.now()
        row.revision += 1
        row.save(update_fields=["acknowledged_at", "considered_at", "revision", "updated_at"])
    return delivery_record(row)


@transaction.atomic
def acknowledge_message(actor, request):
    actor.require("comms:write")
    own_session(actor, request.session_id)
    return _acknowledge(request.session_id, request.message_id)
