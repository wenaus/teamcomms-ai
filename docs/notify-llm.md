# Notify LLM

**Notify LLM** requests model attention deliberately. It is the sparsest
notification category: use it when a named recipient needs to consider something,
with a reason explaining why. It is off by default and independent of severity.
An alarm, ordinary completion notice or live-feed post does not select it.
The TeamComms home page introduces this command with an expandable JSON example,
recipient-directory link and runnable Python example. In SWF, open **System →
TeamComms** to find it.

## Script function

Install the connectors extra and use the existing host-issued program credential.
The callable is `teamcomms.connectors.watcher.notify_llm`. It is asynchronous:

```python
await notify_llm(service, store,
    source="swf-monitor:epic-devcloud.org/prod",
    event_id="applog:SOURCE_EVENT_ID",
    reason="The assigned session needs to review this blocked work.",
    content="The relevant finding, requested consideration, and source link.",
    audience={"session_ids": ["SELECTED_TC_SESSION_UUID"]},
    observed_at="2026-09-15T12:00:00+00:00")
```

`service` is an authenticated `ServiceClient`; `store` is its private durable
outgoing `Store`. [The runnable example](../examples/notify-llm.py) loads both
from an existing connector configuration. Required fields are `source`,
`event_id`, `reason`, `content`, `audience` and timezone-aware `observed_at`.
`topic` is optional. Keep source, event ID, observation time, audience and body
unchanged on retry. Use the actual source timestamp, not the retry time.

The CLI accepts those same fields in a private JSON file or stdin (`-`):

```sh
teamcomms-connect --config /path/to/program.json notify-llm /path/to/notification.json
teamcomms-connect --config /path/to/program.json flush
```

The helper derives a UUID from the installation-qualified source and event ID,
saves the exact body before sending, and marks the outgoing row sent only after a
successful response. Lost responses retain the same UUID/body for retry. A
changed payload conflicts rather than duplicating an event. `flush` retries the
saved queue without changing intent. This namespace differs from `publish-event`,
so an existing recorded event can be escalated explicitly without rewriting it.
No continuously running watcher or automatic escalation is enabled here.

## Service contract

HTTP `POST /api/comms/notify-llm` and MCP `notify_llm(request={...})` share
`comms:write` and the existing authentication, CSRF, authorship, audience and
receipt rules. CLI `call notify_llm` takes the request fields directly, without
an outer `request` object. These lower-level calls require the caller's stable
`message_id`, explicit `audience`, `content`, `observed_at`,
`notify_llm_reason`, `notify_llm_source` and `notify_llm_event_id`.
They fix `kind=notification` and `notify_llm=true`.

The ordinary send API also accepts that explicit intent. Without it,
notifications and externally sourced messages exclude AI sessions from the
resolved audience. Other eligible destinations, including a Mattermost bridge,
can still receive the event. Direct peer conversations and work offers retain
their existing behavior; producers must label automated notices as notifications.
No automatic text or severity classifier grants model attention.

Selection must resolve to at least one eligible AI session or returns 409 with
no publication. The resolved audience is frozen at first publication. Direct
session IDs can name offline sessions for durable delivery; host, participant,
group, resource and topic selectors resolve online eligible sessions at send
time. Choose narrow, maintained subscriptions when using selectors. Bounds are
100 destinations, 16,000 content characters, 1,000 reason characters, 2,048 source
characters and 240 event-ID characters. Exact retries return the original result.

Transport and consideration receipts use code only. A native socket write is not
model consideration; only acknowledgment or a reply establishes that. Notify LLM
does not grant operational authority, execute a job, or enqueue an Inflight offer.
It bypasses routine batching/quiet presentation, subject to native busy/idle
scheduling. Existing committed messages and receipts remain unchanged.

## Capcom and the live feed

Capcom's notice composer has one **Notify LLM** checkbox, initially off. Selecting
it requires a reason and chosen AI sessions. The API takes `notify_llm=true`,
`notify_llm_reason` and `audience` on `publish_notice`; its topic and immutable
notice UUID supply the source identity. Recording and publication are atomic.
Without selection, ordinary notices remain available for human reading; urgency
alone does not create an AI delivery.

A Mattermost post explicitly selects the same category through exact leading
text `Notify LLM:` or boolean post property `notify_llm: true`. The bridge uses
its configured inbound audience and verified post/user provenance. Optional
`notify_llm_reason` supplies the reason; otherwise the recorded reason says the
Mattermost author selected Notify LLM. Ordinary posts are skipped while advancing
the saved cursor. Editing an old post does not trigger notification: publish a
new explicitly selected post instead.

The bridge's own bot posts and reflected TC posts remain excluded even if marked.
A script using that same live-feed bot should call the function above, including
the bridge in its explicit audience when a feed post is desired. Outgoing
selected posts display **Notify LLM** and retain the category in post properties.
External authorship is source provenance, never operator approval. Restart the
bridge on package upgrade to activate inbound selection, retaining its state.
