"""Focused Dialog integrity and recorder recovery check in an isolated database.

This command exercises only Dialog. It does not invoke the TeamComms suite,
standalone startup checks, streaming checks, native clients, or deployed services.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4


async def recorder_check(root):
    from teamcomms.connectors.dialog import Recorder, visible_events
    from teamcomms.connectors.client import ServiceError
    from teamcomms.connectors.state import Store

    class Service:
        def __init__(self):
            self.events = {}
            self.fail = True

        async def request(self, method, path, body):
            key = body["source_id"]
            if key in self.events:
                assert self.events[key] == body
            self.events[key] = dict(body)
            if body["role"] == "assistant" and self.fail:
                self.fail = False
                raise ServiceError("Simulated response loss after durable publication", 503)
            return {"event_id": len(self.events)}

    def line(role, text, phase="final"):
        return json.dumps({"timestamp": "2026-09-14T12:00:00Z", "type": "response_item",
            "payload": {"type": "message", "id": str(uuid4()), "role": role,
                        "phase": phase, "content": [{"type": "input_text", "text": text}]}}) + "\n"

    path = root / "transcript.jsonl"
    path.write_text(line("user", "Investigate this event.") + line("assistant", "Checking the state.", "commentary"))
    service = Service()
    directory = root / "recording"
    store = Store(directory)
    recorder = Recorder(service, store, str(uuid4()), "codex", path, from_start=True)
    try:
        await recorder.once()
    except ServiceError:
        pass  # Expected lost response; the saved line cursor must remain behind it.
    else:
        raise AssertionError("Expected simulated response loss")
    session = recorder.session_id
    store.close()
    store = Store(directory)
    recorder = Recorder(service, store, session, "codex", path, from_start=True)
    await recorder.once()
    assert len(service.events) == 3
    assert {v["role"] for v in service.events.values()} == {"gap", "human", "assistant"}
    partial = line("assistant", "Final result.")
    with path.open("a") as handle:
        handle.write(partial[:-1])
    assert await recorder.once() == 0
    with path.open("a") as handle:
        handle.write("\n" + line("user", "TeamComms history context (recorded material)"))
    assert await recorder.once() == 1
    assert len(service.events) == 4
    path.write_text(line("user", "New transcript after truncation."))
    await recorder.once()
    assert sum(e["role"] == "gap" for e in service.events.values()) == 2
    store.close()
    claude = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": "Reasoning excluded"}, {"type": "text", "text": "Visible answer"}]}}
    assert [e["content"] for e in visible_events("claude", claude)] == ["Visible answer"]
    peer = {"type": "user", "message": {"content": "[TJAI peer example]\nPeer request"}}
    assert visible_events("claude", peer)[0]["role"] == "peer"
    from teamcomms.connectors.dialog import bootstrap_context
    from teamcomms.connectors.config import Configuration
    config = Configuration(url="http://localhost", token_file=root / "unused", host="test", bootstrap={})
    class Unavailable:
        async def request(self, *args):
            raise ServiceError("History unavailable", 503)
    assert "unavailable" in await bootstrap_context(Unavailable(), config)


def exercise(root):
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import close_old_connections, connection, IntegrityError, transaction
    from django.utils import timezone
    from starlette.testclient import TestClient
    from teamcomms.service.access import AccessError, SCOPES, authenticate, mint_credential
    from teamcomms.service.asgi import create_app
    from teamcomms.service.models import Membership, Participant
    from teamcomms.service.operations import bootstrap
    from teamcomms.comms.directory import register_session
    from teamcomms.comms.operations import send_message
    from teamcomms.comms.schemas import RegisterSession, SendMessage
    from teamcomms.dialog.operations import get_dialog, record_dialog, session_bootstrap
    from teamcomms.dialog.schemas import Bootstrap, DialogQuery, RecordDialog
    from teamcomms.dialog.models import Event

    call_command("migrate", verbosity=0)
    call_command("makemigrations", check=True, dry_run=True, verbosity=0)
    record, token = bootstrap("Dialog check", "Operator")
    actor = authenticate(token)
    def member(name):
        person = Participant.objects.create(name=name, kind="ai", operator_id=actor.participant_id)
        membership = Membership.objects.create(team_id=actor.team_id, participant=person)
        _, credential = mint_credential(membership, SCOPES)
        return authenticate(credential)
    sender, unrelated = member("Sender"), member("Unrelated")
    def session(person, name):
        return register_session(person, RegisterSession(native_id=str(uuid4()), client="codex", host="test", name=name))["session_id"]
    target = session(actor, "target")
    native_sender = session(sender, "sender")
    request = RecordDialog(session_id=target, source_id="native:one", source_sequence=1,
        occurred_at=timezone.now(), role="human", content="Operator input", topic="work")
    def save():
        close_old_connections()
        try:
            return record_dialog(actor, request)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(lambda _: save(), range(2)))
    assert results[0]["event_id"] == results[1]["event_id"] and Event.objects.count() == 1
    for who, body, status in [(sender, request, 404),
        (actor, request.model_copy(update={"content": "Changed"}), 409),
        (replace(actor, scopes=actor.scopes - {"dialog:write"}), request, 403)]:
        try:
            record_dialog(who, body)
        except AccessError as error:
            assert error.status == status
        else:
            raise AssertionError("Expected authorization/conflict failure")
    message_id = uuid4()
    send_message(sender, SendMessage(message_id=message_id, sender_session_id=native_sender,
        audience={"session_ids": [target]}, content="Canonical peer content"))
    peer = RecordDialog(session_id=target, source_id="native:peer", source_sequence=2,
        occurred_at=timezone.now(), role="peer", content="Spoofed transcript content", message_id=message_id)
    record_dialog(actor, peer)
    history = get_dialog(actor, DialogQuery())
    assert history["events"][0]["content"] == "Canonical peer content"
    assert history["events"][0]["attribution"]["participant_id"] == str(sender.participant_id)
    assert history["events"][1]["attribution"]["authority"] == "recorder-reported"
    assert len(get_dialog(unrelated, DialogQuery())["events"]) == 1
    assert len(get_dialog(replace(actor, scopes=actor.scopes - {"comms:read"}), DialogQuery())["events"]) == 1
    page = get_dialog(actor, DialogQuery(limit=1))
    record_dialog(actor, request.model_copy(update={"source_id": "native:later", "source_sequence": 3,
                                                   "content": "Latest " + '\\"\n' * 3000, "role": "assistant"}))
    assert get_dialog(actor, DialogQuery(before_id=page["next_before_id"]))["events"][0]["source_id"] == "native:one"
    context = session_bootstrap(actor, Bootstrap(host="test", max_chars=1400))
    assert context["chars"] <= 1400 and context["truncated"]
    assert "Latest" in context["context"] and "permissions" in context["context"]
    from teamcomms.entries.operations import create_entry
    from teamcomms.entries.schemas import CreateEntry
    guidance = create_entry(actor, CreateEntry(state={"title": "SWF guidance", "content": "Coordinate deployments."}))
    context = session_bootstrap(actor, Bootstrap(host="test", max_chars=4000,
                                                guidance_entry_ids=[guidance["entry_id"]]))
    assert context["chars"] <= 4000 and "Coordinate deployments." in context["context"]
    assert "author_id" in context["context"] and "Latest" in context["context"]
    try:
        with transaction.atomic():
            Event.objects.filter(pk=results[0]["event_id"]).update(content="tampered")
    except IntegrityError:
        pass
    else:
        raise AssertionError("Database allowed evidence mutation")
    with TestClient(create_app()) as http:
        http.headers["Authorization"] = "Bearer " + token
        assert http.get("/api/dialog", params={"host": "test"}).status_code == 200
        assert http.post("/api/dialog/events", json=request.model_dump(mode="json")).status_code == 200
        assert http.post("/api/dialog/bootstrap", json={"max_chars": 999}).status_code == 400
        headers = {"Accept": "application/json, text/event-stream"}
        result = http.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": "get_dialog", "arguments": {"request": {"host": "test"}}}})
        assert result.status_code == 200 and not result.json()["result"].get("isError"), result.text
    asyncio.run(recorder_check(root))
    connection.close()
    print("Dialog checks passed: replay race, ownership/scopes, canonical peer access, immutable evidence, pagination, bootstrap bound, HTTP/MCP, recorder restart/partial-line/truncation.")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--exercise":
        exercise(Path(sys.argv[2]))
        return
    bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    with tempfile.TemporaryDirectory(prefix="tc-dialog-check-") as directory:
        root = Path(directory)
        data, socket = root / "data", root / "socket"
        socket.mkdir()
        env = {**os.environ, "TEAMCOMMS_DATABASE_URL": f"postgresql:///postgres?host={socket}",
               "TEAMCOMMS_SECRET_KEY": "isolated-dialog-check", "TEAMCOMMS_ALLOWED_HOSTS": "testserver",
               "DJANGO_SETTINGS_MODULE": "teamcomms.service.settings"}
        subprocess.run([str(bindir / "initdb"), "-D", str(data), "--auth=trust", "--no-locale", "--encoding=UTF8"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
            "-o", f"-k {socket} -c listen_addresses=''", "-w", "start"], check=True, stdout=subprocess.DEVNULL)
        try:
            subprocess.run([sys.executable, __file__, "--exercise", str(root)], env=env, check=True)
        finally:
            subprocess.run([str(bindir / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
