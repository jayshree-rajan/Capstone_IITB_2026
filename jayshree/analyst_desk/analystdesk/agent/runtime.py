"""Run the C1 agent to its next human gate — shared by the CLI and the app.

Tools are always reached over MCP, through one of three transports:
    inprocess  in-memory MCP clients for the four servers (the app: no
               subprocesses, and filings-kb reuses the app's loaded index)
    stdio      one subprocess per server (CLI default during development)
    http       Docker Compose services (when MCP_URL_<SERVER> is set)
The protocol, tool schemas and results are identical in all three.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent.parent
CHECKPOINTS = Path(os.getenv("AGENT_CHECKPOINTS", ROOT / "data" / "runs" / "checkpoints.sqlite"))
RESEARCH_TOOLS = {"search_filings", "get_passage", "list_filings", "get_financials",
                  "get_fact", "run_python", "get_past_notes"}

Event = dict[str, Any]


def make_llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=os.getenv("AGENT_MODEL", os.getenv("C0_MODEL", "openai/gpt-4.1-mini")),
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        temperature=0,
        timeout=120,
    )


@contextlib.asynccontextmanager
async def open_tools(transport: str = "auto"):
    """All MCP tools, over the chosen transport, for the length of one run."""
    from langchain_mcp_adapters.tools import load_mcp_tools
    async with contextlib.AsyncExitStack() as stack:
        tools = []
        if transport == "inprocess":
            from fastmcp import Client
            from ..servers import edgar_server, filings_kb, portal, sandbox
            for module in (filings_kb, edgar_server, sandbox, portal):
                client = await stack.enter_async_context(Client(module.mcp))
                tools += await load_mcp_tools(client.session)
        else:
            from ..servers.client import make_client
            client = make_client()
            for name in client.connections:
                session = await stack.enter_async_context(client.session(name))
                tools += await load_mcp_tools(session)
        yield tools


_passages: dict[str, str] | None = None


def passage_text(pid: str) -> str | None:
    global _passages
    if _passages is None:
        from ..ingest import load_passages
        _passages = {p["passage_id"]: p["text"] for p in load_passages()}
    return _passages.get(pid)


def _tool_events(value: dict) -> list[Event]:
    """Tool results from a ToolNode update, as display events."""
    events = []
    for m in (value or {}).get("messages", []):
        content = m.content if isinstance(m.content, str) else json.dumps(m.content)
        text = content
        try:   # adapter returns [{"type": "text", "text": "<json>"}]
            blocks = json.loads(content)
            if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
                text = blocks[0].get("text", content)
        except (ValueError, TypeError):
            pass
        ok = '"ok":true' in text.replace(" ", "")
        events.append({"node": "tool_result", "tool": getattr(m, "name", "?"), "ok": ok,
                       "preview": text[:400]})
    return events


async def run_until_gate(run_id: str, payload, on_event: Callable[[Event], Awaitable[None]] | None = None,
                         transport: str = "auto") -> dict:
    """Run from a request or Command(resume=...) to the next gate or the end."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from .graph import build_graph
    CHECKPOINTS.parent.mkdir(parents=True, exist_ok=True)
    async with open_tools(transport) as tools, AsyncSqliteSaver.from_conn_string(str(CHECKPOINTS)) as saver:
        research = [t for t in tools if t.name in RESEARCH_TOOLS]
        publish = next(t for t in tools if t.name == "publish_note")
        graph = build_graph(make_llm(), research, publish, passage_text).compile(checkpointer=saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}
        async for update in graph.astream(payload, config, stream_mode="updates"):
            for node, value in update.items():
                if node == "__interrupt__":
                    continue
                events = _tool_events(value) if node == "tools" else (value or {}).get("trace", [])
                for ev in events:
                    if on_event:
                        await on_event(ev)
        return await _snapshot(graph, config)


async def get_run(run_id: str) -> dict:
    """Current state of a saved run, without calling any model or tool."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from .graph import build_graph
    if not CHECKPOINTS.exists():
        return {"values": {}, "interrupts": [], "next": []}
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINTS)) as saver:
        graph = build_graph(_NoModel(), [], None).compile(checkpointer=saver)
        return await _snapshot(graph, {"configurable": {"thread_id": run_id}})


async def _snapshot(graph, config) -> dict:
    snap = await graph.aget_state(config)
    return {"values": snap.values, "interrupts": [i.value for t in snap.tasks for i in t.interrupts],
            "next": list(snap.next)}


class _NoModel:
    """Stand-in model for building the graph without calling an API."""
    def bind_tools(self, tools):
        return self
