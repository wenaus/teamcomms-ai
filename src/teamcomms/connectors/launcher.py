"""Opt-in native launchers and an owning Codex runtime supervisor."""

import asyncio
import json
import logging
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from websockets.exceptions import WebSocketException

from .codex_client import CodexClient
from .startup import spawn

logger = logging.getLogger(__name__)

DISCONNECT_GRACE = 30
STOP_GRACE = 5
POLL_INTERVAL = 2


async def stop_process_groups(group_ids, children=()):
    """Stop only process groups created with start_new_session by this wrapper."""
    remaining = set(group_ids)
    if any(pid <= 1 or pid == os.getpgrp() for pid in remaining):
        raise ValueError("Refusing to stop an unowned process group")
    for pid in list(remaining):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            remaining.remove(pid)
    deadline = time.monotonic() + STOP_GRACE
    while remaining and time.monotonic() < deadline:
        for child in children:
            child.poll()  # Reap receiver children before probing their groups.
        for pid in list(remaining):
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                remaining.remove(pid)
        if remaining:
            await asyncio.sleep(.1)
    for pid in remaining:
        logger.warning("Process group %s did not stop after SIGTERM; sending SIGKILL", pid)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    for child in children:
        try:
            await asyncio.to_thread(child.wait, timeout=1)
        except subprocess.TimeoutExpired:
            logger.error("Owned process %s did not exit after SIGKILL", child.pid)


def disconnected(directory, runtime):
    if (directory / "disconnected").exists():
        return True
    try:
        os.kill(runtime["launcher_pid"], 0)
    except ProcessLookupError:
        return True
    return False


VALUE_OPTIONS = {"-c", "--config", "-C", "--cd", "-m", "--model", "-p", "--profile",
                 "-s", "--sandbox", "-a", "--ask-for-approval", "--enable", "--disable",
                 "--add-dir", "--image", "-i", "--local-provider", "--remote-auth-token-env"}
COMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
            "remote-control", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
            "queue", "archive", "delete", "migrate-rollouts", "unarchive", "cloud", "exec-server",
            "features", "help"}


def launch_options(arguments):
    """Inspect routing/cwd only; forward the original argument list verbatim."""
    cwd = os.getcwd()
    server_config = []
    i = 0
    while i < len(arguments):
        arg = arguments[i]
        if arg in {"-h", "--help", "-V", "--version", "--remote"} or arg.startswith("--remote="):
            return None
        if arg in VALUE_OPTIONS:
            if i + 1 >= len(arguments):
                return None  # Native CLI reports the malformed argument.
            value = arguments[i + 1]
            if arg in {"-C", "--cd"}:
                cwd = os.path.abspath(os.path.expanduser(value))
            if arg in {"-c", "--config", "--enable", "--disable", "-p", "--profile"}:
                server_config.extend([arg, value])
            i += 2
            continue
        if arg.startswith("--cd="):
            cwd = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1]))
        elif arg.startswith(("--config=", "--enable=", "--disable=", "--profile=")):
            server_config.append(arg)
        elif not arg.startswith("-"):
            if arg in COMMANDS:
                return None
            break  # Interactive prompt, resume or fork: other arguments stay native.
        i += 1
    return cwd, server_config



async def supervise(directory, config, config_path):
    runtime = json.loads((directory / "runtime.json").read_text())
    watched = {}
    restarts = {}
    disconnected_at = None
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, task.cancel)
    try:
        while True:
            if disconnected_at is None and disconnected(directory, runtime):
                disconnected_at = time.monotonic()
                # Stop delivery as soon as the owning TUI disappears. Keep
                # native work alive only for its bounded completion grace.
                await stop_process_groups([p.pid for p in watched.values()], watched.values())
                watched.clear()
            remaining = (DISCONNECT_GRACE - (time.monotonic() - disconnected_at)
                         if disconnected_at is not None else DISCONNECT_GRACE)
            if remaining <= 0:
                logger.warning("Disconnected Codex runtime exceeded its completion grace")
                return
            try:
                os.kill(runtime["server_pid"], 0)
                # Bound the whole snapshot, including pagination and close,
                # so an unavailable server cannot defeat the shutdown deadline.
                async with asyncio.timeout(min(15, remaining)):
                    active = await supervise_snapshot(directory, runtime, config, config_path,
                                                      watched, restarts, disconnected_at is None)
                if disconnected_at is not None and not active:
                    return
            except (FileNotFoundError, ProcessLookupError):
                return
            except (OSError, ValueError, RuntimeError, WebSocketException) as error:
                logger.warning("Codex runtime supervision failed (%s): %s", type(error).__name__, error)
            delay = POLL_INTERVAL
            if disconnected_at is not None:
                delay = min(delay, max(0, DISCONNECT_GRACE - (time.monotonic() - disconnected_at)))
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.info("Codex supervisor stopping")
    finally:
        # No further signal may cancel cleanup halfway through.
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, lambda: None)
        try:
            await stop_process_groups([runtime["server_pid"], *[p.pid for p in watched.values()]],
                                      watched.values())
        finally:
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)


async def supervise_snapshot(directory, runtime, config, config_path, watched, restarts, connected):
    async with CodexClient(str(directory / "codex.sock")) as client:
        loaded = await client.loaded_threads()
        active = False
        for native_id in loaded:
            thread = (await client.call("thread/read", {"threadId": native_id, "includeTurns": False}))["thread"]
            if thread.get("threadSource") != "user":
                continue
            status = thread["status"]
            waiting = {"waitingOnApproval", "waitingOnUserInput"}.intersection(status.get("activeFlags", []))
            if not connected:
                logger.info("Disconnected thread %s: status=%s active_flags=%s", native_id,
                            status["type"], status.get("activeFlags", []))
            active |= status["type"] == "active" and not waiting
            child = watched.get(native_id)
            if connected and (child is None or child.poll() is not None):
                attempts, due = restarts.get(native_id, (0, 0))
                if time.monotonic() < due:
                    continue
                watched[native_id] = spawn(config, config_path, client="codex", native_id=native_id,
                    owner_pid=runtime["server_pid"], socket_path=str(directory / "codex.sock"),
                    name=thread.get("name") or f"{config.host}-codex-{native_id[-8:]}",
                    cwd=thread.get("cwd") or runtime["cwd"], model=thread.get("model") or "")
                restarts[native_id] = (attempts + 1, time.monotonic() + min(2 ** min(attempts + 1, 8), 300))
        for native_id in set(watched) - set(loaded):
            child = watched[native_id]
            await stop_process_groups([child.pid], [child])
            watched.pop(native_id)
            restarts.pop(native_id, None)
    return active


def claude_settings(arguments, command):
    arguments = list(arguments)
    setting = {"type": "command", "command": command, "timeout": 10}
    # Preserve an explicitly supplied settings object/file and all its keys.
    for index, arg in enumerate(arguments):
        if arg == "--settings" or arg.startswith("--settings="):
            value = arguments[index + 1] if arg == "--settings" else arg.split("=", 1)[1]
            payload = json.loads(value if value.lstrip().startswith("{") else Path(value).expanduser().read_text())
            payload.setdefault("hooks", {}).setdefault("SessionStart", []).append({"hooks": [setting]})
            if arg == "--settings":
                arguments[index + 1] = json.dumps(payload)
            else:
                arguments[index] = "--settings=" + json.dumps(payload)
            return arguments
    return ["--settings", json.dumps({"hooks": {"SessionStart": [{"hooks": [setting]}]}}), *arguments]


def launch(client, arguments, config, config_path):
    config_path = str(Path(config_path).resolve())
    # Native commands continue to use the installation's existing settings and auth.
    if client == "claude":
        command = shlex.join([sys.executable, "-m", "teamcomms.connectors.cli", "--config", config_path, "hook", "claude"])
        if any(arg in {"-p", "--print"} for arg in arguments):
            return subprocess.call(["claude", *arguments])  # Background jobs do not greet or enroll.
        return subprocess.call(["claude", *claude_settings(arguments, command)])
    options = launch_options(arguments)
    if options is None or not sys.stdin.isatty():
        return subprocess.call(["codex", *arguments])
    cwd, server_config = options
    directory = Path(tempfile.mkdtemp(prefix="teamcomms-codex-"))
    address = "unix://" + str(directory / "codex.sock")
    env = {**os.environ, "TEAMCOMMS_CODEX_SOCKET": str(directory / "codex.sock")}
    with (directory / "runtime.log").open("w") as log:
        server = subprocess.Popen(["codex", *server_config, "app-server", "--listen", address], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        if server.poll() is not None:
            raise RuntimeError(f"Codex app-server exited; see {directory}/runtime.log")
        if (directory / "codex.sock").exists():
            break
        time.sleep(.1)
    else:
        asyncio.run(stop_process_groups([server.pid], [server]))
        raise RuntimeError(f"Codex app-server did not open its socket; see {directory}/runtime.log")
    (directory / "runtime.json").write_text(json.dumps({"server_pid": server.pid, "launcher_pid": os.getpid(), "cwd": cwd}))
    with (directory / "supervisor.log").open("w") as log:
        subprocess.Popen([sys.executable, "-m", "teamcomms.connectors.cli", "--config", config_path,
            "supervise", str(directory)], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    print(f"TeamComms runtime: {directory}", file=sys.stderr)
    try:
        return subprocess.call(["codex", "--remote", address, *arguments], cwd=cwd, env=env)
    finally:
        (directory / "disconnected").touch()
