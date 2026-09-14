"""Receiver publication, replay, and receipts against the real service database."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from teamcomms.connectors.client import ServiceClient
from teamcomms.connectors.config import Configuration
from teamcomms.connectors.receiver import Receiver
from teamcomms.connectors.state import Store
from teamcomms.service.asgi import create_app
from teamcomms.service.operations import bootstrap

pytestmark = pytest.mark.django_db(transaction=True)


def test_receiver_service_roundtrip_greeting_and_restart(tmp_path):
    _, secret = bootstrap("Connector team", "Operator")
    token = tmp_path / "token"
    token.write_text(secret)
    token.chmod(0o600)
    config = Configuration(url="http://localhost", token_file=token, state_dir=tmp_path / "state", host="receiver-host")

    class Adapter:
        calls = []

        async def metadata(self):
            return {"state": "idle", "name": "Receiver", "model": "fixture-model"}

        async def send(self, text, author, message_id):
            self.calls.append(message_id)
            return {"state": "written", "detail": "Synthetic native socket"}

    async def check():
        service = ServiceClient(config)
        await service.http.aclose()
        service.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()))
        registration = {"native_id": str(uuid4()), "host": "receiver-host", "client": "claude", "name": "Receiver"}
        peer = await service.post("/sessions", {"native_id": str(uuid4()), "host": "sender-host", "client": "codex", "name": "Peer"})
        store = Store(tmp_path / "receiver")
        adapter = Adapter()
        receiver = Receiver(service, store, adapter, registration, tmp_path / "config.json")
        enrolled = await receiver.register()
        await receiver.once()
        greeting = await service.get("/messages", session_id=peer["session_id"])
        assert len(greeting["messages"]) == 1
        assert "AI Hi" in greeting["messages"][0]["message"]["content"]
        body = {"message_id": str(uuid4()), "sender_session_id": peer["session_id"],
                "audience": {"session_ids": [enrolled["session_id"]]}, "content": "Work update"}
        sent = await service.post("/messages", body)
        await receiver.once()
        current = await service.get("/messages", session_id=enrolled["session_id"])
        assert current["messages"][0]["state"] == "written"
        assert current["messages"][0]["acknowledged_at"] is None
        store.close()
        store = Store(tmp_path / "receiver")
        restarted = Receiver(service, store, adapter, registration, tmp_path / "config.json")
        await restarted.register()
        await restarted.once()
        assert adapter.calls == [sent["message_id"]]
        assert len((await service.get("/messages", session_id=peer["session_id"]))["messages"]) == 1
        store.close()
        await service.close()

    asyncio.run(check())
