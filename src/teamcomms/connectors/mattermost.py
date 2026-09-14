"""Explicit Mattermost routes with durable publication and independent receipts."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import signal
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import Field, model_validator

from teamcomms.comms.schemas import Audience, Label, Request, SendMessage
from .client import ServiceClient, ServiceError
from .config import Configuration
from .presentation import message_text
from .receiver import Receiver
from .state import Store, session_lock

logger = logging.getLogger(__name__)


class Route(Request):
    channel_id: str = Field(pattern=r"^[a-z0-9]{26}$")
    name: str = Field(min_length=1, max_length=160)
    inbound_audience: Audience | None = None
    topics: list[Label] = Field(default_factory=list, max_length=30)


class MattermostConfig(Configuration):
    routes: list[Route] = Field(min_length=1, max_length=30)
    poll_seconds: float = Field(default=2, ge=1, le=60)
    max_pages: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def unique_routes(self):
        if len({r.channel_id for r in self.routes}) != len(self.routes):
            raise ValueError("Each Mattermost channel must have one route")
        return self


class MattermostClient:
    def __init__(self, config):
        self.config = config
        self.http = httpx.AsyncClient(timeout=15, follow_redirects=False)
        self.user_id = None

    async def request(self, method, path, **kwargs):
        response = await self.http.request(method, self.config.url + "/api/v4" + path,
            headers={"Authorization": "Bearer " + self.config.token()}, **kwargs)
        if response.status_code >= 300:
            # Error bodies can include credentials/headers from upstream systems.
            raise ServiceError(f"Mattermost HTTP {response.status_code}", response.status_code)
        return response.json()

    async def initialize(self):
        self.user_id = (await self.request("GET", "/users/me"))["id"]

    async def page(self, channel_id, before=None):
        params = {"per_page": 100}
        if before:
            params["before"] = before
        page = await self.request("GET", f"/channels/{channel_id}/posts", params=params)
        return [page["posts"][post_id] for post_id in page["order"]]


class MattermostAdapter:
    def __init__(self, mm, service, store, route):
        self.mm, self.service, self.store, self.route = mm, service, store, route

    async def metadata(self):
        return {"state": "idle", "name": self.route.name}

    async def send(self, text, author, message_id):
        # Read the canonical envelope; native-client setup text is never posted.
        message = await self.service.get("/messages/read", message_id=message_id)
        source = message.get("external_source") or {}
        if source.get("server") == self.mm.config.url and source.get("channel_id") == self.route.channel_id:
            self.store.put("mm:send:" + message_id, {"state": "accepted", "reflected": True})
            return {"state": "accepted", "detail": "Source-channel reflection suppressed"}
        key = "mm:send:" + message_id
        prepared = self.store.get(key)
        if prepared and prepared["state"] == "accepted":
            return {"state": "accepted", "detail": "Mattermost post " + prepared["post_id"]}
        if prepared and prepared["state"] == "sending":
            raise RuntimeError("Mattermost POST outcome uncertain; reconcile before retry")
        if prepared is None:
            root = self.store.get("mm:thread:" + (message.get("reply_to") or ""), "")
            body = {"channel_id": self.route.channel_id, "message": message_text(message),
                    "root_id": root, "props": {"teamcomms_message_id": message_id,
                                                "teamcomms_server": self.service.config.url}}
            prepared = {"state": "prepared", "body": body}
            self.store.put(key, prepared)
        prepared["state"] = "sending"
        self.store.put(key, prepared)
        try:
            post = await self.mm.request("POST", "/posts", json=prepared["body"])
        except ServiceError as error:
            # A gateway/server error can follow a successful write. Only explicit
            # client rejections establish that no post was accepted.
            if error.status in {400, 401, 403, 404, 413, 429}:
                self.store.put(key, {**prepared, "state": "failed", "error": str(error)})
            raise
        self.accept(message_id, post, prepared)
        return {"state": "accepted", "detail": "Mattermost post " + post["id"]}

    def accept(self, message_id, post, prepared):
        if (post["channel_id"] != self.route.channel_id or post["user_id"] != self.mm.user_id
                or post["message"] != prepared["body"]["message"]
                or (post.get("root_id") or "") != prepared["body"]["root_id"]):
            raise ValueError("Mattermost post differs from the prepared destination/body")
        self.store.put_many({
            "mm:send:" + message_id: {**prepared, "state": "accepted", "post_id": post["id"]},
            "mm:post:" + post["id"]: message_id,
            "mm:thread:" + message_id: post.get("root_id") or post["id"],
        })

    async def reconcile_send(self, message_id):
        prepared = self.store.get("mm:send:" + message_id)
        if prepared is None or prepared["state"] == "prepared":
            return {**(prepared or {}), "state": "failed", "error": "No external POST was attempted"}
        if prepared["state"] != "sending":
            return prepared
        before = None
        for _ in range(self.mm.config.max_pages):
            posts = await self.mm.page(self.route.channel_id, before)
            for post in posts:
                props = post.get("props") or {}
                if (post["user_id"] == self.mm.user_id
                        and props.get("teamcomms_message_id") == message_id
                        and props.get("teamcomms_server") == self.service.config.url):
                    self.accept(message_id, post, prepared)
                    return self.store.get("mm:send:" + message_id)
            if len(posts) < 100:
                break
            before = posts[-1]["id"]
        return prepared  # Absence in bounded history is not permission to repost.


class MattermostReceiver(Receiver):
    async def register(self):
        result = await super().register()
        topics = self.service.config.topics
        for topic in set(self.store.get("mm:topics", [])) - set(topics):
            await self.service.post("/subscriptions", {"session_id": self.session_id, "topic": topic, "active": False})
        self.store.put("mm:topics", topics)
        return result

    async def dispatch(self, local):
        if local["phase"] in {"injecting", "uncertain"}:
            delivery = json.loads(local["payload"])
            prepared = await self.adapter.reconcile_send(delivery["message_id"])
            if prepared and prepared["state"] in {"accepted", "failed"}:
                current = (await self.service.get("/deliveries/history", delivery_id=local["id"], limit=1))["delivery"]
                if current["state"] == "uncertain":
                    state = prepared["state"]
                    report = {"receipt_id": str(uuid5(NAMESPACE_URL, f"mm-reconcile:{local['id']}:{current['revision']}:{state}")),
                              "delivery_id": local["id"], "expected_revision": current["revision"],
                              "state": state, "detail": "Mattermost reconciled " + state}
                    current = await self.service.post("/deliveries", report)
                if current["state"] == "accepted" or current.get("acknowledged_at"):
                    self.store.update(local["id"], "done")
                    return
                if current["state"] == "failed":
                    report = {"receipt_id": str(uuid5(NAMESPACE_URL, f"mm-retry:{local['id']}:{current['revision']}")),
                              "delivery_id": local["id"], "expected_revision": current["revision"],
                              "state": "pending", "detail": "Retry after definite Mattermost rejection"}
                    current = await self.service.post("/deliveries", report)
                if current["state"] == "pending":
                    self.store.update(local["id"], "uncertain")
                    self.store.ingest({**delivery, **current}, advance=False)
                    return  # The next cycle retries; no tight failure loop.
        await super().dispatch(local)


class Inbound:
    def __init__(self, mm, service, store, route, session_id):
        self.mm, self.service, self.store, self.route, self.session_id = mm, service, store, route, session_id

    async def publish(self, post):
        if post["channel_id"] != self.route.channel_id:
            raise ValueError("Mattermost returned a post outside the configured channel")
        if (post["user_id"] == self.mm.user_id or post.get("delete_at")
                or post.get("type", "").startswith("system_") or not post.get("message")
                or self.store.get("mm:post:" + post["id"])):
            return
        key = "mm:inbound:" + post["id"]
        body = self.store.get(key)
        if body is None:
            user = await self.mm.request("GET", "/users/" + post["user_id"])
            if user["id"] != post["user_id"]:
                raise ValueError("Mattermost user response does not match post author")
            identity = str(uuid5(NAMESPACE_URL, f"teamcomms-mattermost:{self.mm.config.url}:{self.route.channel_id}:{post['id']}"))
            source = {"server": self.mm.config.url, "channel_id": self.route.channel_id,
                      "post_id": post["id"], "thread_id": post.get("root_id") or post["id"],
                      "user_id": user["id"], "username": user["username"],
                      "kind": "bot" if user.get("is_bot") else "human"}
            body = SendMessage(message_id=identity, sender_session_id=self.session_id,
                audience=self.route.inbound_audience, content=post["message"], external_source=source,
                observed_at=datetime.fromtimestamp(post["create_at"] / 1000, timezone.utc)).model_dump(mode="json")
            # A TC reply requires delivery to this channel session. Inbound roots
            # published by it are source references, not invented TC deliveries.
            root = self.store.get("mm:post:" + post.get("root_id", ""))
            if root and self.store.get("mm:send:" + root):
                body["reply_to"] = root
            self.store.put(key, body)
        self.store.enqueue(body)
        await self.service.post("/messages", body)
        self.store.sent(body["message_id"])
        self.store.put_many({"mm:post:" + post["id"]: body["message_id"],
                            "mm:thread:" + body["message_id"]: post.get("root_id") or post["id"]})

    async def once(self):
        newest = await self.mm.page(self.route.channel_id)
        boundary = self.store.get("mm:cursor")
        if boundary is None:
            self.store.put("mm:cursor", newest[0]["id"] if newest else "")
            self.store.put("mm:coverage", "Started at channel end; earlier posts not imported")
            return
        collected, posts, found = [], newest, False
        for page_number in range(self.mm.config.max_pages):
            for post in posts:
                if post["id"] == boundary:
                    found = True
                    break
                collected.append(post)
            if found or len(posts) < 100:
                found = found or not boundary
                break
            if page_number + 1 < self.mm.config.max_pages:
                posts = await self.mm.page(self.route.channel_id, posts[-1]["id"])
        if not found:
            raise RuntimeError("Mattermost coverage gap: saved post missing within bounded history; cursor retained")
        for post in reversed(collected):
            await self.publish(post)
            self.store.put("mm:cursor", post["id"])

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.once()
                self.store.put("mm:inbound_error", "")
            except (ServiceError, httpx.HTTPError, ValueError, RuntimeError, KeyError) as error:
                self.store.put("mm:inbound_error", f"{type(error).__name__}: {error}"[:2000])
                logger.warning("Mattermost inbound route %s: %s", self.route.name, error)
            await Receiver.pause(stop, self.mm.config.poll_seconds)


async def run_route(tc_config, mm_config, route, config_path, stop):
    from .cli import state_path
    identity = str(uuid5(NAMESPACE_URL, mm_config.url + "/channels/" + route.channel_id))
    directory = state_path(tc_config, "mattermost", identity)
    with session_lock(directory):
        store = Store(directory)
        # Platform destinations do not run AI startup context or model capture.
        config = tc_config.model_copy(update={"greeting": False, "bootstrap": None,
                                               "dialog_capture": False, "group_ids": [], "topics": route.topics})
        service, mm = ServiceClient(config), MattermostClient(mm_config)
        tasks = []
        try:
            await mm.initialize()
            prior = store.get("mm:bot")
            if prior and prior != mm.user_id:
                raise ValueError("Mattermost bot changed; select a new state directory")
            store.put("mm:bot", mm.user_id)
            store.put("instructions", True)
            adapter = MattermostAdapter(mm, service, store, route)
            receiver = MattermostReceiver(service, store, adapter,
                {"native_id": identity, "client": "mattermost", "host": config.host,
                 "name": route.name, "delivery_mode": "stream", "capabilities": ["comms", "mattermost"]}, config_path)
            await receiver.register()
            tasks.append(asyncio.create_task(receiver.run(stop)))
            if route.inbound_audience:
                tasks.append(asyncio.create_task(Inbound(mm, service, store, route, receiver.session_id).run(stop)))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Mattermost route task ended: %s", result)
            await service.close()
            await mm.http.aclose()
            store.close()


async def run(tc_config, mm_config, config_path):
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)

    async def managed(route):
        while not stop.is_set():
            try:
                await run_route(tc_config, mm_config, route, config_path, stop)
            except Exception as error:
                logger.error("Mattermost route %s unavailable (%s): %s", route.name, type(error).__name__, error)
            await Receiver.pause(stop, 5)
    await asyncio.gather(*(managed(route) for route in mm_config.routes))
