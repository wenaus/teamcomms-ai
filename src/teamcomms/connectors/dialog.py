"""Opt-in native JSONL capture with durable acknowledged offsets."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from .client import ServiceError

logger = logging.getLogger(__name__)
CONTEXT_PREFIXES = ("TeamComms history context", "TeamComms connector session context:", "TeamComms session_id is")
PEER = re.compile(r"^\[TeamComms peer ([0-9a-fA-F-]{36})\]")
EXTERNAL_PEER = re.compile(r"^\[(?:TJAI|TeamComms) peer\b")
MAX_LINE = 4 * 1024 * 1024


def visible_events(client, record):
    """Use one canonical native record form; ignore mirrored notifications/tools."""
    if client in {"codex", "codex_queue"}:
        payload = record.get("payload", {})
        if record.get("type") != "response_item" or payload.get("type") != "message":
            return []
        role = payload.get("role")
        if role not in {"user", "assistant"} or payload.get("channel") == "analysis":
            return []
        phase = payload.get("phase") or payload.get("channel") or "final"
        if phase not in {"commentary", "final"}:
            phase = "final"
        blocks = payload.get("content", [])
    else:
        role = record.get("type")
        if role not in {"user", "assistant"}:
            return []
        payload = record.get("message", {})
        blocks = payload.get("content", [])
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        phase = "final" if payload.get("stop_reason") == "end_turn" else "commentary"
    result = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") not in {"text", "input_text", "output_text"}:
            continue
        text = block.get("text", "")
        if not text or text.startswith(CONTEXT_PREFIXES):
            continue
        event_role = "assistant" if role == "assistant" else "human"
        match = PEER.match(text) if role == "user" else None
        if role == "user" and EXTERNAL_PEER.match(text):
            event_role = "peer"
        # Preserve Unicode within the encoded request bound, including escaped text.
        for part, start in enumerate(range(0, len(text), 4000)):
            result.append({"role": event_role, "phase": phase if role == "assistant" else "message",
                "content": text[start:start + 4000], "block": f"{index}:{part}",
                "message_id": match.group(1) if match and part == 0 else None})
        if match:
            # The canonical message contains the full text; do not also record envelope parts.
            result = [r for r in result if not r["block"].startswith(f"{index}:")] + [
                {"role": "peer", "phase": "message", "content": text[:4000],
                 "block": f"{index}:0", "message_id": match.group(1)}]
    return result


class Recorder:
    def __init__(self, service, store, session_id, client, transcript, *, from_start=False, topic=""):
        self.service, self.store, self.session_id = service, store, session_id
        self.store.put("session_id", session_id)
        self.client, self.path = client, Path(transcript).expanduser().resolve()
        self.from_start, self.topic = from_start, topic
        self.key = "transcript:" + hashlib.sha256(str(self.path).encode()).hexdigest()

    async def publish(self, event):
        try:
            return await self.service.request("POST", "/api/dialog/events", event)
        except ServiceError as error:
            if error.status == 404 and event.get("message_id"):
                # A quoted/foreign envelope is still peer material, with unverified attribution.
                event = {**event, "message_id": None}
                return await self.service.request("POST", "/api/dialog/events", event)
            raise

    def gap(self, source, text, state):
        return {"session_id": self.session_id, "source_id": source,
                "source_sequence": state["offset"], "occurred_at": state["started_at"],
                "role": "gap", "content": text, "topic": self.topic}

    async def once(self):
        with self.path.open("rb") as handle:
            import os
            info = os.fstat(handle.fileno())
            identity = f"{info.st_dev}:{info.st_ino}"
            state = self.store.get(self.key)
            if state is None or state["identity"] != identity or info.st_size < state["offset"]:
                # Persist reset intent before publication so retry uses the exact same timestamp.
                intent_key = self.key + ":reset"
                fresh = self.store.get(intent_key)
                if fresh is None:
                    fresh = {"identity": identity, "offset": 0 if self.from_start or state else info.st_size,
                             "started_at": datetime.now(timezone.utc).isoformat(), "run_id": ""}
                    self.store.put(intent_key, fresh)
                reason = "Transcript replaced or truncated; prior coverage may be incomplete" if state else (
                    "Recording begins at transcript start; earlier native history may be unavailable" if self.from_start
                    else "Recording begins at current transcript end; earlier content was not imported")
                source = "coverage:" + hashlib.sha256(json.dumps(fresh, sort_keys=True).encode()).hexdigest()
                await self.publish(self.gap(source, reason, fresh))
                state = fresh
                self.store.put(self.key, state)
                self.store.put(intent_key, None)
            handle.seek(state["offset"])
            count = 0
            for _ in range(100):
                offset = handle.tell()
                line = handle.readline(MAX_LINE)
                if not line or (not line.endswith(b"\n") and len(line) < MAX_LINE):
                    break
                source = hashlib.sha256(line).hexdigest()
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("JSONL record must be an object")
                except (ValueError, UnicodeDecodeError):
                    await self.publish(self.gap("invalid:" + str(offset) + ":" + source,
                                               "Invalid or oversized native transcript line", state))
                else:
                    payload = record.get("payload", {})
                    if record.get("type") == "event_msg" and payload.get("type") == "task_started":
                        state["run_id"] = payload.get("turn_id", "")
                    occurred = record.get("timestamp") or state["started_at"]
                    native_source = record.get("uuid") or payload.get("id") or source
                    for event in visible_events(self.client, record):
                        block = event.pop("block")
                        await self.publish({"session_id": self.session_id,
                            "source_id": f"{self.client}:{native_source}:{block}"[:240],
                            "source_sequence": offset, "occurred_at": occurred,
                            "run_id": state.get("run_id", ""), "topic": self.topic, **event})
                        count += 1
                state = {**state, "offset": handle.tell()}
                self.store.put(self.key, state)
            return count

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.once()
                self.store.put("capture_error", "")
            except Exception as error:
                # Capture failure must remain visible without stopping the mailbox receiver.
                detail = f"{type(error).__name__}: {error}"
                if self.store.get("capture_error") != detail:
                    logger.warning("Dialog capture paused: %s", detail)
                self.store.put("capture_error", detail)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                continue


async def bootstrap_context(service, config):
    if config.bootstrap is None:
        return ""
    request = config.bootstrap.model_dump(mode="json", exclude_none=True)
    request.setdefault("host", config.host)
    try:
        result = await asyncio.wait_for(service.request("POST", "/api/dialog/bootstrap", request), timeout=2)
        return result["context"]
    except Exception as error:
        logger.warning("Dialog bootstrap unavailable (%s)", type(error).__name__)
        return "TeamComms history context unavailable; enrollment continues. Use reload after service recovery."
