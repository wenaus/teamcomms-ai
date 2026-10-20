"""Whether the client has the service's MCP tools registered, and how to register them.

The connector delivers messages; the session's TeamComms tools come from the
service's MCP endpoint, which the client registers itself. A session without
them falls back to the command-line helper, so the connector names the gap and
the command that closes it at every start.
"""

import json
import os
from pathlib import Path
import shlex
import tomllib


def endpoint(config):
    return config.url + "/mcp/"


def _points_here(servers, config):
    target = endpoint(config).rstrip("/")
    return any(isinstance(s, dict) and str(s.get("url", "")).rstrip("/") == target for s in servers)


def claude_registered(config, cwd):
    """A Claude Code server at the endpoint, in user or local scope, or in the project's .mcp.json."""
    servers = []
    try:
        data = json.loads((Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json").read_text())
        servers += data.get("mcpServers", {}).values()
        servers += data.get("projects", {}).get(str(cwd), {}).get("mcpServers", {}).values()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        servers += json.loads((Path(cwd) / ".mcp.json").read_text()).get("mcpServers", {}).values()
    except (OSError, ValueError, AttributeError):
        pass
    return _points_here(servers, config)


def codex_registered(config):
    """A Codex server at the endpoint in config.toml."""
    try:
        data = tomllib.loads((Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml").read_text())
        servers = data.get("mcp_servers", {}).values()
    except (OSError, ValueError, AttributeError):
        return False
    return _points_here(servers, config)


def notice(client, config, cwd):
    """The missing-registration notice for this client, or empty when the tools are registered."""
    token = shlex.quote(str(config.token_file))
    if client == "claude":
        if claude_registered(config, cwd):
            return ""
        command = (shlex.join(["claude", "mcp", "add", "--scope", "user", "--transport", "http", "teamcomms",
                               endpoint(config), "--header"]) + f' "Authorization: Bearer $(cat {token})"')
    elif client == "codex":
        if codex_registered(config):
            return ""
        command = (f"add to ~/.codex/config.toml: [mcp_servers.teamcomms] url = \"{endpoint(config)}\", "
                   f"bearer_token_env_var = \"TEAMCOMMS_TOKEN\"; and export TEAMCOMMS_TOKEN=\"$(cat {token})\" "
                   "in the shell profile")
    else:
        return ""
    return (f"TeamComms MCP tools are not registered for {client} on this host, so this session reaches TeamComms "
            f"only through the command-line helper. Tell your operator now, with the command that registers them "
            f"for every later session: {command}")
