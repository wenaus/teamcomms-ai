"""Native protocol fixtures and launcher/configuration preservation."""

import asyncio
import json
import os
import socket
import threading
from uuid import uuid4

import pytest
from websockets.asyncio.server import unix_serve

from teamcomms.connectors.claude_client import send as claude_send
from teamcomms.connectors.codex_client import CodexClient
from teamcomms.connectors.config import Configuration
from teamcomms.connectors.launcher import claude_settings, launch_options


def test_claude_socket_frame_preserves_identity_and_auth(tmp_path):
    path = tmp_path / "claude.sock"
    frames = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen()

        def accept():
            connection, _ = server.accept()
            with connection, connection.makefile("r") as stream:
                frames.extend(json.loads(line) for line in stream)

        thread = threading.Thread(target=accept)
        thread.start()
        session, message_id = str(uuid4()), str(uuid4())
        result = claude_send(path, session, "Hello λ", "sender", message_id, "synthetic-token")
        thread.join(3)
    assert not thread.is_alive()
    assert result["state"] == "written_to_transport"
    assert frames[0] == {"type": "auth", "token": "synthetic-token"}
    assert frames[1]["session_id"] == session and frames[1]["uuid"] == message_id
    assert frames[1]["message"]["content"] == f"[TeamComms peer {message_id}]\nHello λ"


@pytest.mark.parametrize("status,expected", [("idle", "turn/start"), ("active", "turn/steer")])
def test_codex_idle_active_and_approval_requests_remain_with_tui(tmp_path, status, expected):
    async def check():
        calls = []
        thread_id = str(uuid4())

        async def server(ws):
            async for raw in ws:
                request = json.loads(raw)
                calls.append(request)
                method = request["method"]
                if "id" not in request:
                    continue
                values = {"initialize": {}, "thread/loaded/list": {"data": [thread_id], "nextCursor": None},
                    "thread/read": {"thread": {"status": {"type": status}, "threadSource": "user"}},
                    "thread/turns/list": {"data": [{"id": "active-turn", "status": "inProgress"}]},
                    "turn/start": {"turn": {"id": "new-turn"}}, "turn/steer": {"turnId": "active-turn"}}
                if method == "initialize":
                    await ws.send(json.dumps({"method": "item/commandExecution/requestApproval", "id": 999, "params": {}}))
                await ws.send(json.dumps({"id": request["id"], "result": values[method]}))

        path = tmp_path / "codex.sock"
        async with unix_serve(server, path=str(path)):
            os.chmod(path, 0o600)
            async with CodexClient(path) as client:
                response = await client.send(thread_id, "Hello", "sender", str(uuid4()))
                assert response["method"] == expected
                with pytest.raises(ValueError, match="not loaded"):
                    await client.send(str(uuid4()), "Other", "sender", str(uuid4()))
        assert not any(r.get("id") == 999 for r in calls)
        sent = [r for r in calls if r["method"] == expected][0]
        if status == "active":
            assert sent["params"]["expectedTurnId"] == "active-turn"
        assert not any(r["method"] == "thread/resume" for r in calls)

    asyncio.run(check())


def test_launcher_preserves_settings_and_passthrough(tmp_path):
    args = ["-C", str(tmp_path), "-c", 'model="chosen"', "--sandbox", "read-only", "resume", "--last"]
    assert launch_options(args) == (str(tmp_path), ["-c", 'model="chosen"'])
    assert launch_options(["exec", "task"]) is None
    assert launch_options(["--remote", "unix:///existing.sock"]) is None
    settings = {"permissions": {"deny": ["Bash(rm *)"]}, "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "existing"}]}]}}
    result = claude_settings(["--settings", json.dumps(settings), "--model", "chosen"], "new-hook")
    merged = json.loads(result[1])
    assert merged["permissions"] == settings["permissions"]
    assert merged["hooks"]["SessionStart"][0] == settings["hooks"]["SessionStart"][0]
    assert merged["hooks"]["SessionStart"][1]["hooks"][0]["command"] == "new-hook"
    assert result[-2:] == ["--model", "chosen"]


def test_configuration_requires_explicit_private_credentials(tmp_path):
    token = tmp_path / "token"
    token.write_text("synthetic-token")
    token.chmod(0o600)
    config = Configuration(url="https://comms.example", token_file=token)
    assert config.token() == "synthetic-token"
    token.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        config.token()
    with pytest.raises(ValueError):
        Configuration(url="http://external.example", token_file=token)
    with pytest.raises(ValueError):
        Configuration(url="https://user:secret@example", token_file=token)
