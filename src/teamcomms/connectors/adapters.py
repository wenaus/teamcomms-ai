"""Version-specific native transports behind a common receiver interface."""

import asyncio
import json
import os
from pathlib import Path

from .receiver import TargetGone, TargetNotReady


def transcript_model(path):
    if not path:
        return ""
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return ""  # A new session may not have written its first transcript item.
    with handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - 262144))
        lines = handle.read().splitlines()
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue  # A tail read may start in the middle of a JSON line.
        if record.get("type") == "assistant":
            value = (record.get("message") or {}).get("model")
            if isinstance(value, str) and value and not value.startswith("<"):
                return value
    return ""


class NativeAdapter:
    def __init__(self, client, native_id, socket_path, owner_pid, *, name, model="", transcript="", registry=None):
        self.client, self.native_id, self.socket = client, native_id, socket_path
        self.pid, self.name, self.model, self.transcript = owner_pid, name, model, transcript
        self.registry = Path(registry) if registry else Path.home() / ".claude/sessions"

    async def metadata(self):
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            raise TargetGone("Native owner exited") from None
        if self.client == "codex_queue":
            return {"state": "unknown", "name": self.name, "model": self.model}
        if self.client == "codex":
            from .codex_client import CodexClient
            async with CodexClient(self.socket) as client:
                if self.native_id not in await client.loaded_threads():
                    raise TargetGone("Native thread is not loaded in the selected runtime")
                thread = (await client.call("thread/read", {"threadId": self.native_id, "includeTurns": False}))["thread"]
                if thread.get("threadSource") == "system":
                    raise TargetGone("Internal threads cannot register as interactive sessions")
                return {"name": thread.get("name") or self.name, "model": thread.get("model") or self.model,
                        "state": "active" if thread["status"]["type"] == "active" else "idle"}
        for path in self.registry.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                continue  # Registry files can disappear or be replaced during enumeration.
            if record.get("sessionId") == self.native_id and record.get("messagingSocketPath") == self.socket:
                if record.get("pid") != self.pid:
                    raise TargetGone("Claude registry owner differs from the selected process")
                self.model = transcript_model(self.transcript) or self.model
                return {"name": record.get("name") or self.name, "model": self.model,
                        "state": "active" if record.get("status") in {"busy", "active", "working"} else "idle"}
        raise TargetNotReady("Claude session is absent from the live registry")

    async def setup(self, text):
        if self.client != "codex":
            return False
        from .codex_client import CodexClient
        async with CodexClient(self.socket) as client:
            if self.native_id not in await client.loaded_threads():
                raise TargetGone("Native thread unloaded before context setup")
            await client.call("thread/inject_items", {"threadId": self.native_id, "items": [{
                "type": "message", "role": "user", "content": [{"type": "input_text",
                "text": "TeamComms connector session context:\n" + text}]}]})
        return True

    async def send(self, text, sender, message_id):
        # Check the native owner again immediately before dispatch.
        await self.metadata()
        if self.client == "codex":
            from .codex_client import CodexClient
            async with CodexClient(self.socket) as client:
                result = await client.send(self.native_id, text, sender, message_id)
            return {"state": "accepted", "detail": f"Codex {result['method']} accepted; model consideration unconfirmed"}
        if self.client == "codex_queue":
            from .codex_queue import send
            await asyncio.to_thread(send, self.native_id, text, sender, message_id)
            return {"state": "accepted", "detail": "Deferred Codex queue accepted; consumption waits for a native input boundary"}
        from .claude_client import send
        await asyncio.to_thread(send, self.socket, self.native_id, text, sender, message_id,
                                os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN"))
        return {"state": "written", "detail": "Claude socket write completed; client acceptance and model consideration unconfirmed"}
