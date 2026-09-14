"""Crash-aware mailbox delivery with durable outbox and monotonic replay cursor."""

import asyncio
from contextlib import aclosing
import json
import logging
import httpx
import time
from uuid import uuid4, uuid5, NAMESPACE_URL
from websockets.exceptions import WebSocketException

from .client import ServiceError
from .presentation import message_text, session_instructions

logger = logging.getLogger(__name__)


class TargetGone(RuntimeError):
    pass


class TargetNotReady(TargetGone):
    """The owner exists, but its startup registry has not appeared yet."""


class Receiver:
    def __init__(self, service, store, adapter, registration, config_path):
        self.service, self.store, self.adapter = service, store, adapter
        self.registration = registration
        self.config_path = config_path
        self.session_id = None
        self.registered = False
        self.capture_task = None
        self.capture_stop = asyncio.Event()

    async def register(self):
        metadata = await self.adapter.metadata()
        result = await self.service.post("/sessions", {**self.registration, **metadata})
        prior = self.store.get("session_id")
        if prior and prior != result["session_id"]:
            raise RuntimeError("Local state belongs to a different service session; use a new state directory")
        self.session_id = result["session_id"]
        self.store.put("session_id", self.session_id)
        if (not self.store.get("instructions", False) and hasattr(self.adapter, "setup")
                and self.store.get("setup_state") not in {"injecting", "uncertain"}):
            from .dialog import bootstrap_context
            history = await bootstrap_context(self.service, self.service.config)
            instructions = session_instructions(self.session_id, self.config_path)
            if history:
                instructions += "\n\n" + history
            self.store.put("setup_state", "injecting")
            try:
                accepted = await self.adapter.setup(instructions)
            except Exception as error:
                self.store.put("setup_state", "uncertain")
                logger.warning("Session context injection uncertain (%s); use explicit reload", type(error).__name__)
            else:
                self.store.put("setup_state", "accepted" if accepted else "deferred")
                if accepted:
                    self.store.put("instructions", True)
                elif history:
                    self.store.put("deferred_history", history)
        config = self.service.config
        for group in config.group_ids:
            await self.service.post("/subscriptions", {"session_id": self.session_id, "group_id": str(group)})
        for topic in config.topics:
            await self.service.post("/subscriptions", {"session_id": self.session_id, "topic": topic})
        if config.greeting and not self.store.get("greeted", False):
            prepared = self.store.get("greeting_plan")
            peers, offset = [], 0
            while True:
                page = await self.service.get("/sessions", limit=100, offset=offset)
                peers.extend(s["session_id"] for s in page["sessions"] if s["session_id"] != self.session_id)
                offset = page["next_offset"]
                if offset is None:
                    break
                if len(peers) > 1000:
                    raise RuntimeError("Greeting directory exceeds 1000 sessions")
            # Stable outbox IDs make crash/restart greeting retries idempotent.
            text = f"AI Hi — {result['name']} ({result['client']}, {result['model'] or 'model unknown'}) on {result['host']}."
            if config.work:
                text += " Working on: " + config.work
            text += " Coordinating through TeamComms; no reply needed."
            if prepared is None:
                prepared = {"peers": peers, "content": text}
                self.store.put("greeting_plan", prepared)
            for peer in prepared["peers"]:
                self.store.enqueue({"message_id": str(uuid5(NAMESPACE_URL, f"teamcomms-hi:{self.session_id}:{peer}")),
                    "sender_session_id": self.session_id, "audience": {"session_ids": [peer]}, "content": prepared["content"]})
            self.store.put("greeted", True)
        self.registered = True
        return result

    async def flush_outbox(self):
        for body in self.store.outgoing():
            await self.service.post("/messages", body)
            self.store.sent(body["message_id"])

    async def reconcile(self):
        cursor = 0
        while True:
            page = await self.service.get("/messages", session_id=self.session_id, after=cursor, pending_only=True)
            for delivery in page["messages"]:
                self.store.ingest(delivery, advance=False)
            cursor = page["next_after"]
            if not page["has_more"]:
                break
        await self.dispatch_pending()

    async def dispatch_pending(self):
        for local in self.store.pending():
            await self.dispatch(local)

    async def dispatch(self, local):
        delivery = json.loads(local["payload"])
        identity = delivery["delivery_id"]
        phase = local["phase"]
        if phase == "uncertain":
            current = (await self.service.get("/deliveries/history", delivery_id=identity, limit=1))["delivery"]
            if current.get("acknowledged_at") or current["state"] in {"written", "accepted"}:
                self.store.update(identity, "done")
            return
        if phase == "injecting":
            self.store.update(identity, "uncertain", error="Receiver restarted during native injection; reconcile before retry")
            logger.warning("Delivery %s remains uncertain after receiver restart", identity)
            return
        if phase == "new":
            if delivery["acknowledged_at"] or delivery["state"] != "pending":
                self.store.update(identity, "done")
                return
            claim = {"receipt_id": str(uuid4()), "delivery_id": identity,
                     "expected_revision": delivery["revision"], "state": "uncertain",
                     "detail": "Receiver reserved dispatch; native acceptance is not yet known"}
            self.store.update(identity, "claiming", claim=claim)
        else:
            claim = json.loads(local["claim"]) if local["claim"] else None
        if phase in {"new", "claiming"}:
            try:
                claimed = await self.service.post("/deliveries", claim)
            except ServiceError as error:
                if error.status == 409:
                    self.store.update(identity, "done", error="Dispatch claim changed; another receiver or acknowledgment won")
                    return
                raise
            # Persist before touching the native transport. A crash from this point
            # must not cause another native write without reconciliation.
            self.store.update(identity, "injecting")
            text = message_text(delivery["message"])
            needs_instructions = not self.store.get("instructions", False)
            if needs_instructions:
                text += "\n\n" + session_instructions(self.session_id, self.config_path)
                if self.store.get("deferred_history"):
                    text += "\n\n" + self.store.get("deferred_history")
            try:
                result = await self.adapter.send(text, delivery["message"]["author_id"], delivery["message_id"])
            except Exception as error:
                # The native API may have accepted before its connection failed.
                self.store.update(identity, "uncertain", error=f"{type(error).__name__}: {error}"[:2000])
                logger.warning("Delivery %s has no confirmed native receipt (%s)", identity, type(error).__name__)
                return
            report = {"receipt_id": str(uuid4()), "delivery_id": identity, "expected_revision": claimed["revision"],
                      "state": result["state"], "detail": result.get("detail", "")}
            self.store.update(identity, "reporting", report=report)
            if needs_instructions:
                self.store.put("instructions", True)
        else:
            report = json.loads(local["report"])
        try:
            await self.service.post("/deliveries", report)
        except ServiceError as error:
            if error.status != 409:
                raise
            # A fast model acknowledgment can advance the revision before the
            # transport report arrives; reconcile from the authoritative receipt.
            status = await self.service.get("/deliveries/history", delivery_id=identity, limit=1)
            current = status["delivery"]
            if current["state"] == report["state"] or current["state"] == "accepted":
                self.store.update(identity, "done")
                return
            if current["state"] != "uncertain":
                self.store.update(identity, "uncertain", error="Native receipt conflicts with current transport state")
                return
            report = {**report, "receipt_id": str(uuid4()), "expected_revision": current["revision"]}
            self.store.update(identity, "reporting", report=report)
            await self.service.post("/deliveries", report)
        self.store.update(identity, "done")

    async def once(self):
        await self.flush_outbox()
        await self.reconcile()

    async def run(self, stop):
        delay = 1
        startup_deadline = time.monotonic() + 30
        try:
            while not stop.is_set():
                try:
                    if not self.registered:
                        await self.register()
                    if self.service.config.dialog_capture and self.capture_task is None:
                        self.start_capture()
                    metadata = await self.adapter.metadata()
                    await self.service.post("/sessions/heartbeat", {"session_id": self.session_id,
                        **{k: v for k, v in metadata.items() if k in {"state", "name", "model", "effort"}}})
                    await self.once()
                    async with aclosing(self.service.stream(self.session_id, self.store.get("cursor", 0))) as stream:
                        async for incoming in stream:
                            if stop.is_set():
                                break
                            if incoming["event"] == "message":
                                self.store.ingest(incoming["data"])
                                await self.dispatch_pending()
                            elif incoming["event"] == "refresh":
                                await self.reconcile()
                    delay = 1
                except TargetNotReady:
                    if self.registered or time.monotonic() >= startup_deadline:
                        logger.warning("Native session registry is unavailable; receiver stopped")
                        return
                    await self.pause(stop, 1)
                except TargetGone:
                    return
                except ServiceError as error:
                    if error.status in {401, 403}:
                        raise
                    logger.warning("Receiver reconnecting after service failure: %s", error)
                    await self.pause(stop, delay)
                    delay = min(delay * 2, 25)
                except (OSError, ValueError, RuntimeError, httpx.HTTPError, WebSocketException) as error:
                    logger.warning("Receiver reconnecting (%s): %s", type(error).__name__, error)
                    await self.pause(stop, delay)
                    delay = min(delay * 2, 25)
        finally:
            self.capture_stop.set()
            if self.capture_task:
                await self.capture_task
            if self.session_id:
                try:
                    await self.service.post("/sessions/heartbeat", {"session_id": self.session_id, "state": "offline"})
                except Exception as error:
                    logger.warning("Offline heartbeat failed; discovery expires normally (%s)", type(error).__name__)

    def start_capture(self):
        from pathlib import Path
        from .cli import state_path
        from .dialog import Recorder
        from .state import Store, session_lock

        async def capture():
            directory = state_path(self.service.config, self.registration["client"], self.registration["native_id"]) / "dialog"
            try:
                with session_lock(directory):
                    store = Store(directory)
                    try:
                        transcript = getattr(self.adapter, "transcript", "")
                        if not transcript:
                            raise ValueError("Native transcript path unavailable; use explicit record command")
                        recorder = Recorder(self.service, store, self.session_id, self.registration["client"], Path(transcript))
                        await recorder.run(self.capture_stop)
                    finally:
                        store.close()
            except Exception as error:
                logger.warning("Dialog recorder unavailable: %s", error)
                self.store.put("capture_error", f"{type(error).__name__}: {error}")
        self.capture_task = asyncio.create_task(capture())

    @staticmethod
    async def pause(stop, delay):
        try:
            await asyncio.wait_for(stop.wait(), delay)
        except TimeoutError:
            return
