"""Focused history migration, visibility, cursor and HTTP/MCP checks.

Uses synthetic identities in a private socket-only PostgreSQL cluster; no suite,
connectors, native clients, model calls, receipts or deployed credentials.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from threading import Event
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def check():
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import close_old_connections, connections, IntegrityError, transaction
    from django.db.migrations.executor import MigrationExecutor
    from starlette.testclient import TestClient
    from teamcomms.comms.directory import register_session
    from teamcomms.comms.history import get_message_history
    from teamcomms.comms import history
    from teamcomms.comms.models import Delivery, Message, MessagePosition, Receipt
    from teamcomms.comms.operations import get_message, send_message
    from teamcomms.comms.schemas import MessageHistory, MessageQuery, RegisterSession, SendMessage
    from teamcomms.service.access import AccessError, SCOPES, authenticate, mint_credential
    from teamcomms.service.asgi import create_app
    from teamcomms.service.models import Membership, Participant
    from teamcomms.service.operations import bootstrap

    # Start at the exact predecessor to check the real upgrade/backfill path.
    executor = MigrationExecutor(connections["default"])
    executor.migrate([("teamcomms_comms", "0002_immutable_records")])
    _, owner_token = bootstrap("Synthetic history team", "Human operator")
    owner = authenticate(owner_token)
    human = Participant.objects.get(pk=owner.participant_id)

    def member(name, operator=None):
        person = Participant.objects.create(name=name, kind="ai", operator=operator)
        membership = Membership.objects.create(team_id=owner.team_id, participant=person)
        _, token = mint_credential(membership, SCOPES - {"credentials:write", "directory:write"})
        return authenticate(token), token

    alice, alice_token = member("Alice AI", human)
    bob, _ = member("Bob AI")
    outsider, _ = member("Unrelated AI", human)

    def session(actor, name):
        return register_session(actor, RegisterSession(native_id=name, name=name,
            host="synthetic-host", client="synthetic", model="synthetic-model"))["session_id"]

    a1, a2 = session(alice, "Alice one"), session(alice, "Alice two")
    b1, b2 = session(bob, "Bob one"), session(bob, "Bob two")
    o1 = session(outsider, "Unrelated")

    def publish(actor, recipients, content="complete text", sender=None, reply_to=None):
        return send_message(actor, SendMessage(message_id=uuid4(), sender_session_id=sender,
            audience={"session_ids": recipients}, content=content, topic="history-check", reply_to=reply_to))

    legacy = publish(bob, [a1, a2, o1], "legacy body", b1)
    legacy_before = get_message(alice, MessageQuery(message_id=legacy["message_id"]))
    call_command("migrate", verbosity=0)
    call_command("makemigrations", check=True, dry_run=True, verbosity=0)
    assert MessagePosition.objects.count() == 1
    assert get_message(alice, MessageQuery(message_id=legacy["message_id"])) == legacy_before
    long_content = "正文\n" * 4000
    outgoing = publish(alice, [b1], long_content, a1, legacy["message_id"])
    no_session = publish(alice, [b2], "authored without a session")
    private = publish(bob, [o1], "not visible to Alice")
    before = list(Delivery.objects.order_by("id").values())
    receipt_count = Receipt.objects.count()

    def read(actor=alice, **kwargs):
        return get_message_history(actor, MessageHistory(**kwargs))

    page = read(limit=1)
    assert page["has_more"] and len(page["messages"]) == 1
    item = page["messages"][0]
    assert item["message_id"] == legacy["message_id"]
    assert len(item["recipients"]) == 2  # Own two deliveries, one message.
    assert {r["identity"]["participant_id"] for r in item["recipients"]} == {str(alice.participant_id)}
    assert item["author_identity"]["operator_id"] is None
    assert item["sender_session"]["native_id"] == "Bob one"
    assert item["publication_snapshot"]["name"] == "Bob AI"
    assert page["viewer"]["operator_id"] == str(human.id)
    assert page["viewer"]["participant_id"] == str(alice.participant_id)
    assert page["viewer"]["operator_name"] == human.name
    second = read(cursor=page["next_cursor"], limit=1)
    assert second["messages"][0]["content"] == long_content
    assert second["messages"][0]["reply_to"] == legacy["message_id"]
    assert second["messages"][0]["viewer_relation"] == {"sent": True, "received": False}
    last = read(cursor=second["next_cursor"], limit=1)
    assert not last["has_more"] and last["messages"][0]["sender_session"] is None
    assert read(cursor=last["next_cursor"])["messages"] == []
    assert len(read(direction="sent")["messages"]) == 2
    assert len(read(direction="received")["messages"]) == 1
    assert read(topic="absent")["messages"] == []
    assert private["message_id"] not in {x["message_id"] for x in read()["messages"]}
    # A shared human operator grants no access to either AI's private messages.
    assert read(owner)["messages"] == []
    assert len(read(bob)["messages"][0]["recipients"]) == 3
    assert read(replace(alice, team_id=uuid4()))["messages"] == []
    for who, query, status in (
        (bob, MessageHistory(cursor=page["next_cursor"]), 400),
        (alice, MessageHistory(cursor=page["next_cursor"], direction="sent"), 400),
        (alice, MessageHistory(cursor=page["next_cursor"] + "x"), 400),
        (replace(alice, scopes=frozenset()), MessageHistory(), 403),
    ):
        try:
            get_message_history(who, query)
        except AccessError as error:
            assert error.status == status
        else:
            raise AssertionError("Expected history denial")
    history.PAGE_BYTES = 1
    bounded = read()
    assert len(bounded["messages"]) == 1 and bounded["has_more"]
    assert len(read(cursor=bounded["next_cursor"])["messages"]) == 1
    history.PAGE_BYTES = 1_000_000
    assert list(Delivery.objects.order_by("id").values()) == before
    assert Receipt.objects.count() == receipt_count
    for mutation in (lambda: MessagePosition.objects.all().update(message_id=uuid4()),
                     lambda: MessagePosition.objects.all().delete(),
                     lambda: Message.objects.all().update(author_snapshot={})):
        try:
            with transaction.atomic():
                mutation()
        except IntegrityError:
            pass
        else:
            raise AssertionError("Expected immutable evidence")

    # Different destination sessions avoid the pre-existing inbox lock. The
    # new publication index must prevent a later commit crossing the cursor.
    tail = read()["next_cursor"]
    inserted, release, contender = Event(), Event(), Event()

    def delayed():
        close_old_connections()
        try:
            with transaction.atomic():
                result = publish(bob, [a1], "delayed commit")
                inserted.set()
                assert release.wait(8)
            return result
        finally:
            connections.close_all()

    def later():
        close_old_connections()
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SET application_name = 'tc-history-contender'")
            contender.set()
            return publish(bob, [a2], "later commit")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(delayed)
        assert inserted.wait(8)
        following = pool.submit(later)
        try:
            assert contender.wait(8)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE "
                        "application_name = 'tc-history-contender' AND cardinality(pg_blocking_pids(pid)) > 0)")
                    blocked = cursor.fetchone()[0]
                if blocked or following.done():
                    break
                time.sleep(0.02)
            assert blocked, "Later publication must wait for the uncommitted earlier position"
            assert read(cursor=tail)["messages"] == []
            assert not following.done()
        finally:
            release.set()
        ordered = [first.result(timeout=8)["message_id"], following.result(timeout=8)["message_id"]]
    assert [x["message_id"] for x in read(cursor=tail)["messages"]] == ordered

    with TestClient(create_app()) as client:
        client.headers["Authorization"] = f"Bearer {alice_token}"
        response = client.get("/api/comms/messages/history", params={"limit": 1})
        assert response.status_code == 200 and response.json()["viewer"] == page["viewer"], response.text
        assert client.get("/api/comms/messages/history", params={"participant_id": str(bob.participant_id)}).status_code == 400
        response = client.post("/mcp/", headers={"Accept": "application/json, text/event-stream"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "get_message_history", "arguments": {"request": {"limit": 1}}}})
        assert response.status_code == 200 and not response.json()["result"].get("isError"), response.text
    connections.close_all()
    print("PASS: legacy migration/backfill, complete content, verified identities, fixed recipient visibility/deduplication, signed bound cursors, byte/count pages, no receipt writes, immutable evidence, concurrent commit ordering, HTTP/MCP; no suite")


if __name__ == "__main__":
    bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    with tempfile.TemporaryDirectory(prefix="tc-history-") as directory:
        root = Path(directory)
        socket_dir = root / "socket"
        socket_dir.mkdir()
        os.environ.update(TEAMCOMMS_DATABASE_URL=f"postgresql:///postgres?host={socket_dir}",
            TEAMCOMMS_SECRET_KEY="synthetic-history-only", TEAMCOMMS_ALLOWED_HOSTS="testserver,localhost",
            DJANGO_SETTINGS_MODULE="teamcomms.service.settings")
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        subprocess.run([str(bindir / "initdb"), "-D", str(root / "data"), "--auth=trust", "--no-locale", "--encoding=UTF8"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(root / "data"), "-l", str(root / "postgres.log"),
            "-o", f"-k {socket_dir} -c listen_addresses=''", "-w", "start"], check=True, stdout=subprocess.DEVNULL)
        try:
            check()
        finally:
            subprocess.run([str(bindir / "pg_ctl"), "-D", str(root / "data"), "-m", "fast", "-w", "stop"], check=True, stdout=subprocess.DEVNULL)
