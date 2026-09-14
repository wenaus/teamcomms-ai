"""Stable, durable publication of events from an existing watcher."""

from uuid import NAMESPACE_URL, uuid5

from teamcomms.comms.schemas import SendMessage


async def publish_event(service, store, *, source, event_id, content, audience,
                        observed_at, topic=""):
    if not source.strip() or not event_id.strip():
        raise ValueError("Watcher source and event ID must be nonempty")
    import json
    identity = str(uuid5(NAMESPACE_URL, "teamcomms-watcher:" + json.dumps([source, event_id])))
    body = SendMessage(message_id=identity, audience=audience, content=content,
                       kind="notification", topic=topic, observed_at=observed_at).model_dump(mode="json")
    body.pop("external_source")
    store.enqueue(body)
    result = await service.post("/messages", body)
    store.sent(identity)
    return result
