"""Explicit host connector configuration, independent of server/database settings."""

import json
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit
from uuid import UUID
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
from teamcomms.dialog.schemas import Bootstrap


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    token_file: Path
    host: str = Field(default_factory=socket.gethostname, min_length=1, max_length=160)
    state_dir: Path = Field(default_factory=lambda: Path.home() / ".local/state/teamcomms")
    resource_ids: list[UUID] = Field(default_factory=list, max_length=30)
    group_ids: list[UUID] = Field(default_factory=list, max_length=30)
    topics: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=30)
    greeting: bool = True
    work: str = Field(default="", max_length=1000)
    attention_controls: bool = False
    dialog_capture: bool = False
    bootstrap: Bootstrap | None = None

    @model_validator(mode="after")
    def endpoint(self):
        parts = urlsplit(self.url)
        local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parts.scheme != "https" and not local_http) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Service URL requires HTTPS, or HTTP on loopback, without credentials/query/fragment")
        self.url = self.url.rstrip("/")
        return self

    def token(self):
        info = self.token_file.lstat()
        import stat
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Credential file must be a private regular file owned by this user")
        token = self.token_file.read_text().strip()
        if not token or len(token) > 512:
            raise ValueError("Invalid credential file")
        return token


def load(path, model=Configuration):
    path = Path(path).expanduser().resolve()
    data = json.loads(path.read_text())
    for field in ("token_file", "state_dir"):
        if field in data:
            value = Path(data[field]).expanduser()
            data[field] = value if value.is_absolute() else path.parent / value
    return model.model_validate(data)
