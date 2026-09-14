"""Publication integrity, destination isolation, and bounded replay contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Barrier, Event
from uuid import uuid4

from django.db import close_old_connections, IntegrityError, transaction
from django.utils import timezone
import pytest
from starlette.testclient import TestClient

from teamcomms.comms import directory, operations
from teamcomms.comms.models import Delivery, Message, MessageReference, Receipt, Session
from teamcomms.comms.schemas import Inbox, Report, SendMessage
from teamcomms.service.access import AccessError, SCOPES, authenticate, mint_credential
from teamcomms.service.asgi import create_app
from teamcomms.service.models import Membership, Participant
from teamcomms.service.operations import bootstrap

pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/api/comms"


@pytest.fixture
def team():
    record, token = bootstrap("Team", "Operator")
    with TestClient(create_app()) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, token, record


def post(client, path, data):
    response = client.post(BASE + path, json=data)
    assert response.status_code == 200, response.text
    return response.json()


def register(http, name, **extra):
    return post(http, "/sessions", {"native_id": name, "name": name, "host": "host-a", "client": "test", **extra})


def member(record, name):
    person = Participant.objects.create(name=name, kind="ai")
    membership = Membership.objects.create(team_id=record["team_id"], participant=person)
    _, token = mint_credential(membership, SCOPES - {"directory:write", "credentials:write"})
    return token


def envelope(destination, **extra):
    return {"message_id": str(uuid4()), "audience": {"session_ids": [destination["session_id"]]},
            "content": "An update\n", **extra}


def inbox(client, session, **extra):
    response = client.get(BASE + "/messages", params={"session_id": session["session_id"], **extra})
    assert response.status_code == 200, response.text
    return response.json()


def report(client, delivery, state, **extra):
    return post(client, "/deliveries", {"receipt_id": str(uuid4()), "delivery_id": delivery["delivery_id"],
                "expected_revision": delivery["revision"], "state": state, **extra})


def test_directory_refresh_canonical_resources_and_stale_sessions(team):
    client, _, _ = team
    project = post(client, "/resources", {"key": "repo:example/core", "kind": "project", "name": "Core"})
    request = {"key": "checkout:host-a:core", "kind": "checkout", "name": "Core checkout", "host": "host-a",
               "project_id": project["resource_id"], "aliases": ["/work/core", "/home/dev/core"]}
    checkout = post(client, "/resources", request)
    assert post(client, "/resources", request) == checkout
    assert client.post(BASE + "/resources", json={**request, "key": "duplicate"}).status_code == 409
    session = register(client, "native", resource_ids=[checkout["resource_id"]])
    assert register(client, "native", model="current", resource_ids=[checkout["resource_id"]])["session_id"] == session["session_id"]
    assert Session.objects.count() == 1
    post(client, "/sessions/heartbeat", {"session_id": session["session_id"], "state": "active", "model": "changed"})
    assert client.get(BASE + "/sessions").json()["sessions"][0]["model"] == "changed"
    received = post(client, "/messages", envelope(session, audience={"resource_ids": [project["resource_id"]]}))
    assert len(received["deliveries"]) == 1
    Session.objects.update(last_seen=timezone.now() - timedelta(seconds=91))
    assert client.get(BASE + "/sessions").json()["sessions"] == []
    assert not client.get(BASE + "/sessions?include_offline=true").json()["sessions"][0]["online"]
    assert len(post(client, "/messages", envelope(session))["deliveries"]) == 1


def test_group_topic_host_union_snapshots_and_idempotence(team):
    client, _, _ = team
    a, b = register(client, "a"), register(client, "b")
    group = post(client, "/groups", {"key": "ops", "name": "Operations"})
    for target in (a, b):
        post(client, "/subscriptions", {"session_id": target["session_id"], "group_id": group["group_id"]})
        post(client, "/subscriptions", {"session_id": target["session_id"], "topic": "alarms"})
    request = envelope(a, audience={"session_ids": [a["session_id"]], "hosts": ["host-a"],
                                  "group_ids": [group["group_id"]], "topics": ["alarms"]})
    first = post(client, "/messages", request)
    assert len(first["deliveries"]) == 2
    c = register(client, "late")
    post(client, "/subscriptions", {"session_id": a["session_id"], "group_id": group["group_id"], "active": False})
    assert post(client, "/messages", request) == first
    assert inbox(client, c)["messages"] == []
    assert client.post(BASE + "/messages", json={**request, "content": "Changed"}).status_code == 409
    assert Message.objects.count() == 1 and Delivery.objects.count() == 2


def test_authorship_session_ownership_and_private_mailbox(team):
    client, admin, record = team
    a = register(client, "admin")
    other = member(record, "Other AI")
    client.headers["Authorization"] = f"Bearer {other}"
    b = register(client, "ai", host="host-b", client="codex")
    request = envelope(a, sender_session_id=b["session_id"])
    sent = post(client, "/messages", request)
    assert client.get(BASE + "/messages", params={"session_id": a["session_id"]}).status_code == 404
    assert client.post(BASE + "/sessions/heartbeat", json={"session_id": a["session_id"], "state": "offline"}).status_code == 404
    assert client.post(BASE + "/messages", json=envelope(a, sender_session_id=a["session_id"])).status_code == 404
    assert client.post(BASE + "/messages", json={**envelope(a), "author_id": record["participant_id"]}).status_code == 400
    client.headers["Authorization"] = f"Bearer {admin}"
    message = inbox(client, a)["messages"][0]["message"]
    assert message["author_id"] == b["participant_id"] and message["author"]["kind"] == "ai"
    third = member(record, "Unrelated")
    client.headers["Authorization"] = f"Bearer {third}"
    assert client.get(BASE + "/messages/read", params={"message_id": sent["message_id"]}).status_code == 404


def test_receipts_independent_cas_retry_and_acknowledgment(team):
    client, _, _ = team
    a, b = register(client, "a"), register(client, "b")
    sent = post(client, "/messages", envelope(a, audience={"hosts": ["host-a"]}))
    rows = {d["session_id"]: d for d in sent["deliveries"]}
    da = report(client, rows[a["session_id"]], "uncertain")
    assert client.post(BASE + "/deliveries", json={"receipt_id": str(uuid4()), "delivery_id": da["delivery_id"],
        "expected_revision": 1, "state": "uncertain"}).status_code == 409
    da = report(client, da, "accepted")
    ack = post(client, "/messages/acknowledge", {"session_id": a["session_id"], "message_id": sent["message_id"]})
    assert ack["acknowledged_at"] and ack["state"] == "accepted"
    assert inbox(client, a, pending_only=True)["messages"] == []
    db = report(client, rows[b["session_id"]], "failed", detail="Client was unavailable before write")
    request = {"receipt_id": str(uuid4()), "delivery_id": db["delivery_id"], "expected_revision": db["revision"], "state": "pending"}
    retry = post(client, "/deliveries", request)
    assert post(client, "/deliveries", request) == retry
    assert inbox(client, b)["messages"][0]["acknowledged_at"] is None
    history = client.get(BASE + "/deliveries/history", params={"delivery_id": db["delivery_id"], "limit": 1}).json()
    assert len(history["receipts"]) == 1 and history["next_offset"] == 1
    assert history["delivery"]["state"] == "pending"


def test_reply_acknowledges_only_its_sender_destination(team):
    client, _, _ = team
    a, b, c = register(client, "a"), register(client, "b"), register(client, "c")
    sent = post(client, "/messages", envelope(b, sender_session_id=a["session_id"], audience={"hosts": ["host-a"]}))
    post(client, "/messages", envelope(a, sender_session_id=b["session_id"], reply_to=sent["message_id"]))
    assert inbox(client, b)["messages"][0]["acknowledged_at"] is not None
    assert inbox(client, c)["messages"][0]["acknowledged_at"] is None
    invalid = envelope(b, sender_session_id=a["session_id"], reply_to=sent["message_id"])
    assert client.post(BASE + "/messages", json=invalid).status_code == 400


def test_failed_publication_rolls_back_snapshot_references_and_counters(team):
    client, _, _ = team
    target = register(client, "a")
    request = envelope(target, references=[{"entry_id": str(uuid4()), "revision": 1}])
    assert client.post(BASE + "/messages", json=request).status_code == 404
    assert Message.objects.count() == Delivery.objects.count() == 0
    assert Session.objects.get(pk=target["session_id"]).last_sequence == 0
    request = envelope(target, audience={"session_ids": [target["session_id"], str(uuid4())]})
    assert client.post(BASE + "/messages", json=request).status_code == 404
    assert Message.objects.count() == 0


def test_concurrent_duplicate_publication_and_receipt_claim(team):
    client, token, _ = team
    target = register(client, "a")
    request = SendMessage.model_validate(envelope(target))
    actor = authenticate(token)
    barrier = Barrier(2)

    def send(_):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return operations.send_message(actor, request)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert results[0] == results[1] and Message.objects.count() == Delivery.objects.count() == 1
    delivery = results[0]["deliveries"][0]

    def claim(_):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            try:
                return operations.record_delivery(actor, Report(receipt_id=uuid4(), delivery_id=delivery["delivery_id"],
                    expected_revision=1, state="uncertain"))
            except AccessError as error:
                assert error.status == 409
                return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert sum(c is not None for c in claimed) == 1 and Receipt.objects.count() == 1


def test_cursor_cannot_skip_an_uncommitted_publication(team):
    client, token, _ = team
    target = register(client, "a")
    actor = authenticate(token)
    held, release = Event(), Event()

    def delayed():
        close_old_connections()
        try:
            with transaction.atomic():
                operations.send_message(actor, SendMessage.model_validate(envelope(target, content="first")))
                held.set()
                assert release.wait(5)
        finally:
            close_old_connections()

    def following():
        close_old_connections()
        try:
            return operations.send_message(actor, SendMessage.model_validate(envelope(target, content="second")))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(delayed)
        assert held.wait(5)
        second = pool.submit(following)
        assert inbox(client, target)["messages"] == []
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    page = inbox(client, target, limit=1)
    assert page["messages"][0]["message"]["content"] == "first" and page["has_more"]
    rest = inbox(client, target, after=page["next_after"])
    assert rest["messages"][0]["message"]["content"] == "second" and not rest["has_more"]


def test_message_and_receipt_evidence_immutable_and_bounds(team):
    client, _, _ = team
    target = register(client, "a")
    sent = post(client, "/messages", envelope(target, content="  Unicode λ\n"))
    assert inbox(client, target)["messages"][0]["message"]["content"] == "  Unicode λ\n"
    report(client, sent["deliveries"][0], "uncertain")
    for mutation in (lambda: Message.objects.update(envelope={}), lambda: Receipt.objects.update(result={})):
        with pytest.raises(IntegrityError), transaction.atomic():
            mutation()
    for changes in ({"content": "x" * 16001}, {"content": "bad\x00"}, {"audience": {}}, {"observed_at": "2026-01-01"}):
        assert client.post(BASE + "/messages", json=envelope(target, **changes)).status_code == 400
    assert client.get(BASE + "/messages", params={"session_id": target["session_id"], "limit": 26}).status_code == 400
    assert client.get(BASE + "/messages", params={"session_id": target["session_id"], "after": 100}).status_code == 400


def test_mcp_contract_and_limited_scopes(team):
    client, _, record = team
    response = client.post("/mcp/", headers={"Accept": "application/json, text/event-stream"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "register_session", "arguments": {
            "request": {"native_id": "mcp", "client": "codex", "host": "other-host", "name": "MCP"}}}})
    value = response.json()["result"]
    assert not value.get("isError"), value
    target = json.loads(value["content"][0]["text"])
    assert client.get(BASE + "/sessions").json()["sessions"][0]["session_id"] == target["session_id"]
    _, read_only = mint_credential(Membership.objects.get(participant_id=record["participant_id"]), {"comms:read"})
    client.headers["Authorization"] = f"Bearer {read_only}"
    assert inbox(client, target)["messages"] == []
    assert client.post(BASE + "/messages", json=envelope(target)).status_code == 403
    assert client.post(BASE + "/sessions", json={"native_id": "a", "client": "a", "host": "a", "name": "a"}).status_code == 403
