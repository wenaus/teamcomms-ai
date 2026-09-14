"""Real HTTP proxy streaming against a host-authenticated mounted service."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import socket
import threading
import time
from uuid import uuid4

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse
import uvicorn

from teamcomms.service.access import AccessError, MEMBER_SCOPES
from teamcomms.service.asgi import create_app
from teamcomms.service.embedded import HostAuthentication, HostIdentity
from teamcomms.service.models import Credential, Team

pytestmark = pytest.mark.django_db(transaction=True)
PREFIX = "/ops/teamcomms"


@contextmanager
def serving(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on", proxy_headers=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Test HTTP server did not start")
            time.sleep(.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive(), "Test HTTP server did not stop"


class Proxy:
    """Fixture passes methods, credentials, paths, cursors and chunks unchanged."""

    def __init__(self, upstream):
        self.upstream = upstream

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                await send({"type": event["type"] + ".complete"})
                if event["type"] == "lifespan.shutdown":
                    return
        request = Request(scope, receive)
        # The stream's idle recheck is five seconds; the proxy must allow it.
        async with httpx.AsyncClient(timeout=8) as client:
            headers = [(k, v) for k, v in scope["headers"] if k not in {b"host", b"connection"}]
            async with client.stream(scope["method"], self.upstream + scope["path"],
                    params=request.query_params, headers=headers, content=await request.body()) as response:
                forwarded = {k: v for k, v in response.headers.items()
                             if k not in {"transfer-encoding", "connection", "content-length"}}
                await StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                        headers=forwarded)(scope, receive, send)


async def events(response):
    response.raise_for_status()
    event = {}
    async for line in response.aiter_lines():
        if not line:
            if event:
                yield event
            event = {}
        elif line.startswith("event: "):
            event["event"] = line[7:]
        elif line.startswith("data: "):
            event["data"] = json.loads(line[6:])
        elif line.startswith("id: "):
            event["id"] = int(line[4:])


@pytest.mark.parametrize("loss", ["revoked", "scope", "identity", "unavailable"])
def test_proxy_delivery_replay_and_host_revalidation(loss):
    team = Team.objects.create(name="Embedded streaming team")
    identity = HostIdentity("existing-account", "Host operator", scopes=MEMBER_SCOPES,
                            session_authenticated=False)
    state = {"identity": identity}

    async def resolve(scope):
        if dict(scope["headers"]).get(b"authorization") != b"Bearer existing-host-token":
            raise AccessError("Host token required", 401)
        if state.get("revoked"):
            raise AccessError("Host token revoked", 401)
        if state.get("unavailable"):
            raise ConnectionError("Synthetic host account service outage")
        return state["identity"]

    async def revalidate(scope, original):
        return await resolve(scope)

    app = create_app(mount_path=PREFIX, host_auth=HostAuthentication(
        provider="stream-host", team_id=team.id, resolve=resolve, revalidate=revalidate))

    async def check(url):
        async with httpx.AsyncClient(base_url=url + PREFIX, timeout=8,
                headers={"Authorization": "Bearer existing-host-token"}) as client:
            async def post(path, body):
                response = await client.post(path, json=body)
                response.raise_for_status()
                return response.json()

            session = await post("/api/comms/sessions", {"native_id": "embedded-native", "host": "client-host",
                "client": "test", "name": "Embedded receiver"})
            query = {"session_id": session["session_id"], "duration": 10}

            async def publish(content):
                return await post("/api/comms/messages", {"message_id": str(uuid4()), "content": content,
                    "audience": {"session_ids": [session["session_id"]]}})

            first = await publish("Stored before receiver connects")
            async with client.stream("GET", "/api/comms/stream", params=query) as response:
                stream = events(response)
                assert (await anext(stream))["event"] == "ready"
                incoming = await anext(stream)
                assert incoming["data"]["message_id"] == first["message_id"]
                started = time.monotonic()
                next_message = await publish("Live through the proxy")
                async for incoming in stream:
                    if incoming["event"] == "message":
                        break
                assert incoming["data"]["message_id"] == next_message["message_id"]
                assert time.monotonic() - started < 4
                cursor = incoming["id"]
                await stream.aclose()

            saved = await publish("Stored during disconnection")
            async with client.stream("GET", "/api/comms/stream", params=query,
                                      headers={"Last-Event-ID": str(cursor)}) as response:
                stream = events(response)
                assert (await anext(stream))["event"] == "ready"
                replay = await anext(stream)
                assert replay["data"]["message_id"] == saved["message_id"]
                if loss == "scope":
                    state["identity"] = replace(identity, scopes=frozenset())
                elif loss == "identity":
                    state["identity"] = replace(identity, subject="different-account")
                else:
                    state[loss] = True
                started = time.monotonic()
                async for incoming in stream:
                    if incoming["event"] == "error":
                        break
                assert incoming["event"] == "error"
                assert incoming["data"]["status"] == {"revoked": 401, "scope": 403, "identity": 401, "unavailable": 503}[loss]
                assert time.monotonic() - started < 7
                await stream.aclose()

    with serving(app) as upstream, serving(Proxy(upstream)) as external:
        asyncio.run(check(external))
    assert Credential.objects.count() == 0
