"""Host connector commands; no server database or personal-system dependency."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import sys
from uuid import UUID

from .client import ServiceClient
from .config import load
from .state import Store, private_directory, session_lock

CALLS = {
    "list_sessions": ("GET", "/sessions"), "register_session": ("POST", "/sessions"),
    "heartbeat_session": ("POST", "/sessions/heartbeat"), "list_resources": ("GET", "/resources"),
    "list_groups": ("GET", "/groups"), "subscribe": ("POST", "/subscriptions"),
    "send_message": ("POST", "/messages"), "get_messages": ("GET", "/messages"),
    "get_message": ("GET", "/messages/read"), "record_delivery": ("POST", "/deliveries"),
    "acknowledge_message": ("POST", "/messages/acknowledge"), "get_delivery_history": ("GET", "/deliveries/history"),
}
DIALOG_CALLS = {"record_dialog": ("POST", "/events"), "get_dialog": ("GET", ""),
                "session_bootstrap": ("POST", "/bootstrap")}
ENTRY_CALLS = {"create_entry": ("POST", ""), "get_entry": ("GET", "/read"),
    "update_entry": ("POST", "/update"), "restore_entry": ("POST", "/restore"),
    "get_entry_revisions": ("GET", "/revisions"), "search_entries": ("POST", "/search"),
    "read_entry_target": ("POST", "/target"), "edit_entry": ("POST", "/edit"),
    "preview_entry_edits": ("POST", "/edits/preview"), "apply_entry_edits": ("POST", "/edits/apply"),
    "get_entry_edit": ("GET", "/edits/read")}
INFLIGHT_CALLS = {"create_work": ("POST", ""), "list_work": ("GET", ""),
    "get_work": ("GET", "/read"), "mutate_work": ("POST", "/mutate"),
    "get_work_changes": ("GET", "/changes")}
POUCH_CALLS = {"get_pouch": ("GET", ""), "initialize_pouch": ("POST", "/initialize"),
               "get_pouch_changes": ("GET", "/changes"), "export_pouch": ("GET", "/export")}


def state_path(config, client, native_id):
    # Namespace by endpoint and credential-file identity as well as native session.
    key = hashlib.sha256(json.dumps([config.url, str(config.token_file), config.host,
        "codex" if client == "codex_queue" else client, native_id]).encode()).hexdigest()
    return private_directory(config.state_dir) / key


async def receive(args, config):
    from .adapters import NativeAdapter
    from .receiver import Receiver
    UUID(args.native_id)
    if args.pid <= 1:
        raise ValueError("A live native owner PID is required")
    if args.client != "codex_queue" and not args.socket:
        raise ValueError("Native delivery requires an explicit socket")
    directory = state_path(config, args.client, args.native_id)
    with session_lock(directory):
        store = Store(directory)
        service = ServiceClient(config)
        adapter = NativeAdapter(args.client, args.native_id, args.socket, args.pid, name=args.name,
                                model=args.model, transcript=args.transcript)
        registration = {"native_id": args.native_id, "client": "codex" if args.client == "codex_queue" else args.client,
            "host": config.host, "name": args.name, "model": args.model, "workspace": args.cwd,
            "resource_ids": [str(r) for r in config.resource_ids],
            "delivery_mode": {"claude": "claude_socket", "codex": "codex_app_server", "codex_queue": "codex_queue"}[args.client],
            "capabilities": ["comms", "stream-replay"]}
        receiver = Receiver(service, store, adapter, registration, str(Path(args.config).resolve()))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            if args.once:
                print(json.dumps(await receiver.register()), flush=True)
                await receiver.once()
            else:
                await receiver.run(stop)
        finally:
            await service.close()
            store.close()


async def call(args, config):
    service = ServiceClient(config)
    body = json.load(sys.stdin) if args.arguments == "-" else json.loads(args.arguments)
    prefix, methods = next((prefix, methods) for prefix, methods in (
        ("/api/comms", CALLS), ("/api/dialog", DIALOG_CALLS),
        ("/api/inflight", INFLIGHT_CALLS), ("/api/entries", ENTRY_CALLS), ("/api/pouch", POUCH_CALLS)) if args.tool in methods)
    method, path = methods[args.tool]
    try:
        # Persist outgoing messages before attempting network publication.
        if args.tool == "send_message":
            directory = private_directory(config.state_dir) / "outgoing"
            store = Store(directory)
            try:
                store.enqueue(body)
                result = await service.request(method, "/api/comms" + path, body)
                store.sent(body["message_id"])
            finally:
                store.close()
        else:
            result = await service.request(method, prefix + path, body)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await service.close()


async def flush(config):
    service = ServiceClient(config)
    store = Store(private_directory(config.state_dir) / "outgoing")
    count = 0
    try:
        for message in store.outgoing():
            await service.post("/messages", message)
            store.sent(message["message_id"])
            count += 1
        print(json.dumps({"published": count}))
    finally:
        store.close()
        await service.close()


async def record_dialog(args, config):
    from .dialog import Recorder
    session_id = str(UUID(args.session_id))
    native_id = str(UUID(args.native_id))
    service = ServiceClient(config)
    directory = state_path(config, args.client, native_id) / "dialog"
    try:
        # Verify the selected transcript's destination before creating recording state.
        page = await service.get("/sessions", host=config.host, include_offline=True, limit=100)
        while True:
            match = next((r for r in page["sessions"] if r["session_id"] == session_id), None)
            if match or page["next_offset"] is None:
                break
            page = await service.get("/sessions", host=config.host, include_offline=True,
                                     limit=100, offset=page["next_offset"])
        client = "codex" if args.client == "codex_queue" else args.client
        if not match or match["native_id"] != native_id or match["client"] != client:
            raise ValueError("Recording session does not match selected host/client/native identity")
        with session_lock(directory):
            store = Store(directory)
            try:
                recorder = Recorder(service, store, session_id, args.client, args.transcript,
                                    from_start=args.from_start)
                if args.once:
                    print(json.dumps({"recorded": await recorder.once()}))
                else:
                    stop = asyncio.Event()
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
                    await recorder.run(stop)
            finally:
                store.close()
    finally:
        await service.close()


async def reload_context(args, config):
    from .adapters import NativeAdapter
    from .dialog import bootstrap_context
    if config.bootstrap is None:
        raise ValueError("Configure bootstrap filters and bounds before reload")
    service = ServiceClient(config)
    try:
        context = await bootstrap_context(service, config)
        if args.native_id:
            UUID(args.native_id)
            if args.pid <= 1 or (args.client != "codex_queue" and not args.socket):
                raise ValueError("Injection requires the existing native owner PID and socket")
            adapter = NativeAdapter(args.client, args.native_id, args.socket, args.pid, name="history reload")
            await adapter.metadata()
            try:
                if await adapter.setup(context):
                    print(json.dumps({"state": "accepted", "chars": len(context)}))
                else:
                    from uuid import uuid4
                    result = await adapter.send(context, "teamcomms-history", str(uuid4()))
                    print(json.dumps(result))
            except Exception as error:
                raise RuntimeError("History injection outcome uncertain; reconcile native context before retry") from error
        else:
            print(context)
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(prog="teamcomms-connect")
    parser.add_argument("--config", required=True, help="Explicit connector JSON configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    recv = commands.add_parser("receive", help="Receive for one explicitly selected native session")
    recv.add_argument("--client", choices=["claude", "codex", "codex_queue"], required=True)
    recv.add_argument("--native-id", required=True)
    recv.add_argument("--socket", default="")
    recv.add_argument("--pid", type=int, required=True)
    recv.add_argument("--name", required=True)
    recv.add_argument("--model", default="")
    recv.add_argument("--transcript", default="")
    recv.add_argument("--cwd", default=os.getcwd())
    recv.add_argument("--once", action="store_true")
    helper = commands.add_parser("call", help="Invoke a Comms, Dialog, Entries or Pouch operation with JSON or stdin (-)")
    helper.add_argument("tool", choices=sorted(CALLS | DIALOG_CALLS | ENTRY_CALLS | POUCH_CALLS | INFLIGHT_CALLS))
    helper.add_argument("arguments")
    commands.add_parser("flush", help="Retry the durable outgoing message queue")
    commands.add_parser("status", help="Inspect local session dispatch/recovery state")
    mattermost = commands.add_parser("mattermost", help="Run explicit Mattermost channel routes")
    mattermost.add_argument("--routes", required=True, help="Mattermost endpoint/token/routes JSON")
    watcher = commands.add_parser("publish-event", help="Publish one stable watcher event from JSON or stdin (-)")
    watcher.add_argument("event", help="JSON file path or - for stdin")
    record = commands.add_parser("record", help="Record a selected native transcript with a durable cursor")
    record.add_argument("--session-id", required=True)
    record.add_argument("--native-id", required=True)
    record.add_argument("--client", choices=["claude", "codex", "codex_queue"], required=True)
    record.add_argument("--transcript", required=True)
    record.add_argument("--from-start", action="store_true")
    record.add_argument("--once", action="store_true")
    reload = commands.add_parser("reload", help="Retrieve configured history; optionally inject into a selected session")
    reload.add_argument("--native-id")
    reload.add_argument("--client", choices=["claude", "codex", "codex_queue"], default="codex")
    reload.add_argument("--socket", default="")
    reload.add_argument("--pid", type=int, default=0)
    for name in ("codex", "claude"):
        launch = commands.add_parser(name, help=f"Launch native {name} with automatic Comms enrollment")
        launch.add_argument("arguments", nargs=argparse.REMAINDER)
    hook = commands.add_parser("hook", help="Claude SessionStart hook; JSON from stdin")
    hook.add_argument("client", choices=["claude"])
    commands.add_parser("supervise").add_argument("directory")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load(args.config)
        if args.command == "receive":
            asyncio.run(receive(args, config))
        elif args.command == "call":
            asyncio.run(call(args, config))
        elif args.command == "flush":
            asyncio.run(flush(config))
        elif args.command == "record":
            asyncio.run(record_dialog(args, config))
        elif args.command == "reload":
            asyncio.run(reload_context(args, config))
        elif args.command == "mattermost":
            from .mattermost import MattermostConfig, run
            asyncio.run(run(config, load(args.routes, MattermostConfig), str(Path(args.config).resolve())))
        elif args.command == "publish-event":
            from .watcher import publish_event
            event = json.load(sys.stdin) if args.event == "-" else json.loads(Path(args.event).read_text())
            async def publish():
                service = ServiceClient(config)
                store = Store(private_directory(config.state_dir) / "outgoing")
                try:
                    return await publish_event(service, store, **event)
                finally:
                    store.close()
                    await service.close()
            print(json.dumps(asyncio.run(publish())))
        elif args.command == "status":
            directories = set(config.state_dir.glob("*")) | set(config.state_dir.glob("*/dialog"))
            for directory in sorted(directories):
                if (directory / "state.sqlite3").is_file():
                    store = Store(directory)
                    try:
                        print(json.dumps({"directory": str(directory), "session_id": store.get("session_id"),
                            "setup_state": store.get("setup_state"), "capture_error": store.get("capture_error", ""),
                            "mattermost_coverage": store.get("mm:coverage"),
                            "mattermost_inbound_error": store.get("mm:inbound_error"),
                            "cursor": store.get("cursor", 0), "pending": [{"delivery_id": d["id"], "phase": d["phase"],
                            "error": d["error"]} for d in store.pending()], "outgoing": len(store.outgoing())}))
                    finally:
                        store.close()
        elif args.command == "hook":
            from .startup import hook
            print(hook(config, args.config, json.load(sys.stdin)))
        else:
            from .launcher import launch, supervise
            if args.command == "supervise":
                asyncio.run(supervise(Path(args.directory), config, args.config))
            else:
                native_args = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
                return launch(args.command, native_args, config, args.config)
        return 0
    except Exception as error:
        print(f"TeamComms: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
