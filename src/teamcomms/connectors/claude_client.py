"""Deliver a peer message to an explicitly selected local Claude Code inbox.

The version-dependent socket protocol comes from the TJAI connector baseline.
A completed write reports transport progress; the model acknowledges separately.
"""

import json
import os
from pathlib import Path
import socket
import stat
import uuid

from .presentation import envelope


def send(socket_path, session_id, text, sender, message_id, token=None):
    path = Path(socket_path)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Select a Claude inbox socket owned by the current user")
    if info.st_mode & 0o077:
        raise ValueError("The Claude inbox socket must be private to its owner")
    if not text.strip():
        raise ValueError("Message is empty")
    uuid.UUID(session_id)
    uuid.UUID(message_id)
    frame = {"type": "user", "session_id": session_id, "uuid": message_id,
             "msg_id": message_id, "from": sender, "priority": "now",
             "message": {"role": "user", "content": envelope(text, message_id)}}
    payload = (json.dumps(frame, ensure_ascii=False) + "\n").encode()
    if len(payload) > 65536:
        raise ValueError("Peer messages are limited to 64 KiB")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(str(path))
        if token:
            stream.sendall((json.dumps({"type": "auth", "token": token}) + "\n").encode())
        stream.sendall(payload)
    return {"message_id": message_id, "session_id": session_id,
            "state": "written_to_transport"}
