"""Deferred delivery for an already-running embedded Codex CLI.

Its native durable queue is consumed at the client's next input boundary; it
cannot steer an active turn. New interactive launches use the app-server bridge.
"""

import subprocess
import uuid

from .presentation import envelope


def send(thread_id, text, sender, message_id):
    uuid.UUID(thread_id)
    uuid.UUID(message_id)
    content = envelope(text, message_id)
    result = subprocess.run(["codex", "queue", "--thread", thread_id, "--message", content],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"Native Codex queue exited {result.returncode}: {result.stderr[-1000:]}")
    return {"message_id": message_id, "thread_id": thread_id, "state": "queued_in_client"}
