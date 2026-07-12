# Connecting a client

The server speaks MCP over **stdio**: the client spawns `kratos-mcp` and
communicates over stdin/stdout. Pass `KRATOS_ROOT` (and any other
[environment variables](/guide/installation#_3-environment-variables))
through the client's `env` mechanism.

## Claude Code

```bash
claude mcp add kratos -e KRATOS_ROOT=/path/to/Kratos -- \
    uv --directory /path/to/Kratos-MCP-Server run kratos-mcp
```

Verify with `/mcp` inside Claude Code — the `kratos` server should list
30 tools, 7 resources and 4 prompts.

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

- **command**: `uv --directory /path/to/Kratos-MCP-Server run kratos-mcp`
  (or, with the venv activated, just `kratos-mcp`)
- **transport**: stdio
- **environment**: `KRATOS_ROOT=/path/to/Kratos`

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
