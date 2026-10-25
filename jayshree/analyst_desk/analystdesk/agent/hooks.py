"""Hooks around every tool call (L14): checks before, logging after.

    pre-tool    permission: is this tool in the calling agent's allow-list
                (servers.AGENT_TOOLS)? Denied calls never reach the tool.
                budget: has this agent used up its tool calls for the run?
                rate limit: EDGAR tools are throttled to <= 10 calls/second,
                as SEC asks of automated clients.
    post-tool   a trace event for every call: agent, tool, args, ok or error
                code, duration — streamed live to the app and kept in state.

A denied call is returned to the agent as a normal tool result with an
error code (PERMISSION_DENIED, BUDGET_EXCEEDED), so the agent can adjust
instead of crashing. The hooks are code, not prompts: the model cannot talk
its way past them.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from ..servers import AGENT_TOOLS

EDGAR_TOOLS = {"list_filings", "get_financials", "get_fact"}
EDGAR_MIN_INTERVAL_S = 0.1   # 10 requests per second


@dataclass
class Hooks:
    """Per-run hook state. allowed: agent -> tool names (defaults to AGENT_TOOLS)."""
    max_calls_per_agent: int = 12
    allowed: dict = field(default_factory=lambda: {k: set(v) for k, v in AGENT_TOOLS.items()})
    calls: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    _last_edgar: float = 0.0

    async def pre(self, agent: str, tool: str, who: str | None = None) -> dict | None:
        """None to allow the call, or an error result to return instead.

        agent: the role checked against the allow-list ("researcher");
        who: the budget holder ("researcher/MSFT"), so parallel instances
        of one role each get their own budget.
        """
        who = who or agent
        if tool not in self.allowed.get(agent, set()):
            return {"ok": False, "error": {"code": "PERMISSION_DENIED",
                                           "message": f"agent '{agent}' may not call {tool}"}}
        if self.calls.get(who, 0) >= self.max_calls_per_agent:
            return {"ok": False, "error": {"code": "BUDGET_EXCEEDED",
                                           "message": f"'{who}' used its {self.max_calls_per_agent} "
                                                      "tool calls; write up what you have"}}
        self.calls[who] = self.calls.get(who, 0) + 1
        if tool in EDGAR_TOOLS:
            wait = EDGAR_MIN_INTERVAL_S - (time.monotonic() - self._last_edgar)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_edgar = time.monotonic()
        return None

    def post(self, agent: str, tool: str, args: dict, result_text: str, ms: float,
             denied: bool = False) -> dict:
        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                parsed = json.loads(parsed[0].get("text", "{}"))
        except (ValueError, TypeError):
            parsed = {}
        ok = isinstance(parsed, dict) and parsed.get("ok") is True
        code = None if ok else ((parsed.get("error") or {}).get("code") if isinstance(parsed, dict) else None)
        event = {"node": "tool", "agent": agent, "tool": tool, "args": args, "ok": ok,
                 "error": code, "ms": round(ms), "denied": denied}
        self.events.append(event)
        from .. import observability
        observability.record_tool(event)
        return event


def wrap_for_toolnode(hooks: Hooks, agent: str, on_event=None):
    """An async ToolNode wrapper applying the hooks (used by the C1 graph)."""
    from langchain_core.messages import ToolMessage

    async def wrapper(request, execute):
        name, args = request.tool_call["name"], request.tool_call.get("args", {})
        start = time.perf_counter()
        denial = await hooks.pre(agent, name)
        if denial:
            msg = ToolMessage(json.dumps(denial), tool_call_id=request.tool_call["id"], name=name)
        else:
            msg = await execute(request)
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        event = hooks.post(agent, name, args, content, (time.perf_counter() - start) * 1000, bool(denial))
        if on_event:
            on_event(event)
        return msg
    return wrapper
