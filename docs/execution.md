# wrangle-ai execution

The optional execution connector supplies wrangle-ai's Bullpen, Bell and handler
interfaces using the central Inflight authority. Work offers are the only input;
unanswered Comms messages and alarms never become work. Existing SWF accounts,
tokens, database, claims and resource guards remain authoritative.

## Explicit offers and profiles

An offer may include `execution`: a locally configured `profile` name,
`timeout_seconds`, `permissions` (`read_only` or `workspace_write`), `model`,
`effort`, `budget_usd`, and optional `headless_after`. A null headless deadline
means interactive execution only. A timezone-aware deadline enables fallback
only after that time and before offer expiry. Interactive and headless workers
use the same atomic claim operation, which checks mode, eligibility, capabilities,
revision, generation and the complete resource set. A worker starting late must
claim again through this authority; an old discovery result grants nothing.

Private worker configuration maps profile names to an absolute workspace and a
trusted foreground command, or a Claude text-analysis backend. Command profiles
have no model budget and must be reviewed local scripts; remote offers cannot
supply command arguments or environment. Claude profiles disable tools, bound
turn count and pass the configured native spending limit. They produce analysis
only. The invoking OS account and selected command provide filesystem access;
profile permission labels do not create an OS sandbox. Mutation profiles must use
the existing local resource guard configuration and every reserved resource.
The connector never passes the TeamComms token to a child.

Effective profile, permissions, model, effort and budget must match the offer;
the requested timeout must fit the local maximum. This is local admission policy,
not authority derived from a worker's self-reported capabilities. Host configuration
owns credentials and the workspace. No model or worker is launched automatically
by installing the package, adding a session or deploying the server.

## Execution and recovery

Execution admission records a stable run ID, claim generation, profile and command
hash before spawning a child. One unresolved run blocks another run and blocks
release/completion. The worker renews the central lease during the bounded child
process. Signals, timeout or failed renewal terminate the whole foreground process
group with bounded TERM/KILL escalation; escaped daemons are outside this contract.
A guarded mutation also inherits the shared resource locks. Process exit and
bounded output are retained before the result is submitted. Completion validates
current revision, claim generation, lease, successful execution and evidence.
Failure records a stopped run and releases into blocked work with evidence.

A private journal retains exact request UUIDs, uncertain outcomes, command state
and results across restart. A run whose launch or stop is uncertain is never
re-executed automatically. Its claim and resources remain held until stopped-work
reconciliation. A saved result can retry publication without rerunning the command.
Network loss does not authorize takeover. Progress and final outcomes use the
ordinary Inflight history; delivery receipts require no model calls.

Offer notifications are PostgreSQL-backed HTTPS stream hints. The Wrangler reads
the durable offer list after waking and on its bounded safety sweep; notifications
carry no execution authority. Streams refresh authentication at most five seconds
apart, like Comms. Fallback deadlines are evaluated on the server on each claim.

## Interfaces

Existing Inflight scopes apply. `offer_work` accepts the optional execution spec;
`claim_work` adds `mode=interactive|headless` (default interactive). The immutable
receipt format omits absent new defaults for compatibility with prior exact retries.
`list_work_offers` reads eligible offers for an owned session, optional profile,
mode and explicit offer ID, with at most 100 rows. `record_execution` records an
admission or confirmed stop against the claim generation. Its HTTP paths are
`GET /api/inflight/offers/available`, `GET /api/inflight/offers/stream`, and
`POST /api/inflight/executions`. No new credential or production scope exists.

Install the `execution` package extra and apply Inflight migrations `0006_executionrun` and `0007_execution_integrity`.
Run `teamcomms-connect --config CONNECTOR worker --worker-config WORKER` for an
explicitly enabled worker, or add `--offer-id UUID --once` for one bounded offer.
Interactive mode requires the explicitly selected owned session ID. Headless mode
registers a dedicated worker session; it does not attach to existing native clients.
The first SWF acceptance uses a deterministic read-only command and the existing
program token, with no production mutation or model spending.

## Profile and result format

The [example profile](../examples/execution/worker.json) names an absolute command
and workspace. Its doer reads one JSON object on stdin containing `work`,
`execution` and the response instruction. It writes one JSON object on stdout:
`{"outcome":"what happened","evidence":["a bounded observation or link"]}`.
Combined output is limited to 48,000 bytes. Nonzero exit, malformed results,
timeout and failed renewal produce stopped/blocked work instead of completion.
Only reviewed command profiles may interpret task text; arguments and environment
come from the private local profile, never an offer.

For text-only Claude analysis, choose `backend="claude"`, one absolute Claude
executable, a model, optional effort and a positive `budget_usd`. The connector
uses print JSON mode, disables tools, MCP and hooks, limits the call to one turn,
and passes the native spending cap. These flags follow the
[Claude CLI reference](https://code.claude.com/docs/en/cli-usage). Provider-native
spending accounting applies; no independent currency meter or new API credential
system is introduced. The initial SWF commissioning exercises the deterministic
command backend, not a paid model call.

The daemon waits for authenticated offer hints and periodically reconciles
durable eligible offers. `--once --offer-id UUID` performs one immediate eligible
claim attempt and drains its work; if fallback is not eligible yet it exits
without launching. Use the daemon for automatic waiting across fallback deadlines.
Journal recovery retries saved result publication. Uncertain execution stays held
for explicit reconciliation rather than guessing whether a process survived.
