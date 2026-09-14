# TeamComms AI

A distributed collaboration system for humans, AI sessions, programmatic systems, and communication platforms, derived from Torre Wenaus's personal TJAI system.

The [design document](docs/design.md) describes the components, distributed architecture, and implementation sequence, including initial Claude Code and Codex support.

The repository currently provides an installable Python package scaffold. Service
and component behavior are under development.

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
.venv/bin/python -m pip install --editable .
.venv/bin/python -c "import teamcomms; print(teamcomms.__file__)"
```

The scaffold has no runtime dependencies and imports without a database,
credentials, or a TJAI installation. Package builds use setuptools:

```sh
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
```

The [test directory](tests/readme.md) and [example directory](examples/readme.md)
describe their scope.

Licensed under [Apache 2.0](LICENSE).
