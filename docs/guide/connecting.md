# Connecting a client

The server speaks MCP over **stdio**: the client spawns `kratos-mcp` and
communicates over stdin/stdout. Pass `KRATOS_ROOT` (and any other
[environment variables](/guide/installation#_3-environment-variables))
through the client's `env` mechanism — or skip that entirely and let the
assistant pip-install Kratos via `kratos_install` on first use.

Two ways to launch the server:

- **`uvx kratos-mcp-server`** — published on PyPI, this fetches and runs it
  with no local checkout at all (verified end-to-end: `uvx` installs the
  wheel into an ephemeral environment and the bundled templates/tools all
  resolve correctly from there).
- **`uv --directory /path/to/Kratos-MCP-Server run kratos-mcp`** — runs
  from a local clone; use this when developing the server itself.

## Claude Code

```bash
# published on PyPI (recommended)
claude mcp add kratos -- uvx kratos-mcp-server

# from a local checkout
claude mcp add kratos -e KRATOS_ROOT=/path/to/Kratos -- \
    uv --directory /path/to/Kratos-MCP-Server run kratos-mcp
```

`KRATOS_ROOT` is optional either way — omit it to have the assistant
pip-install Kratos itself.

To give the assistant a knowledge base on Kratos itself, also add the public
[DeepWiki](https://deepwiki.com/KratosMultiphysics/Kratos) MCP next to it (free,
no login). The server's instructions and prompts point the assistant at its
`ask_question` tool for "how/why" questions:

```bash
claude mcp add --transport http deepwiki https://mcp.deepwiki.com/mcp
```

See [Documentation lookup](/tools/docs) for the alternative built-in tools.

Verify with `/mcp` inside Claude Code — the `kratos` server should list
113 tools, 21 resources and 8 prompts.

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
    },
    "deepwiki": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.deepwiki.com/mcp"]
    }
  }
}
```

The optional `deepwiki` entry adds the public DeepWiki MCP (via the `mcp-remote`
bridge, since `claude_desktop_config.json` only starts local commands).

Restart Claude Desktop afterwards.

## GitHub Copilot (VS Code)

VS Code's MCP support lives in a `mcp.json` file with a top-level `servers`
key (not `mcpServers` — this is the one place the shape differs from
Claude's config).

**Per-workspace** (recommended — checked into the repo you're working in,
so teammates get it automatically): create `.vscode/mcp.json`:

```json
{
  "servers": {
    "kratos": {
      "type": "stdio",
      "command": "uvx",
      "args": ["kratos-mcp-server"]
    }
  }
}
```

If you're pointing at a local Kratos build instead of letting `kratos_install`
pip-install one, add an `env` block:

```json
{
  "servers": {
    "kratos": {
      "type": "stdio",
      "command": "uvx",
      "args": ["kratos-mcp-server"],
      "env": {
        "KRATOS_ROOT": "/path/to/Kratos"
      }
    }
  }
}
```

**User-level** (available in every workspace): open the Command Palette
(`Ctrl+Shift+P` / `Cmd+Shift+P`) → **MCP: Open User Configuration** → add the
same `"kratos": {...}` block under `servers`.

**Using it**: open the Copilot Chat panel, switch the mode dropdown from
*Ask*/*Edit* to **Agent** — MCP tools are only available in Agent mode. Click
the 🔧 **Tools** icon to confirm `kratos` tools are listed (VS Code caps how
many tools can be active at once across all servers; deselect ones you don't
need if you hit the limit). The first time the model calls a `kratos` tool,
VS Code prompts you to allow it — approve once and it won't ask again for
that tool in that workspace.

Verify the connection: Command Palette → **MCP: List Servers** → `kratos`
should show as running; or just ask Copilot Chat (in Agent mode) *"check the
Kratos installation"* and confirm it calls `kratos_check_installation`.

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
