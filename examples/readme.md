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
