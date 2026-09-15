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
    for field in ("notify_llm", "notify_llm_reason", "notify_llm_source", "notify_llm_event_id"):
        body.pop(field)
    store.enqueue(body)
    result = await service.post("/messages", body)
    store.sent(identity)
    return result


async def notify_llm(service, store, *, source, event_id, reason, content, audience,
                     observed_at, topic=""):
    """Deliberately request model attention; persist exact source-event retries first.

    ``service`` is an authenticated ServiceClient; ``store`` is its private Store.
    Uses the existing installation token and scopes. No recipient is inferred.
    """
    import json
    from teamcomms.comms.schemas import NotifyLLM
    identity = str(uuid5(NAMESPACE_URL, "teamcomms-notify-llm:" + json.dumps([source, event_id])))
    body = NotifyLLM(message_id=identity, audience=audience, content=content,
        topic=topic, observed_at=observed_at, notify_llm_reason=reason,
        notify_llm_source=source, notify_llm_event_id=event_id).model_dump(mode="json")
    body.pop("external_source")
    store.enqueue(body)
    result = await service.post("/notify-llm", body)
    store.sent(identity)
    return result
