"""SessionStart enrollment and receiver process creation."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from .cli import state_path
from .client import ServiceClient
from .presentation import session_instructions
from .state import Store, private_directory


def spawn(config, config_path, *, client, native_id, owner_pid, socket_path, name, cwd, model="", transcript=""):
    directory = private_directory(state_path(config, client, native_id))
    command = [sys.executable, "-m", "teamcomms.connectors.cli", "--config", str(Path(config_path).resolve()),
        "receive", "--client", client, "--native-id", native_id, "--socket", socket_path,
        "--pid", str(owner_pid), "--name", name, "--cwd", cwd, "--model", model, "--transcript", transcript]
    with (directory / "receiver.log").open("a") as log:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


def native_owner():
    pid = os.getppid()
    for _ in range(8):
        row = subprocess.check_output(["ps", "-p", str(pid), "-o", "ppid=", "-o", "comm="], text=True)
        parent, command = row.strip().split(None, 1)
        if Path(command).name == "claude":
            return pid
        pid = int(parent)
        if pid <= 1:
            break
    raise RuntimeError("Claude SessionStart could not identify its native process")


async def register(config, native_id, name, cwd, model):
    service = ServiceClient(config)
    try:
        result = await service.post("/sessions", {"native_id": native_id, "host": config.host, "client": "claude",
            "name": name, "workspace": cwd, "model": model, "delivery_mode": "claude_socket",
            "resource_ids": [str(r) for r in config.resource_ids]})
        from .dialog import bootstrap_context
        result["bootstrap_context"] = await bootstrap_context(service, config)
        return result
    finally:
        await service.close()


def hook(config, config_path, data):
    native_id = str(UUID(data["session_id"]))
    socket_path = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    if not socket_path:
        raise RuntimeError("Claude did not export a messaging socket; this client cannot enroll through the socket connector")
    owner = native_owner()
    name = data.get("session_title") or data.get("session_name") or f"{config.host}-claude-{native_id[-8:]}"
    model = data.get("model") or ""
    if isinstance(model, dict):
        model = model.get("id") or model.get("display_name") or ""
    cwd = data.get("cwd") or os.getcwd()
    spawn(config, config_path, client="claude", native_id=native_id, owner_pid=owner, socket_path=socket_path,
          name=name, cwd=cwd, model=model, transcript=data.get("transcript_path") or "")
    try:
        result = asyncio.run(asyncio.wait_for(register(config, native_id, name, cwd, model), timeout=3))
    except Exception as error:
        return f"TeamComms enrollment is retrying in the receiver ({type(error).__name__}); inspect teamcomms-connect status."
    store = Store(state_path(config, "claude", native_id))
    try:
        store.put("instructions", True)
    finally:
        store.close()
    return session_instructions(result["session_id"], config_path) + "\n\n" + result.get("bootstrap_context", "")
