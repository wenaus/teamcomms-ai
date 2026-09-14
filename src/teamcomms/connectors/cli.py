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
    method, path = CALLS[args.tool]
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
            result = await service.request(method, "/api/comms" + path, body)
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
    helper = commands.add_parser("call", help="Invoke a Comms operation with JSON or stdin (-)")
    helper.add_argument("tool", choices=sorted(CALLS))
    helper.add_argument("arguments")
    commands.add_parser("flush", help="Retry the durable outgoing message queue")
    commands.add_parser("status", help="Inspect local session dispatch/recovery state")
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
        elif args.command == "status":
            for directory in sorted(config.state_dir.glob("*")):
                if (directory / "state.sqlite3").is_file():
                    store = Store(directory)
                    try:
                        print(json.dumps({"directory": str(directory), "session_id": store.get("session_id"),
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
