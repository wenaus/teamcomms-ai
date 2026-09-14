"""Opt-in installed Codex protocol check without starting a model turn."""

import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import time

from teamcomms.connectors.codex_client import CodexClient


async def inspect(path, directory):
    async with CodexClient(path) as client:
        # Read effective configuration without printing credentials; disable every
        # configured MCP server for this synthetic thread. Hooks are disabled on
        # the disposable server as well, so enrollment hooks cannot join a team.
        config = (await client.call("config/read", {}))["config"]
        overrides = {f"mcp_servers.{name}.enabled": False for name in config.get("mcp_servers", {})}
        thread = await client.call("thread/start", {"cwd": str(directory), "ephemeral": True, "config": overrides})
        identity = thread["thread"]["id"]
        assert identity in await client.loaded_threads()
        await client.call("thread/inject_items", {"threadId": identity, "items": [{
            "type": "message", "role": "user", "content": [{"type": "input_text",
                "text": "TeamComms protocol fixture; no model turn is requested."}]}]})
        snapshot = (await client.call("thread/read", {"threadId": identity, "includeTurns": False}))["thread"]
        assert snapshot["status"]["type"] == "idle"
        print(json.dumps({"loaded": True, "context_injection": "accepted", "state": snapshot["status"]["type"]}))


def main():
    version = subprocess.check_output(["codex", "--version"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="tc-codex-check-") as name:
        directory = Path(name)
        path = directory / "runtime.sock"
        with (directory / "server.log").open("w") as log:
            server = subprocess.Popen(["codex", "--disable", "hooks", "app-server", "--listen", "unix://" + str(path)],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while not path.exists():
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError((directory / "server.log").read_text())
                    time.sleep(0.05)
                asyncio.run(inspect(path, directory))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    print(version + ": installed Unix-socket registration/context protocol passed; no model invocation.")


if __name__ == "__main__":
    main()
