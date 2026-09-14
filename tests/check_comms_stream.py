"""Verify cross-process SSE wake-up, replay, retry visibility, and revocation."""

import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx


async def events(response):
    response.raise_for_status()
    item = {}
    async for line in response.aiter_lines():
        if not line:
            if item:
                yield item
            item = {}
        elif line.startswith("data: "):
            item["data"] = json.loads(line[6:])
        elif line.startswith("event: "):
            item["event"] = line[7:]
        elif line.startswith("id: "):
            item["id"] = int(line[4:])


async def check(reader_url, writer_url, token):
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=12) as reader, httpx.AsyncClient(headers=headers, timeout=12) as writer:
        async def post(path, data):
            response = await writer.post(writer_url + "/api" + path, json=data)
            response.raise_for_status()
            return response.json()

        session = await post("/comms/sessions", {"native_id": "stream-check", "client": "test", "host": "receiver", "name": "Receiver"})
        session_id = session["session_id"]

        async def publish(content):
            return await post("/comms/messages", {"message_id": str(uuid4()), "audience": {"session_ids": [session_id]}, "content": content})

        # Writer commits before any listener exists: recovery must use persisted data.
        saved = await publish("Before connection")
        async with reader.stream("GET", reader_url + "/api/comms/stream", params={"session_id": session_id}) as response:
            source = events(response)
            assert (await anext(source))["event"] == "ready"
            recovered = await anext(source)
            assert recovered["data"]["message_id"] == saved["message_id"]
            cursor = recovered["id"]
            # The publication goes through a separate ASGI process; no local Event can wake this reader.
            start = time.monotonic()
            fresh = await publish("Cross-process notification")
            while True:
                incoming = await anext(source)
                if incoming["event"] == "message":
                    break
            assert incoming["data"]["message_id"] == fresh["message_id"]
            assert time.monotonic() - start < 4
            cursor = incoming["id"]
            await source.aclose()

        # Last-Event-ID resumes exactly after the last locally persisted message.
        later = await publish("After disconnect")
        async with reader.stream("GET", reader_url + "/api/comms/stream",
                headers={"Last-Event-ID": str(cursor)}, params={"session_id": session_id}) as response:
            source = events(response)
            assert (await anext(source))["event"] == "ready"
            replayed = await anext(source)
            assert replayed["data"]["message_id"] == later["message_id"]
            cursor = replayed["id"]
            await source.aclose()

        # Revocation ends an already admitted idle stream within the recheck interval.
        who = await writer.get(writer_url + "/api/whoami")
        who.raise_for_status()
        credential = await post("/credentials", {"participant_id": who.json()["participant_id"], "scopes": ["comms:read"]})
        async with reader.stream("GET", reader_url + "/api/comms/stream",
                headers={"Authorization": f"Bearer {credential['token']}"},
                params={"session_id": session_id, "after": cursor}) as response:
            source = events(response)
            assert (await anext(source))["event"] == "ready"
            await post("/credentials/revoke", {"credential_id": credential["credential_id"]})
            async for incoming in source:
                if incoming["event"] == "error":
                    assert incoming["data"]["status"] == 401
                    break
            else:
                raise AssertionError("Revoked stream did not terminate with an error")
            await source.aclose()


def main(reader_url, token_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryFile(mode="w+") as log:
        server = subprocess.Popen([str(Path(sys.executable).parent / "teamcomms"), "serve", "--port", str(port)],
                                  stdout=log, stderr=subprocess.STDOUT)
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    response = httpx.get(url + "/health", timeout=0.5)
                    response.raise_for_status()
                    break
                except httpx.ConnectError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Second service startup timed out")
            asyncio.run(check(reader_url, url, Path(token_path).read_text().strip()))
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    print("Comms cross-process wake-up, durable reconnect replay, and active-stream revocation passed.")


if __name__ == "__main__":
    main(*sys.argv[1:])
