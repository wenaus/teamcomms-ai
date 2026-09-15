# Examples

[`connector.json`](connector.json) is a loopback development configuration for
`teamcomms-connect`. Copy it to a private configuration directory and supply the
service URL and participant credential file as described in
[Connectors setup](../docs/connectors.md). It contains no credentials.

[`mattermost.json`](mattermost.json) configures channel routes, and
[`watcher-event.json`](watcher-event.json) supplies a synthetic notification.
Copy and edit these for the intended installation; configure explicit subscriptions
before publishing. [Mattermost and watcher setup](../docs/mattermost.md) describes
commands, credentials, threading, and recovery. These examples contain no secrets.

[`execution/worker.json`](execution/worker.json) is an explicit read-only worker
profile. [`execution/inspect_package.py`](execution/inspect_package.py) reads the
installed TeamComms provenance and returns a bounded result. Configure absolute
paths and an existing credential outside the checkout, then create an eligible
execution offer as described in [execution](../docs/execution.md). Neither file
starts work on installation or uses a model.
