"""Receiver recovery boundaries using a synthetic versioned mailbox."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from teamcomms.connectors.client import ServiceError
from teamcomms.connectors.receiver import Receiver
from teamcomms.connectors.state import Store


def delivery():
    return {"delivery_id": str(uuid4()), "message_id": str(uuid4()), "sequence": 1,
        "state": "pending", "revision": 1, "acknowledged_at": None,
        "message": {"author_id": str(uuid4()), "author": {"name": "Other AI", "kind": "ai", "host": "other-host"},
                    "content": "Coordination update", "reply_requested": False}}


class Mailbox:
    def __init__(self, row):
        self.row = dict(row)
        self.receipts = {}
        self.calls = []
        self.lose = None

    async def post(self, path, body):
        assert path == "/deliveries"
        self.calls.append(dict(body))
        if body["receipt_id"] in self.receipts:
            return dict(self.receipts[body["receipt_id"]])
        if body["expected_revision"] != self.row["revision"]:
            raise ServiceError("Stale", 409)
        self.row.update(state=body["state"], revision=self.row["revision"] + 1)
        self.receipts[body["receipt_id"]] = dict(self.row)
        if self.lose == body["state"]:
            self.lose = None
            raise ServiceError("Response lost after commit")
        return dict(self.row)

    async def get(self, path, **query):
        return {"delivery": self.row}


class Adapter:
    def __init__(self, failure=False):
        self.sent = []
        self.failure = failure

    async def send(self, text, author, message_id):
        self.sent.append((text, message_id))
        if self.failure:
            raise ConnectionError("Connection lost after possible acceptance")
        return {"state": "accepted"}


def receiver(store, service, adapter):
    result = Receiver(service, store, adapter, {}, "/private/connector.json")
    result.session_id = str(uuid4())
    return result


def test_cursor_ingestion_and_dispatch_are_independent(tmp_path):
    row = delivery()
    store = Store(tmp_path / "state")
    store.ingest(row)
    store.close()
    store = Store(tmp_path / "state")
    assert store.get("cursor") == 1 and store.pending()[0]["phase"] == "new"
    adapter = Adapter()
    asyncio.run(receiver(store, Mailbox(row), adapter).dispatch_pending())
    store.ingest(row)
    asyncio.run(receiver(store, Mailbox(row), adapter).dispatch_pending())
    assert len(adapter.sent) == 1
    assert store.pending() == []
    assert store.get("instructions")
    store.close()


@pytest.mark.parametrize("lost_state", ["uncertain", "accepted"])
def test_lost_service_receipt_retries_same_id_without_duplicate_injection(tmp_path, lost_state):
    row = delivery()
    store = Store(tmp_path / "state")
    store.ingest(row)
    service, adapter = Mailbox(row), Adapter()
    service.lose = lost_state
    first = receiver(store, service, adapter)
    with pytest.raises(ServiceError):
        asyncio.run(first.dispatch_pending())
    store.close()
    store = Store(tmp_path / "state")
    asyncio.run(receiver(store, service, adapter).dispatch_pending())
    assert len(adapter.sent) == 1 and store.pending() == []
    ids = [r["receipt_id"] for r in service.calls if r["state"] == lost_state]
    assert len(set(ids)) == 1
    store.close()


def test_crash_during_injection_and_ambiguous_native_failure_are_not_retried(tmp_path):
    for mode in ("crash", "disconnect"):
        row = delivery()
        store = Store(tmp_path / mode)
        store.ingest(row)
        service, adapter = Mailbox(row), Adapter(failure=True)
        if mode == "crash":
            store.update(row["delivery_id"], "injecting", claim={"receipt_id": str(uuid4())})
        worker = receiver(store, service, adapter)
        asyncio.run(worker.dispatch_pending())
        attempts = len(adapter.sent)
        asyncio.run(worker.dispatch_pending())
        assert len(adapter.sent) == attempts
        assert store.pending()[0]["phase"] == "uncertain"
        assert store.pending()[0]["error"]
        service.row["acknowledged_at"] = "considered after ambiguous write"
        asyncio.run(worker.dispatch_pending())
        assert store.pending() == [] and len(adapter.sent) == attempts
        store.close()


def test_fast_acknowledgment_does_not_lose_transport_receipt(tmp_path):
    row = delivery()
    store = Store(tmp_path / "state")
    store.ingest(row)
    service = Mailbox(row)

    class FastAdapter(Adapter):
        async def send(self, *args):
            result = await super().send(*args)
            service.row["revision"] += 1
            service.row["acknowledged_at"] = "observed"
            return result

    adapter = FastAdapter()
    asyncio.run(receiver(store, service, adapter).dispatch_pending())
    assert store.pending() == []
    assert service.row["state"] == "accepted" and service.row["acknowledged_at"] == "observed"
    assert len(adapter.sent) == 1
    store.close()


def test_explicit_server_retry_reopens_local_uncertainty(tmp_path):
    row = delivery()
    store = Store(tmp_path / "state")
    store.ingest(row)
    store.update(row["delivery_id"], "uncertain", error="No native confirmation")
    store.ingest({**row, "state": "uncertain", "revision": 2}, advance=False)
    assert store.pending()[0]["phase"] == "uncertain"
    store.ingest({**row, "state": "pending", "revision": 4}, advance=False)
    assert store.pending()[0]["phase"] == "new" and store.get("cursor") == 1
    store.close()


def test_durable_outbox_rejects_changed_retry(tmp_path):
    store = Store(tmp_path / "state")
    body = {"message_id": str(uuid4()), "content": "Update"}
    store.enqueue(body)
    store.close()
    store = Store(tmp_path / "state")
    assert store.outgoing() == [body]
    with pytest.raises(ValueError):
        store.enqueue({**body, "content": "different"})
    store.sent(body["message_id"])
    assert store.outgoing() == []
    store.close()


def test_startup_waits_for_registry_and_retries_incomplete_enrollment(tmp_path):
    from teamcomms.connectors.receiver import TargetNotReady

    class StartingAdapter(Adapter):
        attempts = 0

        async def metadata(self):
            self.attempts += 1
            if self.attempts == 1:
                raise TargetNotReady("Registry has not appeared")
            return {"state": "idle"}

    class StartingService:
        config = SimpleNamespace(group_ids=[str(uuid4())], topics=[], greeting=False)
        session_id = str(uuid4())
        subscriptions = 0

        async def post(self, path, body):
            if path == "/sessions":
                return {"session_id": self.session_id}
            if path == "/subscriptions":
                self.subscriptions += 1
                if self.subscriptions == 1:
                    raise ServiceError("Subscription response lost")
            return {}

    async def check():
        store = Store(tmp_path / "startup")
        service, adapter = StartingService(), StartingAdapter()
        worker = Receiver(service, store, adapter, {}, "/private/connector.json")
        stop = asyncio.Event()

        async def pause(*args):
            return

        async def once():
            assert worker.registered and service.subscriptions == 2
            stop.set()

        async def stream(*args):
            if False:
                yield {}

        worker.pause = pause
        worker.once = once
        service.stream = stream
        await worker.run(stop)
        assert adapter.attempts >= 3
        store.close()

    asyncio.run(check())
