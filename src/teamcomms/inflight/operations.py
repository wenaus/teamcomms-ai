"""Transactional work control, snapshots and immutable retry receipts."""
from datetime import datetime
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from teamcomms.service.access import AccessError
from teamcomms.service.models import Membership, Team
from teamcomms.entries.models import Entry, Revision
from teamcomms.entries.operations import _expected, _references, _snapshot, _summary
from teamcomms.entries.schemas import State
from teamcomms.comms.models import Session, Resource
from teamcomms.comms.directory import online
from .models import Work, Dependency, WorkReceipt

DONE = {"completed", "failed", "canceled"}


def work_for(actor, entry_id):
    work = Work.objects.select_related("entry", "owner", "executor").filter(
        pk=entry_id, entry__team_id=actor.team_id).first()
    if work is None:
        raise AccessError("Work not found", 404)
    return work


def member(actor, participant_id):
    row = Membership.objects.select_related("participant").filter(
        team_id=actor.team_id, participant_id=participant_id, active=True).first()
    if row is None:
        raise AccessError("Active team participant not found", 404)
    return row


def coordinator(actor, work):
    if actor.role != "admin" and actor.participant_id != work.owner_id:
        raise AccessError("Only the accountable owner or team administrator may do this")


def contributor(actor, work):
    if actor.role != "admin" and actor.participant_id not in {work.owner_id, work.executor_id}:
        raise AccessError("Only the owner or current executor may report work")


def control(work, action):
    return {"owner_id": str(work.owner_id), "generation": work.generation,
        "executor_id": str(work.executor_id) if work.executor_id else None,
        "session_id": str(work.session_id) if work.session_id else None,
        "state": work.state, "form": work.form, "visibility": work.visibility,
        "parent_id": str(work.parent_id) if work.parent_id else None,
        "dependencies": sorted(str(x) for x in work.dependencies.values_list("prerequisite_id", flat=True)),
        "handoff": work.handoff, **work.detail, "action": action}


def snapshot(actor, work, action, *, initial=False):
    entry = work.entry
    entry.state["status"] = "archived" if work.state in DONE else "active"
    entry.state["metadata"]["inflight"] = control(work, action)
    state = State.model_validate(entry.state)
    # Previously validated references stay readable in immutable history; changing
    # unrelated fields does not require renewed access to every linked component.
    refs = [(r.target_entry, r.target_revision, r.relation)
            for r in Revision.objects.filter(entry=entry, number=entry.revision).first().references.select_related("target_entry", "target_revision")] if not initial else []
    if getattr(work, "_references", None) is not None:
        refs = work._references
    entry.state = state.model_dump(mode="json")
    if not initial:
        entry.revision += 1
    entry.save(update_fields=["state", "revision", "modified_at"])
    work.save()
    revision = _snapshot(entry, actor, refs)
    return result(work, revision)


def result(work, revision):
    return {**_summary(work.entry, revision), "state": revision.state,
            "work": revision.state["metadata"]["inflight"],
            "current_path": f"/inflight/{work.pk}",
            "revision_path": f"/inflight/{work.pk}?revision={revision.number}"}


def start(actor, request):
    actor.require("inflight:read")
    actor.require("inflight:write")
    # One lock order for creation, graph, terminal state and handoffs. It also
    # serializes retry UUID lookup without introducing global cross-team reads.
    if not Team.objects.select_for_update().filter(pk=actor.team_id).exists():
        raise AccessError("Team not found", 404)
    member(actor, actor.participant_id)
    receipt = WorkReceipt.objects.filter(pk=request.operation_id).select_related("entry").first()
    payload = request.model_dump(mode="json")
    if receipt:
        if (receipt.entry.team_id != actor.team_id or receipt.author_id != actor.participant_id
                or receipt.request != payload):
            raise AccessError("Operation UUID was used with a different request or author", 409)
        return receipt.result, payload
    return None, payload


def finish(actor, request, payload, work, value):
    WorkReceipt.objects.create(id=request.operation_id, entry=work.entry,
        author_id=actor.participant_id, request=payload, result=value)
    return value


def description(state):
    if "inflight" in state.metadata or state.status != "active":
        raise AccessError("Inflight controls and archive state require explicit work operations", 400)
    if not state.title.strip():
        raise AccessError("Work needs a title", 400)


def set_graph(actor, work, parent_id, dependencies):
    if len(set(dependencies)) != len(dependencies):
        raise AccessError("Duplicate dependencies", 400)
    for target_id in set(dependencies) | ({parent_id} if parent_id else set()):
        target = work_for(actor, target_id)
        if target.pk == work.pk:
            raise AccessError("Work cannot depend on or contain itself", 409)
    if parent_id and work_for(actor, parent_id).state in DONE:
        raise AccessError("Cannot add work to a closed parent", 409)
    # Completion edges point to prerequisites and children. Check the combined
    # graph, not just each relation type, before replacing either relation.
    rows = list(Work.objects.filter(entry__team_id=actor.team_id).values_list("pk", "parent_id"))
    edges = {key: set() for key, _ in rows}
    for key, parent in rows:
        parent = parent_id if key == work.pk else parent
        if parent:
            edges[parent].add(key)
    for source, target in Dependency.objects.filter(work__entry__team_id=actor.team_id).values_list("work_id", "prerequisite_id"):
        if source != work.pk:
            edges[source].add(target)
    edges[work.pk].update(dependencies)
    visiting, visited = set(), set()
    # Iterative DFS remains safe for deep but valid task trees.
    for root in edges:
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                visiting.remove(node); visited.add(node)
            elif node in visiting:
                raise AccessError("Parent/dependency cycle", 409)
            elif node not in visited:
                visiting.add(node); stack.append((node, True))
                stack.extend((child, False) for child in edges[node])
    work.parent_id = parent_id
    work.save(update_fields=["parent"])
    work.dependencies.all().delete()
    Dependency.objects.bulk_create([Dependency(work=work, prerequisite_id=x) for x in dependencies])


def associations(actor, request):
    for key in ("resource_ids", "message_ids", "dialog_event_ids"):
        values = getattr(request, key)
        if len(values) != len(set(values)):
            raise AccessError("Duplicate source associations", 400)
    if request.resource_ids:
        actor.require("comms:read")
        if Resource.objects.filter(team_id=actor.team_id, pk__in=request.resource_ids).count() != len(request.resource_ids):
            raise AccessError("Resource not found", 404)
    if request.message_ids:
        from teamcomms.comms.operations import get_message
        from teamcomms.comms.schemas import MessageQuery
        for value in request.message_ids:
            get_message(actor, MessageQuery(message_id=value, limit=1))
    if request.dialog_event_ids:
        actor.require("dialog:read")
        from teamcomms.dialog.operations import visible_events
        if visible_events(actor).filter(pk__in=request.dialog_event_ids).count() != len(request.dialog_event_ids):
            raise AccessError("Dialog event not found", 404)


@transaction.atomic
def create_work(actor, request):
    cached, payload = start(actor, request)
    if cached is not None:
        return cached
    description(request.state)
    associations(actor, request)
    refs = _references(actor, request.state)
    entry = Entry.objects.create(team_id=actor.team_id, kind="inflight",
        state=request.state.model_dump(mode="json"), created_by_id=actor.participant_id)
    work = Work.objects.create(entry=entry, owner_id=actor.participant_id, form=request.form,
        visibility=request.visibility, detail={"criteria": request.criteria, "blockers": "", "outcome": "", "evidence": [],
            **{key: payload[key] for key in ("resource_ids", "message_ids", "dialog_event_ids")}})
    work._references = refs
    set_graph(actor, work, request.parent_id, request.dependencies)
    return finish(actor, request, payload, work, snapshot(actor, work, "create", initial=True))


@transaction.atomic
def mutate_work(actor, request):
    cached, payload = start(actor, request)
    if cached is not None:
        return cached
    work = work_for(actor, request.entry_id)
    _expected(work.entry, request.expected_revision)
    if work.generation != request.expected_generation:
        raise AccessError("STALE_GENERATION: ownership changed", 409)
    change = request.change
    action = change.action
    if work.state in DONE and action != "reopen":
        raise AccessError("Closed work requires explicit reopen", 409)
    if action in {"edit", "progress", "transition", "sources"}:
        contributor(actor, work)
    elif action not in {"accept_handoff", "reject_handoff"}:
        coordinator(actor, work)
    if action == "edit":
        state = {**work.entry.state, "metadata": {k: v for k, v in work.entry.state["metadata"].items() if k != "inflight"}}
        state = State.model_validate({**state, **change.changes})
        description(state)
        if "relations" in change.changes:
            work._references = _references(actor, state)
        work.entry.state = state.model_dump(mode="json")
        if change.criteria is not None:
            coordinator(actor, work)
            work.detail["criteria"] = change.criteria
        if change.visibility is not None:
            coordinator(actor, work)
            work.visibility = change.visibility
    elif action == "sources":
        associations(actor, change)
        work.detail.update({key: change.model_dump(mode="json")[key]
                            for key in ("resource_ids", "message_ids", "dialog_event_ids")})
    elif action == "progress":
        if change.state == "blocked" and not change.blockers.strip():
            raise AccessError("Blocked work needs a blocker description", 400)
        work.state = change.state
        work.detail["blockers"] = change.blockers
    elif action == "transition":
        if change.state in {"failed", "canceled"}:
            coordinator(actor, work)
        if not change.outcome.strip() or any(not value.strip() for value in change.evidence):
            raise AccessError("Outcome/evidence cannot be blank", 400)
        if change.state == "completed":
            if not change.evidence:
                raise AccessError("Completion requires evidence", 400)
            if (work.children.exclude(state="completed").exists()
                    or work.dependencies.exclude(prerequisite__state="completed").exists()):
                raise AccessError("Unresolved subtasks or dependencies", 409)
        work.state = change.state
        work.detail.update(outcome=change.outcome, evidence=change.evidence, blockers="")
        work.handoff = None
    elif action == "assign_executor":
        if change.participant_id:
            member(actor, change.participant_id)
        if change.session_id and not Session.objects.filter(pk=change.session_id,
                membership__team_id=actor.team_id, membership__participant_id=change.participant_id,
                membership__active=True).exists():
            raise AccessError("Session does not belong to the executor", 404)
        work.executor_id = change.participant_id
        work.session_id = change.session_id
        # Fence the previous executor even when the new assignment uses the same
        # participant and an old session later retries a stale operation.
        work.generation += 1
    elif action == "graph":
        set_graph(actor, work, change.parent_id, change.dependencies)
    elif action == "offer_handoff":
        member(actor, change.successor_id)
        if change.successor_id == work.owner_id or not change.reason.strip():
            raise AccessError("Handoff needs another successor and a reason", 400)
        if change.expires_at is not None and change.expires_at <= timezone.now():
            raise AccessError("Handoff expiry must be in the future", 400)
        work.handoff = {"handoff_id": str(uuid4()), "successor_id": str(change.successor_id),
            "reason": change.reason, "offered_at": timezone.now().isoformat(),
            "expires_at": change.expires_at.isoformat() if change.expires_at else None}
    elif action in {"accept_handoff", "reject_handoff", "cancel_handoff"}:
        offer = work.handoff
        if not offer or offer["handoff_id"] != str(change.handoff_id):
            raise AccessError("Handoff was superseded or resolved", 409)
        if action != "cancel_handoff":
            if offer["successor_id"] != str(actor.participant_id):
                raise AccessError("Only the named successor may accept or reject")
            if offer["expires_at"] and datetime.fromisoformat(offer["expires_at"]) <= timezone.now():
                raise AccessError("Handoff expired; ownership unchanged", 409)
        if action == "accept_handoff":
            work.owner_id = actor.participant_id
            work.generation += 1
            work.executor_id = work.session_id = None
        work.handoff = None
    elif action == "reopen":
        if work.state not in DONE or not change.reason.strip():
            raise AccessError("Reopen requires closed work and a reason", 400)
        if (work.dependents.filter(work__state="completed").exists()
                or (work.parent_id and work.parent.state == "completed")):
            raise AccessError("Reopen completed parent/dependents first", 409)
        work.state = "planned"
        work.generation += 1
        work.executor_id = work.session_id = None
        work.detail.update(outcome="", evidence=[], blockers="", reopen_reason=change.reason)
    return finish(actor, request, payload, work, snapshot(actor, work, action))


def get_work(actor, request):
    actor.require("inflight:read")
    work = work_for(actor, request.entry_id)
    revision = Revision.objects.filter(entry=work.entry, number=request.revision or work.entry.revision).first()
    if revision is None:
        raise AccessError("Revision not found", 404)
    value = result(work, revision)
    value["state"] = dict(value["state"])
    content = value["state"]["content"]
    start = request.content_offset
    value["state"]["content"] = content[start:start + request.max_content_length]
    end = start + len(value["state"]["content"])
    # Presence is current, explicitly outside the immutable snapshot.
    current_member = Membership.objects.filter(team_id=actor.team_id, participant_id=work.owner_id).first()
    value["current_presence"] = {"owner_active": bool(current_member and current_member.active),
        "owner_online": online(Session.objects.filter(membership__team_id=actor.team_id,
            membership__participant_id=work.owner_id)).exists(),
        "executor_session_online": bool(work.session_id and online(Session.objects.filter(pk=work.session_id)).exists())}
    value["owner_name"] = work.owner.name if request.revision is None else None
    value["executor_name"] = work.executor.name if work.executor_id and request.revision is None else None
    targets = {str(e.pk): e.kind for e in Entry.objects.filter(team_id=actor.team_id,
               pk__in=[r["entry_id"] for r in revision.state["relations"]])}
    value["reference_paths"] = [{"path": ("/inflight/" if targets.get(r["entry_id"]) == "inflight" else "/entries/")
                               + r["entry_id"] + (f"?revision={r['revision']}" if r.get("revision") else ""),
                               "relation": r["relation"], "revision": r.get("revision")}
                              for r in revision.state["relations"]]
    return {**value, "content_length": len(content), "content_offset": start,
            "next_content_offset": end if end < len(content) else None}


def list_work(actor, request):
    actor.require("inflight:read")
    rows = Work.objects.select_related("entry", "owner", "executor").filter(entry__team_id=actor.team_id)
    if request.view == "live":
        rows = rows.exclude(state__in=DONE)
    elif request.view == "done":
        rows = rows.filter(state__in=DONE)
    if request.visibility != "all":
        rows = rows.filter(visibility=request.visibility)
    if request.parent_id:
        rows = rows.filter(parent_id=request.parent_id)
    if request.owner_id:
        rows = rows.filter(owner_id=request.owner_id)
    if request.query:
        rows = rows.filter(Q(entry__state__title__icontains=request.query) | Q(entry__state__content__icontains=request.query))
    page = list(rows.order_by("-entry__modified_at", "pk")[request.offset:request.offset + request.limit + 1])
    return {"entries": [{"entry_id": str(w.pk), "revision": w.entry.revision, "title": w.entry.state["title"],
        "modified_at": w.entry.modified_at.isoformat(), "state": w.state, "form": w.form,
        "visibility": w.visibility, "owner_id": str(w.owner_id), "owner_name": w.owner.name,
        "executor_id": str(w.executor_id) if w.executor_id else None,
        "executor_name": w.executor.name if w.executor_id else None,
        "generation": w.generation, "handoff": w.handoff,
        "blockers": w.detail["blockers"], "current_path": f"/inflight/{w.pk}"} for w in page[:request.limit]],
        "next_offset": request.offset + request.limit if len(page) > request.limit else None}


def get_work_changes(actor, request):
    actor.require("inflight:read")
    work = work_for(actor, request.entry_id)
    page = list(Revision.objects.filter(entry=work.entry).order_by("-number")[request.offset:request.offset + request.limit + 1])
    return {"revisions": [{**_summary(work.entry, r), "revision_path": f"/inflight/{work.pk}?revision={r.number}",
        "notice": f"{r.state['metadata']['inflight']['action']} · {r.state['metadata']['inflight']['state']}",
        "owner_id": r.state["metadata"]["inflight"]["owner_id"],
        "generation": r.state["metadata"]["inflight"]["generation"]} for r in page[:request.limit]],
        "next_offset": request.offset + request.limit if len(page) > request.limit else None}
