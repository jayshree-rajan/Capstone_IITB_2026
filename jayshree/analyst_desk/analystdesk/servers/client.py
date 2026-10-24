"""Load MCP tools into LangChain/LangGraph, filtered per agent.

Each server runs as its own process over stdio (development); inside Docker
the same servers will be reached over HTTP. langchain-mcp-adapters turns each
MCP tool schema into a LangChain tool an agent can call.

load_agent_tools("quant") returns only the quant agent's tools (see
AGENT_TOOLS), so an agent cannot even see a tool it is not allowed to use.
Hooks (Phase 6) attach here as tool_interceptors and check each call again.

Note: by default the adapter starts a fresh server process for every tool
call. filings-kb takes ~30 s to load its index, so agent runs (Phase 4)
should hold persistent sessions (client.session(name) + load_mcp_tools) for
the length of a run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

from . import AGENT_TOOLS, SERVERS

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

# Secrets the servers do not need are not passed to them
_WITHHELD_ENV = ("OPENROUTER_API_KEY",)


def connections(servers: list[str] | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _WITHHELD_ENV}
    return {
        name: {"transport": "stdio", "command": sys.executable, "args": ["-m", module],
               "cwd": str(PROJECT_DIR), "env": env}
        for name, module in SERVERS.items()
        if servers is None or name in servers
    }


def make_client(servers: list[str] | None = None, interceptors: list | None = None) -> MultiServerMCPClient:
    return MultiServerMCPClient(connections(servers), tool_interceptors=interceptors or [])


async def load_agent_tools(agent: str, client: MultiServerMCPClient | None = None) -> list:
    """The LangChain tools this agent is allowed to use, and no others."""
    if agent not in AGENT_TOOLS:
        raise KeyError(f"Unknown agent {agent!r}; known: {sorted(AGENT_TOOLS)}")
    allowed = AGENT_TOOLS[agent]
    client = client or make_client()
    return [t for t in await client.get_tools() if t.name in allowed]
