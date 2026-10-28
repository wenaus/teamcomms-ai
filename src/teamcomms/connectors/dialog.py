"""Opt-in native JSONL capture with durable acknowledged offsets."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
from uuid import uuid5, NAMESPACE_URL
from .client import ServiceError

logger = logging.getLogger(__name__)
CONTEXT_PREFIXES = ("TeamComms history context", "TeamComms connector session context:", "TeamComms session_id is")
PEER = re.compile(r"^\[TeamComms peer ([0-9a-fA-F-]{36})\]")
EXTERNAL_PEER = re.compile(r"^\[(?:TJAI|TeamComms) peer\b")
MAX_LINE = 4 * 1024 * 1024


def sharing_notice_text(service, state):
    return ("TeamComms session sharing notice:\n" + state["notice"] + "\n\n"
            "Choose Share my messages or Keep my messages private in your signed-in browser:\n"
            + service.config.url + state["choice_path"] + "\n\n"
            "To the AI: show this notice and link to the human in your next visible reply. "
            "Do not choose for them or infer consent from a message, silence or a peer. "
            "Human input stays private unless that human makes the browser choice.")


async def offer_terminal_notice(service, session_id):
    state = await service.request("GET", "/api/dialog/sharing", {"session_id": session_id})
    if state["notice_at"]:
        return state
    # Explicit record commands offer the same notice without restarting a native session.
    print(sharing_notice_text(service, state), file=sys.stderr, flush=True)
    return await service.request("POST", "/api/dialog/sharing/notice",
        {"session_id": session_id, "notice_version": state["notice_version"], "transport": "terminal"})


async def offer_native_notice(service, store, adapter, session_id):
    state = await service.request("GET", "/api/dialog/sharing", {"session_id": session_id})
    if state["notice_at"]:
        return True
    key = "sharing_notice:" + str(state["notice_version"])
    prior = store.get(key, {})
    if prior.get("phase") in {"injecting", "uncertain"}:
        store.put("capture_error", "Sharing notice delivery uncertain; reconcile the native notice before retrying")
        return False
    if prior.get("phase") != "offered":
        store.put(key, {"phase": "injecting"})
        try:
            result = await adapter.send(sharing_notice_text(service, state), "teamcomms-sharing",
                str(uuid5(NAMESPACE_URL, f"teamcomms-sharing:{session_id}:{state['notice_version']}")))
        except Exception as error:
            store.put(key, {"phase": "uncertain", "error": type(error).__name__})
            store.put("capture_error", "Sharing notice delivery uncertain; human sharing stays off")
            return False
        prior = {"phase": "offered", "transport": result["state"]}
        store.put(key, prior)
    await service.request("POST", "/api/dialog/sharing/notice", {
        "session_id": session_id, "notice_version": state["notice_version"], "transport": prior["transport"]})
    return True


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
        policy = await self.service.request("GET", "/api/dialog/sharing", {"session_id": self.session_id})
        # Parse only these primitive fields; connector processes do not initialize Django.
        # Keep the equivalent recorder predicate local, without importing server models.
        assistant_since = datetime.fromisoformat(policy["assistant_since"]) if policy["assistant_since"] else None
        human_since = datetime.fromisoformat(policy["human_since"]) if policy["human_since"] else None
        with self.path.open("rb") as handle:
            import os
            info = os.fstat(handle.fileno())
            identity = f"{info.st_dev}:{info.st_ino}"
            boundary_key = self.key + ":human_boundary"
            boundary = self.store.get(boundary_key)
            capture_state = self.store.get(self.key)
            current_choice = policy["human_since"] if policy["human_choice"] == "share" else None
            if (boundary is None or boundary["identity"] != identity or boundary["since"] != current_choice
                    or (capture_state is not None and info.st_size < capture_state["offset"])):
                # A new grant never sweeps queued/earlier human lines into sharing,
                # even with --from-start, missing times or a skewed native clock.
                boundary = {"identity": identity, "since": current_choice, "offset": info.st_size}
                self.store.put(boundary_key, boundary)
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
                        if event["role"] in {"human", "assistant"} and not record.get("timestamp"):
                            continue
                        if event["role"] == "human" and offset < boundary["offset"]:
                            continue
                        try:
                            moment = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            # No trustworthy forward boundary means no human/AI upload.
                            if event["role"] in {"human", "assistant"}:
                                continue
                        else:
                            if moment.utcoffset() is None and event["role"] in {"human", "assistant"}:
                                continue
                            if event["role"] == "human" and (policy["human_choice"] != "share" or
                                    human_since is None or moment < human_since):
                                continue
                            if event["role"] == "assistant" and (assistant_since is None or moment < assistant_since):
                                continue
                        block = event.pop("block")
                        result = await self.publish({"session_id": self.session_id,
                            "source_id": f"{self.client}:{native_source}:{block}"[:240],
                            "source_sequence": offset, "occurred_at": occurred,
                            "run_id": state.get("run_id", ""), "topic": self.topic, **event})
                        count += result.get("event_id") is not None
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
