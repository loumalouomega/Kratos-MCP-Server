# Connecting a client

The server speaks MCP over **stdio**: the client spawns `kratos-mcp` and
communicates over stdin/stdout. Pass `KRATOS_ROOT` (and any other
[environment variables](/guide/installation#_3-environment-variables))
through the client's `env` mechanism — or skip that entirely and let the
assistant pip-install Kratos via `kratos_install` on first use.

Two ways to launch the server, depending on whether it's published to PyPI
yet:

- **`uvx kratos-mcp-server`** — once released, this fetches and runs it with no
  local checkout at all (verified end-to-end: `uvx` installs the wheel into
  an ephemeral environment and the bundled templates/tools all resolve
  correctly from there).
- **`uv --directory /path/to/Kratos-MCP-Server run kratos-mcp`** — runs
  from a local clone; use this before the first release, or when developing
  the server itself.

## Claude Code

```bash
# once published to PyPI
claude mcp add kratos -- uvx kratos-mcp-server

# from a local checkout
claude mcp add kratos -e KRATOS_ROOT=/path/to/Kratos -- \
    uv --directory /path/to/Kratos-MCP-Server run kratos-mcp
```

`KRATOS_ROOT` is optional either way — omit it to have the assistant
pip-install Kratos itself.

Verify with `/mcp` inside Claude Code — the `kratos` server should list
31 tools, 7 resources and 4 prompts.

To make it available in every project, add `--scope user`.

## Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "kratos": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/Kratos-MCP-Server",
        "run", "kratos-mcp"
      ],
      "env": {
        "KRATOS_ROOT": "/path/to/Kratos"
      }
    }
  }
}
```

Restart Claude Desktop afterwards.

## Any MCP client

The generic server description:

- **command**: `uvx kratos-mcp-server` (published) or `uv --directory
  /path/to/Kratos-MCP-Server run kratos-mcp` (local checkout)
- **transport**: stdio
- **environment**: `KRATOS_ROOT=/path/to/Kratos` (optional — omit to use
  `kratos_install` instead)

## Smoke test without a client

```bash
uv run python tests/smoke_client.py
```

This spawns the server exactly like a real client, lists tools/resources/
prompts, calls `kratos_check_installation` and generates a mesh — useful to
confirm the stdio transport is clean before wiring up an assistant.

## A note on the first call

The first Kratos-touching call after a build change (e.g.
`kratos_check_installation`) spawns a fresh interpreter and imports Kratos,
which takes a few seconds. Introspection results are cached on disk under
`~/.kratos-mcp/cache/`, so subsequent calls are instant.
