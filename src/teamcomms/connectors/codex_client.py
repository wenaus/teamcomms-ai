"""Local Codex transport connected to an explicitly selected owning runtime."""

import asyncio
import json

from .presentation import envelope
import os
from pathlib import Path
import stat

from websockets.asyncio.client import unix_connect


class RPCError(RuntimeError):
    def __init__(self, method, error):
        super().__init__(f"{method}: {error}")
        self.error = error


class CodexClient:
    """One connection to the owning app-server, with explicit request timeouts."""

    def __init__(self, socket_path, timeout=15):
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._counter = 0
        self._pending = {}

    async def __aenter__(self):
        info = self.socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Select an app-server socket owned by the current user")
        if info.st_mode & 0o077:
            raise ValueError("The app-server socket must be private to its owner")
        self.ws = await unix_connect(
            str(self.socket_path), uri="ws://localhost/", compression=None,
            proxy=None, open_timeout=self.timeout, max_size=4 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read())
        try:
            self.server = await self.call("initialize", {
                "clientInfo": {"name": "teamcomms_connector", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            })
            await self.ws.send(json.dumps({"method": "initialized"}))
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_):
        await self.ws.close()
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)

    async def _read(self):
        failure = ConnectionError("App-server reader stopped")
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if "method" in message:
                    # The owning TUI handles server requests, including approvals.
                    # A transport companion does not queue token events or answer them.
                    continue
                else:
                    future = self._pending.get(message.get("id"))
                    if future is not None and not future.done():
                        future.set_result(message)
        except Exception as exc:
            failure = exc
        else:
            failure = ConnectionError("App-server connection closed")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(failure)

    async def call(self, method, params):
        if self._reader.done():
            raise ConnectionError("App-server reader is no longer running")
        self._counter += 1
        request_id = self._counter
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
            reply = await asyncio.wait_for(future, timeout=self.timeout)
            if "error" in reply:
                raise RPCError(method, reply["error"])
            return reply["result"]
        finally:
            self._pending.pop(request_id, None)

    async def loaded_threads(self):
        ids = []
        cursor = None
        while True:
            page = await self.call("thread/loaded/list", {"cursor": cursor})
            ids.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                return ids

    async def send(self, thread_id, text, sender, message_id, expected_turn_id=None):
        if not text.strip() or not sender.strip() or not message_id.strip():
            raise ValueError("Message, sender and message ID must be nonempty")
        # A stored thread is not proof this server owns the live session.
        if thread_id not in await self.loaded_threads():
            raise ValueError("Target is not loaded in this app-server; no session was resumed")
        thread = (await self.call("thread/read", {"threadId": thread_id, "includeTurns": False}))["thread"]
        if thread.get("canAcceptDirectInput") is False:
            raise ValueError("Target does not accept direct input")
        if thread.get("threadSource") == "system":
            raise ValueError("Internal Codex housekeeping threads are not peer sessions")
        status = thread["status"]["type"]
        content = envelope(text, message_id)
        if len(content.encode()) > 65536:
            raise ValueError("Peer messages are limited to 64 KiB")
        params = {"threadId": thread_id, "clientUserMessageId": message_id,
                  "input": [{"type": "text", "text": content}]}
        if expected_turn_id is None and status == "active":
            page = await self.call("thread/turns/list", {
                "threadId": thread_id, "limit": 1, "itemsView": "notLoaded",
                "sortDirection": "desc",
            })
            active = [turn for turn in page["data"] if turn["status"] == "inProgress"]
            if not active:
                raise ValueError("Active turn changed before delivery; message was not sent")
            expected_turn_id = active[0]["id"]
        if expected_turn_id is not None:
            # Let the server enforce the active-turn precondition, even if it
            # changed after thread/read. Never silently convert stale steering
            # into a new turn or retry an ambiguous transport failure.
            method = "turn/steer"
            params["expectedTurnId"] = expected_turn_id
        elif status == "idle":
            method = "turn/start"
        else:
            raise ValueError(f"Target is {status}; active delivery requires its expected turn ID")
        result = await self.call(method, params)
        return {"message_id": message_id, "thread_id": thread_id,
                "state": "accepted_by_client", "method": method,
                "turn_id": result.get("turnId") or result.get("turn", {}).get("id")}
