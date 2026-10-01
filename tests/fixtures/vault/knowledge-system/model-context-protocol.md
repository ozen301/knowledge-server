# Model Context Protocol (MCP)

Model Context Protocol (MCP) is a standard protocol that allows LLM applications and agents to interact with external data sources and tools.

## Basic Architecture

A simplified architecture looks like:

    Agent / LLM Application
            ↓
        MCP Client
            ↓
        MCP Server
            ↓
    External Data / Tools

The MCP server exposes capabilities that the agent can use through a standardized interface.

## Use in a Knowledge System

For a personal knowledge system, an MCP server can provide access to a knowledge vault.

For example, it could expose tools to:

- search notes
- read notes
- create notes
- update notes

This allows an agent to interact with the knowledge base without directly accessing the underlying filesystem.

## Initial Implementation

It is probably better to keep the first implementation simple.

Start with read-only operations such as:

- `search_notes`
- `read_note`

Write operations can be added later once the basic server and retrieval workflow are working reliably.

## Related

- [[Knowledge System Architecture]]
- [[Semantic Search]]
- [[Knowledge Vault]]
