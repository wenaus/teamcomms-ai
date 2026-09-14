# Agent instructions

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
