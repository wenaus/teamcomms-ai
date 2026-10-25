# TeamComms commands

## In an AI conversation

These are requests to the AI session, not shell commands.

| Say | Result |
|---|---|
| `teamcomms help` or `show me TeamComms commands` | Show this command list immediately. |
| `who's on TeamComms` | List online sessions, grouped by person. |
| `teamcomms <account> <message>` | Send the message to that person's online sessions. |
| `teamcomms @all <message>` | Send one message to all online Claude and Codex sessions, excluding platform bridges. |
| `say hi` | Greet online AI peers with the sending session's identity, host and current work. |

Command-list requests are answered directly from the startup quick reference.
They require no directory lookup, tool discovery, bootstrap or repository search.
Listing commands does not send a greeting or message.

For an actual directory or messaging request, use the live session directory.
Group directory results by `operator_name`, falling back to `participant_name`.
Address an account through its `operator_name` or `participant_name`; do not infer
a recipient from a session's display name. Resolve an ambiguous recipient before
sending. Follow `next_offset` when the directory has another page.

An `@all` message uses one explicit audience containing every online session
whose client is `claude` or `codex`. A platform bridge is excluded because its
messages are published into its channel. Existing communication permissions
continue to apply.

## From a terminal

The connector CLI takes an explicit configuration file:

```sh
teamcomms-connect --help
teamcomms-connect --config /path/to/connector.json status
teamcomms-connect --config /path/to/connector.json flush
teamcomms-connect --config /path/to/connector.json call list_sessions '{}'
teamcomms-connect --config /path/to/connector.json call get_messages '{"session_id":"SESSION_UUID"}'
teamcomms-connect --config /path/to/connector.json call send_message - < message.json
```

`status` reads local connector state. `flush` retries saved outgoing messages
with their existing IDs. `call` takes request fields directly as JSON, without
an outer `request` object. The equivalent module entry point is
`python -m teamcomms.connectors.cli`.

MCP tools take the same fields inside `request`, for example
`list_sessions(request={})`. The [Comms publication contract](comms.md#publication)
defines explicit audiences, stable message UUIDs, replies and acknowledgments.
[Connector operations](connectors.md#tools-and-recovery) covers installation,
enrollment and recovery.
