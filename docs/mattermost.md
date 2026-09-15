# Mattermost and watcher connectors

The Mattermost connector maps each explicitly configured channel to a durable
Comms session. Topic subscriptions deliver the same canonical event independently
to that channel. AI destinations require an explicit **Notify LLM** selection.
Each route maintains its own cursor,
outbox, post mappings, and delivery receipts in private local state.

The connector uses existing TeamComms host authentication and a Mattermost bot
token authorized for the selected channels. It uses outbound HTTPS only. Tokens
are read from private files; native AI clients retain their existing credentials.

## Authorship and routing

Inbound messages retain the authenticated TC transport author. The optional
`external_source` envelope records Mattermost server, channel, post, root thread,
external user ID, name, and kind. Its authority is `connector-reported`: it is
source provenance, not a TC identity mapping or operator authorization. The
bridge obtains these fields from authenticated Mattermost post/user reads.
Ordinary messages cannot replace their authenticated TC author.

Each channel has explicit inbound audience and outbound topic subscriptions.
Inbound publication requires **Notify LLM**: either the exact leading text
`Notify LLM:` or post property `notify_llm: true`. Ordinary posts advance the
bridge cursor without TC publication or model calls. The selection is independent
of severity. See [Notify LLM](notify-llm.md) for the script function and bounds.
Replies to a bridged TC message use the channel session as destination. Stored
post mappings translate TC reply references to Mattermost `root_id`, preserving
the original thread. Unmapped external roots remain explicit provenance without
asserting a TC reply to an unread message.

Bot-owned posts and posts already mapped by this bridge are excluded from inbound
publication. Source-channel reflections are receipted without reposting. Canonical
TC message IDs in Mattermost post properties support positive reconciliation of
an uncertain send; a property on another user's post is not accepted as proof.

## Recovery and bounds

Incoming posts are captured from the end of the configured channel at first
startup; existing channel history is not imported. Later polling walks bounded
pages back to the saved post, then publishes in chronological order. If that
boundary cannot be found within the configured limit, the route records a
coverage error and retains its cursor. Reconfiguration or an explicit operator
decision is required to skip the gap. Edits and deleted posts are not synchronized.

Outgoing posts use a durable prepared/sending/accepted record. Definite HTTP
rejections can retry independently. A lost response or restart during a POST
requires finding the bot's matching post before marking acceptance; absence in a
bounded search leaves delivery uncertain, without blindly creating another post.
Local state and canonical delivery status expose these outcomes.

Mattermost API references: [create post](https://docs.mattermost.com/api/reference/create-post),
[channel posts](https://docs.mattermost.com/api/reference/get-posts-for-channel),
and [user identity](https://docs.mattermost.com/api/reference/get-user).

## Watcher publication

Ordinary watcher notifications exclude AI destinations, even when a subscribed
topic includes them. Use [Notify LLM](notify-llm.md) to request model attention
deliberately. Watchers publish notifications with an explicit audience and a
stable source event identifier. The publisher derives one message UUID from source and event
ID and persists the complete request before sending. Retries reuse that request;
changing an existing event's payload is a conflict. An observation timestamp
comes from the source event rather than the time of a retry. Production state
changes remain the responsibility of the source application.

## Commands and configuration

Install the `connectors` extra. Copy [the Mattermost example](../examples/mattermost.json)
outside the checkout, specifying the HTTPS server, private bot token file, and
channel routes. Use a separate TC connector config with existing host credentials
and a private state directory. Start the bridge with:

```sh
teamcomms-connect --config /path/to/tc-connector.json mattermost --routes /path/to/mattermost.json
```

Each route registers one TC session with client `mattermost` and subscribes to
its listed topics. `inbound_audience` enables inbound routing; omit it for an
outbound-only destination. Participants subscribe their own AI sessions to the
same event topic using `subscribe`. No routes or subscriptions are inferred from
other bots' configuration. Run the bridge under the host's existing documented
process manager; SIGTERM stops it while preserving private state.

The event publisher reads a JSON file or `-` for stdin:

```sh
teamcomms-connect --config /path/to/tc-program.json publish-event /path/to/event.json
```

Required fields are `source`, `event_id`, `content`, `observed_at` (timezone-aware
ISO timestamp), and `audience`; `topic` is optional. See [the event example](../examples/watcher-event.json).
Select the event and its destinations before freezing this file. Include the
source event identifier and evidence link in content. A lost response can be
retried with the same file or the existing `flush` command. `status` reports
outgoing messages, delivery uncertainty, and Mattermost coverage/error state.
