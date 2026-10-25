"""Run C0 / C1 / C2 on golden cases and report (L13; experiment E1).

Each case runs as an analyst would use the system, without publishing:
  C0  fixed pipeline: one retrieval, one model call
  C1  single agent    plan gate auto-approved, stops at the note gate
  C2  team            same
Memory is forced off (AGENT_EVAL=1) so every run starts equal and held-out
cases can never benefit from earlier runs. Same model and tools for all.

Reported per configuration (evaluation protocol): task success, policy
violations, numbers verified, refusals, errors and timeouts, tokens and cost
per case, median and 95th-percentile latency.

    python scripts/run_eval.py --configs c0 c1 c2 --split dev
    python scripts/run_eval.py --configs c2 --split dev --cases d01 d14
    python scripts/run_eval.py --configs c1 c2 --split heldout --no-lessons    # E3 before
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AGENT_EVAL"] = "1"            # memory off for every evaluation run

from dotenv import load_dotenv              # noqa: E402

load_dotenv(ROOT / ".env")

from analystdesk.evaluation import load_cases, score   # noqa: E402

RESULTS = ROOT / "data" / "evaluation" / "results"
TIMEOUT_S = 480
# Blended price for openai/gpt-4.1-mini on OpenRouter ($0.40/M input, $1.60/M
# output), assuming ~90% of tokens are input as in our runs. An estimate.
PRICE_PER_M = float(os.getenv("PRICE_PER_M_TOKENS", "0.52"))


async def run_c0(case: dict) -> dict:
    from analystdesk.c0_pipeline import answer
    from analystdesk.retrieve import get_retriever
    r = await asyncio.to_thread(answer, case["request"], get_retriever())
    return {"text": r["answer"], "refused": False, "tools": ["fixed:search", "fixed:edgar"],
            "tokens": r.get("tokens", 0)}


async def run_agent(case: dict, mode: str, tag: str) -> dict:
    from langgraph.types import Command
    from analystdesk.agent.runtime import run_until_gate
    rid = f"eval-{tag}-{mode}-{case['id']}"
    r = await run_until_gate(rid, {"request": case["request"], "mode": mode, "memory_on": False},
                             transport="inprocess")
    if r["interrupts"] and r["interrupts"][0]["gate"] == "plan":
        r = await run_until_gate(rid, Command(resume={"action": "approve", "approver": "eval"}),
                                 transport="inprocess")
    v = r["values"]
    refused = bool(v.get("guard")) and not v["guard"].get("ok")
    tools = []
    for e in v.get("trace", []):
        if e.get("node") == "research":
            tools += [c if isinstance(c, str) else c["name"] for c in e.get("tool_calls") or []]
        if e.get("node") == "handoff":
            tools += [t["tool"] for t in e.get("tools", [])]
    text = "" if refused else (v.get("draft") or {}).get("body", "")
    if refused:
        text = v["guard"]["reason"]
    return {"text": text, "refused": refused, "tools": tools, "tokens": v.get("tokens", 0),
            "check": v.get("check"), "run_id": rid}


async def run_one(config: str, case: dict, tag: str) -> dict:
    start = time.perf_counter()
    try:
        coro = run_c0(case) if config == "c0" else run_agent(case, config, tag)
        out = await asyncio.wait_for(coro, timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        out = {"text": "", "error": "timeout"}
    except Exception as exc:   # noqa: BLE001 — failed runs are reported, not hidden
        out = {"text": "", "error": f"{type(exc).__name__}: {exc}"[:300]}
    out["seconds"] = round(time.perf_counter() - start, 1)
    res = score(case, out)
    res.update(config=config, run_id=out.get("run_id"))
    return res


def summarise(rows: list[dict]) -> dict:
    lat = sorted(r["seconds"] for r in rows)
    tokens = [r.get("tokens") or 0 for r in rows]
    by_type = {}
    for r in rows:
        t = by_type.setdefault(r["type"], [0, 0])
        t[0] += r["success"]
        t[1] += 1
    shares = [r["verified_share"] for r in rows if r.get("verified_share") is not None]
    return {
        "cases": len(rows),
        "success": sum(r["success"] for r in rows),
        "by_type": {k: f"{a}/{b}" for k, (a, b) in by_type.items()},
        "policy_violations": sum(r["policy_violation"] for r in rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "timeouts": sum(1 for r in rows if r.get("error") == "timeout"),
        "verified_share_mean": round(statistics.mean(shares), 1) if shares else None,
        "tokens_mean": round(statistics.mean(tokens)),
        "cost_per_case_usd": round(statistics.mean(tokens) * PRICE_PER_M / 1e6, 4),
        "latency_median_s": round(statistics.median(lat), 1),
        "latency_p95_s": lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))],
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", default=["c0", "c1", "c2"], choices=["c0", "c1", "c2"])
    ap.add_argument("--split", default="dev", choices=["dev", "heldout"])
    ap.add_argument("--cases", nargs="*", help="only these case ids")
    ap.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--no-lessons", action="store_true", help="E3 'before playbook' run")
    args = ap.parse_args()
    if args.no_lessons:
        os.environ["AGENT_LESSONS"] = "0"
    cases = [c for c in load_cases(args.split) if not args.cases or c["id"] in args.cases]
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"eval_{args.split}_{args.tag}.json"
    all_rows: dict[str, list] = {}
    for config in args.configs:
        rows = []
        for case in cases:
            r = await run_one(config, case, args.tag)
            rows.append(r)
            print(f"{config} {case['id']:4} {'PASS' if r['success'] else 'fail'}  {r['seconds']:6.1f}s  "
                  f"tokens {r.get('tokens') or 0:6}  verified {r.get('verified', '-'):>6}  {r['reason'][:70]}",
                  flush=True)
            all_rows[config] = rows
            path.write_text(json.dumps({"split": args.split, "tag": args.tag, "price_per_m": PRICE_PER_M,
                                        "lessons": not args.no_lessons,
                                        "rows": all_rows,
                                        "summary": {c: summarise(rs) for c, rs in all_rows.items()}},
                                       indent=1), encoding="utf-8")
    summary = {c: summarise(rs) for c, rs in all_rows.items()}
    keys = ["cases", "success", "by_type", "policy_violations", "verified_share_mean", "errors", "timeouts",
            "tokens_mean", "cost_per_case_usd", "latency_median_s", "latency_p95_s"]
    print("\n" + f"{'':22}" + "".join(f"{c:>26}" for c in summary))
    for k in keys:
        print(f"{k:22}" + "".join(f"{str(summary[c][k]):>26}" for c in summary))
    print(f"\nsaved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
