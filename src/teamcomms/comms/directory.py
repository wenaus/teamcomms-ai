"""Participant-owned sessions and shared routing directory."""

from datetime import timedelta
from django.db import connection, transaction
from django.utils import timezone

from teamcomms.service.access import AccessError
from .models import Group, Resource, Session, Subscription

FRESH_SECONDS = 90


def lock(key):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [key])


def sessions(actor):
    return Session.objects.filter(membership__team_id=actor.team_id)


def own_session(actor, session_id):
    row = sessions(actor).filter(pk=session_id, membership__participant_id=actor.participant_id).first()
    if row is None:
        raise AccessError("Session not found for this participant", 404)
    return row


def online(rows):
    return rows.filter(membership__active=True, last_seen__gte=timezone.now() - timedelta(seconds=FRESH_SECONDS)).exclude(state="offline")


def session_record(row):
    return {"session_id": str(row.id), "participant_id": str(row.membership.participant_id),
        **{k: getattr(row, k) for k in ("native_id", "client", "host", "name", "workspace", "model", "effort", "capabilities", "delivery_mode", "state")},
        "last_seen": row.last_seen.isoformat(),
        "online": row.membership.active and row.state != "offline" and row.last_seen >= timezone.now() - timedelta(seconds=FRESH_SECONDS),
        "resource_ids": [str(r.id) for r in row.resources.all()]}


@transaction.atomic
def register_session(actor, request):
    actor.require("sessions:write")
    values = request.model_dump()
    resources = list(Resource.objects.filter(team_id=actor.team_id, id__in=values.pop("resource_ids")))
    if len(resources) != len(set(request.resource_ids)):
        raise AccessError("Resource not found", 404)
    identity = {k: values.pop(k) for k in ("native_id", "client", "host")}
    # Prevent concurrent re-registration from losing the sequence counter or identity.
    lock(f"tc-session:{actor.membership_id}:{identity}")
    row, _ = Session.objects.update_or_create(membership_id=actor.membership_id, **identity,
        defaults={**values, "last_seen": timezone.now()})
    row.resources.set(resources)
    return session_record(row)


def heartbeat_session(actor, request):
    actor.require("sessions:write")
    row = own_session(actor, request.session_id)
    changes = request.model_dump(exclude_none=True, exclude={"session_id"})
    Session.objects.filter(pk=row.id).update(**changes, last_seen=timezone.now())
    row.refresh_from_db()
    return session_record(row)


def list_sessions(actor, query):
    actor.require("directory:read")
    rows = sessions(actor).select_related("membership").prefetch_related("resources").order_by("id")
    if not query.include_offline:
        rows = online(rows)
    if query.host:
        rows = rows.filter(host=query.host)
    if query.participant_id:
        rows = rows.filter(membership__participant_id=query.participant_id)
    if query.resource_id:
        rows = rows.filter(resources=query.resource_id)
    return page(rows, query, "sessions", session_record)


def page(rows, query, name, record):
    result = list(rows[query.offset:query.offset + query.limit + 1])
    return {name: [record(r) for r in result[:query.limit]],
            "next_offset": query.offset + query.limit if len(result) > query.limit else None}


def resource_record(row):
    return {"resource_id": str(row.id), "key": row.key, "kind": row.kind, "name": row.name,
            "host": row.host, "project_id": str(row.project_id) if row.project_id else None, "aliases": row.aliases}


@transaction.atomic
def register_resource(actor, request):
    actor.require("directory:write", admin=True)
    lock(f"tc-resources:{actor.team_id}")
    if request.kind == "checkout" and (not request.host or not request.project_id or not request.aliases):
        raise AccessError("Checkouts require a host, project, and canonical path aliases", 400)
    if request.project_id and not Resource.objects.filter(team_id=actor.team_id, pk=request.project_id, kind="project").exists():
        raise AccessError("Project not found", 404)
    rows = Resource.objects.filter(team_id=actor.team_id)
    existing = rows.filter(key=request.key).first()
    if existing:
        if {k: resource_record(existing)[k] for k in type(request).model_fields} != request.model_dump(mode="json"):
            raise AccessError("Resource key already has a different definition", 409)
        return resource_record(existing)
    if request.aliases and any(set(r.aliases) & set(request.aliases) for r in rows.filter(host=request.host)):
        raise AccessError("Path alias already belongs to a resource on this host", 409)
    return resource_record(Resource.objects.create(team_id=actor.team_id, **request.model_dump()))


def list_resources(actor, query):
    actor.require("directory:read")
    return page(Resource.objects.filter(team_id=actor.team_id).order_by("key"), query, "resources", resource_record)


def group_record(row):
    return {"group_id": str(row.id), "key": row.key, "name": row.name}


@transaction.atomic
def register_group(actor, request):
    actor.require("directory:write", admin=True)
    lock(f"tc-groups:{actor.team_id}:{request.key}")
    row, _ = Group.objects.get_or_create(team_id=actor.team_id, key=request.key, defaults={"name": request.name})
    if row.name != request.name:
        raise AccessError("Group key already has a different definition", 409)
    return group_record(row)


def list_groups(actor, query):
    actor.require("directory:read")
    return page(Group.objects.filter(team_id=actor.team_id).order_by("key"), query, "groups", group_record)


@transaction.atomic
def subscribe(actor, request):
    actor.require("sessions:write")
    row = own_session(actor, request.session_id)
    Session.objects.select_for_update().get(pk=row.id)
    if request.group_id and not Group.objects.filter(pk=request.group_id, team_id=actor.team_id).exists():
        raise AccessError("Group not found", 404)
    Subscription.objects.update_or_create(session=row, group_id=request.group_id, topic=request.topic or "",
                                          defaults={"active": request.active})
    return request.model_dump(mode="json")
