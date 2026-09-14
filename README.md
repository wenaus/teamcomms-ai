# TeamComms AI

A distributed collaboration system for humans, AI sessions, programmatic systems, and communication platforms, derived from Torre Wenaus's personal TJAI system.

The [design document](docs/design.md) describes the components, distributed architecture, and implementation sequence, including initial Claude Code and Codex support.

The central service provides PostgreSQL migrations, team and participant
identity, scoped credentials, and authenticated HTTP and MCP operations.
[Entries](docs/entries.md) provides notes and documents with revision history,
relationships, and search. [Comms](docs/comms.md) provides the session directory,
routing, durable messages, delivery receipts, and HTTP streaming/replay.
[Connectors](docs/connectors.md) packages Claude Code and Codex enrollment,
native delivery, and local recovery, with explicit host configuration.
The remaining component packages are scaffolds.

## Repository structure

| Path | Responsibility |
|---|---|
| `src/teamcomms/entries/` | Typed entries, revisions, relationships, search, and editing |
| `src/teamcomms/dialog/` | Conversation capture, provenance, and retrieval |
| `src/teamcomms/comms/` | Directory, messaging, subscriptions, delivery, and receipts |
| `src/teamcomms/pouch/` | The single shared working document, backed by Entries |
| `src/teamcomms/inflight/` | Tasks, ownership, handoffs, and resource reservations |
| `src/teamcomms/capcom/` | Topical views of work, events, decisions, and results |
| `src/teamcomms/connectors/` | AI clients, workers, and communication platforms |
| `src/teamcomms/service/` | Shared application assembly, interfaces, and lifecycle |
| `src/teamcomms/config.py` | Shared installation configuration |
| `tests/` | Component and integration tests |
| `examples/` | Runnable integration examples |
| `docs/` | Design and component documentation, with lowercase filenames |

The distribution is named `teamcomms-ai`; its Python import package is
`teamcomms`. The components share one package build and release.

## Development

Python 3.11 or later is required. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --editable '.[test]'
.venv/bin/python -c "import teamcomms; print(teamcomms.__file__)"
```

The package uses Django 5.2, PostgreSQL, and the official MCP Python SDK.
[Service setup](docs/service.md) covers database provisioning, credentials,
startup, and the API. Package builds use setuptools:

```sh
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
```

With PostgreSQL server tools installed, the [service checks](tests/readme.md)
run against a temporary database cluster:

```sh
.venv/bin/python tests/run_postgres.py -q
```

The [example directory](examples/readme.md) describes the planned integrations.

Licensed under [Apache 2.0](LICENSE).
