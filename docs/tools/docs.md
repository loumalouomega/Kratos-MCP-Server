# Documentation lookup

Ask [DeepWiki](https://deepwiki.com/KratosMultiphysics/Kratos), which indexes
`KratosMultiphysics/Kratos`, "how/why" questions: what a solver setting does,
which parameters a process takes, what an error means. These tools are
**opt-in** and **send your question to `https://mcp.deepwiki.com`**.

Enable them by starting the server with `KRATOS_MCP_ENABLE_DEEPWIKI=1`:

```bash
claude mcp add kratos -e KRATOS_MCP_ENABLE_DEEPWIKI=1 -- uvx kratos-mcp-server
```

Without it each tool returns an `{"error": ...}` explaining how to enable it.
If your client can talk to the DeepWiki MCP directly, you can instead register it
next to this server (see [Connecting](/guide/connecting)); the server's
instructions and prompts point the assistant at either route.

DeepWiki describes the upstream repository, not the build installed here. Treat
answers as hints and confirm with `kratos_list_*`, `kratos_get_solver_defaults`
or `kratos_get_process_defaults`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KRATOS_MCP_ENABLE_DEEPWIKI` | unset | `1` enables the tools |
| `KRATOS_MCP_DEEPWIKI_URL` | `https://mcp.deepwiki.com/mcp` | endpoint (streamable HTTP) |
| `KRATOS_MCP_DEEPWIKI_TIMEOUT` | `60` | seconds to wait |

## kratos_docs_structure

List the documentation topics DeepWiki has for a repository.

| Parameter | Type | Description |
| --- | --- | --- |
| `repo` | string | `owner/name`, default `KratosMultiphysics/Kratos` |

## kratos_docs_read

Read the repository's DeepWiki documentation. Output is truncated to about
20 000 characters (`truncated: true`); prefer `kratos_docs_ask` for a specific question.

| Parameter | Type | Description |
| --- | --- | --- |
| `repo` | string | `owner/name`, default `KratosMultiphysics/Kratos` |

## kratos_docs_ask

Ask a free-form question.

| Parameter | Type | Description |
| --- | --- | --- |
| `question` | string | the question |
| `repo` | string | `owner/name`, default `KratosMultiphysics/Kratos` |

**Returns** `{repo, content, truncated, source: "deepwiki"}`, or `{"error": ...}`
when disabled, unreachable or timed out.
