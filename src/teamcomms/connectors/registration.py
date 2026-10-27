"""Whether the client has the service's MCP tools registered, and how to register them.

The connector delivers messages; the session's TeamComms tools come from the
service's MCP endpoint. A client with a registration of its own keeps it. For a
client without one, the launcher supplies the endpoint to the session it starts,
and only to that session (launch_supplied, claude_mcp_config, codex_overrides),
so sessions started without the connector never reach TeamComms. A session
without the tools falls back to the command-line helper, and the connector names
the gap at its start.
"""

import json
import os
from pathlib import Path
import shlex
import tomllib


LAUNCHED = "TEAMCOMMS_MCP_ENDPOINT"  # set by the launcher when it supplies the endpoint itself


def endpoint(config):
    return config.url + "/mcp/"


def launch_supplied(config):
    """True in a session the launcher started with the endpoint it supplied."""
    return os.environ.get(LAUNCHED, "").rstrip("/") == endpoint(config).rstrip("/")


def claude_mcp_config(config, directory):
    """A private --mcp-config file for one launched Claude session."""
    path = Path(directory) / "mcp.json"
    token = config.token()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"mcpServers": {"teamcomms": {"type": "http", "url": endpoint(config),
                                                "headers": {"Authorization": f"Bearer {token}"}}}}, f)
    return path


def codex_overrides(config):
    """Codex configuration overrides that give one launched app-server the endpoint."""
    return ["-c", f'mcp_servers.teamcomms.url="{endpoint(config)}"',
            "-c", 'mcp_servers.teamcomms.bearer_token_env_var="TEAMCOMMS_TOKEN"']


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
    if launch_supplied(config):
        return ""
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
