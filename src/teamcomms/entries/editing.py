"""Immutable previews, deterministic entry locks and atomic retry-safe application."""

from django.db import connection, transaction

from teamcomms.service.access import AccessError
from .models import EditPlan
from .operations import _entry, _references, _snapshot, _summary, read_entry
from .schemas import ReadEntry, State
from .edit_schemas import EntryEdits, PreviewEdits, ApplyEdits
from .transforms import EditConflict, section_range, state_diff, transform


def _lock(operation_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"tc-edit:{operation_id}"])


def _plan(actor, operation_id):
    plan = EditPlan.objects.filter(pk=operation_id, team_id=actor.team_id, author_id=actor.participant_id).first()
    if plan is None:
        raise AccessError("Edit plan not found for this participant", 404)
    return plan


def _record(plan):
    return {"operation_id": str(plan.id), "status": "prepared" if plan.result is None else plan.result["status"],
            "atomic": True, "created_at": plan.created_at.isoformat(),
            "entries": [{"entry_id": item["entry_id"], "expected_revision": item["expected_revision"],
                         "changed": item["before"] != item["after"]} for item in plan.prepared],
            "result": plan.result,
            "diff_reference": {"tool": "get_entry_edit", "operation_id": str(plan.id)}}


@transaction.atomic
def preview_edits(actor, request):
    actor.require("entries:read")
    actor.require("entries:write")
    _lock(request.operation_id)
    original = request.model_dump(mode="json")
    existing = EditPlan.objects.filter(pk=request.operation_id).first()
    if existing:
        if existing.team_id != actor.team_id or existing.author_id != actor.participant_id or existing.request != original:
            raise AccessError("Operation ID already belongs to a different request or author", 409)
        return _record(existing)
    prepared = []
    for item in request.entries:
        entry = _entry(actor, item.entry_id)
        if entry.revision != item.expected_revision:
            raise AccessError(f"STALE_REVISION: {entry.id}: expected {item.expected_revision}, current {entry.revision}", 409)
        try:
            after = transform(entry.state, item.edits)
        except EditConflict as error:
            raise AccessError(f"{entry.id}: {error}", 409) from error
        _references(actor, State.model_validate(after))
        prepared.append({"entry_id": str(entry.id), "expected_revision": entry.revision,
                         "before": entry.state, "after": after})
    plan = EditPlan.objects.create(id=request.operation_id, team_id=actor.team_id, author_id=actor.participant_id,
                                   request=original, prepared=prepared)
    result = _record(plan)
    # Compact preview; complete diffs are retrieved by explicit entry and offset.
    result["previews"] = []
    for item in prepared:
        diff = state_diff(item["before"], item["after"])
        result["previews"].append({"entry_id": item["entry_id"], "diff": diff[:1000],
                                   "diff_length": len(diff), "next_diff_offset": 1000 if len(diff) > 1000 else None})
    return result


def read_edit(actor, request):
    actor.require("entries:read")
    plan = _plan(actor, request.operation_id)
    result = _record(plan)
    if request.entry_id is not None:
        item = next((p for p in plan.prepared if p["entry_id"] == str(request.entry_id)), None)
        if item is None:
            raise AccessError("Entry not selected in this plan", 404)
        _entry(actor, request.entry_id)
        diff = state_diff(item["before"], item["after"])
        start = request.diff_offset
        value = diff[start:start + request.max_diff_chars]
        end = start + len(value)
        result["diff"] = {"entry_id": str(request.entry_id), "content": value, "length": len(diff),
                          "offset": start, "next_offset": end if end < len(diff) else None}
    return result


@transaction.atomic
def apply_edits(actor, request):
    actor.require("entries:read")
    actor.require("entries:write")
    _lock(request.operation_id)
    plan = _plan(actor, request.operation_id)
    if plan.result is not None:
        return _record(plan)
    entries, failures, references = {}, {}, {}
    for item in sorted(plan.prepared, key=lambda p: p["entry_id"]):
        entry_id = item["entry_id"]
        try:
            entry = _entry(actor, entry_id, lock=True)
            entries[entry_id] = entry
            if entry.revision != item["expected_revision"]:
                raise AccessError(f"STALE_REVISION: expected {item['expected_revision']}, current {entry.revision}", 409)
            references[entry_id] = _references(actor, State.model_validate(item["after"]))
        except (AccessError, ValueError) as error:
            failures[entry_id] = str(error)
    if failures:
        plan.result = {"status": "conflicted", "entries": [
            {"entry_id": p["entry_id"], "status": "conflicted" if p["entry_id"] in failures else "not_applied",
             "detail": failures.get(p["entry_id"], "Atomic batch rejected; no entry changed"),
             "current_revision": entries[p["entry_id"]].revision if p["entry_id"] in entries else None}
            for p in plan.prepared]}
    else:
        results = []
        for item in plan.prepared:
            entry = entries[item["entry_id"]]
            if item["before"] == item["after"]:
                results.append({"entry_id": str(entry.id), "status": "unchanged", "revision": entry.revision})
                continue
            entry.state = item["after"]
            entry.revision += 1
            entry.save(update_fields=["state", "revision", "modified_at"])
            revision = _snapshot(entry, actor, references[str(entry.id)])
            results.append({**_summary(entry, revision), "status": "applied"})
        plan.result = {"status": "applied", "entries": results}
    plan.save(update_fields=["result"])
    return _record(plan)


def edit_entry(actor, request):
    # Two durable transactions: a lost response can be recovered by operation ID.
    preview_edits(actor, PreviewEdits(operation_id=request.operation_id, entries=[
        EntryEdits.model_validate(request.model_dump(exclude={"operation_id"}))]))
    return apply_edits(actor, ApplyEdits(operation_id=request.operation_id))


def read_target(actor, request):
    saved = read_entry(actor, ReadEntry(entry_id=request.entry_id, revision=request.revision, max_content_length=40000))
    content = saved["state"]["content"]
    start, end = 0, len(content)
    if request.section:
        try:
            start, end = section_range(content, request.section)
        except EditConflict as error:
            raise AccessError(str(error), 409) from error
    offset = min(start + request.content_offset, end)
    value = content[offset:min(offset + request.max_content_length, end)]
    saved["state"]["content"] = value
    saved.update(target_start=start, target_end=end, content_offset=offset,
                 next_content_offset=(offset + len(value) - start) if offset + len(value) < end else None)
    return saved
