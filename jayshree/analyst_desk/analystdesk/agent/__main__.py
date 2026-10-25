"""Run the C1 agent from the command line, one human gate at a time.

Each command runs the graph until the next human gate (or the end), saves
the state in the SQLite checkpointer and exits. The next command can come
from a new process — that is the stop-and-resume demo (L8).

    python -m analystdesk.agent start "Compare Microsoft and Alphabet operating margins 2022-2024"
    python -m analystdesk.agent resume RUN_ID --approve                 # plan gate
    python -m analystdesk.agent resume RUN_ID --edit-plan plan.json
    python -m analystdesk.agent resume RUN_ID --approve --approver Jayshree   # note gate
    python -m analystdesk.agent resume RUN_ID --edit-note note.md
    python -m analystdesk.agent resume RUN_ID --reject "reason"
    python -m analystdesk.agent show RUN_ID                             # state + trace
    python -m analystdesk.agent graph                                   # Mermaid diagram
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

CHECKPOINTS = Path(os.getenv("AGENT_CHECKPOINTS", ROOT / "data" / "runs" / "checkpoints.sqlite"))
RESEARCH_TOOLS = {"search_filings", "get_passage", "list_filings", "get_financials",
                  "get_fact", "run_python", "get_past_notes"}


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
async def open_tools():
    """Persistent MCP sessions for the whole run (stdio or HTTP)."""
    from langchain_mcp_adapters.tools import load_mcp_tools
    from ..servers.client import make_client
    client = make_client()
    async with contextlib.AsyncExitStack() as stack:
        tools = []
        for name in client.connections:
            session = await stack.enter_async_context(client.session(name))
            tools += await load_mcp_tools(session)
        yield tools


def _passage_text():
    from ..ingest import load_passages
    texts = {p["passage_id"]: p["text"] for p in load_passages()}
    return texts.get


async def _run(run_id: str, payload) -> dict:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from .graph import build_graph
    CHECKPOINTS.parent.mkdir(parents=True, exist_ok=True)
    async with open_tools() as tools, AsyncSqliteSaver.from_conn_string(str(CHECKPOINTS)) as saver:
        research = [t for t in tools if t.name in RESEARCH_TOOLS]
        publish = next(t for t in tools if t.name == "publish_note")
        graph = build_graph(make_llm(), research, publish, _passage_text()).compile(checkpointer=saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}
        async for update in graph.astream(payload, config, stream_mode="updates"):
            for node, value in update.items():
                if node == "__interrupt__":
                    continue
                for ev in (value or {}).get("trace", []):
                    print(f"  · {ev['node']}: " + json.dumps({k: v for k, v in ev.items() if k != 'node'})[:220],
                          flush=True)
        snap = await graph.aget_state(config)
        return {"values": snap.values, "interrupts": [i.value for t in snap.tasks for i in t.interrupts],
                "next": list(snap.next)}


def _report(run_id: str, result: dict) -> None:
    v = result["values"]
    print(f"\nrun {run_id}")
    if result["interrupts"]:
        gate = result["interrupts"][0]
        if gate["gate"] == "plan":
            print("WAITING FOR PLAN APPROVAL:\n" + json.dumps(gate["plan"], indent=2))
            print(f"\n  approve: python -m analystdesk.agent resume {run_id} --approve"
                  f"\n  edit:    python -m analystdesk.agent resume {run_id} --edit-plan plan.json")
        else:
            c = gate["check"]
            print(f"WAITING FOR NOTE APPROVAL — code check: {'PASS' if c['ok'] else 'ISSUES'}, "
                  f"{c['verified']}/{c['total']} numbers verified ({c['verified_share']}%)")
            for i in c["issues"]:
                print("  ! " + i)
            print(f"\n# {gate['note']['title']}\n\n{gate['note']['body']}")
            print(f"\n  approve: python -m analystdesk.agent resume {run_id} --approve --approver <name>"
                  f"\n  edit:    python -m analystdesk.agent resume {run_id} --edit-note note.md"
                  f"\n  reject:  python -m analystdesk.agent resume {run_id} --reject \"reason\"")
    else:
        print(f"FINISHED: {v.get('outcome') or ('out of scope: ' + str(v.get('guard', {}).get('reason')))}")
        if v.get("published"):
            print(v["published"].get("result", ""))
    print(f"tool calls {v.get('tool_calls', 0)}, tokens {v.get('tokens', 0)}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m analystdesk.agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("request"); s.add_argument("--run-id")
    r = sub.add_parser("resume"); r.add_argument("run_id")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--edit-plan", type=Path)
    g.add_argument("--edit-note", type=Path)
    g.add_argument("--reject")
    r.add_argument("--approver", default="analyst")
    sh = sub.add_parser("show"); sh.add_argument("run_id")
    sub.add_parser("graph")
    args = ap.parse_args()

    from langgraph.types import Command
    if args.cmd == "graph":
        from .graph import build_graph
        print(build_graph(_FakeToolLLM(), [], None).compile().get_graph().draw_mermaid())
        return
    if args.cmd == "start":
        run_id = args.run_id or uuid.uuid4().hex[:8]
        result = asyncio.run(_run(run_id, {"request": args.request}))
    elif args.cmd == "resume":
        run_id = args.run_id
        if args.approve:
            decision = {"action": "approve", "approver": args.approver}
        elif args.edit_plan:
            decision = {"action": "edit", "plan": json.loads(args.edit_plan.read_text(encoding="utf-8"))}
        elif args.edit_note:
            decision = {"action": "edit", "note": {"body": args.edit_note.read_text(encoding="utf-8")}}
        else:
            decision = {"action": "reject", "reason": args.reject}
        result = asyncio.run(_run(run_id, Command(resume=decision)))
    else:
        run_id = args.run_id
        result = asyncio.run(_show(run_id))
        for ev in result["values"].get("trace", []):
            print(f"  · {ev['node']}: " + json.dumps({k: v for k, v in ev.items() if k != 'node'})[:300])
    _report(run_id, result)


async def _show(run_id: str) -> dict:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from .graph import build_graph
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINTS)) as saver:
        graph = build_graph(_FakeToolLLM(), [], None).compile(checkpointer=saver)
        snap = await graph.aget_state({"configurable": {"thread_id": run_id}})
        return {"values": snap.values, "interrupts": [i.value for t in snap.tasks for i in t.interrupts],
                "next": list(snap.next)}


class _FakeToolLLM:
    """Stand-in model for building the graph without calling an API (show/graph)."""
    def bind_tools(self, tools):
        return self


if __name__ == "__main__":
    main()
