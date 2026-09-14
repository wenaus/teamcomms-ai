"""Focused 09/10 checks in a private socket-only PostgreSQL cluster.

Only new editing/Pouch operations and their HTTP/MCP bindings are exercised.
No pytest suite, service startup, streams, connectors or deployed credentials.
Run: .venv/bin/python tests/check_editing_pouch.py
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Barrier
from uuid import UUID, uuid4


def check():
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import close_old_connections, connections, IntegrityError, transaction
    from starlette.testclient import TestClient
    from teamcomms.service.access import AccessError, authenticate
    from teamcomms.service.asgi import create_app
    from teamcomms.service.operations import bootstrap
    from teamcomms.entries.models import Entry, EditPlan, RevisionReference
    from teamcomms.entries.operations import create_entry, update_entry, read_entry, restore_entry
    from teamcomms.entries.schemas import CreateEntry, State, UpdateEntry, ReadEntry, RestoreEntry
    from teamcomms.entries.edit_schemas import EditEntry, EntryEdits, PreviewEdits, ApplyEdits, ReadEdit, ReadTarget, Section
    from teamcomms.entries.editing import edit_entry, preview_edits, apply_edits, read_edit, read_target
    from teamcomms.entries.transforms import EditConflict, transform, unified_diff
    from teamcomms.pouch.models import Pouch
    from teamcomms.pouch.api import initialize_pouch, get_pouch, ReadPouch, export_pouch, ExportPouch

    call_command("migrate", verbosity=0)
    call_command("makemigrations", check=True, dry_run=True, verbosity=0)
    _, token = bootstrap("Synthetic editing team", "Synthetic editor")
    actor = authenticate(token)

    def denied(fn, status):
        try:
            fn()
        except AccessError as error:
            assert error.status == status, str(error)
        else:
            raise AssertionError("Expected denied operation")

    def race(fn):
        barrier = Barrier(2)
        def work(_):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return fn()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(work, range(2)))

    denied(lambda: get_pouch(actor, ReadPouch()), 404)
    assert Pouch.objects.count() == Entry.objects.count() == 0
    initial = race(lambda: initialize_pouch(actor))
    assert initial[0]["entry_id"] == initial[1]["entry_id"]
    assert sorted(r["created"] for r in initial) == [False, True]
    pouch_id = initial[0]["entry_id"]
    assert Pouch.objects.count() == Entry.objects.count() == 1
    source = "lead  \n# Plan\n\nold\n```md\n# Not a section\n```\n\n# End\nkeep\n"
    update_entry(actor, UpdateEntry(entry_id=pouch_id, expected_revision=1, changes={
        "content": source, "metadata": {"keep": {"nested": True}}}))
    edit = EditEntry(operation_id=uuid4(), entry_id=pouch_id, expected_revision=2, edits=[
        {"op": "section", "heading": "Plan", "content": "\nnew\n\n"},
        {"op": "insert", "anchor": "new", "text": " precise", "position": "after"},
        {"op": "metadata", "set": {"topic": "commissioning"}},
    ])
    results = race(lambda: edit_entry(actor, edit))
    assert results[0] == results[1] and results[0]["status"] == "applied"
    current = get_pouch(actor, ReadPouch())
    assert current["revision"] == 3
    assert current["state"]["content"] == "lead  \n# Plan\n\nnew precise\n\n# End\nkeep\n"
    assert current["state"]["metadata"] == {"keep": {"nested": True}, "topic": "commissioning"}
    assert get_pouch(actor, ReadPouch(revision=2))["state"]["content"] == source
    target = read_target(actor, ReadTarget(entry_id=pouch_id, section=Section(heading="Plan"), max_content_length=4))
    rest = read_target(actor, ReadTarget(entry_id=pouch_id, revision=3, section=Section(heading="Plan"), content_offset=target["next_content_offset"]))
    assert target["state"]["content"] + rest["state"]["content"] == "\nnew precise\n\n"
    diff = read_edit(actor, ReadEdit(operation_id=edit.operation_id, entry_id=pouch_id, max_diff_chars=20))
    assert diff["diff"]["next_offset"] == 20
    denied(lambda: edit_entry(actor, edit.model_copy(update={"expected_revision": 3})), 409)

    other = create_entry(actor, CreateEntry(state=State(content="other")))
    request = PreviewEdits(operation_id=uuid4(), entries=[
        EntryEdits(entry_id=pouch_id, expected_revision=3, edits=[{"op": "replace", "old_text": "new precise", "new_text": "reviewed"}]),
        EntryEdits(entry_id=other["entry_id"], expected_revision=1, edits=[{"op": "append", "content": "append"}]),
    ])
    assert preview_edits(actor, request)["status"] == "prepared"
    update_entry(actor, UpdateEntry(entry_id=other["entry_id"], expected_revision=1, changes={"title": "changed concurrently"}))
    result = apply_edits(actor, ApplyEdits(operation_id=request.operation_id))
    assert result["status"] == "conflicted"
    assert [x["status"] for x in result["result"]["entries"]] == ["not_applied", "conflicted"]
    assert get_pouch(actor, ReadPouch())["revision"] == 3
    assert apply_edits(actor, ApplyEdits(operation_id=request.operation_id)) == result

    request = request.model_copy(update={"operation_id": uuid4(), "entries": [
        request.entries[0], request.entries[1].model_copy(update={"expected_revision": 2})]})
    preview_edits(actor, request)
    successful = race(lambda: apply_edits(actor, ApplyEdits(operation_id=request.operation_id)))
    assert successful[0] == successful[1] and successful[0]["status"] == "applied"
    assert get_pouch(actor, ReadPouch())["revision"] == 4

    # Fixed revision references survive later surgical edits and restoration.
    linked = edit_entry(actor, EditEntry(operation_id=uuid4(), entry_id=other["entry_id"], expected_revision=3,
        edits=[{"op": "relations", "add": [{"entry_id": pouch_id, "revision": 3, "relation": "reviews"}]}]))
    assert linked["status"] == "applied" and RevisionReference.objects.get().target_revision.number == 3
    restore_entry(actor, RestoreEntry(entry_id=pouch_id, expected_revision=4, revision=2))
    exported = export_pouch(actor, ExportPouch(revision=3))
    assert exported["state"]["content"] == current["state"]["content"] and exported["author_id"] == str(actor.participant_id)
    assert Pouch.objects.get().entry_id == UUID(pouch_id)

    for mutation in (lambda: Pouch.objects.all().update(entry_id=other["entry_id"]),
                     lambda: Pouch.objects.all().delete(),
                     lambda: Entry.objects.filter(pk=pouch_id).update(kind="note"),
                     lambda: EditPlan.objects.filter(pk=edit.operation_id).update(result={"forged": True})):
        try:
            with transaction.atomic():
                mutation()
        except IntegrityError:
            pass
        else:
            raise AssertionError("Expected immutable database guard")

    reader = replace(actor, scopes=frozenset({"entries:read"}))
    denied(lambda: initialize_pouch(reader), 403)
    denied(lambda: apply_edits(reader, ApplyEdits(operation_id=edit.operation_id)), 403)
    # The current installation is single-team; a foreign UUID must never resolve.
    foreign = replace(actor, team_id=uuid4())
    denied(lambda: read_edit(foreign, ReadEdit(operation_id=edit.operation_id)), 404)
    denied(lambda: read_entry(foreign, ReadEntry(entry_id=pouch_id)), 404)

    for operations in ([{"op": "replace", "old_text": "x", "new_text": "y"}],
                       [{"op": "section", "heading": "Not a section", "content": "z"}]):
        original = State(content=source if operations[0]["op"] == "section" else "x x").model_dump(mode="json")
        parsed = EntryEdits(entry_id=pouch_id, expected_revision=1, edits=operations)
        try:
            transform(original, parsed.edits)
        except EditConflict:
            pass
        else:
            raise AssertionError("Expected ambiguous/fenced target rejection")
    parsed = EntryEdits(entry_id=pouch_id, expected_revision=1, edits=[{"op": "replace", "old_text": "x", "new_text": "", "all_matches": True}])
    assert transform(State(content="x x").model_dump(mode="json"), parsed.edits)["content"] == " "
    assert "No newline at end of file" in unified_diff("text", "text\n")

    with TestClient(create_app(mount_path="/nested/tc")) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/nested/tc/api/pouch").json()["entry_id"] == pouch_id
        assert client.get("/nested/tc/pouch?revision=3").status_code == 200
        assert client.post("/nested/tc/api/pouch/initialize", json={}).json()["created"] is False
        response = client.post("/nested/tc/mcp/", headers={"Accept": "application/json, text/event-stream"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_pouch", "arguments": {"request": {"revision": 3}}}})
        assert response.status_code == 200 and not response.json()["result"].get("isError"), response.text
    connections.close_all()
    print("PASS: Pouch initialization race/integrity, surgical whitespace/section/metadata, concurrent retry, atomic bulk conflict and success, pinned links/restore/export, cross-team/scopes, new HTTP/MCP paths; no suite or native checks")


if __name__ == "__main__":
    bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    with tempfile.TemporaryDirectory(prefix="tc-editing-check-") as directory:
        root = Path(directory)
        socket_dir = root / "socket"
        socket_dir.mkdir()
        os.environ.update(TEAMCOMMS_DATABASE_URL=f"postgresql:///postgres?host={socket_dir}",
                          TEAMCOMMS_SECRET_KEY="synthetic-editing-only", TEAMCOMMS_ALLOWED_HOSTS="testserver,localhost",
                          DJANGO_SETTINGS_MODULE="teamcomms.service.settings")
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        subprocess.run([str(bindir / "initdb"), "-D", str(root / "data"), "--auth=trust", "--no-locale", "--encoding=UTF8"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(root / "data"), "-l", str(root / "postgres.log"),
                        "-o", f"-k {socket_dir} -c listen_addresses=''", "-w", "start"], check=True, stdout=subprocess.DEVNULL)
        try:
            check()
        finally:
            subprocess.run([str(bindir / "pg_ctl"), "-D", str(root / "data"), "-m", "fast", "-w", "stop"], check=True, stdout=subprocess.DEVNULL)
