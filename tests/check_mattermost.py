"""Focused platform/watcher recovery checks; no native clients or live services."""

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

import httpx
from pydantic import ValidationError

from teamcomms.comms.schemas import SendMessage
from teamcomms.connectors.client import ServiceError
from teamcomms.connectors.config import Configuration
from teamcomms.connectors.mattermost import Inbound, MattermostAdapter, MattermostReceiver, Route
from teamcomms.connectors.state import Store
from teamcomms.connectors.watcher import publish_event

CHANNEL = "a" * 26
BOT = "b" * 26
HUMAN = "h" * 26


class TC:
    def __init__(self, directory):
        self.config = Configuration(url="https://tc.example", token_file=directory / "token", greeting=False)
        self.messages = {}
        self.published = []
        self.deliveries = {}
        self.lose_response = False
        self.subscriptions = []

    async def get(self, path, **query):
        if path == "/messages/read":
            return self.messages[query["message_id"]]
        if path == "/deliveries/history":
            return {"delivery": self.deliveries[query["delivery_id"]]}
        raise AssertionError(path)

    async def post(self, path, body):
        if path == "/sessions":
            return {"session_id": str(uuid4())}
        if path == "/subscriptions":
            self.subscriptions.append(body)
            return body
        if path == "/messages":
            self.published.append(body)
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("response lost")
            return {"message_id": body["message_id"], "deliveries": []}
        if path == "/deliveries":
            current = self.deliveries[body["delivery_id"]]
            if current["revision"] != body["expected_revision"]:
                raise ServiceError("stale", 409)
            current.update(state=body["state"], revision=current["revision"] + 1)
            return dict(current)
        raise AssertionError(path)


class MM:
    def __init__(self):
        self.config = SimpleNamespace(url="https://mm.example", max_pages=2, poll_seconds=2)
        self.user_id = BOT
        self.posts = []
        self.sends = 0
        self.lose_response = False
        self.reject = False

    async def request(self, method, path, **kwargs):
        if path.startswith("/users/"):
            return {"id": path.split("/")[-1], "username": "operator", "is_bot": False}
        assert path == "/posts" and method == "POST"
        self.sends += 1
        if self.reject:
            raise ServiceError("Mattermost HTTP 429", 429)
        post = {**kwargs["json"], "id": str(uuid4()), "user_id": BOT, "create_at": 1000}
        self.posts.insert(0, post)
        if self.lose_response:
            self.lose_response = False
            raise httpx.ReadTimeout("response lost")
        return post

    async def page(self, channel_id, before=None):
        posts = [p for p in self.posts if p["channel_id"] == channel_id]
        start = next((i + 1 for i, p in enumerate(posts) if p["id"] == before), 0)
        return posts[start:start + 100]


class PlatformChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.store = Store(self.directory)
        self.store.put("instructions", True)
        self.tc, self.mm = TC(self.directory), MM()
        self.route = Route(channel_id=CHANNEL, name="live", topics=["events"],
                           inbound_audience={"topics": ["events"]})
        self.adapter = MattermostAdapter(self.mm, self.tc, self.store, self.route)
        self.receiver = MattermostReceiver(self.tc, self.store, self.adapter, {}, "config")
        self.receiver.session_id = str(uuid4())

    async def asyncTearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def delivery(self, **fields):
        message_id, delivery_id = str(uuid4()), str(uuid4())
        message = {"message_id": message_id, "author_id": str(uuid4()),
                   "author": {"name": "watcher", "kind": "program"}, "reply_requested": False,
                   "content": "Workflow finished", "reply_to": None, **fields}
        self.tc.messages[message_id] = message
        delivery = {"delivery_id": delivery_id, "message_id": message_id, "session_id": self.receiver.session_id,
                    "message": message, "revision": 1, "state": "pending", "sequence": 1, "acknowledged_at": None}
        self.tc.deliveries[delivery_id] = dict(delivery)
        self.store.ingest(delivery)
        return delivery

    async def test_lost_post_response_reconciles_after_restart_without_reposting(self):
        delivery = self.delivery()
        self.mm.lose_response = True
        await self.receiver.dispatch_pending()
        self.assertEqual(self.store.pending()[0]["phase"], "uncertain")
        self.store.close()
        self.store = Store(self.directory)
        self.adapter.store = self.receiver.store = self.store
        await self.receiver.dispatch_pending()
        self.assertEqual(self.mm.sends, 1)
        self.assertEqual(self.tc.deliveries[delivery["delivery_id"]]["state"], "accepted")
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.get("mm:thread:" + delivery["message_id"]), self.mm.posts[0]["id"])

    async def test_definite_rejection_retries_without_duplicate_post(self):
        delivery = self.delivery()
        self.mm.reject = True
        await self.receiver.dispatch_pending()
        await self.receiver.dispatch_pending()
        self.assertEqual(self.tc.deliveries[delivery["delivery_id"]]["state"], "pending")
        self.mm.reject = False
        await self.receiver.dispatch_pending()
        self.assertEqual(self.mm.sends, 2)
        self.assertEqual(len(self.mm.posts), 1)

    async def test_uncertain_absence_and_forged_property_do_not_authorize_retry(self):
        delivery = self.delivery()
        self.mm.lose_response = True
        await self.receiver.dispatch_pending()
        self.mm.posts[0]["user_id"] = HUMAN
        await self.receiver.dispatch_pending()
        self.assertEqual(self.mm.sends, 1)
        self.assertEqual(self.store.pending()[0]["phase"], "uncertain")

    async def test_reply_thread_and_external_author_survive_replay(self):
        root = self.delivery()
        await self.receiver.dispatch_pending()
        root_post = self.mm.posts[0]
        inbound = Inbound(self.mm, self.tc, self.store, self.route, self.receiver.session_id)
        post = {"id": "human-post", "channel_id": CHANNEL, "user_id": HUMAN,
                "message": "Acknowledged by operator", "root_id": root_post["id"], "create_at": 2000}
        await inbound.publish(post)
        await inbound.publish(post)
        self.assertEqual(len(self.tc.published), 1)
        body = self.tc.published[0]
        self.assertEqual(body["reply_to"], root["message_id"])
        self.assertEqual(body["external_source"]["user_id"], HUMAN)
        self.assertEqual(body["external_source"]["authority"], "connector-reported")
        reply = self.delivery(reply_to=body["message_id"])
        await self.receiver.dispatch_pending()
        self.assertEqual(self.mm.posts[0]["root_id"], root_post["id"])
        await inbound.publish(self.mm.posts[0])
        self.assertEqual(len(self.tc.published), 1)

    async def test_inbound_lost_response_freezes_body_and_cursor_gap_is_visible(self):
        inbound = Inbound(self.mm, self.tc, self.store, self.route, self.receiver.session_id)
        await inbound.once()  # Empty channel baseline.
        post = {"id": "human-post", "channel_id": CHANNEL, "user_id": HUMAN,
                "message": "Original", "root_id": "", "create_at": 2000}
        self.mm.posts = [post]
        self.tc.lose_response = True
        with self.assertRaises(httpx.ReadTimeout):
            await inbound.once()
        post["message"] = "Edited during retry"
        await inbound.once()
        self.assertEqual(self.tc.published[0], self.tc.published[1])
        self.assertEqual(self.store.get("mm:cursor"), "human-post")
        self.mm.posts = []
        with self.assertRaisesRegex(RuntimeError, "coverage gap"):
            await inbound.once()
        self.assertEqual(self.store.get("mm:cursor"), "human-post")

    async def test_source_reflection_does_not_post(self):
        self.delivery(external_source={"server": self.mm.config.url, "channel_id": CHANNEL,
            "platform": "mattermost", "username": "operator", "kind": "human", "post_id": "post"})
        await self.receiver.dispatch_pending()
        self.assertEqual(self.mm.sends, 0)
        self.assertEqual(self.store.pending(), [])

    async def test_crash_before_post_preparation_can_retry(self):
        delivery = self.delivery()
        self.tc.deliveries[delivery["delivery_id"]].update(state="uncertain", revision=2)
        self.store.update(delivery["delivery_id"], "injecting")
        await self.receiver.dispatch_pending()
        await self.receiver.dispatch_pending()
        self.assertEqual(self.mm.sends, 1)
        self.assertEqual(self.store.pending(), [])

    async def test_watcher_retry_is_stable_and_changed_event_conflicts(self):
        event = dict(source="monitor", event_id="applog:123", content="Observed completion",
                     audience={"topics": ["events"]}, observed_at="2026-09-14T08:00:00Z")
        self.tc.lose_response = True
        with self.assertRaises(httpx.ReadTimeout):
            await publish_event(self.tc, self.store, **event)
        await publish_event(self.tc, self.store, **event)
        self.assertEqual(self.tc.published[0], self.tc.published[1])
        with self.assertRaises(ValueError):
            await publish_event(self.tc, self.store, **{**event, "content": "Changed"})
        self.assertEqual(self.store.outgoing(), [])

    async def test_source_provenance_cannot_replace_author_or_claim_authority(self):
        source = dict(server="https://mm.example", channel_id=CHANNEL, post_id="post", user_id=HUMAN,
                      username="operator", kind="human")
        base = dict(message_id=str(uuid4()), content="Hello", audience={"topics": ["events"]})
        with self.assertRaises(ValidationError):
            SendMessage(**base, author_id=str(uuid4()), external_source=source)
        with self.assertRaises(ValidationError):
            SendMessage(**base, external_source={**source, "authority": "operator-approved"})

    async def test_removed_route_topics_are_unsubscribed(self):
        self.store.put("mm:topics", ["old-events"])
        self.tc.config.topics = ["events"]
        await self.receiver.register()
        self.assertEqual(self.store.get("mm:topics"), ["events"])
        self.assertEqual([s for s in self.tc.subscriptions if s.get("active") is False],
                         [{"session_id": self.receiver.session_id, "topic": "old-events", "active": False}])


if __name__ == "__main__":
    unittest.main()
