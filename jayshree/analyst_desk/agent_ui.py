"""Research Agent tab — run the C1 agent from the app, with both human gates.

Flow in the UI:
  1. Analyst enters a request -> Start. The agent checks scope and drafts a plan.
  2. Plan gate: approve, or edit the JSON and approve the edited plan.
  3. The agent researches with MCP tools; every tool call and result streams
     into the activity log.
  4. Note gate: the draft note, its code check (numbers verified) and buttons
     to approve, edit (re-checked, needs a fresh approval) or reject.
  5. On approval the note is signed and published; it appears under Past notes.
A run id identifies each run: paste it into "Load run" to resume later — even
after restarting the app — because every step is saved in the checkpointer.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid

import gradio as gr

EXAMPLES = [
    "Compare Microsoft's and Alphabet's operating margins over fiscal 2022-2024 and what drove the change",
    "Compare JPMorgan and Goldman Sachs pre-tax margins in 2023 and 2024 and their main risk factors",
    "How did NVIDIA's revenue and operating margin change from fiscal 2023 to 2024, and why?",
]

_FACT_TAG = re.compile(r"\[(fact|calc):\s*([^\]]+?)\s*\]")


# ── formatting ────────────────────────────────────────────────────────────────

def _args_short(args: dict) -> str:
    parts = []
    for k, v in (args or {}).items():
        if k == "code":
            v = (v.strip().splitlines() or [""])[0][:60] + ("…" if len(v) > 60 else "")
        elif k == "data":
            v = "{…}"
        parts.append(f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}")
    return ", ".join(parts)[:160]


def _event_line(ev: dict) -> str:
    n = ev.get("node")
    if n == "input_guard":
        return (f"✅ **Scope check** — companies {', '.join(ev['companies'])}" if ev["ok"]
                else f"⛔ **Out of scope** — {ev['reason']}")
    if n == "plan":
        return "🔁 **Revised plan drafted**" if ev.get("replan") else "📝 **Plan drafted** by the research lead"
    if n == "review_plan":
        return f"👤 **Plan {'edited and approved' if ev['action'] == 'edit' else 'approved'}** by the analyst"
    if n == "research":
        calls = ev.get("tool_calls") or []
        if not calls:
            return "🧠 **Agent** has enough evidence — moving to writing"
        # older runs stored just tool names; newer ones name + args
        return "🧠 **Agent calls** " + "; ".join(
            f"`{c}`" if isinstance(c, str) else f"`{c['name']}`({_args_short(c.get('args', {}))})"
            for c in calls)
    if n == "tool_result":
        mark = "✔" if ev["ok"] else "✖"
        preview = ev["preview"].replace("\n", " ")[:140]
        return f"&nbsp;&nbsp;&nbsp;{mark} `{ev['tool']}` → {preview}"
    if n == "replan":
        return f"🔁 **Replanning** — a tool result contradicted the plan: {ev['reason'][:160]}"
    if n == "write":
        return f"✍️ **Writer** drafted the note" + (f" (revision {ev['revision']})" if ev["revision"] else "")
    if n == "check":
        return (f"🔎 **Code check** — {ev['verified']}/{ev['total']} numbers verified"
                + ("" if ev["ok"] else f", {len(ev['issues'])} issue(s) sent back"))
    if n == "review_note":
        return f"👤 **Note {ev['action']}{'d' if ev['action'] != 'edit' else 'ed'}** by the analyst"
    if n == "publish":
        if ev["ok"]:
            return f"📤 **Published** to the research portal" + (f" as note #{ev['note_id']}" if ev.get("note_id") else "")
        return "❌ **Publish refused** by the portal" + (f": {ev['error']}" if ev.get("error") else "")
    if n == "status_corrected":
        return f"🛠 **Status corrected** to published (note #{ev.get('note_id')}): {ev.get('reason', '')}"
    return f"· {n}"



def _note_display(body: str) -> str:
    """Note with long [fact: ...] / [calc: ...] tags turned into numbered sources."""
    sources: list[str] = []

    def repl(m):
        sources.append(f"**{m.group(1)}** `{m.group(2)}`")
        return f"<sup>[{len(sources)}]</sup>"

    text = _FACT_TAG.sub(repl, body)
    if sources:
        text += "\n\n---\n**Sources** (XBRL facts; `calc` = numerator / denominator, computed in code)\n\n"
        text += "\n".join(f"{i}. {s}" for i, s in enumerate(sources, 1))
    return text


def _check_display(c: dict) -> str:
    head = (f"### {'✅' if c['ok'] else '⚠️'} Code check: {c['verified']}/{c['total']} numbers "
            f"verified ({c['verified_share']}%)")
    if c["issues"]:
        head += "\n\nIssues (the analyst decides whether to edit or reject):\n" + "\n".join(
            f"- {i}" for i in c["issues"])
    return head


def _past_notes() -> list[list]:
    from analystdesk.servers.portal import _db_path
    path = _db_path()
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, published_at, approver, tickers, title FROM notes "
                           "ORDER BY id DESC LIMIT 50").fetchall()
    finally:
        con.close()
    return [[r[0], r[1], r[2], ", ".join(json.loads(r[3])), r[4]] for r in rows]


# ── driving the agent ─────────────────────────────────────────────────────────

async def _drive(run_id: str, payload):
    """Yield ("event", line) while the agent runs, then ("done", result)."""
    from analystdesk.agent.runtime import run_until_gate
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev):
        await queue.put(ev)

    task = asyncio.create_task(run_until_gate(run_id, payload, on_event, transport="inprocess"))
    while not (task.done() and queue.empty()):
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            yield "event", _event_line(ev)
        except asyncio.TimeoutError:
            continue
    yield "done", task.result()


def _view(run_id: str, result: dict, log: list[str]):
    """All tab outputs for a run state."""
    v = result.get("values", {})
    gate = result["interrupts"][0] if result.get("interrupts") else None
    usage = f"tool calls **{v.get('tool_calls', 0)}** · tokens **{v.get('tokens', 0):,}**"

    plan_md, plan_json, note_md, check_md, note_body = "", "", "", "", ""
    show_plan = show_note = False
    if gate and gate["gate"] == "plan":
        status = f"### ⏸ Waiting for you: approve or edit the plan\nRun `{run_id}` · {usage}"
        p = gate["plan"]
        plan_md = (f"**Companies:** {', '.join(p['companies'])} · **Fiscal years:** "
                   f"{', '.join(map(str, p['fiscal_years']))}\n\n**Sub-questions**\n"
                   + "\n".join(f"1. {q}" for q in p["sub_questions"])
                   + "\n\n**Outline:** " + " → ".join(p["outline"]))
        plan_json = json.dumps(p, indent=2)
        show_plan = True
    elif gate and gate["gate"] == "note":
        status = f"### ⏸ Waiting for you: approve, edit or reject the note\nRun `{run_id}` · {usage}"
        note_md = f"# {gate['note']['title']}\n\n" + _note_display(gate["note"]["body"])
        check_md = _check_display(gate["check"])
        note_body = gate["note"]["body"]
        show_note = True
    else:
        outcome = v.get("outcome")
        if outcome == "published":
            status = f"### ✅ Published\nRun `{run_id}` · {usage}"
        elif outcome == "rejected":
            status = f"### 🛑 Rejected by the analyst\nRun `{run_id}` · {usage}"
        elif v.get("guard") and not v["guard"].get("ok"):
            status = f"### ⛔ Out of scope\n{v['guard']['reason']}"
        elif not v:
            status = f"### No run found with id `{run_id}`"
        else:
            status = f"### Run `{run_id}`: {outcome or 'finished'} · {usage}"
        if v.get("draft"):
            note_md = f"# {v['draft']['title']}\n\n" + _note_display(v["draft"]["body"])
            if v.get("check"):
                check_md = _check_display(v["check"])

    return (run_id, status, "\n\n".join(log) or "_No activity yet._",
            gr.update(value=plan_md), gr.update(value=plan_json), gr.update(visible=show_plan),
            gr.update(value=note_md), gr.update(value=check_md), gr.update(value=note_body),
            gr.update(visible=show_note), _past_notes())


def _log_from_trace(values: dict) -> list[str]:
    return [_event_line(ev) for ev in values.get("trace", [])]


# ── the tab ───────────────────────────────────────────────────────────────────

def build_agent_tab():
    with gr.Tab("🤖 Research Agent"):
        gr.Markdown(
            "**C1 agent** — one LangGraph agent using all MCP tools. It plans, waits for your "
            "approval, researches with tools (every call is shown live), writes a note whose "
            "numbers are inserted and checked by code, and publishes **only after you approve "
            "the exact note**. Each run has an id: load it later to resume."
        )
        with gr.Row():
            request = gr.Textbox(label="Research request", scale=5,
                                 placeholder="e.g. Compare Microsoft and Alphabet operating margins 2022-2024")
            start_btn = gr.Button("▶ Start", variant="primary", scale=1)
        gr.Examples([[e] for e in EXAMPLES], inputs=request)
        with gr.Row():
            run_id = gr.Textbox(label="Run id (paste one to resume)", scale=2)
            load_btn = gr.Button("⟳ Load run", scale=1)
            approver = gr.Textbox(label="Your name (recorded on approvals)", value="analyst", scale=2)
        with gr.Accordion("📂 Your runs: click a row to load it", open=True):
            runs_table = gr.Dataframe(
                headers=["run id", "updated (UTC)", "status", "numbers verified", "tool calls", "request"],
                interactive=False, wrap=True, max_height=260)
            refresh_runs_btn = gr.Button("↻ Refresh list", size="sm")
        status = gr.Markdown("### Enter a request and press Start, or pick a run above")

        with gr.Group(visible=False) as plan_box:
            gr.Markdown("## 📝 Plan — your approval is needed")
            plan_md = gr.Markdown()
            with gr.Accordion("✏️ Edit the plan (JSON)", open=False):
                plan_json = gr.Code(language="json", label="Plan")
                edit_plan_btn = gr.Button("Approve the edited plan")
            approve_plan_btn = gr.Button("✅ Approve plan and start research", variant="primary")

        with gr.Group(visible=False) as note_box:
            gr.Markdown("## 🧾 Draft note — your approval is needed")
            check_md = gr.Markdown()
            note_md = gr.Markdown()
            with gr.Accordion("✏️ Edit the note (an edit is re-checked and needs a fresh approval)",
                              open=False):
                note_body = gr.Textbox(lines=18, label="Note body (keep the [fact: …] tags)")
                edit_note_btn = gr.Button("Save edit and re-check")
            with gr.Row():
                approve_note_btn = gr.Button("✅ Approve and publish", variant="primary")
                reject_reason = gr.Textbox(label="Reason for rejecting", scale=2)
                reject_btn = gr.Button("🛑 Reject", variant="stop")

        with gr.Accordion("🔧 Agent activity: steps, tool calls and results (live)", open=True):
            activity = gr.Markdown("_No activity yet._")

        with gr.Accordion("📚 Published notes (research portal)", open=False):
            past = gr.Dataframe(headers=["id", "published", "approver", "tickers", "title"],
                                value=_past_notes, wrap=True)

        outputs = [run_id, status, activity, plan_md, plan_json, plan_box,
                   note_md, check_md, note_body, note_box, past]

        async def run_step(rid, payload, prior_log):
            log = list(prior_log)
            yield (rid, "### ⏳ Agent working…", "\n\n".join(log) or "_Starting…_",
                   gr.update(), gr.update(), gr.update(visible=False),
                   gr.update(), gr.update(), gr.update(), gr.update(visible=False), gr.update())
            async for kind, item in _drive(rid, payload):
                if kind == "event":
                    log.append(item)
                    yield (rid, "### ⏳ Agent working…", "\n\n".join(log),
                           gr.update(), gr.update(), gr.update(visible=False),
                           gr.update(), gr.update(), gr.update(), gr.update(visible=False), gr.update())
                else:
                    yield _view(rid, item, log)

        async def on_start(req):
            if not req.strip():
                yield (gr.update(), "### Please enter a request", *[gr.update() for _ in range(9)])
                return
            async for out in run_step(uuid.uuid4().hex[:8], {"request": req}, []):
                yield out

        async def resume_with(rid, decision):
            from langgraph.types import Command
            from analystdesk.agent.runtime import get_run
            prior = _log_from_trace((await get_run(rid))["values"])
            async for out in run_step(rid, Command(resume=decision), prior):
                yield out

        async def on_approve_plan(rid):
            async for out in resume_with(rid, {"action": "approve"}):
                yield out

        async def on_edit_plan(rid, text):
            try:
                plan = json.loads(text)
            except ValueError as e:
                yield (rid, f"### ⚠️ Plan JSON is invalid: {e}", *[gr.update() for _ in range(9)])
                return
            async for out in resume_with(rid, {"action": "edit", "plan": plan}):
                yield out

        async def on_approve_note(rid, name):
            async for out in resume_with(rid, {"action": "approve", "approver": name or "analyst"}):
                yield out

        async def on_edit_note(rid, body):
            async for out in resume_with(rid, {"action": "edit", "note": {"body": body}}):
                yield out

        async def on_reject(rid, reason):
            async for out in resume_with(rid, {"action": "reject", "reason": reason or "no reason given"}):
                yield out

        async def on_load(rid):
            from analystdesk.agent.runtime import get_run
            rid = (rid or "").strip()
            result = await get_run(rid)
            return _view(rid, result, _log_from_trace(result["values"]))

        async def refresh_runs():
            from analystdesk.agent.runtime import list_runs
            return [[r["run_id"], r["updated"], r["status"], r["check"], r["tool_calls"], r["request"]]
                    for r in await list_runs()]

        async def on_pick_run(table, evt: gr.SelectData):
            row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
            rid = table.iloc[row, 0] if hasattr(table, "iloc") else table[row][0]
            return await on_load(str(rid))

        # every action refreshes the runs list afterwards
        for trigger, fn, inputs in (
                (start_btn.click, on_start, [request]),
                (request.submit, on_start, [request]),
                (approve_plan_btn.click, on_approve_plan, [run_id]),
                (edit_plan_btn.click, on_edit_plan, [run_id, plan_json]),
                (approve_note_btn.click, on_approve_note, [run_id, approver]),
                (edit_note_btn.click, on_edit_note, [run_id, note_body]),
                (reject_btn.click, on_reject, [run_id, reject_reason]),
                (load_btn.click, on_load, [run_id])):
            trigger(fn, inputs, outputs).then(refresh_runs, None, runs_table)
        runs_table.select(on_pick_run, [runs_table], outputs)
        refresh_runs_btn.click(refresh_runs, None, runs_table)
        return refresh_runs, runs_table
