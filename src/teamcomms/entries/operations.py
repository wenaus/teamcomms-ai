"""Transactional entry operations adapted from TJAI's serialized mutation pattern."""

from django.contrib.postgres.search import SearchQuery
from django.db import IntegrityError, transaction
from django.db.models import F, Q

from teamcomms.service.access import AccessError
from .models import Entry, Revision, RevisionReference
from .schemas import State


def _entry(actor, entry_id, *, lock=False):
    entries = Entry.objects.filter(team_id=actor.team_id, pk=entry_id)
    entry = (entries.select_for_update() if lock else entries).first()
    if entry is None:
        raise AccessError("Entry not found", 404)
    if entry.kind not in ("note", "document"):
        raise AccessError("Entry kind requires its component-specific interface", 400)
    return entry


def _expected(entry, number):
    if entry.revision != number:
        raise AccessError(f"STALE_REVISION: expected {number}, current {entry.revision}", 409)


def _references(actor, state):
    references = []
    for ref in state.relations:
        target = _entry(actor, ref.entry_id)
        revision = None
        if ref.revision is not None:
            revision = Revision.objects.filter(entry=target, number=ref.revision).first()
            if revision is None:
                raise AccessError("Referenced revision not found", 404)
        references.append((target, revision, ref.relation))
    return references


def _snapshot(entry, actor, references, restored_from=None):
    revision = Revision.objects.create(entry=entry, number=entry.revision,
        base_revision=entry.revision - 1, state=entry.state, author_id=actor.participant_id,
        restored_from=restored_from)
    RevisionReference.objects.bulk_create([
        RevisionReference(source=revision, target_entry=target, target_revision=version, relation=relation)
        for target, version, relation in references
    ])
    return revision


def _summary(entry, revision):
    return {"entry_id": str(entry.id), "slug": entry.slug, "kind": entry.kind,
            "revision": revision.number, "revision_id": str(revision.id),
            "base_revision": revision.base_revision, "author_id": str(revision.author_id),
            "created_at": revision.created_at.isoformat(),
            "restored_from": str(revision.restored_from_id) if revision.restored_from_id else None}


@transaction.atomic
def create_entry(actor, request):
    actor.require("entries:write")
    references = _references(actor, request.state)
    try:
        with transaction.atomic():
            entry = Entry.objects.create(team_id=actor.team_id, kind=request.kind, slug=request.slug,
                state=request.state.model_dump(mode="json"), created_by_id=actor.participant_id)
    except IntegrityError:
        raise AccessError("Entry slug already exists", 409) from None
    return _summary(entry, _snapshot(entry, actor, references))


def read_entry(actor, request):
    actor.require("entries:read")
    entry = _entry(actor, request.entry_id)
    revision = Revision.objects.filter(entry=entry, number=request.revision or entry.revision).first()
    if revision is None:
        raise AccessError("Revision not found", 404)
    state = dict(revision.state)
    content = state["content"]
    start = request.content_offset
    state["content"] = content[start:start + request.max_content_length]
    end = start + len(state["content"])
    return {**_summary(entry, revision), "state": state, "content_length": len(content),
            "content_offset": start, "next_content_offset": end if end < len(content) else None}


@transaction.atomic
def update_entry(actor, request):
    actor.require("entries:write")
    entry = _entry(actor, request.entry_id, lock=True)
    _expected(entry, request.expected_revision)
    state = State.model_validate({**entry.state, **request.changes})
    references = _references(actor, state)
    entry.state = state.model_dump(mode="json")
    entry.revision += 1
    entry.save(update_fields=["state", "revision", "modified_at"])
    return _summary(entry, _snapshot(entry, actor, references))


@transaction.atomic
def restore_entry(actor, request):
    actor.require("entries:write")
    entry = _entry(actor, request.entry_id, lock=True)
    _expected(entry, request.expected_revision)
    previous = Revision.objects.filter(entry=entry, number=request.revision).first()
    if previous is None:
        raise AccessError("Revision not found", 404)
    state = State.model_validate(previous.state)
    references = _references(actor, state)
    entry.state = state.model_dump(mode="json")
    entry.revision += 1
    entry.save(update_fields=["state", "revision", "modified_at"])
    return _summary(entry, _snapshot(entry, actor, references, restored_from=previous))


def list_revisions(actor, request):
    actor.require("entries:read")
    entry = _entry(actor, request.entry_id)
    page = list(Revision.objects.filter(entry=entry).defer("state").order_by("-number")
                [request.offset:request.offset + request.limit + 1])
    return {"revisions": [_summary(entry, r) for r in page[:request.limit]],
            "next_offset": request.offset + request.limit if len(page) > request.limit else None}


def search_entries(actor, request):
    actor.require("entries:read")
    entries = Entry.objects.filter(team_id=actor.team_id, kind__in=["note", "document"])
    if request.kind is not None:
        entries = entries.filter(kind=request.kind)
    if request.slug is not None:
        entries = entries.filter(slug=request.slug)
    if request.tag is not None:
        entries = entries.filter(state__tags__contains=[request.tag])
    if request.status is not None:
        entries = entries.filter(state__status=request.status)
    if request.metadata:
        entries = entries.filter(state__metadata__contains=request.metadata)
    if request.modified_since is not None:
        entries = entries.filter(modified_at__gte=request.modified_since)
    if request.modified_before is not None:
        entries = entries.filter(modified_at__lt=request.modified_before)
    if request.related_to is not None:
        filters = {"versions__number": F("revision"),
                   "versions__references__target_entry_id": request.related_to}
        if request.relation is not None:
            filters["versions__references__relation"] = request.relation
        entries = entries.filter(**filters).distinct()
    if request.query:
        if any(c.isalnum() for c in request.query):
            entries = entries.filter(search_vector=SearchQuery(request.query.replace("/", " "),
                                                               config="english", search_type="websearch"))
        else:
            entries = entries.filter(Q(state__content__icontains=request.query) | Q(state__title__icontains=request.query))
    page = list(entries.order_by("-modified_at", "id")[request.offset:request.offset + request.limit + 1])
    return {"entries": [{"entry_id": str(e.id), "slug": e.slug, "kind": e.kind,
                         "revision": e.revision, "title": e.state["title"],
                         "modified_at": e.modified_at.isoformat()} for e in page[:request.limit]],
            "next_offset": request.offset + request.limit if len(page) > request.limit else None}
