# Tests

Component tests belong under directories matching the packages in
`src/teamcomms/`. Integration tests cover shared persistence, concurrent
operations, and connector delivery. Tests use isolated data and explicit
configuration.

## Service foundation

Install the test dependencies and PostgreSQL server tools (`pg_config`, `initdb`,
and `pg_ctl`), then run from the repository root:

```sh
.venv/bin/python -m pip install --editable '.[test]'
.venv/bin/python tests/run_postgres.py -q
```

The runner creates a temporary PostgreSQL cluster with a Unix socket inside a
private directory and TCP listening disabled. It overrides database configuration,
applies migrations, bootstraps synthetic identities, checks standalone startup,
and runs pytest. It stops and removes that cluster on exit. It requires a non-root
user and never uses the deployment database or credentials.

`service/test_foundation.py` checks the HTTP/MCP identity and authorization
contract, shared directory access, credential scopes and revocation, expiry,
inactive membership, invalid fields, pagination, and transport limits.

`entries/test_entries.py` checks shared entry access, text preservation, bounded
reads, concurrent and stale updates, revision attribution, pinned references,
restoration, database immutability, search, and scope enforcement. The temporary
cluster uses UTF-8 encoding. Standalone verification also provisions a scoped
credential and creates an entry through MCP for retrieval through HTTP.

`comms/test_comms.py` checks session ownership, resource identity, routing
snapshots, publication retry races, independent receipts, dispatch conflicts,
commit-ordered cursors, immutable evidence, and access bounds.
`check_comms_stream.py` exercises two standalone service processes sharing the
temporary database: cross-process wake-up, pre-connection recovery, cursor replay,
and revocation while a stream is open.
