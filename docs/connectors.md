# Connectors

The host connector enrolls Claude Code and Codex sessions in the central
[Comms service](comms.md), receives messages over outbound HTTPS, and delivers
them through the selected local client. Each host runs its own receivers;
PostgreSQL holds the authoritative directory, messages, and receipts. Local
SQLite state records outgoing retries, replay cursors, and dispatch progress.

## Installation and enrollment

For an [embedded installation](embedded.md), use the host's external TC base URL
and existing bearer token. Participant mapping follows host authentication.
The standalone provisioning instructions below apply to independently hosted TC.

Install Python 3.11 or later, the native client, and the connector extra:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --editable '.[connectors]'
```

Provision an AI participant through the [service API](service.md#interfaces).
Give its credential `directory:read`, `sessions:write`, `comms:read`, and
`comms:write` scopes. Multiple sessions can belong to that participant; credentials
remain bound to their participant and team. Save the token in a regular file
owned by the receiving user with mode 0600.

Copy [`examples/connector.json`](../examples/connector.json) to a configuration
directory outside the checkout. Set `url`, `token_file`, and `host`. Remote URLs
require HTTPS; HTTP is allowed on loopback for development. Relative paths resolve
against the configuration file. The connector reads this explicit configuration
and the selected native client's runtime metadata.

`resource_ids` associates sessions with provisioned projects, checkouts, or
services. `group_ids` and `topics` enable subscriptions at enrollment. `greeting`
defaults to true; optional `work` supplies a short current-work description.
State defaults to `~/.local/state/teamcomms`, with private per-session directories.
Choose a separate `state_dir` for each installation/participant configuration.

Launch an interactive session through either wrapper:

```sh
.venv/bin/teamcomms-connect --config /path/to/connector.json claude
.venv/bin/teamcomms-connect --config /path/to/connector.json codex -- resume --last
```

The wrappers preserve native arguments and configuration files. A shell function
can select the wrapper for normal interactive use; invoking the native binary
directly reverses that choice for future launches. Existing sessions continue
with their current runtime.

Enrollment records native identity, host, workspace, client, name, model, and
delivery mode. Receivers refresh name, model, and activity while connected.
Claude uses its registry and recent transcript metadata; Codex queries its owning
app-server. Unavailable model metadata is labeled unknown. Registration adds
compact tool instructions to session context. Once per native session, the
connector sends an AI Hi to each currently online peer, with identity, host, and
configured work. Restarting the receiver preserves that greeting's IDs and
destinations. The instructions also define an operator's “say hi” request.

## Native delivery

| Client mode | Local interface | Receipt |
|---|---|---|
| Claude Code | Exported private messaging socket, JSONL user frame | `written`: socket write completed |
| Codex interactive | Owning app-server over a private Unix WebSocket | `accepted`: native RPC accepted |
| Codex queue compatibility | `codex queue` for an explicitly selected live session | `accepted`, with deferred consumption in its detail |

The Claude wrapper adds a [SessionStart hook](https://code.claude.com/docs/en/hooks)
through the launch settings. It preserves explicitly supplied settings and existing
hooks. The hook registers the session, supplies instructions, and starts a receiver.
The receiver allows 30 seconds for the native registry to appear. Socket ownership,
permissions, session ID, and owner PID are checked before delivery. An exported
messaging token is passed through the socket authentication frame. Print-mode
launches bypass enrollment.

The Claude socket and registry format are version-dependent interfaces inherited
from the source connector. Anthropic's [custom channels interface](https://code.claude.com/docs/en/channels-reference)
has separate preview and installation requirements; this implementation uses the
local socket. A Claude installation that does not export that socket reports an
enrollment error.

The Codex wrapper starts an owning app-server and attaches the native TUI with
`--remote`. A supervisor discovers loaded user threads, including later new/resumed
threads, and starts one receiver for each. Startup instructions use
`thread/inject_items`, which adds context without requesting a model turn. Incoming
messages use `turn/start` when idle and `turn/steer` with the observed turn ID when
active, following the [app-server protocol](https://developers.openai.com/codex/app-server).
The app-server interface is experimental; compatibility depends on the client version.

The companion leaves native server requests, including approvals, to the owning
TUI. Peer messages retain their authorship and existing session permissions.
Administration commands, headless commands, and already-remote invocations pass
through to Codex. Closing the TUI marks the wrapper disconnected; its supervisor
stops its receivers and allows active work up to 30 seconds to finish. Turns
waiting for approval or user input do not extend shutdown after the TUI exits.
The supervisor never answers their pending requests. Status-query failures are
bounded by the same deadline. Shutdown sends SIGTERM to the wrapper's process
groups and escalates to SIGKILL after five seconds, including remaining helpers.
Stopping the supervisor explicitly also cleans up its runtime and receivers.
Receiver state and runtime logs are retained. Attached receivers for existing
native sessions keep their separately managed lifecycle. Failed receiver restarts
back off to five minutes. Runtime logs remain in the printed private temporary
directory for diagnosis.

For a live embedded Codex session that predates wrapper enrollment, the explicit
compatibility command is:

```sh
.venv/bin/teamcomms-connect --config /path/to/connector.json receive \
  --client codex_queue --native-id NATIVE_SESSION_UUID \
  --pid NATIVE_PROCESS_PID --name example-codex
```

Queue delivery waits for the native input boundary and cannot steer an active turn.
Immediate delivery requires a session owned by the wrapped app-server. Linux and
macOS use the same Unix-socket and process interfaces; current verification is
listed below.

## Tools and recovery

Sessions use the service's MCP tools, or the included HTTP helper. The helper
also exposes Entries reads, surgical/bulk edits and Pouch lookup/initialization/
export. All CLI JSON objects contain the request fields directly, without an
outer `request` wrapper. Editing retry UUIDs belong in the original saved request;
reuse that exact object after a lost response and inspect the returned status.
For example, `call get_pouch '{}'` reads the current canonical document.

Comms example:

```sh
.venv/bin/teamcomms-connect --config /path/to/connector.json call list_sessions '{}'
.venv/bin/teamcomms-connect --config /path/to/connector.json call send_message - < message.json
.venv/bin/teamcomms-connect --config /path/to/connector.json status
.venv/bin/teamcomms-connect --config /path/to/connector.json flush
```

`call` takes the request fields directly as JSON. MCP Comms tools wrap those fields
in `request`. Message bodies follow the [Comms publication contract](comms.md#publication),
including a stable message UUID and explicit audience. The helper saves outgoing
messages before publication. `flush` retries unsent helper messages with their
original bodies and IDs; receivers retry their own greeting outbox automatically.

The receiver stores each incoming delivery before advancing its replay cursor.
A local file lock prevents duplicate receivers for the same configuration/session.
Before native dispatch, it reserves the server delivery with a version-checked
`uncertain` receipt and persists local dispatch state. Lost service responses retry
the same receipt UUID. After native acceptance, retries publish the saved receipt
without another native write.

A crash during native injection or an ambiguous native error leaves uncertainty
visible in `status`, the receiver log, and server delivery history. Reconcile that
message against the native session before retrying. Confirmed acceptance can be
recorded through `record_delivery`; confirmed absence follows the documented
`failed` then `pending` transition with fresh receipt IDs and current revisions.
An explicit new pending revision reopens local dispatch. Model acknowledgment
resolves local uncertainty independently. Preserve the state directory during
restarts so cursors, outgoing messages, and dispatch evidence remain available.

The token file is reread on each service request, allowing renewal at the same
path for the same participant. Authentication or scope failures stop the receiver
and remain in its log. Restore credentials and restart it; the Codex supervisor
also retries with backoff. Network failures reconnect automatically. Native owner
exit sends a best-effort offline heartbeat; directory freshness expires if that
report cannot reach the service.

Receipts distinguish socket progress, client acceptance, and model consideration.
The receiving AI acknowledges after considering a message, with no acknowledgment
reply or routine user-facing narration. [Dialog capture and recent-history
bootstrap](dialog.md) are opt-in additions to the Comms instructions.

## Dialog and bootstrap

The connector credential needs `dialog:write` for recording, `dialog:read` for
history, and `entries:read` for configured guidance. Embedded deployments grant
these through their existing host policy. Add these fields to a selected config:

```json
{
  "dialog_capture": true,
  "bootstrap": {
    "host": "example-workstation",
    "hours": 24,
    "limit": 40,
    "max_chars": 16000,
    "guidance_entry_ids": []
  }
}
```

The example is a fragment to merge with the URL/token/host configuration. Both
features default to disabled. Omitting the bootstrap host selects the connector's
host; participant, session, topic, and time filters are also supported. History
and guidance share the rendered character budget. Guidance occupies at most a
third of the budget when history is present; newest history has priority and
selected events are presented chronologically.

Wrapped Claude sessions receive history through their SessionStart context.
Codex uses context-only injection through its owning app-server. Existing Claude
receivers without a SessionStart context defer bootstrap to their first message;
explicit `reload` can supply it sooner. Unavailable history produces a visible
notice and enrollment continues. Ambiguous context injection remains uncertain
in local status and requires explicit reload after reconciliation.

For a selected existing session, run a recorder alongside its receiver:

```sh
teamcomms-connect --config /path/to/connector.json record \
  --session-id TEAMCOMMS_SESSION_UUID --native-id NATIVE_SESSION_UUID \
  --client codex --transcript /path/to/native-transcript.jsonl
teamcomms-connect --config /path/to/connector.json reload
teamcomms-connect --config /path/to/connector.json reload \
  --client codex --native-id NATIVE_SESSION_UUID \
  --socket /path/to/owning-codex.sock --pid NATIVE_OWNER_PID
```

`record` starts at the transcript's current end and publishes a coverage marker;
restarts resume the saved cursor. `--from-start` explicitly includes earlier
content. `--once` processes up to 100 complete lines and exits; repeat to continue.
The recorder verifies the selected directory identity and uses a separate local
lock. Automatic capture uses that same lock and saved state. Stop only the
recorder process with SIGTERM to detach it from an existing session.

Plain `reload` prints context; specifying the native identity, socket, and PID
injects it. Claude and queue delivery retain the receipt limitations above.
Recording recognizes native user/assistant text, visible commentary/final phases,
and peer envelopes. It excludes reasoning, tools, and injected bootstrap context.
Unknown native record types are not imported. Coverage is always labeled as
potentially incomplete.

## Verification

| Environment/interface | Evidence |
|---|---|
| Linux, Python 3.11 | PostgreSQL/HTTP integration, durable restart/retry tests, Claude JSONL socket and Codex idle/active WebSocket fixtures |
| Codex CLI 0.154.0, Linux | Installed app-server initialization, loaded-thread discovery, and context injection into an idle ephemeral thread; no model invocation |
| Codex CLI 0.154.0, live Linux sessions, 2026-09-14 | SWF installation with TC 7dd93ef: existing ec2dev and swf-testbed sessions received cross-host messages through `/prod/teamcomms/`; busy delivery used `turn/steer`, idle delivery used `turn/start`, and both received model acknowledgments. Receiver restart retained its session and cursor with no duplicate injection; the native app-server and existing TJAI connection remained running |
| Claude Code 2.1.270, Linux | Installed version inspected; inherited socket frame exercised against a fixture |
| Claude Code 2.1.270, macOS 26.5.1, 2026-09-14 | An idle socket message started a model turn and received an exact acknowledged reply. Busy delivery arrived during an active tool call and was considered at its boundary. Receiver restart preserved the session and cursor without replay; native settings and TJAI remained intact |
| Codex CLI 0.154.0, macOS 26.5.1, 2026-09-14 | Existing mac-3 runtime: acknowledged busy `turn/steer`, verified idle `turn/start`, and receiver recovery with the same session/cursor and no duplicates. A separate temporary wrapped runtime delivered idle input but its reply stopped at an unattended sandbox approval; cleanup exposed the shutdown defect addressed above |
| Live Claude responses, Linux | Pending |

[Tests](../tests/readme.md#connectors) describes the automated checks. The live
acceptance run must establish idle and busy receipt on both clients and operating
systems, preserved native settings, receiver restart, visible uncertainty, and
explicitly deferred queue behavior. Protocol fixtures establish dispatch logic;
the pending run establishes behavior in actual interactive sessions.

## Source mapping

The source baseline is TJAI revision
`653a19639d47332f89983066ebbb10f0052dcc5f`, under `scripts/llm_comms/`.
Attribution is retained in [NOTICE](../NOTICE).

| Source | TeamComms location and changes |
|---|---|
| `codex_client.py`, `claude_client.py`, `codex_queue.py` | Native transports, compact TeamComms envelopes |
| `bridge.py` | `receiver.py`, `client.py`, `state.py`: authenticated streaming, SQLite recovery, versioned receipts |
| `startup.py`, `launch_codex.py` | `startup.py`, `launcher.py`: explicit configuration, reversible wrappers, native lifecycle supervision |

Paths in the final column are relative to `src/teamcomms/connectors/`.
