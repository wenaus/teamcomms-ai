# Agent instructions

## Command discovery

The [command quick reference](docs/commands.md) lists operator phrases and
terminal commands. Answer requests for TeamComms commands or help immediately
from the startup quick reference. Do not invoke tools, reload context, query the
directory or search the repository merely to list commands. Directory and
messaging requests use the live tools described there.

## Testing requires explicit approval

Do not run the full TeamComms test suite without Torre's explicit approval for
that run. Approval to implement, continue, commit, or deploy does not authorize
the suite.

This includes `tests/run_postgres.py` and equivalent full-suite pytest commands.
The PostgreSQL runner also executes startup and streaming checks when given a
test selector; selecting one test does not make that runner a focused check.
Do not bypass this requirement by splitting the suite into multiple commands.

Keep verification proportional to the requested change. Do not add broad test
coverage or repeat successful checks by default. If a suite run is necessary,
state its scope and reason and obtain approval before executing it.
